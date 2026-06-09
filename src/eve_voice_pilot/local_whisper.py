from __future__ import annotations

from collections import deque
from pathlib import Path
import queue
import tempfile
import threading
import time
from typing import Callable
import wave

import sounddevice as sd

from .transcription import (
    BLOCK_SECONDS,
    CHANNELS,
    INITIAL_SILENCE_SECONDS,
    PRE_ROLL_SECONDS,
    SPEECH_RMS_THRESHOLD,
    audio_rms,
    block_size_for_rate,
    capture_rate_for_device,
)


DEFAULT_LOCAL_WHISPER_MODEL = "base.en"
WHISPER_AUTO_STOP_SILENCE_SECONDS = 0.80
WHISPER_MAX_RECORD_SECONDS = 12.0


class LocalWhisperTranscriber:
    def __init__(
        self,
        log: Callable[[str], None],
        input_device_index: int | None = None,
        model_name: str = DEFAULT_LOCAL_WHISPER_MODEL,
    ):
        self.log = log
        self.input_device_index = input_device_index
        self.model_name = model_name.strip() or DEFAULT_LOCAL_WHISPER_MODEL
        self.model = None
        self.lock = threading.RLock()

    def record_until_stopped(
        self,
        stop_event: threading.Event,
        on_ready: Callable[[], None] | None = None,
        on_partial_match: Callable[[str], bool] | None = None,
    ) -> str:
        _ = on_partial_match
        audio_queue: queue.Queue[bytes] = queue.Queue()
        status_messages: queue.Queue[str] = queue.Queue()

        def audio_callback(indata, frames, time_info, status) -> None:
            if status:
                status_messages.put(str(status))
            audio_queue.put(bytes(indata))

        capture_rate = capture_rate_for_device(self.input_device_index)
        block_size = block_size_for_rate(capture_rate)
        pre_roll: deque[bytes] = deque(maxlen=max(1, int(PRE_ROLL_SECONDS / BLOCK_SECONDS)))
        recorded: list[bytes] = []
        started_at = time.monotonic()
        last_speech_at: float | None = None
        speech_started = False

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
            while not stop_event.is_set():
                max_rms = self._drain_audio(audio_queue, pre_roll, recorded, speech_started)
                if max_rms >= SPEECH_RMS_THRESHOLD:
                    if not speech_started:
                        recorded[:0] = list(pre_roll)
                        pre_roll.clear()
                    speech_started = True
                    last_speech_at = time.monotonic()

                now = time.monotonic()
                if last_speech_at and now - last_speech_at >= WHISPER_AUTO_STOP_SILENCE_SECONDS:
                    break
                if not last_speech_at and now - started_at >= INITIAL_SILENCE_SECONDS:
                    return ""
                if speech_started and now - started_at >= WHISPER_MAX_RECORD_SECONDS:
                    break
                self._log_status(status_messages)
                time.sleep(0.01)

        if stop_event.is_set() or not recorded:
            return ""

        self._drain_audio(audio_queue, pre_roll, recorded, speech_started=True)
        wav_path = self._write_temp_wav(recorded, capture_rate)
        try:
            return self._transcribe_wav(wav_path)
        finally:
            try:
                wav_path.unlink()
            except OSError:
                pass

    def close(self) -> None:
        return

    def warm_up(self) -> None:
        self._load_model()

    def _load_model(self):
        with self.lock:
            if self.model is not None:
                return self.model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "Local Whisper dictation needs the optional faster-whisper package. "
                    "Run scripts\\install-whisper-dictation.ps1, then restart the app."
                ) from exc

            self.log(f"Loading local Whisper model {self.model_name}.")
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self.log("Local Whisper model ready.")
            return self.model

    def _drain_audio(
        self,
        audio_queue: queue.Queue[bytes],
        pre_roll: deque[bytes],
        recorded: list[bytes],
        speech_started: bool,
    ) -> float:
        max_rms = 0.0
        while True:
            try:
                raw = audio_queue.get_nowait()
            except queue.Empty:
                break
            rms = audio_rms(raw)
            max_rms = max(max_rms, rms)
            if speech_started:
                recorded.append(raw)
            else:
                pre_roll.append(raw)
        return max_rms

    def _write_temp_wav(self, chunks: list[bytes], sample_rate: int) -> Path:
        temp_file = tempfile.NamedTemporaryFile(prefix="eve-voice-whisper-", suffix=".wav", delete=False)
        wav_path = Path(temp_file.name)
        temp_file.close()
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(b"".join(chunks))
        return wav_path

    def _transcribe_wav(self, wav_path: Path) -> str:
        model = self._load_model()
        segments, _info = model.transcribe(str(wav_path), language="en", beam_size=1)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def _log_status(self, status_messages: queue.Queue[str]) -> None:
        while True:
            try:
                message = status_messages.get_nowait()
            except queue.Empty:
                return
            self.log(f"Microphone notice: {message}")
