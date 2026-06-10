from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import threading
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import winsound

from .commands import VoiceCommand


ROOT = Path(__file__).resolve().parents[2]
RESPONSE_CACHE_DIR = ROOT / "cache" / "speech"
DEFAULT_RESPONSE_SUFFIX = "Merlin"
RESPONSE_ENGINE_OPENAI = "OpenAI cached"
RESPONSE_ENGINE_ELEVENLABS = "ElevenLabs cached"
RESPONSE_ENGINE_WINDOWS = "Windows local"
RESPONSE_ENGINES = [RESPONSE_ENGINE_OPENAI, RESPONSE_ENGINE_ELEVENLABS, RESPONSE_ENGINE_WINDOWS]
DEFAULT_RESPONSE_ENGINE = RESPONSE_ENGINE_OPENAI
DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "ballad"
OPENAI_TTS_VOICES = ["ballad", "marin", "cedar", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "alloy", "ash"]
DEFAULT_ELEVENLABS_TTS_MODEL = "eleven_multilingual_v2"
DEFAULT_ELEVENLABS_TTS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
ELEVENLABS_TTS_MODELS = ["eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_v3"]
ELEVENLABS_OUTPUT_FORMAT = "pcm_22050"
DEFAULT_POWER_BALLAD_INSTRUCTIONS = (
    "Speak as a starship AI with a dramatic 1980s power ballad cadence: soaring, confident, "
    "a little theatrical, but concise and clear. Do not sing; speak the line."
)
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
ELEVENLABS_SPEECH_URL = "https://api.elevenlabs.io/v1/text-to-speech"

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


def normalize_response_text(text: str) -> str:
    return SPACE_RE.sub(" ", str(text or "").strip())


def elevenlabs_model_id(model: str = "") -> str:
    clean_model = str(model or "").strip()
    if not clean_model or clean_model == DEFAULT_OPENAI_TTS_MODEL:
        return DEFAULT_ELEVENLABS_TTS_MODEL
    return clean_model


def elevenlabs_voice_id(voice: str = "") -> str:
    clean_voice = str(voice or "").strip()
    if clean_voice and clean_voice not in OPENAI_TTS_VOICES:
        return clean_voice
    env_voice = os.environ.get("INTEL_PET_ELEVENLABS_VOICE_ID", "").strip() or os.environ.get(
        "ELEVENLABS_VOICE_ID",
        "",
    ).strip()
    return env_voice or DEFAULT_ELEVENLABS_TTS_VOICE_ID


def response_engine_requires_api_key(engine: str) -> bool:
    return engine in {RESPONSE_ENGINE_OPENAI, RESPONSE_ENGINE_ELEVENLABS}


def missing_api_key_message(engine: str) -> str:
    if engine == RESPONSE_ENGINE_ELEVENLABS:
        return "Add your ElevenLabs API key before generating ElevenLabs voice responses."
    return "Add your OpenAI API key before generating OpenAI voice responses."


def response_cache_path(
    command: VoiceCommand,
    engine: str = RESPONSE_ENGINE_WINDOWS,
    model: str = DEFAULT_OPENAI_TTS_MODEL,
    voice: str = DEFAULT_OPENAI_TTS_VOICE,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
) -> Path:
    text = response_text_for_command(command)
    digest = hashlib.sha1(
        f"{engine}\n{model}\n{voice}\n{instructions}\n{response_suffix(command)}\n{text}".encode("utf-8")
    ).hexdigest()[:16]
    return RESPONSE_CACHE_DIR / f"{digest}.wav"


def response_text_cache_path(
    text: str,
    engine: str = RESPONSE_ENGINE_WINDOWS,
    model: str = DEFAULT_OPENAI_TTS_MODEL,
    voice: str = DEFAULT_OPENAI_TTS_VOICE,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
) -> Path:
    clean_text = normalize_response_text(text)
    digest = hashlib.sha1(
        f"text\n{engine}\n{model}\n{voice}\n{instructions}\n{clean_text}".encode("utf-8")
    ).hexdigest()[:16]
    return RESPONSE_CACHE_DIR / f"text-{digest}.wav"


