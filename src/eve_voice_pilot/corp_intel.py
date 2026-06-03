from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fnmatch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import webbrowser


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_CACHE_PATH = ROOT / "cache" / "eve_solar_systems.json"
DEFAULT_ESI_BASE_URL = "https://esi.evetech.net/latest"
DEFAULT_PORT = 8765
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_MAX_EVENTS = 500
DEFAULT_CHANNELS = "Corp,Corporation,Fleet,Alliance,Local,*Intel*"
CHAT_LINE_RE = re.compile(
    r"^\[\s*(?P<timestamp>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*\]\s*"
    r"(?P<speaker>.*?)\s*>\s*(?P<message>.*)$"
)
CHANNEL_NAME_RE = re.compile(r"^\s*Channel\s+Name\s*:\s*(?P<channel>.+?)\s*$", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
TEXT_BOUNDARY_LEFT = r"(?<![A-Za-z0-9])"
TEXT_BOUNDARY_RIGHT = r"(?![A-Za-z0-9])"

COMMON_SYSTEM_NAMES = (
    "Ahbazon",
    "Amarr",
    "Dodixie",
    "Hek",
    "Jita",
    "Old Man Star",
    "Perimeter",
    "Rens",
    "Tama",
    "Uedama",
)

AID_KEYWORDS = (
    "help",
    "need help",
    "need reps",
    "need logi",
    "need extraction",
    "tackled",
    "pointed",
    "scrammed",
    "dying",
    "under attack",
    "save me",
)

HOSTILE_KEYWORDS = (
    "hostile",
    "enemy",
    "war target",
    "wt",
    "red",
    "neut",
    "neutral",
    "camp",
    "gate camp",
    "bubble",
    "cyno",
    "dictor",
    "tackle",
    "smartbomb",
    "bombers",
    "dreads",
)

SEVERITY_RANK = {
    "info": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


class CorpIntelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatMessage:
    log_path: str
    channel: str
    timestamp: str
    speaker: str
    text: str

    @property
    def observed_at(self) -> str:
        return eve_timestamp_to_iso(self.timestamp)


@dataclass(frozen=True)
class KeywordRule:
    keyword: str
    category: str
    severity: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class IntelEvent:
    source: str
    channel: str
    speaker: str
    message: str
    categories: tuple[str, ...]
    severity: str
    systems: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    observed_at: str = ""
    reported_at: str = ""
    log_path: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "source": self.source,
            "channel": self.channel,
            "speaker": self.speaker,
            "message": self.message,
            "categories": list(self.categories),
            "severity": self.severity,
            "systems": list(self.systems),
            "keywords": list(self.keywords),
            "observed_at": self.observed_at,
            "reported_at": self.reported_at,
            "log_path": self.log_path,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntelEvent":
        return cls(
            event_id=str(payload.get("id") or payload.get("event_id") or uuid.uuid4().hex),
            source=str(payload.get("source") or "unknown"),
            channel=str(payload.get("channel") or "unknown"),
            speaker=str(payload.get("speaker") or ""),
            message=str(payload.get("message") or ""),
            categories=tuple(str(item) for item in payload.get("categories") or ()),
            severity=str(payload.get("severity") or "info"),
            systems=tuple(str(item) for item in payload.get("systems") or ()),
            keywords=tuple(str(item) for item in payload.get("keywords") or ()),
            observed_at=str(payload.get("observed_at") or ""),
            reported_at=str(payload.get("reported_at") or now_iso()),
            log_path=str(payload.get("log_path") or ""),
        )


@dataclass
class ChatLogState:
    path: Path
    channel: str
    encoding: str
    offset: int


class ChannelFilter:
    def __init__(self, patterns: Iterable[str] = (), *, all_channels: bool = False):
        self.all_channels = all_channels
        self.patterns = tuple(pattern.strip().casefold() for pattern in patterns if pattern.strip())

    def allows(self, channel: str) -> bool:
        if self.all_channels:
            return True
        folded = channel.casefold()
        return any(fnmatch.fnmatch(folded, pattern) for pattern in self.patterns)

    def describe(self) -> str:
        if self.all_channels:
            return "all channels"
        return ", ".join(self.patterns) if self.patterns else "no channels"


class SystemMatcher:
    def __init__(self, system_names: Iterable[str]):
        names = sorted({name.strip() for name in system_names if name.strip()}, key=lambda item: (-len(item), item))
        self._canonical = {name.casefold(): name for name in names}
        if not names:
            self._pattern = None
            return
        body = "|".join(re.escape(name) for name in names)
        self._pattern = re.compile(f"{TEXT_BOUNDARY_LEFT}(?:{body}){TEXT_BOUNDARY_RIGHT}", re.IGNORECASE)

    def find(self, text: str) -> tuple[str, ...]:
        if self._pattern is None:
            return ()
        found: list[str] = []
        seen: set[str] = set()
        for match in self._pattern.finditer(text):
            folded = match.group(0).casefold()
            canonical = self._canonical.get(folded)
            if canonical and canonical.casefold() not in seen:
                seen.add(canonical.casefold())
                found.append(canonical)
        return tuple(found)


class IntelParser:
    def __init__(self, system_names: Iterable[str]):
        self.system_matcher = SystemMatcher(system_names)
        self.keyword_rules = build_keyword_rules()

    def analyze(self, message: ChatMessage, *, source: str = "local") -> IntelEvent | None:
        categories: set[str] = set()
        keywords: list[str] = []
        severity = "info"
        systems = self.system_matcher.find(message.text)

        for rule in self.keyword_rules:
            if rule.pattern.search(message.text):
                categories.add(rule.category)
                keywords.append(rule.keyword)
                severity = higher_severity(severity, rule.severity)

        if not categories:
            return None

        if systems and "hostile" in categories:
            severity = higher_severity(severity, "high")
        if "aid" in categories:
            severity = higher_severity(severity, "critical")

        return IntelEvent(
            source=source,
            channel=message.channel,
            speaker=message.speaker,
            message=message.text,
            categories=tuple(sorted(categories)),
            severity=severity,
            systems=systems,
            keywords=tuple(dedupe_preserve_order(keywords)),
            observed_at=message.observed_at,
            reported_at=now_iso(),
            log_path=message.log_path,
        )


class IntelEventStore:
    def __init__(self, *, max_events: int = DEFAULT_MAX_EVENTS):
        self.max_events = max_events
        self._events: list[IntelEvent] = []
        self._lock = threading.Lock()

    def add(self, event: IntelEvent) -> IntelEvent:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]
            return event

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)

        newest_first = list(reversed(events))
        return {
            "generated_at": now_iso(),
            "events": [event.to_dict() for event in newest_first],
            "systems": summarize_systems(events),
            "counts": summarize_counts(events),
        }


