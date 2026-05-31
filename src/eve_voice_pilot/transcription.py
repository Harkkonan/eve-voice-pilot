from __future__ import annotations

from array import array
import base64
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
CAPTURE_RATE = 48000
API_RATE = 24000
CHANNELS = 1
BLOCK_SIZE = 960
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


def downsample_48k_to_24k(raw: bytes) -> bytes:
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) < 2:
        return b""

    output = array("h")
    for index in range(0, len(samples) - 1, 2):
        output.append((samples[index] + samples[index + 1]) // 2)
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


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
    def __init__(self, api_key: str, log: Callable[[str], None]):
        self.api_key = api_key
        self.log = log
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
        try:
            with sd.RawInputStream(
                samplerate=CAPTURE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCK_SIZE,
                callback=audio_callback,
            ):
                if on_ready:
                    on_ready()
                started_at = time.monotonic()
                last_speech_at: float | None = None
                partial_transcript = ""
                while not stop_event.is_set():
                    pump = self._send_audio_queue(ws, audio_queue, partial_transcript, on_partial_match)
                    partial_transcript = pump.partial_transcript
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
                        self.log("Silence detected. Processing command.")
                        break
                    elif not last_speech_at and now - started_at >= INITIAL_SILENCE_SECONDS:
                        break
                    elif now - started_at >= MAX_RECORD_SECONDS:
                        self.log("Recording limit reached. Processing command.")
                        break
                    self._log_status(status_messages)
                    time.sleep(0.01)

                if stop_event.is_set():
                    self._clear_input_buffer(ws)
                    return ""

                drain_until = time.monotonic() + DRAIN_SECONDS
                while time.monotonic() < drain_until:
                    pump = self._send_audio_queue(ws, audio_queue, partial_transcript, on_partial_match)
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
    ) -> AudioPumpResult:
        sent_any = False
        max_rms = 0.0
        completed_transcript: str | None = None
        fast_matched = False
        while True:
            try:
                raw = audio_queue.get_nowait()
            except queue.Empty:
                break
            max_rms = max(max_rms, audio_rms(raw))
            pcm_24k = downsample_48k_to_24k(raw)
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
        return AudioPumpResult(max_rms, partial_transcript, completed_transcript, fast_matched)

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
