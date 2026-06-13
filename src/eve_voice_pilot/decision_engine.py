from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def _mapping_list(values: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in values or ():
        if isinstance(value, Mapping):
            rows.append(dict(value))
    return rows


def _text_list(values: Iterable[Any] | None) -> list[str]:
    return [text for text in (_clean_text(value) for value in values or ()) if text]


@dataclass(frozen=True)
class ManualChecklistItem:
    label: str
    value: Any
    detail: str = ""
    warning: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": _clean_text(self.label),
            "value": str(self.value),
            "detail": _clean_text(self.detail),
            "warning": bool(self.warning),
        }


@dataclass(frozen=True)
class DecisionAction:
    label: str
    href: str
    detail: str = ""
    target_tab: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": _clean_text(self.label),
            "href": str(self.href or ""),
            "target_tab": _clean_text(self.target_tab),
            "detail": _clean_text(self.detail),
        }


@dataclass(frozen=True)
class ExternalLink:
    label: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"label": _clean_text(self.label), "url": str(self.url or "")}


@dataclass(frozen=True)
class DataSourceBadge:
    key: str
    label: str
    status: str = ""
    posture: str = ""
    freshness: str = ""
    persistence: str = ""
    scope: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": _clean_text(self.key),
            "label": _clean_text(self.label),
            "status": _clean_text(self.status),
            "posture": _clean_text(self.posture),
            "freshness": _clean_text(self.freshness),
            "persistence": _clean_text(self.persistence),
            "scope": _clean_text(self.scope),
            "detail": _clean_text(self.detail),
        }


@dataclass(frozen=True)
class ParsedInput:
    primary_kind: str
    label: str
    confidence: int
    signals: Iterable[Mapping[str, Any]] = field(default_factory=tuple)
    line_count: int = 0
    raw_line_count: int = 0
    nonempty_line_count: int = 0
    character_count: int = 0
    stored: bool = False
    summary: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        confidence = max(0, min(int(self.confidence or 0), 100))
        line_count = max(0, int(self.line_count or self.nonempty_line_count or 0))
        nonempty = max(0, int(self.nonempty_line_count or line_count))
        return {
            "primary_kind": _clean_text(self.primary_kind),
            "detected_type": _clean_text(self.primary_kind),
            "label": _clean_text(self.label),
            "confidence": confidence,
            "signals": _mapping_list(self.signals),
            "line_count": line_count,
            "raw_line_count": max(0, int(self.raw_line_count or 0)),
            "nonempty_line_count": nonempty,
            "character_count": max(0, int(self.character_count or 0)),
            "stored": bool(self.stored),
            "summary": dict(self.summary or {}),
        }


@dataclass(frozen=True)
class LearningSummary:
    source: str
    status: str
    detail: str
    saved: int = 0
    signal_count: int = 0
    evidence_item_count: int = 0
    signal_counts: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": _clean_text(self.source),
            "status": _clean_text(self.status),
            "detail": _clean_text(self.detail),
            "saved": max(0, int(self.saved or 0)),
            "signal_count": max(0, int(self.signal_count or 0)),
            "evidence_item_count": max(0, int(self.evidence_item_count or 0)),
            "signal_counts": {str(key): int(value or 0) for key, value in (self.signal_counts or {}).items()},
        }


@dataclass(frozen=True)
class Recommendation:
    key: str
    title: str
    plain_reason: str
    priority: int
    confidence: str = ""
    risk_level: str = ""
    assumptions: Iterable[Any] = field(default_factory=tuple)
    missing_data: Iterable[Any] = field(default_factory=tuple)
    source_keys: Iterable[Any] = field(default_factory=tuple)
    manual_checklist: Iterable[Mapping[str, Any]] = field(default_factory=tuple)
    next_actions: Iterable[Mapping[str, Any]] = field(default_factory=tuple)
    links: Iterable[Mapping[str, Any]] = field(default_factory=tuple)
    learning_signals: Iterable[Mapping[str, Any]] = field(default_factory=tuple)
    learning_summary: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        priority = max(0, min(int(self.priority or 0), 100))
        source_keys = _text_list(self.source_keys)
        data: dict[str, Any] = {
            "key": _clean_text(self.key),
            "title": _clean_text(self.title),
            "priority": priority,
            "confidence": _clean_text(self.confidence),
            "risk_level": _clean_text(self.risk_level),
            "plain_reason": _clean_text(self.plain_reason),
            "explanation": _clean_text(self.plain_reason),
            "summary": _clean_text(self.plain_reason),
            "assumptions": _text_list(self.assumptions),
            "missing_data": _text_list(self.missing_data),
            "source_keys": source_keys,
            "data_source_keys": source_keys,
            "manual_checklist": _mapping_list(self.manual_checklist),
            "next_actions": _mapping_list(self.next_actions),
            "links": _mapping_list(self.links),
            "learning_signals": _mapping_list(self.learning_signals),
        }
        if self.learning_summary:
            data["learning_summary"] = dict(self.learning_summary)
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data


def checklist_item(label: str, value: Any, detail: str = "", *, warning: bool = False) -> dict[str, Any]:
    return ManualChecklistItem(label=label, value=value, detail=detail, warning=warning).to_dict()


def decision_action(label: str, href: str, detail: str, *, target_tab: str = "") -> dict[str, Any]:
    return DecisionAction(label=label, href=href, detail=detail, target_tab=target_tab).to_dict()


def external_link(label: str, url: str) -> dict[str, str]:
    return ExternalLink(label=label, url=url).to_dict()
