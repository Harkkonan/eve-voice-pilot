from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
from pathlib import Path
import queue
import threading
from typing import Any, Iterable

from eve_voice_pilot.corp_intel import (
    COMMON_SYSTEM_NAMES,
    DEFAULT_CHANNELS,
    DEFAULT_POLL_SECONDS,
    ROOT,
    ChannelFilter,
    ChatMessage,
    CorpIntelError,
    IntelParser,
    IntelWatchlist,
    WatchlistStore,
    clean_watchlist_terms,
    compile_phrase_pattern,
    default_chat_log_dir,
    higher_severity,
    now_iso,
    parse_csv,
    watch_chat_logs,
)


DEFAULT_SETTINGS_PATH = ROOT / "profiles" / "intel_pet_settings.json"
DEFAULT_ALERT_SECONDS = 18.0


@dataclass(frozen=True)
class IntelPetSettings:
    pilot_names: tuple[str, ...] = ()
    extra_keywords: tuple[str, ...] = ()
    help_phrases: tuple[str, ...] = ()
    show_message_text: bool = True
    alert_seconds: float = DEFAULT_ALERT_SECONDS

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntelPetSettings":
        return cls(
            pilot_names=clean_watchlist_terms(payload.get("pilot_names")),
            extra_keywords=clean_watchlist_terms(payload.get("extra_keywords")),
            help_phrases=clean_watchlist_terms(payload.get("help_phrases")),
            show_message_text=bool(payload.get("show_message_text", True)),
            alert_seconds=safe_float(payload.get("alert_seconds"), DEFAULT_ALERT_SECONDS),
        )

    def to_watchlist(self) -> IntelWatchlist:
        return IntelWatchlist(
            help_phrases=self.help_phrases,
            keywords=self.extra_keywords,
        )


@dataclass(frozen=True)
class IntelPetAlert:
    title: str
    severity: str
    channel: str
    speaker: str
    message: str
    observed_at: str
    reported_at: str
    categories: tuple[str, ...]
    keywords: tuple[str, ...]


class IntelPetEngine:
    def __init__(self, settings: IntelPetSettings, *, system_names: Iterable[str] = COMMON_SYSTEM_NAMES):
        self.settings = settings
        self.parser = IntelParser(
            system_names,
            watchlist_store=WatchlistStore(watchlist=settings.to_watchlist()),
        )

    def analyze(self, message: ChatMessage) -> IntelPetAlert | None:
        event = self.parser.analyze(message, source="intel pet")
        mentions = find_matching_terms(self.settings.pilot_names, message.text)
        self_mentioned_by_other = bool(mentions) and not speaker_matches_any(
            message.speaker,
            self.settings.pilot_names,
        )

        categories = set(event.categories if event else ())
        keywords = list(event.keywords if event else ())
        severity = event.severity if event else "info"

        if self_mentioned_by_other:
            categories.add("self-mention")
            severity = higher_severity(severity, "high")
            for mention in mentions:
                keywords.append(f"name: {mention}")

        if not categories:
            return None

        title = alert_title(categories, message.channel)
        return IntelPetAlert(
            title=title,
            severity=severity,
            channel=message.channel,
            speaker=message.speaker,
            message=message.text if self.settings.show_message_text else "",
            observed_at=message.observed_at,
            reported_at=now_iso(),
            categories=tuple(sorted(categories)),
            keywords=tuple(dedupe_preserve_order(keywords)),
        )


