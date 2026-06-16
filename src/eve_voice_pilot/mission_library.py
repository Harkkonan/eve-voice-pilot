from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from eve_voice_pilot.commands import normalize_phrase
from eve_voice_pilot.corp_intel import ROOT
from eve_voice_pilot.speech_responses import normalize_response_text


DEFAULT_MISSION_LIBRARY_PATH = ROOT / "data" / "intel_pet_missions_starter.json"
USER_MISSION_LIBRARY_PATH = ROOT / "profiles" / "intel_pet_missions.json"
MISSION_LIBRARY_VERSION = 1


@dataclass(frozen=True)
class MissionLibraryEntry:
    id: str
    title: str
    mission_giver: str = ""
    agent_corporation: str = ""
    faction: str = ""
    level: str = ""
    mission_type: str = ""
    objective_text: str = ""
    completion_steps: tuple[str, ...] = ()
    completion_notes: str = ""
    briefing_text: str = ""
    standing_rewards: tuple[str, ...] = ()
    isk_reward: str = ""
    bonus_isk_reward: str = ""
    item_rewards: tuple[str, ...] = ()
    lp_reward: str = ""
    reward_notes: str = ""
    source: str = ""
    source_url: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def giver_label(self) -> str:
        return self.mission_giver or "Unknown mission giver"

    @property
    def reward_summary(self) -> str:
        parts: list[str] = []
        if self.isk_reward:
            parts.append(f"ISK: {self.isk_reward}")
        if self.bonus_isk_reward:
            parts.append(f"Bonus: {self.bonus_isk_reward}")
        if self.lp_reward:
            parts.append(f"LP: {self.lp_reward}")
        if self.item_rewards:
            parts.append(f"Items: {', '.join(self.item_rewards)}")
        if self.standing_rewards:
            parts.append(f"Standings: {', '.join(self.standing_rewards)}")
        return "; ".join(parts) or "Rewards are not recorded for this mission."


@dataclass(frozen=True)
class MissionReadOptions:
    opener: str = "Mission briefing"
    include_giver: bool = True
    include_level: bool = True
    include_rewards: bool = True
    include_reward_notes: bool = True
    include_source: bool = False
    include_completion: bool = True
    include_briefing: bool = True


def clean_mission_text(value: Any) -> str:
    return normalize_response_text(str(value or ""))


