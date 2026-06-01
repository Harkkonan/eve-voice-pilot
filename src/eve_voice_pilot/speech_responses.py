from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re
import subprocess
import threading
from typing import Callable
import winsound

from .commands import VoiceCommand


ROOT = Path(__file__).resolve().parents[2]
RESPONSE_CACHE_DIR = ROOT / "cache" / "speech"
DEFAULT_RESPONSE_SUFFIX = "Aura"

CREATE_NO_WINDOW = 0x08000000
SPEECH_RE = re.compile(r"[^a-zA-Z0-9 ]+")
SPACE_RE = re.compile(r"\s+")

KNOWN_RESPONSE_TEXT = {
    "all drones: return to drone bay": "Drones returning.",
    "all drones: engage": "Drones engaging.",
    "all drones: return and orbit": "Drones returning to orbit.",
    "launch drones": "Drones launching.",
    "stop ship": "Full stop.",
    "warp to": "Warp command sent.",
    "dock/jump/activate gate": "Gate command sent.",
    "directional scan": "Directional scan pulsed.",
    "map": "Map open.",
    "solar system map": "System map open.",
}


def response_enabled(command: VoiceCommand) -> bool:
    return bool(command.response_suffix.strip())


def response_suffix(command: VoiceCommand) -> str:
    return command.response_suffix.strip() or DEFAULT_RESPONSE_SUFFIX


def response_text_for_command(command: VoiceCommand) -> str:
    if command.response_text.strip():
        return command.response_text.strip()

    known = KNOWN_RESPONSE_TEXT.get(command.name.strip().casefold())
    if known:
        return known

    cleaned = command.name.replace("Broadcast: ", "Broadcast ")
    cleaned = SPEECH_RE.sub(" ", cleaned)
    cleaned = SPACE_RE.sub(" ", cleaned).strip()
    return f"{cleaned} confirmed." if cleaned else "Command confirmed."


def response_cache_path(command: VoiceCommand) -> Path:
    text = response_text_for_command(command)
    digest = hashlib.sha1(f"{response_suffix(command)}\n{text}".encode("utf-8")).hexdigest()[:16]
    return RESPONSE_CACHE_DIR / f"{digest}.wav"


def command_snapshot(command: VoiceCommand) -> VoiceCommand:
    return VoiceCommand(
        name=command.name,
        phrases=list(command.phrases),
        key=command.key,
        hold_seconds=command.hold_seconds,
        response_suffix=command.response_suffix,
        response_text=command.response_text,
    )


def generate_response_audio(command: VoiceCommand) -> Path:
    path = response_cache_path(command)
    if path.exists():
        return path

    RESPONSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}.tmp.wav")
    if temp_path.exists():
        temp_path.unlink()

    script = r"""
Add-Type -AssemblyName System.Speech
$text = $env:EVE_VOICE_RESPONSE_TEXT
$out = $env:EVE_VOICE_RESPONSE_OUT
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = -1
$synth.Volume = 85
$synth.SetOutputToWaveFile($out)
$synth.Speak($text)
$synth.Dispose()
"""
    env = dict(os.environ)
    env["EVE_VOICE_RESPONSE_TEXT"] = response_text_for_command(command)
    env["EVE_VOICE_RESPONSE_OUT"] = str(temp_path)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
        env=env,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Windows speech synthesis failed.").strip()
        raise RuntimeError(detail)
    if not temp_path.exists():
        raise RuntimeError("Windows speech synthesis did not create an audio file.")
    temp_path.replace(path)
    return path


class SpeechResponseManager:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self.lock = threading.RLock()
        self.pending: set[Path] = set()

    def prepare_commands_async(self, commands: list[VoiceCommand]) -> None:
        snapshots = [command_snapshot(command) for command in commands if response_enabled(command)]
        if not snapshots:
            return
        self._prepare_async(snapshots)

    def prepare_command_async(self, command: VoiceCommand) -> None:
        if not response_enabled(command):
            return
        self._prepare_async([command_snapshot(command)])

    def play(self, command: VoiceCommand) -> None:
        if not response_enabled(command):
            return
        path = response_cache_path(command)
        if not path.exists():
            self.prepare_command_async(command)
            return
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError as exc:
            self.log(f"Could not play voice response for {command.name}: {exc}")

    def stop(self) -> None:
        try:
            winsound.PlaySound(None, 0)
        except RuntimeError:
            pass

    def _prepare_async(self, commands: list[VoiceCommand]) -> None:
        work: list[VoiceCommand] = []
        with self.lock:
            for command in commands:
                path = response_cache_path(command)
                if path.exists() or path in self.pending:
                    continue
                self.pending.add(path)
                work.append(command)
        if not work:
            return
        threading.Thread(target=self._prepare_worker, args=(work,), name="speech-response-worker", daemon=True).start()

    def _prepare_worker(self, commands: list[VoiceCommand]) -> None:
        for command in commands:
            path = response_cache_path(command)
            try:
                generate_response_audio(command)
            except Exception as exc:
                self.log(f"Could not prepare voice response for {command.name}: {exc}")
            finally:
                with self.lock:
                    self.pending.discard(path)