def load_settings(path: Path | None, *, overrides: argparse.Namespace | None = None) -> IntelPetSettings:
    payload: dict[str, Any] = {}
    if path and path.expanduser().exists():
        try:
            loaded = json.loads(path.expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpIntelError(f"Could not read intel pet settings {path}: {exc}") from exc
        if isinstance(loaded, dict):
            payload = loaded
    settings = IntelPetSettings.from_dict(payload)
    if overrides is None:
        return settings
    if overrides.pilot_name:
        settings = replace(
            settings,
            pilot_names=merge_terms(settings.pilot_names, overrides.pilot_name),
        )
    if overrides.keyword:
        settings = replace(
            settings,
            extra_keywords=merge_terms(settings.extra_keywords, overrides.keyword),
        )
    if overrides.help_phrase:
        settings = replace(
            settings,
            help_phrases=merge_terms(settings.help_phrases, overrides.help_phrase),
        )
    if overrides.no_message_text:
        settings = replace(settings, show_message_text=False)
    if overrides.alert_seconds is not None:
        settings = replace(settings, alert_seconds=max(3.0, float(overrides.alert_seconds)))
    return settings


def merge_terms(existing: tuple[str, ...], additions: Iterable[str]) -> tuple[str, ...]:
    return tuple(dedupe_preserve_order((*existing, *clean_watchlist_terms(list(additions)))))


def find_matching_terms(terms: Iterable[str], text: str) -> tuple[str, ...]:
    matches: list[str] = []
    for term in terms:
        if compile_phrase_pattern(term).search(text):
            matches.append(term)
    return tuple(matches)


def speaker_matches_any(speaker: str, names: Iterable[str]) -> bool:
    folded = speaker.strip().casefold()
    return bool(folded) and any(folded == name.strip().casefold() for name in names if name.strip())


def alert_title(categories: set[str], channel: str) -> str:
    if "aid" in categories:
        return f"Help call in {channel}"
    if "self-mention" in categories:
        return f"Your name was mentioned in {channel}"
    if "hostile" in categories:
        return f"Intel in {channel}"
    return f"Keyword match in {channel}"


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(value)
    return result


def safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def format_alert(alert: IntelPetAlert) -> str:
    message = f" | {alert.message}" if alert.message else ""
    keywords = f" | {', '.join(alert.keywords)}" if alert.keywords else ""
    return f"[{alert.severity.upper()}] {alert.title} | {alert.speaker}{message}{keywords}"


def run_console(args: argparse.Namespace, engine: IntelPetEngine) -> None:
    channel_filter = channel_filter_from_args(args)

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            print(format_alert(alert), flush=True)

    watch_chat_logs(
        log_dir=args.log_dir,
        channel_filter=channel_filter,
        on_message=on_message,
        poll_seconds=args.poll_seconds,
        read_existing=args.read_existing,
    )


def run_overlay(args: argparse.Namespace, engine: IntelPetEngine) -> None:
    import tkinter as tk
    from tkinter import ttk

    alert_queue: queue.Queue[IntelPetAlert | str] = queue.Queue()
    stop_event = threading.Event()
    channel_filter = channel_filter_from_args(args)

    def on_message(message: ChatMessage) -> None:
        alert = engine.analyze(message)
        if alert:
            alert_queue.put(alert)

    def watcher() -> None:
        try:
            watch_chat_logs(
                log_dir=args.log_dir,
                channel_filter=channel_filter,
                on_message=on_message,
                poll_seconds=args.poll_seconds,
                read_existing=args.read_existing,
                stop_event=stop_event,
                log=lambda text: alert_queue.put(text),
            )
        except Exception as exc:  # pragma: no cover - surfaced in the UI.
            alert_queue.put(f"Watcher stopped: {exc}")

    thread = threading.Thread(target=watcher, daemon=True)
    thread.start()

    root = tk.Tk()
    root.title("EVE Intel Pet")
    root.geometry("420x190+40+40")
    root.minsize(360, 160)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.95)
    except tk.TclError:
        pass

    colors = {
        "idle": "#243142",
        "info": "#2f5f7d",
        "medium": "#7a5c24",
        "high": "#8a3f08",
        "critical": "#8a1f1f",
    }
    root.configure(bg=colors["idle"])

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    style = ttk.Style(root)
    style.configure("Pet.TFrame", background=colors["idle"])
    style.configure("PetTitle.TLabel", background=colors["idle"], foreground="#f6f8fb", font=("Segoe UI", 14, "bold"))
    style.configure("PetBody.TLabel", background=colors["idle"], foreground="#f6f8fb", font=("Segoe UI", 10))
    style.configure("PetMeta.TLabel", background=colors["idle"], foreground="#d7e0ea", font=("Segoe UI", 9))
    frame.configure(style="Pet.TFrame")

    title_var = tk.StringVar(value="Intel Pet")
    status_var = tk.StringVar(value=f"Watching {channel_filter.describe()}")
    message_var = tk.StringVar(value="Quiet. Waiting for new chat lines.")
    meta_var = tk.StringVar(value="Local only. No Discord or server connection.")

    ttk.Label(frame, textvariable=title_var, style="PetTitle.TLabel").pack(anchor="w")
    ttk.Label(frame, textvariable=status_var, style="PetMeta.TLabel").pack(anchor="w", pady=(2, 8))
    message_label = ttk.Label(frame, textvariable=message_var, style="PetBody.TLabel", wraplength=380, justify="left")
    message_label.pack(anchor="w", fill="x")
    ttk.Label(frame, textvariable=meta_var, style="PetMeta.TLabel", wraplength=380, justify="left").pack(
        anchor="w",
        fill="x",
        pady=(8, 0),
    )

    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e", fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Clear", command=lambda: set_idle()).pack(side="right")
    ttk.Button(buttons, text="Quit", command=root.destroy).pack(side="right", padx=(0, 8))

    idle_after_id: str | None = None

    def apply_severity(severity: str) -> None:
        color = colors.get(severity, colors["info"])
        root.configure(bg=color)
        for stylename in ("Pet.TFrame", "PetTitle.TLabel", "PetBody.TLabel", "PetMeta.TLabel"):
            style.configure(stylename, background=color)

    def set_idle() -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
            idle_after_id = None
        apply_severity("idle")
        title_var.set("Intel Pet")
        message_var.set("Quiet. Waiting for new chat lines.")
        meta_var.set("Local only. No Discord or server connection.")

    def show_alert(alert: IntelPetAlert) -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
        apply_severity(alert.severity)
        title_var.set(alert.title)
        message = alert.message or "Message text hidden by settings."
        message_var.set(f"{alert.speaker}: {message}")
        meta_var.set(f"{alert.severity.upper()} | {', '.join(alert.keywords) or 'matched chat'}")
        idle_after_id = root.after(int(engine.settings.alert_seconds * 1000), set_idle)

    def poll_queue() -> None:
        while True:
            try:
                item = alert_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, IntelPetAlert):
                show_alert(item)
            else:
                status_var.set(item)
        root.after(250, poll_queue)

    def on_close() -> None:
        stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    poll_queue()
    root.mainloop()
    stop_event.set()


