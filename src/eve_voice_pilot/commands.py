from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import json
import re
from pathlib import Path


NORMALIZE_RE = re.compile(r"[^a-z0-9 ]+")
SPACE_RE = re.compile(r"\s+")
DEFAULT_HOLD_SECONDS = 0.10
DEFAULT_PRESS_COUNT = 1
DEFAULT_REPEAT_GAP_SECONDS = 0.10
DEFAULT_RESPONSE_CALL_SIGN = "merlin"


def normalize_phrase(value: str) -> str:
    value = value.lower().strip()
    value = NORMALIZE_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def response_call_signs(value: str) -> list[str]:
    call_signs: list[str] = []
    for item in value.split(","):
        normalized = normalize_phrase(item)
        if normalized and normalized not in call_signs:
            call_signs.append(normalized)
    return call_signs


def strip_response_call_sign(transcript: str, call_signs: list[str]) -> tuple[str, bool]:
    heard = normalize_phrase(transcript)
    if not heard:
        return "", False

    for call_sign in call_signs:
        if heard == call_sign:
            return "", True
        prefix = f"{call_sign} "
        suffix = f" {call_sign}"
        if heard.startswith(prefix):
            return heard[len(prefix):].strip(), True
        if heard.endswith(suffix):
            return heard[:-len(suffix)].strip(), True
    return heard, False


@dataclass
class VoiceCommand:
    name: str
    phrases: list[str]
    key: str
    hold_seconds: float = DEFAULT_HOLD_SECONDS
    response_suffix: str = ""
    response_text: str = ""
    press_count: int = DEFAULT_PRESS_COUNT
    repeat_gap_seconds: float = DEFAULT_REPEAT_GAP_SECONDS

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceCommand":
        try:
            hold_seconds = float(data.get("hold_seconds", DEFAULT_HOLD_SECONDS))
        except (TypeError, ValueError):
            hold_seconds = DEFAULT_HOLD_SECONDS
        hold_seconds = min(max(hold_seconds, 0.01), 2.0)
        try:
            press_count = int(data.get("press_count", DEFAULT_PRESS_COUNT))
        except (TypeError, ValueError):
            press_count = DEFAULT_PRESS_COUNT
        press_count = min(max(press_count, 1), 10)
        try:
            repeat_gap_seconds = float(data.get("repeat_gap_seconds", DEFAULT_REPEAT_GAP_SECONDS))
        except (TypeError, ValueError):
            repeat_gap_seconds = DEFAULT_REPEAT_GAP_SECONDS
        repeat_gap_seconds = min(max(repeat_gap_seconds, 0.0), 2.0)
        return cls(
            name=str(data.get("name", "")).strip(),
            phrases=[str(item).strip() for item in data.get("phrases", []) if str(item).strip()],
            key=str(data.get("key", "")).strip().upper(),
            hold_seconds=hold_seconds,
            press_count=press_count,
            repeat_gap_seconds=repeat_gap_seconds,
            response_suffix=str(data.get("response_suffix", "")).strip(),
            response_text=str(data.get("response_text", "")).strip(),
        )

    @property
    def action_summary(self) -> str:
        if self.press_count <= 1:
            return f"{self.key} for {self.hold_seconds:.2f}s"
        return f"{self.key} x{self.press_count}, hold {self.hold_seconds:.2f}s, gap {self.repeat_gap_seconds:.2f}s"

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "phrases": self.phrases,
            "key": self.key,
            "hold_seconds": round(self.hold_seconds, 3),
        }
        if self.press_count > 1:
            data["press_count"] = self.press_count
            data["repeat_gap_seconds"] = round(self.repeat_gap_seconds, 3)
        if self.response_suffix.strip():
            data["response_suffix"] = self.response_suffix.strip()
        if self.response_text.strip():
            data["response_text"] = self.response_text.strip()
        return data


@dataclass
class CommandProfile:
    name: str = "EVE commands"
    commands: list[VoiceCommand] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "CommandProfile":
        data = json.loads(path.read_text(encoding="utf-8"))
        commands = [VoiceCommand.from_dict(item) for item in data.get("commands", [])]
        return cls(name=str(data.get("name", "EVE commands")), commands=commands)

    def save(self, path: Path) -> None:
        data = {"name": self.name, "commands": [command.to_dict() for command in self.commands]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@dataclass
class CommandMatch:
    command: VoiceCommand
    phrase: str
    score: float


def find_command_match(
    transcript: str,
    commands: list[VoiceCommand],
    threshold: float = 0.84,
    ambiguity_gap: float = 0.04,
) -> CommandMatch | None:
    heard = normalize_phrase(transcript)
    if not heard:
        return None

    matches: list[CommandMatch] = []
    for command in commands:
        for phrase in command.phrases:
            normalized = normalize_phrase(phrase)
            if not normalized:
                continue
            score = SequenceMatcher(None, heard, normalized).ratio()
            if normalized in heard:
                score = max(score, 0.96)
            matches.append(CommandMatch(command=command, phrase=phrase, score=score))

    matches.sort(key=lambda item: item.score, reverse=True)
    if not matches or matches[0].score < threshold:
        return None
    if len(matches) > 1 and matches[0].score - matches[1].score < ambiguity_gap:
        return None
    return matches[0]


def find_exact_phrase_match(transcript: str, commands: list[VoiceCommand]) -> CommandMatch | None:
    heard = normalize_phrase(transcript)
    if not heard:
        return None

    padded_heard = f" {heard} "
    matches: list[CommandMatch] = []
    for command in commands:
        for phrase in command.phrases:
            normalized = normalize_phrase(phrase)
            if not normalized:
                continue
            phrase_word_count = len(normalized.split())
            if phrase_word_count == 1 and heard != normalized:
                continue
            if phrase_word_count > 1 and f" {normalized} " not in padded_heard:
                continue
            if normalized:
                matches.append(CommandMatch(command=command, phrase=phrase, score=1.0))

    if not matches:
        return None

    matches.sort(key=lambda item: (len(normalize_phrase(item.phrase).split()), len(item.phrase)), reverse=True)
    best = matches[0]
    best_length = len(normalize_phrase(best.phrase).split())
    competing_commands = [
        item for item in matches[1:]
        if len(normalize_phrase(item.phrase).split()) == best_length and item.command.name != best.command.name
    ]
    if competing_commands:
        return None
    return best
