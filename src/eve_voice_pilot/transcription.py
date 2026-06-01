from __future__ import annotations

from array import array
import base64
from collections import deque
from dataclasses import dataclass
import json
import math
import queue
import sys
import threading
import time
from typing import Callable

import sounddevice as sd
import websocket


REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
API_RATE = 24000
CHANNELS = 1
FALLBACK_CAPTURE_RATE = 48000
BLOCK_SECONDS = 0.02
PRE_ROLL_SECONDS = 0.25
SPEECH_RMS_THRESHOLD = 450
AUTO_STOP_SILENCE_SECONDS = 0.30
INITIAL_SILENCE_SECONDS = 4.0
MAX_RECORD_SECONDS = 4.0
DRAIN_SECONDS = 0.05


@dataclass
class AudioPumpResult:
    max_rms: float
    partial_transcript: str
    completed_transcript: str | None = None
    fast_matched: bool = False
    saw_speech: bool = False


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    sample_rate: int
    channels: int

    @property
    def label(self) -> str:
        return f"{self.index}: {self.name} ({self.sample_rate} Hz)"


def list_input_devices() -> list[InputDevice]:
    devices: list[InputDevice] = []
    try:
        raw_devices = sd.query_devices()
    except Exception:
        return devices
    for index, device in enumerate(raw_devices):
        channels = int(device.get("max_input_channels", 0))
        if channels <= 0:
            continue
        sample_rate = _sample_rate_from_device(device)
        name = str(device.get("name", f"Input {index}"))
        devices.append(InputDevice(index=index, name=name, sample_rate=sample_rate, channels=channels))
    return devices


def default_input_device_index() -> int | None:
    default_device = sd.default.device
    if isinstance(default_device, (list, tuple)):
        index = default_device[0]
    else:
        index = default_device
    try:
        return int(index)
    except (TypeError, ValueError):
        return None


def resolve_input_device_label(label: str) -> int | None:
    prefix = label.split(":", 1)[0].strip()
    try:
        return int(prefix)
    except ValueError:
        return None


def capture_rate_for_device(device_index: int | None) -> int:
    try:
        if device_index is None:
            device = sd.query_devices(kind="input")
        else:
            device = sd.query_devices(device_index, kind="input")
        return _sample_rate_from_device(device)
    except Exception:
        return FALLBACK_CAPTURE_RATE


def block_size_for_rate(sample_rate: int) -> int:
    return max(160, int(sample_rate * BLOCK_SECONDS))


def _sample_rate_from_device(device: dict) -> int:
    try:
        sample_rate = int(float(device.get("default_samplerate", FALLBACK_CAPTURE_RATE)))
    except (TypeError, ValueError):
        return FALLBACK_CAPTURE_RATE
    return sample_rate if sample_rate > 0 else FALLBACK_CAPTURE_RATE