def channel_filter_from_args(args: argparse.Namespace) -> ChannelFilter:
    channels = parse_csv(args.channels)
    if args.all_channels:
        return ChannelFilter(all_channels=True)
    if not channels:
        raise CorpIntelError("Choose channels with --channels or explicitly pass --all-channels.")
    return ChannelFilter(channels)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local-only EVE chat alert pet overlay.",
    )
    parser.add_argument("--log-dir", type=Path, default=default_chat_log_dir(), help="EVE Chatlogs folder.")
    parser.add_argument(
        "--channels",
        default=DEFAULT_CHANNELS,
        help="Comma-separated channel allowlist. Wildcards are allowed, like *Intel*.",
    )
    parser.add_argument("--all-channels", action="store_true", help="Allow all chat log channels. Use carefully.")
    parser.add_argument("--settings-path", type=Path, default=DEFAULT_SETTINGS_PATH, help="Local intel pet settings JSON.")
    parser.add_argument("--pilot-name", action="append", default=(), help="Your character name for mention alerts.")
    parser.add_argument("--keyword", action="append", default=(), help="Extra keyword to alert on.")
    parser.add_argument("--help-phrase", action="append", default=(), help="Extra help phrase to treat as critical.")
    parser.add_argument("--no-message-text", action="store_true", help="Hide the matched message text in the overlay.")
    parser.add_argument("--alert-seconds", type=float, default=None, help="Seconds before the overlay returns to idle.")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="Chat log polling interval.")
    parser.add_argument("--read-existing", action="store_true", help="Process existing log lines instead of new lines only.")
    parser.add_argument("--console", action="store_true", help="Print alerts to the console instead of opening the overlay.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.settings_path, overrides=args)
    engine = IntelPetEngine(settings)
    if args.console:
        run_console(args, engine)
    else:
        run_overlay(args, engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
