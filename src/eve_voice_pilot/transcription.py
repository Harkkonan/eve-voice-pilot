from __future__ import annotations

from array import array
import base64
import json
import queue
import sys
import threading
import time
from typing import Callable

import sounddevice as sd
import websocket


REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-realtime-whisper"
CAPTURE_RATE = 48000
API_RATE = 24000
CHANNELS = 1
BLOCK_SIZE = 2400


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


class RealtimeTranscriber:
    def __init__(self, api_key: str, log: Callable[[str], None]):
        self.api_key = api_key
        self.log = log

    def record_until_stopped(self, stop_event: threading.Event) -> str:
        if not self.api_key.strip():
            raise RuntimeError("Add your OpenAI API key first.")

        audio_queue: queue.Queue[bytes] = queue.Queue()
        status_messages: queue.Queue[str] = queue.Queue()

        def audio_callback(indata, frames, time_info, status) -> None:
            if status:
                status_messages.put(str(status))
            audio_queue.put(bytes(indata))

        headers = [
            f"Authorization: Bearer {self.api_key.strip()}",
            "OpenAI-Safety-Identifier: eve-voice-pilot-local-user",
        ]

        ws = websocket.create_connection(REALTIME_URL, header=headers, timeout=10)
        try:
            ws.send(json.dumps({
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

            with sd.RawInputStream(
                samplerate=CAPTURE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCK_SIZE,
                callback=audio_callback,
            ):
                self.log("Listening. Speak one command, then press the hotkey again.")
                while not stop_event.is_set():
                    self._send_audio_queue(ws, audio_queue)
                    self._log_status(status_messages)
                    time.sleep(0.01)

                drain_until = time.monotonic() + 0.25
                while time.monotonic() < drain_until:
                    self._send_audio_queue(ws, audio_queue)
                    time.sleep(0.01)

            ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            return self._receive_transcript(ws)
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _send_audio_queue(self, ws, audio_queue: queue.Queue[bytes]) -> None:
        sent_any = False
        while True:
            try:
                raw = audio_queue.get_nowait()
            except queue.Empty:
                break
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
                    if event.get("type") == "error":
                        self.log(f"OpenAI error: {event}")
            except Exception:
                pass
            finally:
                ws.settimeout(10)

    def _receive_transcript(self, ws) -> str:
        deadline = time.monotonic() + 12
        last_delta = ""
        while time.monotonic() < deadline:
            ws.settimeout(max(0.5, deadline - time.monotonic()))
            message = ws.recv()
            event = json.loads(message)
            event_type = event.get("type")
            if event_type == "conversation.item.input_audio_transcription.delta":
                last_delta += str(event.get("delta", ""))
            elif event_type == "conversation.item.input_audio_transcription.completed":
                return str(event.get("transcript", "")).strip()
            elif event_type == "error":
                raise RuntimeError(str(event.get("error", event)))
        return last_delta.strip()

    def _log_status(self, status_messages: queue.Queue[str]) -> None:
        while True:
            try:
                message = status_messages.get_nowait()
            except queue.Empty:
                return
            self.log(f"Microphone notice: {message}")