def command_snapshot(command: VoiceCommand) -> VoiceCommand:
    return VoiceCommand(
        name=command.name,
        phrases=list(command.phrases),
        key=command.key,
        hold_seconds=command.hold_seconds,
        press_count=command.press_count,
        repeat_gap_seconds=command.repeat_gap_seconds,
        response_suffix=command.response_suffix,
        response_text=command.response_text,
    )


def generate_response_audio(
    command: VoiceCommand,
    engine: str = RESPONSE_ENGINE_WINDOWS,
    api_key: str = "",
    model: str = DEFAULT_OPENAI_TTS_MODEL,
    voice: str = DEFAULT_OPENAI_TTS_VOICE,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    force: bool = False,
) -> Path:
    if engine == RESPONSE_ENGINE_OPENAI:
        return generate_openai_response_audio(command, api_key, model, voice, instructions, force)
    if engine == RESPONSE_ENGINE_ELEVENLABS:
        return generate_elevenlabs_response_audio(command, api_key, model, voice, instructions, force)
    return generate_windows_response_audio(command, force)


def generate_text_audio(
    text: str,
    engine: str = RESPONSE_ENGINE_WINDOWS,
    api_key: str = "",
    model: str = DEFAULT_OPENAI_TTS_MODEL,
    voice: str = DEFAULT_OPENAI_TTS_VOICE,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    force: bool = False,
) -> Path:
    clean_text = normalize_response_text(text)
    if not clean_text:
        raise RuntimeError("Voice response text is empty.")
    if engine == RESPONSE_ENGINE_ELEVENLABS:
        model = elevenlabs_model_id(model)
        voice = elevenlabs_voice_id(voice)
    path = response_text_cache_path(
        clean_text,
        engine=engine,
        model=model,
        voice=voice,
        instructions=instructions,
    )
    if engine == RESPONSE_ENGINE_OPENAI:
        return generate_openai_text_audio(clean_text, path, api_key, model, voice, instructions, force)
    if engine == RESPONSE_ENGINE_ELEVENLABS:
        return generate_elevenlabs_text_audio(clean_text, path, api_key, model, voice, instructions, force)
    return generate_windows_text_audio(clean_text, path, force)


def generate_windows_response_audio(command: VoiceCommand, force: bool = False) -> Path:
    path = response_cache_path(command, engine=RESPONSE_ENGINE_WINDOWS)
    return generate_windows_text_audio(response_text_for_command(command), path, force)


def generate_windows_text_audio(text: str, path: Path, force: bool = False) -> Path:
    clean_text = normalize_response_text(text)
    if not clean_text:
        raise RuntimeError("Voice response text is empty.")
    if path.exists():
        if force:
            path.unlink()
        else:
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
    env["EVE_VOICE_RESPONSE_TEXT"] = clean_text
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


def generate_openai_response_audio(
    command: VoiceCommand,
    api_key: str,
    model: str = DEFAULT_OPENAI_TTS_MODEL,
    voice: str = DEFAULT_OPENAI_TTS_VOICE,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    force: bool = False,
) -> Path:
    path = response_cache_path(command, engine=RESPONSE_ENGINE_OPENAI, model=model, voice=voice, instructions=instructions)
    return generate_openai_text_audio(response_text_for_command(command), path, api_key, model, voice, instructions, force)