def clean_mission_terms(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = list(value or [])
    terms: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = clean_mission_text(item)
        folded = text.casefold()
        if text and folded not in seen:
            terms.append(text)
            seen.add(folded)
    return tuple(terms)


def mission_entry_from_dict(payload: dict[str, Any]) -> MissionLibraryEntry:
    title = clean_mission_text(payload.get("title"))
    entry_id = clean_mission_text(payload.get("id")) or normalize_phrase(title).replace(" ", "-")
    if not title:
        raise ValueError("Mission entry is missing title.")
    if not entry_id:
        raise ValueError(f"Mission entry {title!r} is missing id.")
    return MissionLibraryEntry(
        id=entry_id,
        title=title,
        mission_giver=clean_mission_text(payload.get("mission_giver")),
        agent_corporation=clean_mission_text(payload.get("agent_corporation")),
        faction=clean_mission_text(payload.get("faction")),
        level=clean_mission_text(payload.get("level")),
        mission_type=clean_mission_text(payload.get("mission_type")),
        objective_text=clean_mission_text(payload.get("objective_text")),
        completion_steps=clean_mission_terms(payload.get("completion_steps")),
        completion_notes=clean_mission_text(payload.get("completion_notes")),
        briefing_text=clean_mission_text(payload.get("briefing_text")),
        standing_rewards=clean_mission_terms(payload.get("standing_rewards")),
        isk_reward=clean_mission_text(payload.get("isk_reward")),
        bonus_isk_reward=clean_mission_text(payload.get("bonus_isk_reward")),
        item_rewards=clean_mission_terms(payload.get("item_rewards")),
        lp_reward=clean_mission_text(payload.get("lp_reward")),
        reward_notes=clean_mission_text(payload.get("reward_notes")),
        source=clean_mission_text(payload.get("source")),
        source_url=clean_mission_text(payload.get("source_url")),
        tags=clean_mission_terms(payload.get("tags")),
    )


def mission_entry_to_dict(entry: MissionLibraryEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "title": entry.title,
        "mission_giver": entry.mission_giver,
        "agent_corporation": entry.agent_corporation,
        "faction": entry.faction,
        "level": entry.level,
        "mission_type": entry.mission_type,
        "objective_text": entry.objective_text,
        "completion_steps": list(entry.completion_steps),
        "completion_notes": entry.completion_notes,
        "briefing_text": entry.briefing_text,
        "standing_rewards": list(entry.standing_rewards),
        "isk_reward": entry.isk_reward,
        "bonus_isk_reward": entry.bonus_isk_reward,
        "item_rewards": list(entry.item_rewards),
        "lp_reward": entry.lp_reward,
        "reward_notes": entry.reward_notes,
        "source": entry.source,
        "source_url": entry.source_url,
        "tags": list(entry.tags),
    }


def load_mission_entries_from_path(path: Path) -> tuple[MissionLibraryEntry, ...]:
    library_path = path.expanduser()
    if not library_path.exists():
        return ()
    payload = json.loads(library_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        mission_payloads = payload
    elif isinstance(payload, dict):
        mission_payloads = payload.get("missions", [])
    else:
        raise ValueError(f"Mission library should be a JSON object or array: {library_path}")
    return tuple(mission_entry_from_dict(item) for item in mission_payloads if isinstance(item, dict))


def sorted_mission_entries(entries: Iterable[MissionLibraryEntry]) -> tuple[MissionLibraryEntry, ...]:
    return tuple(sorted(entries, key=lambda entry: (entry.giver_label.casefold(), entry.title.casefold())))


def load_mission_library(path: Path | None = None) -> tuple[MissionLibraryEntry, ...]:
    if path is not None:
        return sorted_mission_entries(load_mission_entries_from_path(path))
    merged: dict[str, MissionLibraryEntry] = {}
    for entry in load_mission_entries_from_path(DEFAULT_MISSION_LIBRARY_PATH):
        merged[entry.id] = entry
    for entry in load_mission_entries_from_path(USER_MISSION_LIBRARY_PATH):
        merged[entry.id] = entry
    return sorted_mission_entries(merged.values())


def mission_library_path(path: Path | None = None) -> Path:
    if path is not None:
        return path.expanduser()
    if USER_MISSION_LIBRARY_PATH.exists():
        return USER_MISSION_LIBRARY_PATH
    return DEFAULT_MISSION_LIBRARY_PATH


def save_mission_library(path: Path, entries: Iterable[MissionLibraryEntry]) -> None:
    mission_entries = sorted_mission_entries(entries)
    payload = {
        "version": MISSION_LIBRARY_VERSION,
        "notes": [
            "Local Intel Pet mission library. This file is ignored by git.",
            "Entries here add to or override bundled starter missions by id.",
        ],
        "missions": [mission_entry_to_dict(entry) for entry in mission_entries],
    }
    library_path = path.expanduser()
    library_path.parent.mkdir(parents=True, exist_ok=True)
    library_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_user_mission_library() -> tuple[MissionLibraryEntry, ...]:
    return sorted_mission_entries(load_mission_entries_from_path(USER_MISSION_LIBRARY_PATH))


def save_user_mission_library(entries: Iterable[MissionLibraryEntry]) -> None:
    save_mission_library(USER_MISSION_LIBRARY_PATH, entries)


def upsert_user_mission_entry(entry: MissionLibraryEntry) -> tuple[MissionLibraryEntry, ...]:
    entries = {current.id: current for current in load_user_mission_library()}
    entries[entry.id] = entry
    save_user_mission_library(entries.values())
    return load_user_mission_library()


def delete_user_mission_entry(entry_id: str) -> tuple[MissionLibraryEntry, ...]:
    clean_id = clean_mission_text(entry_id)
    entries = [entry for entry in load_user_mission_library() if entry.id != clean_id]
    save_user_mission_library(entries)
    return tuple(entries)


def mission_read_options_from_dict(payload: dict[str, Any] | None) -> MissionReadOptions:
    data = payload or {}
    opener = clean_mission_text(data.get("opener")) or MissionReadOptions.opener
    return MissionReadOptions(
        opener=opener,
        include_giver=bool(data.get("include_giver", True)),
        include_level=bool(data.get("include_level", True)),
        include_rewards=bool(data.get("include_rewards", True)),
        include_reward_notes=bool(data.get("include_reward_notes", True)),
        include_source=bool(data.get("include_source", False)),
        include_completion=bool(data.get("include_completion", True)),
        include_briefing=bool(data.get("include_briefing", True)),
    )


def mission_read_options_to_dict(options: MissionReadOptions) -> dict[str, Any]:
    return {
        "opener": clean_mission_text(options.opener) or MissionReadOptions.opener,
        "include_giver": bool(options.include_giver),
        "include_level": bool(options.include_level),
        "include_rewards": bool(options.include_rewards),
        "include_reward_notes": bool(options.include_reward_notes),
        "include_source": bool(options.include_source),
        "include_completion": bool(options.include_completion),
        "include_briefing": bool(options.include_briefing),
    }


def grouped_missions_by_giver(entries: Iterable[MissionLibraryEntry]) -> dict[str, tuple[MissionLibraryEntry, ...]]:
    grouped: dict[str, list[MissionLibraryEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.giver_label, []).append(entry)
    return {
        giver: tuple(sorted(items, key=lambda entry: entry.title.casefold()))
        for giver, items in sorted(grouped.items(), key=lambda item: item[0].casefold())
    }


def mission_search_text(entry: MissionLibraryEntry) -> str:
    fields = (
        entry.title,
        entry.mission_giver,
        entry.agent_corporation,
        entry.faction,
        entry.level,
        entry.mission_type,
        entry.source,
        " ".join(entry.tags),
    )
    return normalize_phrase(" ".join(field for field in fields if field))


def mission_matches_query(entry: MissionLibraryEntry, query: str) -> bool:
    tokens = normalize_phrase(query).split()
    if not tokens:
        return True
    haystack = mission_search_text(entry)
    return all(token in haystack for token in tokens)


def mission_match_score(entry: MissionLibraryEntry, query: str) -> float:
    normalized_query = normalize_phrase(query)
    if not normalized_query:
        return 1.0
    title = normalize_phrase(entry.title)
    giver = normalize_phrase(entry.mission_giver)
    haystack = mission_search_text(entry)
    if normalized_query == title:
        return 1.0
    if title.startswith(normalized_query):
        return 0.95
    if normalized_query in title:
        return 0.9
    if normalized_query in giver:
        return 0.78
    tokens = normalized_query.split()
    if tokens and all(token in haystack for token in tokens):
        return 0.72 + min(0.18, len(tokens) * 0.02)
    return 0.0


def find_mission_entries(
    query: str,
    entries: Iterable[MissionLibraryEntry],
    *,
    limit: int = 8,
    minimum_score: float = 0.65,
) -> tuple[MissionLibraryEntry, ...]:
    scored = [
        (mission_match_score(entry, query), entry)
        for entry in entries
    ]
    filtered = [(score, entry) for score, entry in scored if score >= minimum_score]
    filtered.sort(key=lambda item: (-item[0], item[1].title.casefold()))
    return tuple(entry for _score, entry in filtered[: max(1, limit)])


def mission_detail_text(entry: MissionLibraryEntry) -> str:
    lines = [
        entry.title,
        f"Mission giver: {entry.giver_label}",
    ]
    if entry.agent_corporation:
        lines.append(f"Corporation: {entry.agent_corporation}")
    if entry.faction:
        lines.append(f"Faction: {entry.faction}")
    if entry.level or entry.mission_type:
        lines.append(f"Level/type: {' / '.join(item for item in (entry.level, entry.mission_type) if item)}")
    lines.extend(("", "Rewards", entry.reward_summary))
    if entry.reward_notes:
        lines.append(f"Notes: {entry.reward_notes}")
    completion_lines: list[str] = []
    if entry.objective_text:
        completion_lines.append(entry.objective_text)
    for index, step in enumerate(entry.completion_steps, start=1):
        completion_lines.append(f"{index}. {step}")
    if entry.completion_notes:
        completion_lines.append(f"Notes: {entry.completion_notes}")
    if completion_lines:
        lines.extend(("", "Completion", *completion_lines))
    lines.extend(("", "Briefing", entry.briefing_text or "No briefing text recorded."))
    if entry.source or entry.source_url:
        lines.extend(("", "Source", " - ".join(item for item in (entry.source, entry.source_url) if item)))
    return "\n".join(lines)


def mission_read_aloud_text(entry: MissionLibraryEntry, options: MissionReadOptions | None = None) -> str:
    read_options = options or MissionReadOptions()
    opener = clean_mission_text(read_options.opener) or MissionReadOptions.opener
    lines = [f"{opener} for {entry.title}."]
    if read_options.include_giver:
        lines.append(f"Mission giver: {entry.giver_label}.")
    if read_options.include_level and (entry.level or entry.mission_type):
        lines.append(f"Level and type: {'; '.join(item for item in (entry.level, entry.mission_type) if item)}.")
    if read_options.include_rewards and entry.reward_summary:
        lines.append(f"Known rewards: {entry.reward_summary}.")
    if read_options.include_reward_notes and entry.reward_notes:
        lines.append(f"Reward note: {entry.reward_notes}.")
    if read_options.include_source and (entry.source or entry.source_url):
        lines.append(f"Source: {'; '.join(item for item in (entry.source, entry.source_url) if item)}.")
    if read_options.include_completion:
        if entry.objective_text:
            lines.append(f"Objective: {entry.objective_text}.")
        if entry.completion_steps:
            step_text = " ".join(f"Step {index}: {step}." for index, step in enumerate(entry.completion_steps, start=1))
            lines.append(step_text)
        if entry.completion_notes:
            lines.append(f"Completion note: {entry.completion_notes}.")
    if read_options.include_briefing and entry.briefing_text:
        lines.append(entry.briefing_text)
    elif read_options.include_briefing:
        lines.append("No mission briefing text is recorded in the local mission library yet.")
    return normalize_response_text(" ".join(lines))
