from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import queue
import threading
import time
from typing import Callable

import sounddevice as sd

from .commands import VoiceCommand, normalize_phrase
from .transcription import (
    AUTO_STOP_SILENCE_SECONDS,
    BLOCK_SECONDS,
    CHANNELS,
    DRAIN_SECONDS,
    INITIAL_SILENCE_SECONDS,
    MAX_RECORD_SECONDS,
    PRE_ROLL_SECONDS,
    SPEECH_RMS_THRESHOLD,
    AudioPumpResult,
    audio_rms,
    block_size_for_rate,
    capture_rate_for_device,
    resample_pcm,
)


ROOT = Path(__file__).resolve().parents[2]
LOCAL_RATE = 16000
DEFAULT_MODEL_PATH = ROOT / "models" / "vosk-model-small-en-us-0.15"


def command_phrases_for_grammar(commands: list[VoiceCommand], response_call_signs: list[str] | None = None) -> list[str]:
    command_phrases = [
        (command, normalized)
        for command in commands
        for phrase in command.phrases
        if (normalized := normalize_phrase(phrase))
    ]
    phrases = {phrase for _, phrase in command_phrases}
    for call_sign in response_call_signs or []:
        normalized_call_sign = normalize_phrase(call_sign)
        if not normalized_call_sign:
            continue
        for command, phrase in command_phrases:
            if not command.response_suffix.strip():
                continue
            phrases.add(f"{phrase} {normalized_call_sign}")
            phrases.add(f"{normalized_call_sign} {phrase}")
    return sorted(phrases)


class LocalVoskTranscriber:
    def __init__(
        self,
        commands: list[VoiceCommand],
        log: Callable[[str], None],
        input_device_index: int | None = None,
        model_path: Path | None = None,
        response_call_signs: list[str] | None = None,
    ):
        self.commands = commands
        self.log = log
        self.input_device_index = input_device_index
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.response_call_signs = response_call_signs or []
        self.model = None
        self.lock = threading.RLock()

    def record_until_stopped(
        self,
        stop_event: threading.Event,
        on_ready: Callable[[], None] | None = None,
        on_partial_match: Callable[[str], bool] | None = None,
    ) -> str:
        model = self._load_model()
        recognizer = self._create_recognizer(model)
        audio_queue: queue.Queue[bytes] = queue.Queue()
        status_messages: queue.Queue[str] = queue.Queue()

        def audio_callback(indata, frames, time_info, status) -> None:
            if status:
                status_messages.put(str(status))
            audio_queue.put(bytes(indata))

        capture_rate = capture_rate_for_device(self.input_device_index)
        block_size = block_size_for_rate(capture_rate)
        pre_roll: deque[bytes] = deque(maxlen=max(1, int(PRE_ROLL_SECONDS / BLOCK_SECONDS)))
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
                pump = self._process_audio_queue(
                    recognizer,
                    audio_queue,
                    partial_transcript,
                    on_partial_match,
                    process_audio=speech_started,
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
                    return partial_transcript
                rms = pump.max_rms
                now = time.monotonic()
                if rms >= SPEECH_RMS_THRESHOLD:
                    last_speech_at = now
                elif last_speech_at and now - last_speech_at >= AUTO_STOP_SILENCE_SECONDS:
                    break
                elif not last_speech_at and now - started_at >= INITIAL_SILENCE_SECONDS:
                    return ""
                elif speech_started and now - started_at >= MAX_RECORD_SECONDS:
                    break
                self._log_status(status_messages)
                time.sleep(0.01)

            if stop_event.is_set():
                return ""

            drain_until = time.monotonic() + DRAIN_SECONDS
            while time.monotonic() < drain_until:
                pump = self._process_audio_queue(
                    recognizer,
                    audio_queue,
                    partial_transcript,
                    on_partial_match,
                    process_audio=speech_started,
                    pre_roll=pre_roll,
                    source_rate=capture_rate,
                )
                partial_transcript = pump.partial_transcript
                if pump.completed_transcript:
                    return pump.completed_transcript
                if pump.fast_matched:
                    self.log("Fast command phrase detected.")
                    return partial_transcript
                time.sleep(0.01)

        final_text = self._text_from_result(recognizer.FinalResult())
        return final_text or partial_transcript.strip()

    def close(self) -> None:
        return

    def warm_up(self) -> None:
        self._load_model()

    def _load_model(self):
        with self.lock:
            if self.model is not None:
                return self.model
            if not self.model_path.exists():
                raise RuntimeError(
                    "Local speech model is missing. Run scripts\\download-vosk-model.ps1, then start the app again."
                )
            try:
                from vosk import Model, SetLogLevel
            except ImportError as exc:
                raise RuntimeError("Local speech package is missing. Run scripts\\setup.ps1, then start the app again.") from exc

            SetLogLevel(-1)
            self.log("Loading local speech model.")
            self.model = Model(str(self.model_path))
            self.log("Local speech model ready.")
            return self.model

    def _create_recognizer(self, model):
        from vosk import KaldiRecognizer

        grammar = command_phrases_for_grammar(self.commands, self.response_call_signs)
        if not grammar:
            grammar = ["[unk]"]
        elif "[unk]" not in grammar:
            grammar.append("[unk]")
        recognizer = KaldiRecognizer(model, LOCAL_RATE, json.dumps(grammar))
        recognizer.SetWords(False)
        return recognizer

    def _process_audio_queue(
        self,
        recognizer,
        audio_queue: queue.Queue[bytes],
        partial_transcript: str,
        on_partial_match: Callable[[str], bool] | None,
        process_audio: bool,
        pre_roll: deque[bytes],
        source_rate: int,
    ) -> AudioPumpResult:
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
            if not process_audio and not saw_speech:
                pre_roll.append(raw)
                continue

            pending_audio: list[bytes] = []
            if not process_audio and saw_speech:
                pending_audio.extend(pre_roll)
                pre_roll.clear()
            pending_audio.append(raw)

            pcm_16k = b"".join(resample_pcm(item, source_rate, LOCAL_RATE) for item in pending_audio)
            if not pcm_16k:
                continue

            if recognizer.AcceptWaveform(pcm_16k):
                text = self._text_from_result(recognizer.Result())
                if text:
                    completed_transcript = text
                    break

            partial = self._partial_from_result(recognizer.PartialResult())
            if partial:
                partial_transcript = partial
                if on_partial_match and on_partial_match(partial_transcript):
                    fast_matched = True
                    break

        return AudioPumpResult(max_rms, partial_transcript, completed_transcript, fast_matched, saw_speech)

    def _text_from_result(self, payload: str) -> str:
        try:
            return str(json.loads(payload).get("text", "")).strip()
        except (TypeError, json.JSONDecodeError):
            return ""

    def _partial_from_result(self, payload: str) -> str:
        try:
            return str(json.loads(payload).get("partial", "")).strip()
        except (TypeError, json.JSONDecodeError):
            return ""

    def _log_status(self, status_messages: queue.Queue[str]) -> None:
        while True:
            try:
                message = status_messages.get_nowait()
            except queue.Empty:
                return
            self.log(f"Microphone notice: {message}")