def resample_pcm(raw: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate <= 0:
        source_rate = FALLBACK_CAPTURE_RATE
    if target_rate <= 0:
        target_rate = API_RATE
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return b""
    if source_rate == target_rate:
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()

    output_len = max(1, int(len(samples) * target_rate / source_rate))
    output = array("h")
    for output_index in range(output_len):
        source_pos = output_index * source_rate / target_rate
        left_index = int(source_pos)
        right_index = min(left_index + 1, len(samples) - 1)
        fraction = source_pos - left_index
        sample = int(samples[left_index] * (1 - fraction) + samples[right_index] * fraction)
        output.append(sample)
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def resample_pcm_to_24k(raw: bytes, source_rate: int) -> bytes:
    return resample_pcm(raw, source_rate, API_RATE)


def audio_rms(raw: bytes) -> float:
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return math.sqrt(mean_square)


class RealtimeTranscriber:
    def __init__(self, api_key: str, log: Callable[[str], None], input_device_index: int | None = None):
        self.api_key = api_key
        self.log = log
        self.input_device_index = input_device_index
        self.ws = None
        self.connected = False
        self.lock = threading.RLock()

    def record_until_stopped(
        self,
        stop_event: threading.Event,
        on_ready: Callable[[], None] | None = None,
        on_partial_match: Callable[[str], bool] | None = None,
    ) -> str:
        if not self.api_key.strip():
            raise RuntimeError("Add your OpenAI API key first.")

        audio_queue: queue.Queue[bytes] = queue.Queue()
        status_messages: queue.Queue[str] = queue.Queue()

        def audio_callback(indata, frames, time_info, status) -> None:
            if status:
                status_messages.put(str(status))
            audio_queue.put(bytes(indata))

        ws = self._connect()
        capture_rate = capture_rate_for_device(self.input_device_index)
        block_size = block_size_for_rate(capture_rate)
        pre_roll: deque[bytes] = deque(maxlen=max(1, int(PRE_ROLL_SECONDS / BLOCK_SECONDS)))
        try:
            with sd.RawInputStream(
                samplerate=capture_rate,
                device=self.input_device_index,
                channels=CHANNELS,
                dtype="int16",
                blocksize=block_size,
                callback=audio_callback,
            ):
                if on_ready:
                    on_ready()
                started_at = time.monotonic()
                last_speech_at: float | None = None
                speech_started = False
                partial_transcript = ""
                while not stop_event.is_set():
                    pump = self._send_audio_queue(
                        ws,
                        audio_queue,
                        partial_transcript,
                        on_partial_match,
                        send_audio=speech_started,
                        pre_roll=pre_roll,
                        source_rate=capture_rate,
                    )
                    partial_transcript = pump.partial_transcript
                    if pump.saw_speech:
                        speech_started = True
                    if pump.completed_transcript:
                        return pump.completed_transcript
                    if pump.fast_matched:
                        self.log("Fast command phrase detected.")
                        self._clear_input_buffer(ws)
                        return partial_transcript
                    rms = pump.max_rms
                    now = time.monotonic()
                    if rms >= SPEECH_RMS_THRESHOLD:
                        last_speech_at = now
                    elif last_speech_at and now - last_speech_at >= AUTO_STOP_SILENCE_SECONDS:
                        break
                    elif not last_speech_at and now - started_at >= INITIAL_SILENCE_SECONDS:
                        self._clear_input_buffer(ws)
                        return ""
                    elif speech_started and now - started_at >= MAX_RECORD_SECONDS:
                        break
                    self._log_status(status_messages)
                    time.sleep(0.01)

                if stop_event.is_set():
                    self._clear_input_buffer(ws)
                    return ""

                drain_until = time.monotonic() + DRAIN_SECONDS
                while time.monotonic() < drain_until:
                    pump = self._send_audio_queue(
                        ws,
                        audio_queue,
                        partial_transcript,
                        on_partial_match,
                        send_audio=speech_started,
                        pre_roll=pre_roll,
                        source_rate=capture_rate,
                    )
                    partial_transcript = pump.partial_transcript
                    if pump.completed_transcript:
                        return pump.completed_transcript
                    if pump.fast_matched:
                        self.log("Fast command phrase detected.")
                        self._clear_input_buffer(ws)
                        return partial_transcript
                    time.sleep(0.01)

            ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            return self._receive_transcript(ws, partial_transcript, on_partial_match)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        with self.lock:
            ws = self.ws
            self.ws = None
            self.connected = False
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def warm_up(self) -> None:
        self._connect()

    def _connect(self):
        with self.lock:
            if self.ws and self.connected:
                return self.ws

            headers = [
                f"Authorization: Bearer {self.api_key.strip()}",
                "OpenAI-Safety-Identifier: eve-voice-pilot-local-user",
            ]

            self.log("Connecting to OpenAI.")
            self.ws = websocket.create_connection(REALTIME_URL, header=headers, timeout=10)
            self.ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": API_RATE},
                            "transcription": {
                                "model": "gpt-realtime-whisper",
                                "language": "en",
                                "delay": "low"
                            }
                        }
                    }
                }
            }))
            self._raise_if_openai_error(self.ws)
            self.connected = True
            self.log("OpenAI connection ready.")
            return self.ws

    def _send_audio_queue(
        self,
        ws,
        audio_queue: queue.Queue[bytes],
        partial_transcript: str,
        on_partial_match: Callable[[str], bool] | None,
        send_audio: bool,
        pre_roll: deque[bytes],
        source_rate: int,
    ) -> AudioPumpResult:
        sent_any = False
        max_rms = 0.0
        completed_transcript: str | None = None
        fast_matched = False
        saw_speech = False
        while True:
            try:
                raw = audio_queue.get_nowait()
            except queue.Empty:
                break
            rms = audio_rms(raw)
            max_rms = max(max_rms, rms)
            if rms >= SPEECH_RMS_THRESHOLD:
                saw_speech = True
            if not send_audio and not saw_speech:
                pre_roll.append(raw)
                continue

            pending_audio: list[bytes] = []
            if not send_audio and saw_speech:
                pending_audio.extend(pre_roll)
                pre_roll.clear()
            pending_audio.append(raw)

            pcm_24k = b"".join(resample_pcm_to_24k(item, source_rate) for item in pending_audio)
            if not pcm_24k:
                continue
            ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm_24k).decode("ascii"),
            }))
            sent_any = True
        if sent_any:
            ws.settimeout(0.05)
            try:
                while True:
                    message = ws.recv()
                    event = json.loads(message)
                    event_type = event.get("type")
                    if event_type == "conversation.item.input_audio_transcription.delta":
                        partial_transcript += str(event.get("delta", ""))
                        if on_partial_match and on_partial_match(partial_transcript):
                            fast_matched = True
                            break
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        completed_transcript = str(event.get("transcript", "")).strip()
                        break
                    elif event_type == "error":
                        raise RuntimeError(self._format_openai_error(event))
            except websocket.WebSocketTimeoutException:
                pass
            finally:
                ws.settimeout(10)
        return AudioPumpResult(max_rms, partial_transcript, completed_transcript, fast_matched, saw_speech)

    def _receive_transcript(
        self,
        ws,
        partial_transcript: str = "",
        on_partial_match: Callable[[str], bool] | None = None,
    ) -> str:
        deadline = time.monotonic() + 4
        last_delta = partial_transcript
        while time.monotonic() < deadline:
            ws.settimeout(max(0.5, deadline - time.monotonic()))
            message = ws.recv()
            event = json.loads(message)
            event_type = event.get("type")
            if event_type == "conversation.item.input_audio_transcription.delta":
                last_delta += str(event.get("delta", ""))
                if on_partial_match and on_partial_match(last_delta):
                    return last_delta.strip()
            elif event_type == "conversation.item.input_audio_transcription.completed":
                return str(event.get("transcript", "")).strip()
            elif event_type == "error":
                raise RuntimeError(self._format_openai_error(event))
        return last_delta.strip()

    def _clear_input_buffer(self, ws) -> None:
        try:
            ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
        except Exception:
            pass

    def _log_status(self, status_messages: queue.Queue[str]) -> None:
        while True:
            try:
                message = status_messages.get_nowait()
            except queue.Empty:
                return
            self.log(f"Microphone notice: {message}")

    def _raise_if_openai_error(self, ws) -> None:
        ws.settimeout(1)
        try:
            while True:
                event = json.loads(ws.recv())
                if event.get("type") == "error":
                    raise RuntimeError(self._format_openai_error(event))
                if event.get("type") in {"session.updated", "transcription_session.updated"}:
                    return
        except websocket.WebSocketTimeoutException:
            return
        finally:
            ws.settimeout(10)

    def _format_openai_error(self, event: dict) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if message and code:
                return f"OpenAI error ({code}): {message}"
            if message:
                return f"OpenAI error: {message}"
        return f"OpenAI error: {event}"