def generate_openai_text_audio(
    text: str,
    path: Path,
    api_key: str,
    model: str = DEFAULT_OPENAI_TTS_MODEL,
    voice: str = DEFAULT_OPENAI_TTS_VOICE,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    force: bool = False,
) -> Path:
    if not api_key.strip():
        raise RuntimeError("Add your OpenAI API key before generating OpenAI voice responses.")

    clean_text = normalize_response_text(text)
    if not clean_text:
        raise RuntimeError("Voice response text is empty.")
    voice = voice.strip() or DEFAULT_OPENAI_TTS_VOICE
    instructions = instructions.strip() or DEFAULT_POWER_BALLAD_INSTRUCTIONS
    if path.exists():
        if force:
            path.unlink()
        else:
            return path

    RESPONSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}.tmp.wav")
    if temp_path.exists():
        temp_path.unlink()

    payload = {
        "model": model,
        "voice": voice,
        "input": clean_text,
        "instructions": instructions,
        "response_format": "wav",
    }
    request = urllib.request.Request(
        OPENAI_SPEECH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "OpenAI-Safety-Identifier": "eve-voice-pilot-local-user",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            audio = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_format_openai_error(detail)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI voice generation failed: {exc.reason}") from exc

    if not audio:
        raise RuntimeError("OpenAI voice generation returned no audio.")
    audio = normalize_wav_bytes(audio)
    temp_path.write_bytes(audio)
    temp_path.replace(path)
    return path


def generate_elevenlabs_response_audio(
    command: VoiceCommand,
    api_key: str,
    model: str = DEFAULT_ELEVENLABS_TTS_MODEL,
    voice: str = DEFAULT_ELEVENLABS_TTS_VOICE_ID,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    force: bool = False,
) -> Path:
    clean_model = elevenlabs_model_id(model)
    clean_voice = elevenlabs_voice_id(voice)
    path = response_cache_path(
        command,
        engine=RESPONSE_ENGINE_ELEVENLABS,
        model=clean_model,
        voice=clean_voice,
        instructions=instructions,
    )
    return generate_elevenlabs_text_audio(
        response_text_for_command(command),
        path,
        api_key,
        clean_model,
        clean_voice,
        instructions,
        force,
    )


def generate_elevenlabs_text_audio(
    text: str,
    path: Path,
    api_key: str,
    model: str = DEFAULT_ELEVENLABS_TTS_MODEL,
    voice: str = DEFAULT_ELEVENLABS_TTS_VOICE_ID,
    instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    force: bool = False,
) -> Path:
    _ = instructions
    if not api_key.strip():
        raise RuntimeError(missing_api_key_message(RESPONSE_ENGINE_ELEVENLABS))

    clean_text = normalize_response_text(text)
    if not clean_text:
        raise RuntimeError("Voice response text is empty.")
    clean_model = elevenlabs_model_id(model)
    clean_voice = elevenlabs_voice_id(voice)
    if path.exists():
        if force:
            path.unlink()
        else:
            return path

    RESPONSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}.tmp.wav")
    if temp_path.exists():
        temp_path.unlink()

    payload = {
        "text": clean_text,
        "model_id": clean_model,
    }
    voice_id = urllib.parse.quote(clean_voice, safe="")
    url = f"{ELEVENLABS_SPEECH_URL}/{voice_id}?output_format={ELEVENLABS_OUTPUT_FORMAT}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": api_key.strip(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            audio = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_format_elevenlabs_error(detail)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ElevenLabs voice generation failed: {exc.reason}") from exc

    if not audio:
        raise RuntimeError("ElevenLabs voice generation returned no audio.")
    temp_path.write_bytes(normalize_elevenlabs_audio(audio, ELEVENLABS_OUTPUT_FORMAT))
    temp_path.replace(path)
    return path


def normalize_elevenlabs_audio(audio: bytes, output_format: str = ELEVENLABS_OUTPUT_FORMAT) -> bytes:
    if len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return normalize_wav_bytes(audio)
    if output_format.startswith("pcm_"):
        return pcm_s16le_to_wav_bytes(audio, sample_rate=sample_rate_from_pcm_output_format(output_format))
    return audio


def sample_rate_from_pcm_output_format(output_format: str) -> int:
    match = re.fullmatch(r"pcm_(\d+)", str(output_format or ""))
    if not match:
        return 22050
    return int(match.group(1))


def pcm_s16le_to_wav_bytes(pcm_audio: bytes, *, sample_rate: int, channels: int = 1) -> bytes:
    sample_width = 2
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    data_size = len(pcm_audio)
    return b"".join(
        (
            b"RIFF",
            (36 + data_size).to_bytes(4, "little"),
            b"WAVE",
            b"fmt ",
            (16).to_bytes(4, "little"),
            (1).to_bytes(2, "little"),
            channels.to_bytes(2, "little"),
            sample_rate.to_bytes(4, "little"),
            byte_rate.to_bytes(4, "little"),
            block_align.to_bytes(2, "little"),
            (sample_width * 8).to_bytes(2, "little"),
            b"data",
            data_size.to_bytes(4, "little"),
            pcm_audio,
        )
    )


def normalize_wav_bytes(audio: bytes) -> bytes:
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio

    normalized = bytearray(audio)
    normalized[4:8] = (len(normalized) - 8).to_bytes(4, "little")
    position = 12
    while position + 8 <= len(normalized):
        chunk_id = bytes(normalized[position:position + 4])
        chunk_size = int.from_bytes(normalized[position + 4:position + 8], "little")
        chunk_start = position + 8
        remaining = max(0, len(normalized) - chunk_start)
        if chunk_size == 0xFFFFFFFF or chunk_start + chunk_size > len(normalized):
            chunk_size = remaining
            normalized[position + 4:position + 8] = chunk_size.to_bytes(4, "little")
        position = chunk_start + chunk_size + (chunk_size % 2)
        if chunk_id == b"data":
            break
    return bytes(normalized)


def _format_openai_error(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return f"OpenAI voice generation failed: {payload}"
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if message and code:
            return f"OpenAI voice generation failed ({code}): {message}"
        if message:
            return f"OpenAI voice generation failed: {message}"
    return f"OpenAI voice generation failed: {payload}"


def _format_elevenlabs_error(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return f"ElevenLabs voice generation failed: {payload}"
    detail = data.get("detail")
    if isinstance(detail, dict):
        message = detail.get("message")
        status = detail.get("status")
        if message and status:
            return f"ElevenLabs voice generation failed ({status}): {message}"
        if message:
            return f"ElevenLabs voice generation failed: {message}"
    if isinstance(detail, str) and detail:
        return f"ElevenLabs voice generation failed: {detail}"
    return f"ElevenLabs voice generation failed: {payload}"


class SpeechResponseManager:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self.lock = threading.RLock()
        self.pending: set[Path] = set()
        self.engine = DEFAULT_RESPONSE_ENGINE
        self.api_key = ""
        self.model = DEFAULT_OPENAI_TTS_MODEL
        self.voice = DEFAULT_OPENAI_TTS_VOICE
        self.instructions = DEFAULT_POWER_BALLAD_INSTRUCTIONS
        self.missing_key_notice_shown = False

    def configure(
        self,
        engine: str = DEFAULT_RESPONSE_ENGINE,
        api_key: str = "",
        model: str = DEFAULT_OPENAI_TTS_MODEL,
        voice: str = DEFAULT_OPENAI_TTS_VOICE,
        instructions: str = DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    ) -> None:
        with self.lock:
            self.engine = engine if engine in RESPONSE_ENGINES else DEFAULT_RESPONSE_ENGINE
            self.api_key = api_key.strip()
            if self.engine == RESPONSE_ENGINE_ELEVENLABS:
                self.model = elevenlabs_model_id(model)
                self.voice = elevenlabs_voice_id(voice)
            else:
                self.model = model.strip() or DEFAULT_OPENAI_TTS_MODEL
                self.voice = voice.strip() or DEFAULT_OPENAI_TTS_VOICE
            self.instructions = instructions.strip() or DEFAULT_POWER_BALLAD_INSTRUCTIONS
            if self.api_key:
                self.missing_key_notice_shown = False

    def prepare_commands_async(self, commands: list[VoiceCommand], force: bool = False) -> None:
        snapshots = [command_snapshot(command) for command in commands if response_enabled(command)]
        if not snapshots:
            return
        self._prepare_async(snapshots, force)

    def prepare_command_async(self, command: VoiceCommand, force: bool = False) -> None:
        if not response_enabled(command):
            return
        self._prepare_async([command_snapshot(command)], force)

    def play(self, command: VoiceCommand) -> None:
        if not response_enabled(command):
            return
        path = self._cache_path(command)
        if not path.exists():
            self.log(f"Voice response for {command.name} is generating; try the command again in a moment.")
            self.prepare_command_async(command)
            return
        self._play_path(path, f"voice response for {command.name}")

    def play_text(self, text: str, label: str = "pet message") -> None:
        clean_text = normalize_response_text(text)
        if not clean_text:
            return
        path = self._text_cache_path(clean_text)
        if path.exists():
            self._play_path(path, label)
            return
        self._prepare_text_async(clean_text, label=label, play_when_ready=True)

    def prepare_text_async(self, text: str, label: str = "pet message", force: bool = False) -> None:
        clean_text = normalize_response_text(text)
        if not clean_text:
            return
        self._prepare_text_async(clean_text, label=label, play_when_ready=False, force=force)

    def text_cache_path(self, text: str) -> Path:
        return self._text_cache_path(normalize_response_text(text))

    def text_cached(self, text: str) -> bool:
        clean_text = normalize_response_text(text)
        return bool(clean_text) and self._text_cache_path(clean_text).exists()

    def _play_path(self, path: Path, label: str) -> None:
        try:
            normalize_cached_wav(path)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception as exc:
            self.log(f"Could not play {label}: {exc}")

    def stop(self) -> None:
        try:
            winsound.PlaySound(None, 0)
        except RuntimeError:
            pass

    def _cache_path(self, command: VoiceCommand) -> Path:
        return response_cache_path(
            command,
            engine=self.engine,
            model=self.model,
            voice=self.voice,
            instructions=self.instructions,
        )

    def _text_cache_path(self, text: str) -> Path:
        return response_text_cache_path(
            text,
            engine=self.engine,
            model=self.model,
            voice=self.voice,
            instructions=self.instructions,
        )

    def _prepare_async(self, commands: list[VoiceCommand], force: bool) -> None:
        work: list[tuple[VoiceCommand, Path]] = []
        with self.lock:
            engine = self.engine
            model = self.model
            voice = self.voice
            instructions = self.instructions
            api_key = self.api_key
            if response_engine_requires_api_key(engine) and not api_key:
                if not self.missing_key_notice_shown:
                    self.missing_key_notice_shown = True
                    self.log(missing_api_key_message(engine))
                return
            for command in commands:
                path = response_cache_path(command, engine=engine, model=model, voice=voice, instructions=instructions)
                if not force and path.exists():
                    continue
                if path in self.pending:
                    continue
                self.pending.add(path)
                work.append((command, path))
        if not work:
            return
        threading.Thread(
            target=self._prepare_worker,
            args=(work, force, engine, api_key, model, voice, instructions),
            name="speech-response-worker",
            daemon=True,
        ).start()

    def _prepare_worker(
        self,
        commands: list[tuple[VoiceCommand, Path]],
        force: bool,
        engine: str,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
    ) -> None:
        for command, path in commands:
            try:
                generate_response_audio(
                    command,
                    engine=engine,
                    api_key=api_key,
                    model=model,
                    voice=voice,
                    instructions=instructions,
                    force=force,
                )
            except Exception as exc:
                self.log(f"Could not prepare voice response for {command.name}: {exc}")
            finally:
                with self.lock:
                    self.pending.discard(path)

    def _prepare_text_async(self, text: str, *, label: str, play_when_ready: bool = False, force: bool = False) -> None:
        with self.lock:
            engine = self.engine
            model = self.model
            voice = self.voice
            instructions = self.instructions
            api_key = self.api_key
            if response_engine_requires_api_key(engine) and not api_key:
                if not self.missing_key_notice_shown:
                    self.missing_key_notice_shown = True
                    self.log(missing_api_key_message(engine))
                return
            path = response_text_cache_path(text, engine=engine, model=model, voice=voice, instructions=instructions)
            if not force and path.exists():
                if play_when_ready:
                    self._play_path(path, label)
                return
            if path in self.pending:
                return
            self.pending.add(path)
        threading.Thread(
            target=self._prepare_text_worker,
            args=(text, path, label, play_when_ready, force, engine, api_key, model, voice, instructions),
            name="speech-text-worker",
            daemon=True,
        ).start()

    def _prepare_text_worker(
        self,
        text: str,
        path: Path,
        label: str,
        play_when_ready: bool,
        force: bool,
        engine: str,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
    ) -> None:
        try:
            generated_path = generate_text_audio(
                text,
                engine=engine,
                api_key=api_key,
                model=model,
                voice=voice,
                instructions=instructions,
                force=force,
            )
            if play_when_ready:
                self._play_path(generated_path, label)
        except Exception as exc:
            self.log(f"Could not prepare {label}: {exc}")
        finally:
            with self.lock:
                self.pending.discard(path)


def normalize_cached_wav(path: Path) -> None:
    try:
        original = path.read_bytes()
    except OSError:
        return
    normalized = normalize_wav_bytes(original)
    if normalized != original:
        path.write_bytes(normalized)
