from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import json
import re
from pathlib import Path


NORMALIZE_RE = re.compile(r"[^a-z0-9 ]+")
SPACE_RE = re.compile(r"\s+")
DEFAULT_HOLD_SECONDS = 0.10


def normalize_phrase(value: str) -> str:
    value = value.lower().strip()
    value = NORMALIZE_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


@dataclass
class VoiceCommand:
    name: str
    phrases: list[str]
    key: str
    hold_seconds: float = DEFAULT_HOLD_SECONDS
    response_suffix: str = ""
    response_text: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceCommand":
        try:
            hold_seconds = float(data.get("hold_seconds", DEFAULT_HOLD_SECONDS))
        except (TypeError, ValueError):
            hold_seconds = DEFAULT_HOLD_SECONDS
        hold_seconds = min(max(hold_seconds, 0.01), 2.0)
        return cls(
            name=str(data.get("name", "")).strip(),
            phrases=[str(item).strip() for item in data.get("phrases", []) if str(item).strip()],
            key=str(data.get("key", "")).strip().upper(),
            hold_seconds=hold_seconds,
            response_suffix=str(data.get("response_suffix", "")).strip(),
            response_text=str(data.get("response_text", "")).strip(),
        )

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "phrases": self.phrases,
            "key": self.key,
            "hold_seconds": round(self.hold_seconds, 3),
        }
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
