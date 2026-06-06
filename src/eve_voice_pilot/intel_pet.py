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
DEFAULT_SPRITE_DIR = ROOT / "src" / "eve_voice_pilot" / "static" / "intel-pet"
DEFAULT_ALERT_SECONDS = 18.0
SHIP_FRAME_COUNT = 8
SHIP_FRAME_MS = 150
IDLE_ANIMATION_MS = 5 * 60 * 1000
IDLE_SPRITE_SEQUENCE = (0, 1, 2, 3, 4, 5, 6, 7, 0)
ALERT_SPRITE_SEQUENCE = (0, 7, 6, 5, 4, 3, 2, 1, 0)


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "pilot_names": list(self.pilot_names),
            "extra_keywords": list(self.extra_keywords),
            "help_phrases": list(self.help_phrases),
            "show_message_text": self.show_message_text,
            "alert_seconds": self.alert_seconds,
        }


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
        self._system_names = tuple(system_names)
        self._lock = threading.Lock()
        self.settings = settings
        self.parser = self._build_parser(settings)

    def analyze(self, message: ChatMessage) -> IntelPetAlert | None:
        with self._lock:
            settings = self.settings
            parser = self.parser

        event = parser.analyze(message, source="intel pet")
        mentions = find_matching_terms(settings.pilot_names, message.text)
        self_mentioned_by_other = bool(mentions) and not speaker_matches_any(
            message.speaker,
            settings.pilot_names,
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
            message=message.text if settings.show_message_text else "",
            observed_at=message.observed_at,
            reported_at=now_iso(),
            categories=tuple(sorted(categories)),
            keywords=tuple(dedupe_preserve_order(keywords)),
        )

    def current_settings(self) -> IntelPetSettings:
        with self._lock:
            return self.settings

    def update_settings(self, settings: IntelPetSettings) -> IntelPetSettings:
        parser = self._build_parser(settings)
        with self._lock:
            self.settings = settings
            self.parser = parser
        return settings

    def _build_parser(self, settings: IntelPetSettings) -> IntelParser:
        return IntelParser(
            self._system_names,
            watchlist_store=WatchlistStore(watchlist=settings.to_watchlist()),
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


def save_settings(path: Path, settings: IntelPetSettings) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(settings.to_dict(), indent=2) + "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(body, encoding="utf-8")
    tmp_path.replace(path)


def replace_alert_terms(
    settings: IntelPetSettings,
    *,
    pilot_names: Iterable[str] | None = None,
    extra_keywords: Iterable[str] | None = None,
    help_phrases: Iterable[str] | None = None,
) -> IntelPetSettings:
    updates: dict[str, tuple[str, ...]] = {}
    if pilot_names is not None:
        updates["pilot_names"] = clean_user_terms(pilot_names)
    if extra_keywords is not None:
        updates["extra_keywords"] = clean_user_terms(extra_keywords)
    if help_phrases is not None:
        updates["help_phrases"] = clean_user_terms(help_phrases)
    if not updates:
        return settings
    return replace(settings, **updates)


def replace_extra_keywords(settings: IntelPetSettings, keywords: Iterable[str]) -> IntelPetSettings:
    return replace_alert_terms(settings, extra_keywords=keywords)


def merge_terms(existing: tuple[str, ...], additions: Iterable[str]) -> tuple[str, ...]:
    return tuple(dedupe_preserve_order((*existing, *clean_user_terms(additions))))


def clean_user_terms(values: Iterable[str]) -> tuple[str, ...]:
    terms: list[str] = []
    for value in values:
        terms.extend(clean_watchlist_terms(value))
    return tuple(dedupe_preserve_order(terms))


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


def ship_sprite_frame_paths(asset_dir: Path = DEFAULT_SPRITE_DIR) -> tuple[Path, ...]:
    return tuple(asset_dir / f"ship-frame-{index:02d}.png" for index in range(SHIP_FRAME_COUNT))


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
    settings_path = args.settings_path.expanduser()

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
    root.geometry("460x300+40+40")
    root.minsize(380, 260)
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

    sprite_frames = load_sprite_frames(tk, root)
    sprite_after_id: str | None = None
    idle_cycle_after_id: str | None = None

    title_var = tk.StringVar(value="Intel Pet")
    status_var = tk.StringVar(value=f"Watching {channel_filter.describe()}")
    message_var = tk.StringVar(value="Quiet. Waiting for new chat lines.")
    meta_var = tk.StringVar(value="Local only. No Discord or server connection.")

    sprite_canvas = tk.Canvas(frame, width=128, height=96, bg=colors["idle"], highlightthickness=0)
    sprite_image_id = None
    if sprite_frames:
        sprite_image_id = sprite_canvas.create_image(64, 48, image=sprite_frames[0])
        sprite_canvas.pack(anchor="center", pady=(0, 8))

    ttk.Label(frame, textvariable=title_var, style="PetTitle.TLabel").pack(anchor="w")
    ttk.Label(frame, textvariable=status_var, style="PetMeta.TLabel").pack(anchor="w", pady=(2, 8))
    message_label = ttk.Label(frame, textvariable=message_var, style="PetBody.TLabel", wraplength=380, justify="left")
    message_label.pack(anchor="w", fill="x")
    ttk.Label(frame, textvariable=meta_var, style="PetMeta.TLabel", wraplength=380, justify="left").pack(
        anchor="w",
        fill="x",
        pady=(8, 0),
    )

    def open_alert_settings() -> None:
        editor = tk.Toplevel(root)
        editor.title("Intel Pet Alerts")
        editor.geometry("520x620+80+80")
        editor.minsize(440, 500)
        editor.transient(root)
        editor.attributes("-topmost", True)

        editor_frame = ttk.Frame(editor, padding=12)
        editor_frame.pack(fill="both", expand=True)

        editor_status_var = tk.StringVar(value="Saved locally only.")

        ttk.Label(editor_frame, text="Local alert settings", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            editor_frame,
            text="These match new chat lines on this computer and are saved to your ignored profile settings.",
            wraplength=480,
        ).pack(anchor="w", pady=(2, 8))

        term_lists: dict[str, Any] = {}
        term_vars: dict[str, Any] = {}

        def refresh_list(name: str, terms: Iterable[str]) -> None:
            term_list = term_lists[name]
            term_list.delete(0, tk.END)
            for term in terms:
                term_list.insert(tk.END, term)

        def current_terms(name: str) -> tuple[str, ...]:
            return tuple(str(item) for item in term_lists[name].get(0, tk.END))

        def persist_terms(action: str) -> None:
            try:
                settings = replace_alert_terms(
                    engine.current_settings(),
                    pilot_names=current_terms("pilot_names"),
                    extra_keywords=current_terms("extra_keywords"),
                    help_phrases=current_terms("help_phrases"),
                )
                save_settings(settings_path, settings)
                engine.update_settings(settings)
            except Exception as exc:
                editor_status_var.set(f"Save failed: {exc}")
                return
            refresh_list("pilot_names", settings.pilot_names)
            refresh_list("help_phrases", settings.help_phrases)
            refresh_list("extra_keywords", settings.extra_keywords)
            counts = (
                f"{len(settings.pilot_names)} name{'s' if len(settings.pilot_names) != 1 else ''}",
                f"{len(settings.help_phrases)} help phrase{'s' if len(settings.help_phrases) != 1 else ''}",
                f"{len(settings.extra_keywords)} keyword{'s' if len(settings.extra_keywords) != 1 else ''}",
            )
            editor_status_var.set(f"{action}. {', '.join(counts)} saved.")
            meta_var.set(f"Alerts active: {', '.join(counts)}.")

        def add_term(name: str) -> None:
            term_var = term_vars[name]
            merged = clean_user_terms((*current_terms(name), term_var.get()))
            if not merged:
                editor_status_var.set("Enter a term first.")
                return
            refresh_list(name, merged)
            term_var.set("")
            persist_terms("Added")

        def selected_index(name: str) -> int | None:
            selection = term_lists[name].curselection()
            return int(selection[0]) if selection else None

        def change_term(name: str) -> None:
            index = selected_index(name)
            term_var = term_vars[name]
            replacement = term_var.get().strip()
            if index is None:
                editor_status_var.set("Select a term to change.")
                return
            if not replacement:
                editor_status_var.set("Enter the replacement term.")
                return
            terms = list(current_terms(name))
            terms[index] = replacement
            refresh_list(name, clean_user_terms(terms))
            term_var.set("")
            persist_terms("Changed")

        def remove_term(name: str) -> None:
            index = selected_index(name)
            if index is None:
                editor_status_var.set("Select a term to remove.")
                return
            terms = list(current_terms(name))
            del terms[index]
            refresh_list(name, terms)
            term_vars[name].set("")
            persist_terms("Removed")

        def fill_entry(name: str) -> None:
            index = selected_index(name)
            if index is not None:
                term_vars[name].set(term_lists[name].get(index))

        sections = (
            (
                "pilot_names",
                "Your pilot names",
                "High alerts when someone else mentions one of these names.",
                engine.current_settings().pilot_names,
            ),
            (
                "help_phrases",
                "Help phrases",
                "Critical alerts for calls that sound like someone needs help.",
                engine.current_settings().help_phrases,
            ),
            (
                "extra_keywords",
                "Extra keywords",
                "Medium alerts for local watch terms like market or intel phrases.",
                engine.current_settings().extra_keywords,
            ),
        )

        first_entry = None
        for section_name, title, description, initial_terms in sections:
            section = ttk.LabelFrame(editor_frame, text=title, padding=8)
            section.pack(fill="both", expand=True, pady=(0, 8))
            ttk.Label(section, text=description, wraplength=460).pack(anchor="w", pady=(0, 6))

            list_frame = ttk.Frame(section)
            list_frame.pack(fill="both", expand=True)
            term_list = tk.Listbox(list_frame, height=4, exportselection=False)
            term_list.pack(side="left", fill="both", expand=True)
            scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=term_list.yview)
            scrollbar.pack(side="right", fill="y")
            term_list.configure(yscrollcommand=scrollbar.set)

            term_var = tk.StringVar()
            term_lists[section_name] = term_list
            term_vars[section_name] = term_var
            refresh_list(section_name, initial_terms)
            term_list.bind("<<ListboxSelect>>", lambda _event, name=section_name: fill_entry(name))

            entry_row = ttk.Frame(section)
            entry_row.pack(fill="x", pady=(8, 6))
            term_entry = ttk.Entry(entry_row, textvariable=term_var)
            term_entry.pack(side="left", fill="x", expand=True)
            term_entry.bind("<Return>", lambda _event, name=section_name: add_term(name))
            if first_entry is None:
                first_entry = term_entry

            action_row = ttk.Frame(section)
            action_row.pack(fill="x")
            ttk.Button(action_row, text="Add", command=lambda name=section_name: add_term(name)).pack(side="left")
            ttk.Button(action_row, text="Change", command=lambda name=section_name: change_term(name)).pack(
                side="left",
                padx=(6, 0),
            )
            ttk.Button(action_row, text="Remove", command=lambda name=section_name: remove_term(name)).pack(
                side="left",
                padx=(6, 0),
            )

        footer = ttk.Frame(editor_frame)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=editor_status_var, wraplength=380).pack(side="left", anchor="w")
        ttk.Button(footer, text="Close", command=editor.destroy).pack(side="right")
        if first_entry is not None:
            first_entry.focus_set()

    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e", fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Alerts", command=open_alert_settings).pack(side="left")
    ttk.Button(buttons, text="Clear", command=lambda: set_idle()).pack(side="right")
    ttk.Button(buttons, text="Quit", command=lambda: on_close()).pack(side="right", padx=(0, 8))

    idle_after_id: str | None = None

    def set_sprite_frame(index: int) -> None:
        if not sprite_frames or sprite_image_id is None:
            return
        clean_index = max(0, min(index, len(sprite_frames) - 1))
        sprite_canvas.itemconfigure(sprite_image_id, image=sprite_frames[clean_index])

    def cancel_sprite_cycle() -> None:
        nonlocal sprite_after_id
        if sprite_after_id is not None:
            root.after_cancel(sprite_after_id)
            sprite_after_id = None

    def cancel_idle_sprite_cycle() -> None:
        nonlocal idle_cycle_after_id
        if idle_cycle_after_id is not None:
            root.after_cancel(idle_cycle_after_id)
            idle_cycle_after_id = None

    def schedule_idle_sprite_cycle() -> None:
        nonlocal idle_cycle_after_id
        if not sprite_frames:
            return
        cancel_idle_sprite_cycle()
        idle_cycle_after_id = root.after(IDLE_ANIMATION_MS, run_idle_sprite_cycle)

    def start_sprite_cycle(sequence: tuple[int, ...], *, reschedule_idle: bool = True) -> None:
        nonlocal sprite_after_id
        if not sprite_frames:
            return
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()

        def advance(position: int = 0) -> None:
            nonlocal sprite_after_id
            set_sprite_frame(sequence[position])
            next_position = position + 1
            if next_position < len(sequence):
                sprite_after_id = root.after(SHIP_FRAME_MS, lambda: advance(next_position))
            else:
                sprite_after_id = None
                set_sprite_frame(0)
                if reschedule_idle:
                    schedule_idle_sprite_cycle()

        advance()

    def run_idle_sprite_cycle() -> None:
        nonlocal idle_cycle_after_id
        idle_cycle_after_id = None
        start_sprite_cycle(IDLE_SPRITE_SEQUENCE)

    def apply_severity(severity: str) -> None:
        color = colors.get(severity, colors["info"])
        root.configure(bg=color)
        for stylename in ("Pet.TFrame", "PetTitle.TLabel", "PetBody.TLabel", "PetMeta.TLabel"):
            style.configure(stylename, background=color)
        sprite_canvas.configure(bg=color)

    def set_idle() -> None:
        nonlocal idle_after_id
        if idle_after_id is not None:
            root.after_cancel(idle_after_id)
            idle_after_id = None
        cancel_sprite_cycle()
        set_sprite_frame(0)
        schedule_idle_sprite_cycle()
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
        start_sprite_cycle(ALERT_SPRITE_SEQUENCE)
        idle_after_id = root.after(int(engine.current_settings().alert_seconds * 1000), set_idle)

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
        cancel_sprite_cycle()
        cancel_idle_sprite_cycle()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    schedule_idle_sprite_cycle()
    poll_queue()
    root.mainloop()
    stop_event.set()


def load_sprite_frames(tk_module: Any, root: Any, paths: Iterable[Path] | None = None) -> tuple[Any, ...]:
    frames: list[Any] = []
    for path in paths or ship_sprite_frame_paths():
        if not path.exists():
            return ()
        frames.append(tk_module.PhotoImage(file=str(path), master=root))
    return tuple(frames)


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