def default_chat_log_dir() -> Path:
    candidates = [
        Path.home() / "Documents" / "EVE" / "logs" / "Chatlogs",
        Path.home() / "OneDrive" / "Documents" / "EVE" / "logs" / "Chatlogs",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def build_keyword_rules() -> tuple[KeywordRule, ...]:
    rules: list[KeywordRule] = []
    for keyword in AID_KEYWORDS:
        rules.append(
            KeywordRule(
                keyword=keyword,
                category="aid",
                severity="critical",
                pattern=compile_phrase_pattern(keyword),
            )
        )
    for keyword in HOSTILE_KEYWORDS:
        severity = "high" if keyword in {"hostile", "enemy", "war target", "wt", "red", "neut"} else "medium"
        rules.append(
            KeywordRule(
                keyword=keyword,
                category="hostile",
                severity=severity,
                pattern=compile_phrase_pattern(keyword),
            )
        )
    return tuple(rules)


def compile_phrase_pattern(phrase: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in SPACE_RE.split(phrase.strip()) if word]
    body = r"\s+".join(words)
    return re.compile(f"{TEXT_BOUNDARY_LEFT}{body}{TEXT_BOUNDARY_RIGHT}", re.IGNORECASE)


def higher_severity(left: str, right: str) -> str:
    return right if SEVERITY_RANK.get(right, 0) > SEVERITY_RANK.get(left, 0) else left


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


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def eve_timestamp_to_iso(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return now_iso()
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_chat_line(line: str, *, channel: str, log_path: str = "") -> ChatMessage | None:
    match = CHAT_LINE_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    return ChatMessage(
        log_path=log_path,
        channel=channel,
        timestamp=match.group("timestamp"),
        speaker=match.group("speaker").strip(),
        text=match.group("message").strip(),
    )


def parse_channel_name_from_text(text: str) -> str | None:
    for line in text.splitlines()[:80]:
        match = CHANNEL_NAME_RE.match(line)
        if match:
            return match.group("channel").strip()
    return None


def fallback_channel_name(path: Path) -> str:
    name = re.sub(r"_\d{8}.*$", "", path.stem)
    return name.replace("_", " ").strip() or path.stem


def detect_encoding(path: Path) -> str:
    try:
        raw = path.read_bytes()[:4]
    except OSError:
        return "utf-8"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def read_channel_name(path: Path, encoding: str) -> str:
    try:
        with path.open("r", encoding=encoding, errors="replace") as handle:
            text = "".join(handle.readline() for _ in range(80))
    except OSError:
        return fallback_channel_name(path)
    return parse_channel_name_from_text(text) or fallback_channel_name(path)


def file_end_offset(path: Path, encoding: str) -> int:
    try:
        with path.open("r", encoding=encoding, errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            return handle.tell()
    except OSError:
        return 0


def watch_chat_logs(
    *,
    log_dir: Path,
    channel_filter: ChannelFilter,
    on_message: Callable[[ChatMessage], None],
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    read_existing: bool = False,
    stop_event: threading.Event | None = None,
    log: Callable[[str], None] = print,
) -> None:
    states: dict[Path, ChatLogState] = {}
    stop_event = stop_event or threading.Event()
    log_dir = log_dir.expanduser()

    if not log_dir.exists():
        raise CorpIntelError(f"Chat log folder does not exist: {log_dir}")

    log(f"Watching EVE chat logs in {log_dir}")
    log(f"Channel allowlist: {channel_filter.describe()}")
    if not read_existing:
        log("Starting at the end of existing files. New chat lines will be processed.")

    while not stop_event.is_set():
        discover_chat_log_files(log_dir, states, channel_filter, read_existing=read_existing, log=log)
        for state in list(states.values()):
            for message in read_new_messages(state):
                on_message(message)
        stop_event.wait(poll_seconds)


def discover_chat_log_files(
    log_dir: Path,
    states: dict[Path, ChatLogState],
    channel_filter: ChannelFilter,
    *,
    read_existing: bool,
    log: Callable[[str], None] = print,
) -> None:
    for path in sorted(log_dir.glob("*.txt")):
        if path in states:
            continue
        encoding = detect_encoding(path)
        channel = read_channel_name(path, encoding)
        if not channel_filter.allows(channel):
            continue
        offset = 0 if read_existing else file_end_offset(path, encoding)
        states[path] = ChatLogState(path=path, channel=channel, encoding=encoding, offset=offset)
        log(f"Sharing channel {channel!r} from {path.name}")


def read_new_messages(state: ChatLogState) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    try:
        with state.path.open("r", encoding=state.encoding, errors="replace") as handle:
            handle.seek(state.offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                parsed = parse_chat_line(line, channel=state.channel, log_path=str(state.path))
                if parsed:
                    messages.append(parsed)
            state.offset = handle.tell()
    except OSError:
        return []
    return messages


def load_system_names(
    *,
    cache_path: Path = DEFAULT_SYSTEM_CACHE_PATH,
    base_url: str = DEFAULT_ESI_BASE_URL,
    refresh: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[str, ...]:
    if not refresh:
        cached = read_system_cache(cache_path)
        if cached:
            return cached

    try:
        names = fetch_system_names_from_esi(base_url=base_url)
    except CorpIntelError as exc:
        log(f"Could not refresh ESI system names: {exc}")
        cached = read_system_cache(cache_path)
        if cached:
            return cached
        log("Using a small built-in system list until ESI is reachable.")
        return COMMON_SYSTEM_NAMES

    write_system_cache(cache_path, names)
    return names


def read_system_cache(cache_path: Path) -> tuple[str, ...]:
    if not cache_path.exists():
        return ()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    systems = payload.get("systems") if isinstance(payload, dict) else None
    if not isinstance(systems, list):
        return ()
    names = tuple(str(item.get("name") or "") for item in systems if isinstance(item, dict))
    return tuple(name for name in names if name)


def write_system_cache(cache_path: Path, names: Iterable[str]) -> None:
    systems = [{"name": name} for name in sorted({name for name in names if name})]
    payload = {"fetched_at": now_iso(), "systems": systems}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def fetch_system_names_from_esi(*, base_url: str = DEFAULT_ESI_BASE_URL) -> tuple[str, ...]:
    ids = get_json(f"{base_url.rstrip('/')}/universe/systems/?datasource=tranquility")
    if not isinstance(ids, list):
        raise CorpIntelError("ESI universe systems endpoint returned unexpected data.")

    names: list[str] = []
    for chunk in chunked([int(item) for item in ids], 1000):
        payload = post_json(f"{base_url.rstrip('/')}/universe/names/?datasource=tranquility", chunk)
        if not isinstance(payload, list):
            raise CorpIntelError("ESI universe names endpoint returned unexpected data.")
        for item in payload:
            if isinstance(item, dict) and item.get("category") == "solar_system":
                name = str(item.get("name") or "").strip()
                if name:
                    names.append(name)
    if not names:
        raise CorpIntelError("ESI returned no solar system names.")
    return tuple(sorted(set(names)))


def get_json(url: str, *, timeout_seconds: float = 30.0) -> Any:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "EVE Voice Pilot Corp Intel Board"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CorpIntelError(f"GET {url} failed: {exc}") from exc


def post_json(url: str, body: Any, *, token: str = "", timeout_seconds: float = 30.0) -> Any:
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "EVE Voice Pilot Corp Intel Board",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CorpIntelError(f"POST {url} returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise CorpIntelError(f"POST {url} failed: {exc}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorpIntelError(f"POST {url} returned non-JSON data: {raw[:200]!r}") from exc


def chunked(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def summarize_counts(events: list[IntelEvent]) -> dict[str, int]:
    counts = {"events": len(events), "critical": 0, "high": 0, "aid": 0, "hostile": 0}
    for event in events:
        if event.severity in {"critical", "high"}:
            counts[event.severity] += 1
        for category in event.categories:
            if category in {"aid", "hostile"}:
                counts[category] += 1
    return counts


def summarize_systems(events: list[IntelEvent]) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for event in events:
        for system in event.systems:
            item = summary.setdefault(
                system,
                {
                    "system": system,
                    "count": 0,
                    "latest_at": "",
                    "severity": "info",
                    "categories": set(),
                    "keywords": set(),
                    "sources": set(),
                },
            )
            item["count"] += 1
            item["latest_at"] = max(str(item["latest_at"]), event.observed_at or event.reported_at)
            item["severity"] = higher_severity(str(item["severity"]), event.severity)
            item["categories"].update(event.categories)
            item["keywords"].update(event.keywords)
            item["sources"].add(event.source)

    result: list[dict[str, Any]] = []
    for item in summary.values():
        result.append(
            {
                "system": item["system"],
                "count": item["count"],
                "latest_at": item["latest_at"],
                "severity": item["severity"],
                "categories": sorted(item["categories"]),
                "keywords": sorted(item["keywords"]),
                "sources": sorted(item["sources"]),
            }
        )
    result.sort(key=lambda item: (SEVERITY_RANK.get(str(item["severity"]), 0), str(item["latest_at"])), reverse=True)
    return result


def start_local_watcher_thread(
    *,
    log_dir: Path,
    channel_filter: ChannelFilter,
    intel_parser: IntelParser,
    store: IntelEventStore,
    source: str,
    poll_seconds: float,
    read_existing: bool,
) -> threading.Thread:
    def on_message(message: ChatMessage) -> None:
        event = intel_parser.analyze(message, source=source)
        if event:
            store.add(event)
            print(format_event_line(event))

    thread = threading.Thread(
        target=watch_chat_logs,
        kwargs={
            "log_dir": log_dir,
            "channel_filter": channel_filter,
            "on_message": on_message,
            "poll_seconds": poll_seconds,
            "read_existing": read_existing,
        },
        daemon=True,
    )
    thread.start()
    return thread


def run_agent(args: argparse.Namespace) -> int:
    channel_filter = channel_filter_from_args(args)
    system_names = load_system_names(refresh=args.refresh_systems)
    intel_parser = IntelParser(system_names)
    endpoint = args.server.rstrip("/") + "/api/ingest"

    print("Corp intel agent is read-only.")
    print(f"Pilot/source label: {args.pilot}")
    print(f"Server endpoint: {endpoint}")
    print(f"Channel allowlist: {channel_filter.describe()}")
    if args.dry_run:
        print("Dry run is on. Matching intel events will be printed but not uploaded.")

    def on_message(message: ChatMessage) -> None:
        event = intel_parser.analyze(message, source=args.pilot)
        if event is None:
            return
        if args.dry_run:
            print(format_event_line(event))
            return
        try:
            post_json(endpoint, event.to_dict(), token=args.token, timeout_seconds=args.post_timeout)
            print(format_event_line(event))
        except CorpIntelError as exc:
            print(f"Upload failed: {exc}", file=sys.stderr)

    try:
        watch_chat_logs(
            log_dir=args.log_dir,
            channel_filter=channel_filter,
            on_message=on_message,
            poll_seconds=args.poll,
            read_existing=args.read_existing,
        )
    except CorpIntelError as exc:
        print(f"Corp intel error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_server(args: argparse.Namespace) -> int:
    channel_filter = channel_filter_from_args(args)
    system_names = load_system_names(refresh=args.refresh_systems)
    store = IntelEventStore(max_events=args.max_events)
    intel_parser = IntelParser(system_names)

    if args.watch_local:
        start_local_watcher_thread(
            log_dir=args.log_dir,
            channel_filter=channel_filter,
            intel_parser=intel_parser,
            store=store,
            source=args.source,
            poll_seconds=args.poll,
            read_existing=args.read_existing,
        )

    server = build_http_server(args.host, args.port, store, ingest_token=args.ingest_token)
    url_host = "127.0.0.1" if args.host in {"0.0.0.0", ""} else args.host
    url = f"http://{url_host}:{args.port}/"
    print(f"Corp intel board listening at {url}")
    if args.ingest_token:
        print("Remote agent uploads require the shared ingest token.")
    if args.host == "0.0.0.0":
        print("LAN mode is enabled. Share your computer's LAN IP and port with opted-in corp members.")
    if args.open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


def build_http_server(host: str, port: int, store: IntelEventStore, *, ingest_token: str = "") -> ThreadingHTTPServer:
    class CorpIntelHandler(BaseHTTPRequestHandler):
        server_version = "CorpIntelBoard/0.1"

        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                self._send_html(DASHBOARD_HTML)
                return
            if self.path == "/api/state":
                self._send_json(store.snapshot())
                return
            if self.path == "/api/health":
                self._send_json({"ok": True, "generated_at": now_iso()})
                return
            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            if self.path != "/api/ingest":
                self.send_error(404, "Not found")
                return
            if ingest_token and not request_has_token(self, ingest_token):
                self.send_error(401, "Missing or invalid ingest token")
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_error(400, f"Invalid JSON: {exc}")
                return

            try:
                added = ingest_payload(payload, store)
            except (TypeError, ValueError) as exc:
                self.send_error(400, f"Invalid event payload: {exc}")
                return
            self._send_json({"ok": True, "added": added})

        def log_message(self, format: str, *args: Any) -> None:
            print(f"{self.address_string()} - {format % args}")

        def _send_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, markup: str) -> None:
            body = markup.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), CorpIntelHandler)


def request_has_token(handler: BaseHTTPRequestHandler, expected: str) -> bool:
    auth = handler.headers.get("Authorization", "")
    token = handler.headers.get("X-Intel-Token", "")
    return auth == f"Bearer {expected}" or token == expected


def ingest_payload(payload: Any, store: IntelEventStore) -> int:
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        events = payload["events"]
    else:
        events = [payload]
    added = 0
    for item in events:
        if not isinstance(item, dict):
            raise TypeError("Event must be a JSON object.")
        event = IntelEvent.from_dict(item)
        if not event.message:
            raise ValueError("Event message is required.")
        store.add(event)
        print(format_event_line(event))
        added += 1
    return added


def format_event_line(event: IntelEvent) -> str:
    systems = ", ".join(event.systems) if event.systems else "no system"
    keywords = ", ".join(event.keywords) if event.keywords else "no keywords"
    speaker = f"{event.speaker}: " if event.speaker else ""
    return f"[{event.severity.upper()}] {systems} | {event.channel} | {event.source} | {keywords} | {speaker}{event.message}"


def channel_filter_from_args(args: argparse.Namespace) -> ChannelFilter:
    channels = parse_csv(args.channels)
    if not args.all_channels and not channels:
        raise CorpIntelError("Choose channels with --channels or explicitly pass --all-channels.")
    return ChannelFilter(channels, all_channels=args.all_channels)


def parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a read-only EVE corp intel board from opt-in chat logs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start the web dashboard and optional local log watcher.")
    add_common_watch_args(serve)
    serve.add_argument("--host", default="127.0.0.1", help="Bind address. Use 0.0.0.0 for LAN sharing.")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Dashboard port.")
    serve.add_argument("--watch-local", action="store_true", help="Watch this computer's chat logs too.")
    serve.add_argument("--source", default=os.environ.get("USERNAME", "local"), help="Source label for local events.")
    serve.add_argument("--ingest-token", default="", help="Shared token required for remote agent uploads.")
    serve.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS, help="Maximum events retained in memory.")
    serve.add_argument("--open-browser", action="store_true", help="Open the dashboard in your default browser.")
    serve.set_defaults(func=run_server)

    agent = subparsers.add_parser("agent", help="Watch local logs and upload matching intel to a dashboard server.")
    add_common_watch_args(agent)
    agent.add_argument("--server", required=True, help="Dashboard server URL, like http://1.2.3.4:8765")
    agent.add_argument("--token", default="", help="Shared ingest token from the dashboard server.")
    agent.add_argument("--pilot", default=os.environ.get("USERNAME", "pilot"), help="Pilot/source label shown on events.")
    agent.add_argument("--dry-run", action="store_true", help="Print matching events without uploading.")
    agent.add_argument("--post-timeout", type=float, default=10.0, help="Seconds to wait for upload responses.")
    agent.set_defaults(func=run_agent)

    return parser


def add_common_watch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-dir", type=Path, default=default_chat_log_dir(), help="EVE Chatlogs folder.")
    parser.add_argument(
        "--channels",
        default=DEFAULT_CHANNELS,
        help="Comma-separated channel allowlist. Wildcards are allowed, like *Intel*.",
    )
    parser.add_argument("--all-channels", action="store_true", help="Allow all chat log channels. Use carefully.")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS, help="Seconds between log checks.")
    parser.add_argument("--read-existing", action="store_true", help="Process existing lines instead of only new lines.")
    parser.add_argument("--refresh-systems", action="store_true", help="Refresh solar system names from public ESI.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except CorpIntelError as exc:
        print(f"Corp intel error: {exc}", file=sys.stderr)
        return 1


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Corp Intel Board</title>
  <style>
    :root {
      --bg: #f5f6f3;
      --panel: #ffffff;
      --ink: #1f2528;
      --muted: #667078;
      --line: #d8ddd8;
      --critical: #b42318;
      --high: #b05d00;
      --medium: #276a73;
      --info: #4d6678;
      --green: #2f6f54;
      --blue: #285f91;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      padding: 16px 22px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfa;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      line-height: 1.2;
      font-weight: 700;
    }
    .subtitle {
      margin-top: 3px;
      color: var(--muted);
      font-size: 13px;
    }
    .status {
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--green);
      display: inline-block;
    }
    main {
      display: grid;
      grid-template-columns: minmax(260px, 360px) 1fr;
      gap: 18px;
      padding: 18px;
    }
    section {
      min-width: 0;
    }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric, .panel, .event {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric {
      padding: 12px;
      min-height: 76px;
    }
    .metric strong {
      display: block;
      font-size: 24px;
      line-height: 1.2;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }
    .panel {
      overflow: hidden;
    }
    .panel h2 {
      margin: 0;
      padding: 12px 14px;
      font-size: 15px;
      border-bottom: 1px solid var(--line);
      background: #f9faf7;
    }
    .system-list {
      display: grid;
    }
    .system {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      padding: 11px 14px;
      border-bottom: 1px solid var(--line);
    }
    .system > div {
      min-width: 0;
    }
    .system:last-child {
      border-bottom: 0;
    }
    .system-name {
      font-weight: 700;
    }
    .details {
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      color: #fff;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      justify-self: start;
      white-space: nowrap;
    }
    .system .badge {
      justify-self: end;
    }
    .critical { background: var(--critical); }
    .high { background: var(--high); }
    .medium { background: var(--medium); }
    .info { background: var(--info); }
    .event-list {
      display: grid;
      gap: 10px;
    }
    .event {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 12px;
      align-items: start;
      padding: 12px;
    }
    .event-message {
      font-size: 15px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .event-meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 5px;
      overflow-wrap: anywhere;
    }
    .empty {
      padding: 18px;
      color: var(--muted);
      font-size: 14px;
    }
    @media (max-width: 860px) {
      header {
        align-items: flex-start;
        flex-direction: column;
      }
      main {
        grid-template-columns: 1fr;
      }
      .event {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 520px) {
      .metric-row {
        grid-template-columns: 1fr;
      }
      .system {
        grid-template-columns: 1fr;
      }
      .system .badge {
        justify-self: start;
      }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Corp Intel Board</h1>
      <div class="subtitle">Read-only chat intel from opted-in pilots</div>
    </div>
    <div class="status"><span class="dot"></span><span id="status">Connecting</span></div>
  </header>
  <main>
    <section>
      <div class="metric-row">
        <div class="metric"><strong id="count-critical">0</strong><span>critical aid calls</span></div>
        <div class="metric"><strong id="count-high">0</strong><span>high hostile reports</span></div>
        <div class="metric"><strong id="count-aid">0</strong><span>aid matches</span></div>
        <div class="metric"><strong id="count-hostile">0</strong><span>hostile matches</span></div>
      </div>
      <div class="panel">
        <h2>Hot Systems</h2>
        <div class="system-list" id="systems"></div>
      </div>
    </section>
    <section>
      <div class="panel">
        <h2>Live Intel</h2>
        <div class="event-list" id="events"></div>
      </div>
    </section>
  </main>
  <script>
    const state = { lastEventId: "" };

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }

    function ageLabel(value) {
      const time = Date.parse(value);
      if (!Number.isFinite(time)) return "unknown age";
      const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
      if (seconds < 60) return `${seconds}s ago`;
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      return `${Math.floor(minutes / 60)}h ago`;
    }

    function renderCounts(counts) {
      document.getElementById("count-critical").textContent = counts.critical ?? 0;
      document.getElementById("count-high").textContent = counts.high ?? 0;
      document.getElementById("count-aid").textContent = counts.aid ?? 0;
      document.getElementById("count-hostile").textContent = counts.hostile ?? 0;
    }

    function renderSystems(systems) {
      const target = document.getElementById("systems");
      if (!systems.length) {
        target.innerHTML = `<div class="empty">No system-linked intel yet.</div>`;
        return;
      }
      target.innerHTML = systems.slice(0, 20).map(item => `
        <div class="system">
          <div>
            <div class="system-name">${escapeHtml(item.system)}</div>
            <div class="details">${escapeHtml((item.keywords || []).join(", "))}</div>
            <div class="details">${escapeHtml((item.sources || []).join(", "))} - ${ageLabel(item.latest_at)}</div>
          </div>
          <span class="badge ${escapeHtml(item.severity)}">${escapeHtml(item.severity)}</span>
        </div>
      `).join("");
    }

    function renderEvents(events) {
      const target = document.getElementById("events");
      if (!events.length) {
        target.innerHTML = `<div class="empty">Waiting for hostile reports or aid calls.</div>`;
        return;
      }
      target.innerHTML = events.slice(0, 100).map(event => {
        const systems = (event.systems || []).length ? event.systems.join(", ") : "No system";
        const keywords = (event.keywords || []).join(", ");
        return `
          <div class="event">
            <span class="badge ${escapeHtml(event.severity)}">${escapeHtml(event.severity)}</span>
            <div>
              <div class="event-message">${escapeHtml(event.message)}</div>
              <div class="event-meta">${escapeHtml(systems)} - ${escapeHtml(event.channel)} - ${escapeHtml(event.source)} - ${escapeHtml(keywords)}</div>
              <div class="event-meta">${escapeHtml(event.speaker)} - ${ageLabel(event.observed_at || event.reported_at)}</div>
            </div>
            <div class="event-meta">${escapeHtml((event.categories || []).join(", "))}</div>
          </div>
        `;
      }).join("");
    }

    async function refresh() {
      try {
        const response = await fetch("/api/state", { cache: "no-store" });
        const payload = await response.json();
        renderCounts(payload.counts || {});
        renderSystems(payload.systems || []);
        renderEvents(payload.events || []);
        document.getElementById("status").textContent = `Live - ${ageLabel(payload.generated_at)}`;
      } catch (error) {
        document.getElementById("status").textContent = "Connection lost";
      }
    }

    refresh();
    setInterval(refresh, 1500);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
