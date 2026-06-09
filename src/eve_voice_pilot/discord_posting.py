from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_DISCORD_ALERT_SENDER_NAME = "IntelPet"
DEFAULT_DISCORD_ALERT_ROUTE_NAME = "IntelPet server webhook"
DEFAULT_DISCORD_ALERT_DESTINATION = "Configured Discord alert channel"
DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR = "CORP_MARKET_DISCORD_WEBHOOK_URL"
DEFAULT_DISCORD_POST_SENDER_NAME = "Corp Market Concierge"
DEFAULT_DISCORD_POST_DESTINATION = "Corp buy-or-sell channel"
DEFAULT_DISCORD_FITTING_POST_SENDER_NAME = "Fittings Desk"
DEFAULT_DISCORD_FITTING_POST_DESTINATION = "Fittings"
DEFAULT_DISCORD_ALERT_SETTINGS_PATH = Path("profiles") / "corp_discord_alert_settings.json"
DEFAULT_DISCORD_POST_SETTINGS_PATH = Path("profiles") / "corp_discord_post_settings.json"
DEFAULT_DISCORD_FITTING_POST_SETTINGS_PATH = Path("profiles") / "corp_fitting_discord_post_settings.json"
DISCORD_ALERT_ROUTE_TYPES = frozenset({"webhook", "user_oauth_future"})
DISCORD_ALERT_EVENT_TYPES = frozenset({"intel", "help", "market", "location", "combat", "custom"})
DISCORD_ALERT_SEVERITIES = frozenset({"critical", "high", "medium", "info"})
DISCORD_DIRECT_POST_TYPES = frozenset(
    {"wts", "wtb", "buyback", "market_order", "contract", "hauling", "service", "announcement"}
)
MAX_DISCORD_ALERT_ROUTES = 6
MAX_DISCORD_ALERT_RULES = 24
MAX_DISCORD_ALERT_PHRASES = 24
MAX_DISCORD_ALERT_EVENT_TEXT = 700
MAX_DISCORD_DIRECT_POST_DETAILS = 1800
DISCORD_CONTENT_MAX_LENGTH = 2000
DISCORD_THREAD_NAME_MAX_LENGTH = 100
DEFAULT_MAX_FITTING_TEXT_LENGTH = 12000
LISTING_CATEGORIES = {
    "general": "General",
    "ships": "Ships",
    "modules": "Modules",
    "ammo": "Ammo",
    "ore": "Ore",
    "minerals": "Minerals",
    "pi": "PI",
    "salvage": "Salvage",
    "blueprints": "Blueprints",
    "hauling": "Hauling",
}
SPACE_RE = re.compile(r"\s+")
DISCORD_SNOWFLAKE_RE = re.compile(r"^\d{5,25}$")
FIT_HEADER_RE = re.compile(r"^\[(?P<hull>[^,\]]+),\s*(?P<name>[^\]]+)\]\s*$")
FIT_QUANTITY_RE = re.compile(r"\sx[\d,]+\s*$", re.IGNORECASE)
DISCORD_WEBHOOK_PATH_RE = re.compile(r"^/api/(?:v\d+/)?webhooks/\d+/[^/]+/?$")
DISCORD_USER_AGENT = "EveVoicePilot-CorpMarket/0.1"


@dataclass(frozen=True)
class DiscordPostResult:
    message_id: str = ""
    channel_id: str = ""
    thread_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"message_id": self.message_id, "channel_id": self.channel_id, "thread_id": self.thread_id}


@dataclass(frozen=True)
class DiscordPostingDependencies:
    market_error: Callable[[str], Exception]
    clean_discord_snowflake: Callable[[Any, str], str]
    request_factory: Callable[..., Any] = Request
    urlopen: Callable[..., Any] = urlopen


def add_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    replacements = {key: value for key, value in params.items() if value}
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in replacements]
    query.extend(replacements.items())
    return parsed._replace(query=urlencode(query), fragment="").geturl()


def validate_discord_webhook_url(webhook_url: str, *, deps: DiscordPostingDependencies | None = None) -> None:
    parsed = urlparse(webhook_url)
    error_factory = deps.market_error if deps is not None else ValueError
    if parsed.scheme != "https" or parsed.netloc not in {"discord.com", "discordapp.com"}:
        raise error_factory("Discord webhook URL must start with https://discord.com/api/webhooks/...")
    if not DISCORD_WEBHOOK_PATH_RE.match(parsed.path):
        raise error_factory(
            "Discord webhook URL looks wrong. Copy it from Channel Settings > Integrations > Webhooks > Copy "
            "Webhook URL; do not use the Discord channel or forum post link."
        )


def build_discord_message_edit_url(
    webhook_url: str,
    message_id: str,
    *,
    thread_id: str = "",
    deps: DiscordPostingDependencies,
) -> str:
    validate_discord_webhook_url(webhook_url, deps=deps)
    clean_message_id = deps.clean_discord_snowflake(message_id, "discord_message_id")
    parsed = urlparse(webhook_url)
    base_url = parsed._replace(path=f"{parsed.path.rstrip('/')}/messages/{clean_message_id}", query="", fragment="").geturl()
    if thread_id:
        base_url = add_query_params(base_url, {"thread_id": deps.clean_discord_snowflake(thread_id, "discord_thread_id")})
    return base_url


def discord_message_edit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in ("content", "embeds", "allowed_mentions") if key in payload}


def parse_discord_message_response(body: bytes, *, deps: DiscordPostingDependencies) -> DiscordPostResult:
    if not body.strip():
        return DiscordPostResult()
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise deps.market_error(f"Discord returned an unreadable message response: {exc}") from exc
    if not isinstance(payload, dict):
        return DiscordPostResult()
    message_id = deps.clean_discord_snowflake(payload.get("id", ""), "discord_message_id")
    channel_id = deps.clean_discord_snowflake(payload.get("channel_id", ""), "discord_channel_id")
    thread_id = deps.clean_discord_snowflake(payload.get("thread_id", ""), "discord_thread_id")
    return DiscordPostResult(message_id=message_id, channel_id=channel_id, thread_id=thread_id)


def post_discord_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    deps: DiscordPostingDependencies,
) -> DiscordPostResult | None:
    if not webhook_url:
        return None
    validate_discord_webhook_url(webhook_url, deps=deps)
    request = deps.request_factory(
        add_query_params(webhook_url, {"wait": "true"}),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": DISCORD_USER_AGENT},
        method="POST",
    )
    try:
        with deps.urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise deps.market_error(f"Discord webhook returned HTTP {response.status}.")
            return parse_discord_message_response(response.read(), deps=deps)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise deps.market_error(f"Discord webhook returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise deps.market_error(f"Discord webhook failed: {exc.reason}") from exc


def edit_discord_webhook_message(
    webhook_url: str,
    message_id: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    thread_id: str = "",
    deps: DiscordPostingDependencies,
) -> DiscordPostResult | None:
    validate_discord_webhook_url(webhook_url, deps=deps)
    url = build_discord_message_edit_url(webhook_url, message_id, thread_id=thread_id, deps=deps)
    request = deps.request_factory(
        url,
        data=json.dumps(discord_message_edit_payload(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": DISCORD_USER_AGENT},
        method="PATCH",
    )
    try:
        with deps.urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise deps.market_error(f"Discord webhook edit returned HTTP {response.status}.")
            return parse_discord_message_response(response.read(), deps=deps)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise deps.market_error(f"Discord webhook edit returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise deps.market_error(f"Discord webhook edit failed: {exc.reason}") from exc


@dataclass(frozen=True)
class FitNoteSummary:
    hull: str
    fit_name: str
    fitted_lines: tuple[str, ...]
    cargo_lines: tuple[str, ...]
    empty_slots: int

    @property
    def display_name(self) -> str:
        return f"{self.hull} - {self.fit_name}"


def query_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def clean_text(value: Any, field: str, *, max_length: int, required: bool = False) -> str:
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    if required and not text:
        raise ValueError(f"{field} is required.")
    if len(text) > max_length:
        raise ValueError(f"{field} must be {max_length} characters or less.")
    return text


def clean_multiline(value: Any, field: str, *, max_length: int) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.split("\n")]
    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line)
        previous_blank = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    text = "\n".join(cleaned)
    if len(text) > max_length:
        raise ValueError(f"{field} must be {max_length} characters or less.")
    return text


def clean_fitting_text(value: Any) -> str:
    text = clean_multiline(value, "fitting_text", max_length=DEFAULT_MAX_FITTING_TEXT_LENGTH)
    if not text:
        raise ValueError("fitting_text is required.")
    if parse_fit_note(text) is None:
        raise ValueError("fitting_text must use the standard EVE fitting clipboard format.")
    return text


def clean_choice(value: Any, allowed: set[str] | frozenset[str], field: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    if text not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}.")
    return text


def clean_optional_url(value: Any, field: str) -> str:
    text = clean_text(value, field, max_length=500)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be a full http or https URL.")
    return text


def clean_listing_id(value: Any) -> str:
    listing_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", listing_id):
        raise ValueError("listing_id is invalid.")
    return listing_id


def clean_discord_snowflake(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not DISCORD_SNOWFLAKE_RE.fullmatch(text):
        raise ValueError(f"{field} must be a Discord numeric ID.")
    return text


def format_isk(value: float | None) -> str:
    if value is None:
        return "quote"
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}b ISK"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}m ISK"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}k ISK"
    return f"{value:,.0f} ISK"


def listing_public_url(listing_id: str, public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/offers/{quote(clean_listing_id(listing_id))}"


def parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in re.split(r"[,\s]+", value) if item.strip())


def parse_forum_tag_map(value: str | None) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for item in parse_csv(value):
        if ":" not in item:
            raise ValueError("Forum tag map entries must look like key:tag_id.")
        key, tag_id = item.split(":", 1)
        normalized_key = key.strip().lower()
        normalized_tag_id = tag_id.strip()
        if not normalized_key or not normalized_tag_id:
            raise ValueError("Forum tag map entries must include both key and tag id.")
        result.setdefault(normalized_key, [])
        if normalized_tag_id not in result[normalized_key]:
            result[normalized_key].append(normalized_tag_id)
    return {key: tuple(values) for key, values in result.items()}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def shorten(value: str, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max(0, max_length - 1)].rstrip() + "..."


def parse_fit_note(notes: str) -> FitNoteSummary | None:
    lines = [line.strip() for line in notes.splitlines()]
    first_line_index = next((index for index, line in enumerate(lines) if line), None)
    if first_line_index is None:
        return None
    header_match = FIT_HEADER_RE.match(lines[first_line_index])
    if not header_match:
        return None
    fitted_lines: list[str] = []
    cargo_lines: list[str] = []
    empty_slots = 0
    for line in lines[first_line_index + 1 :]:
        if not line:
            continue
        if FIT_QUANTITY_RE.search(line):
            cargo_lines.append(line)
            continue
        if line.lower().startswith("[empty "):
            empty_slots += 1
            continue
        fitted_lines.append(line)
    return FitNoteSummary(
        hull=header_match.group("hull").strip(),
        fit_name=header_match.group("name").strip(),
        fitted_lines=tuple(fitted_lines),
        cargo_lines=tuple(cargo_lines),
        empty_slots=empty_slots,
    )


@dataclass(frozen=True)
class DiscordAlertRoute:
    name: str
    destination: str
    webhook_env_var: str
    enabled: bool = False
    route_type: str = "webhook"
    sender_name: str = DEFAULT_DISCORD_ALERT_SENDER_NAME

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "destination": self.destination,
            "webhook_env_var": self.webhook_env_var,
            "enabled": self.enabled,
            "route_type": self.route_type,
            "sender_name": self.sender_name,
            "sender_mode": "IntelPet webhook" if self.route_type == "webhook" else "User-owned sender (future)",
        }


@dataclass(frozen=True)
class DiscordAlertRule:
    name: str
    event_type: str
    severity: str
    phrases: tuple[str, ...]
    route_name: str
    include_matched_text: bool = False
    enabled: bool = False
    source: str = "intel_pet"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "event_type": self.event_type,
            "severity": self.severity,
            "phrases": list(self.phrases),
            "route_name": self.route_name,
            "include_matched_text": self.include_matched_text,
            "enabled": self.enabled,
            "source": self.source,
        }


@dataclass(frozen=True)
class DiscordAlertEvent:
    event_type: str
    severity: str
    summary: str
    source: str
    channel: str = ""
    system_name: str = ""
    matched_text: str = ""
    observed_at: str = ""


@dataclass(frozen=True)
class DiscordAlertSettings:
    enabled: bool = False
    dry_run: bool = True
    default_sender_name: str = DEFAULT_DISCORD_ALERT_SENDER_NAME
    routes: tuple[DiscordAlertRoute, ...] = field(default_factory=tuple)
    rules: tuple[DiscordAlertRule, ...] = field(default_factory=tuple)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "default_sender_name": self.default_sender_name,
            "routes": [route.to_dict() for route in self.routes],
            "rules": [rule.to_dict() for rule in self.rules],
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class DiscordWebhookDestination:
    destination_id: str
    label: str
    webhook_url: str = ""
    forum_posts: bool = False

    def to_dict(self, *, include_webhook_url: bool = False) -> dict[str, Any]:
        payload = {
            "id": self.destination_id,
            "label": self.label,
            "forum_posts": self.forum_posts,
            "webhook_configured": bool(self.webhook_url),
            "webhook_url_preview": redacted_discord_webhook_url(self.webhook_url),
        }
        if include_webhook_url:
            payload["webhook_url"] = self.webhook_url
        return payload


@dataclass(frozen=True)
class DiscordPostSettings:
    webhook_url: str = ""
    destination_label: str = DEFAULT_DISCORD_POST_DESTINATION
    sender_name: str = DEFAULT_DISCORD_POST_SENDER_NAME
    public_base_url: str = ""
    forum_posts: bool = False
    selected_webhook_id: str = ""
    webhook_destinations: tuple[DiscordWebhookDestination, ...] = field(default_factory=tuple)
    forum_tag_ids: tuple[str, ...] = field(default_factory=tuple)
    forum_tag_map: dict[str, tuple[str, ...]] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self, *, include_webhook_url: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "destination_label": self.destination_label,
            "footer_label": self.destination_label,
            "sender_name": self.sender_name,
            "public_base_url": self.public_base_url,
            "forum_posts": self.forum_posts,
            "selected_webhook_id": self.selected_webhook_id,
            "webhook_destinations": [
                destination.to_dict(include_webhook_url=include_webhook_url)
                for destination in self.webhook_destinations
            ],
            "forum_tag_ids": list(self.forum_tag_ids),
            "forum_tag_map": {key: list(values) for key, values in sorted(self.forum_tag_map.items())},
            "forum_tag_map_text": forum_tag_map_to_text(self.forum_tag_map),
            "updated_at": self.updated_at,
        }
        if include_webhook_url:
            payload["webhook_url"] = self.webhook_url
        return payload


@dataclass(frozen=True)
class DirectDiscordPost:
    post_type: str
    category: str
    title: str
    item_name: str
    quantity: str
    price_text: str
    location: str
    contact: str
    link_url: str
    details: str

    @property
    def type_label(self) -> str:
        labels = {
            "wts": "WTS",
            "wtb": "WTB",
            "buyback": "Buyback",
            "market_order": "Market Order",
            "contract": "Contract",
            "hauling": "Hauling",
            "service": "Service",
            "announcement": "Announcement",
        }
        return labels.get(self.post_type, self.post_type.upper())

    @property
    def category_label(self) -> str:
        return LISTING_CATEGORIES.get(self.category, self.category.title())

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_type": self.post_type,
            "type_label": self.type_label,
            "category": self.category,
            "category_label": self.category_label,
            "title": self.title,
            "item_name": self.item_name,
            "quantity": self.quantity,
            "price_text": self.price_text,
            "location": self.location,
            "contact": self.contact,
            "link_url": self.link_url,
            "details": self.details,
        }




def build_discord_webhook_payload(
    listing: MarketListing,
    *,
    public_base_url: str,
    forum_post: bool = False,
    forum_tag_ids: Iterable[str] = (),
    forum_tag_map: dict[str, tuple[str, ...]] | None = None,
    sender_name: str = "",
) -> dict[str, Any]:
    url = listing_public_url(listing.listing_id, public_base_url)
    color = discord_embed_color(listing)
    title = discord_listing_title(listing)
    contact_label = "Seller" if listing.listing_type == "sell" else "Buyer"
    fit_note = parse_fit_note(listing.notes)
    fields = [
        {"name": "Status", "value": discord_status_label(listing), "inline": True},
        {"name": "Category", "value": listing.category_label, "inline": True},
        {"name": "Quantity", "value": f"{listing.quantity:,}", "inline": True},
        {
            "name": "Unit",
            "value": format_isk(listing.unit_price_isk) if listing.unit_price_isk is not None else "Quote",
            "inline": True,
        },
        {
            "name": "Total",
            "value": format_isk(listing.total_price_isk) if listing.total_price_isk is not None else "Quote",
            "inline": True,
        },
        {"name": "Location", "value": listing.location or "Not specified", "inline": False},
        {"name": contact_label, "value": listing.owner, "inline": True},
    ]
    if fit_note:
        fields.append({"name": "Fit Note", "value": discord_fit_summary(fit_note), "inline": False})
    if listing.delivery:
        fields.append({"name": "Delivery", "value": listing.delivery, "inline": True})
    if listing.fit_image_url:
        fields.append({"name": "Fit Image", "value": f"[Open screenshot]({listing.fit_image_url})", "inline": True})
    embed: dict[str, Any] = {
        "title": title,
        "url": url,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Offer {listing.listing_id} \u00b7 manual EVE mail \u00b7 {listing.status}"},
        "timestamp": listing.updated_at or listing.created_at,
    }
    if fit_note:
        embed["description"] = "Fit note detected. Open the listing for the full copy/paste block."
    elif listing.notes:
        embed["description"] = shorten(listing.notes, 700)
    if listing.fit_image_url:
        embed["image"] = {"url": listing.fit_image_url}
    payload: dict[str, Any] = {
        "content": f"Open the listing to copy an EVE mail draft:\n{url}",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    if sender_name:
        payload["username"] = sanitize_discord_text(sender_name, max_length=80)
    if forum_post:
        payload["thread_name"] = discord_thread_name(listing)
        tag_ids = resolve_forum_tag_ids(
            listing,
            default_tag_ids=forum_tag_ids,
            tag_map=forum_tag_map or {},
        )
        if tag_ids:
            payload["applied_tags"] = list(tag_ids)
    return payload


DISCORD_ALERT_COLORS = {
    "critical": 0xE05A47,
    "high": 0xF0BA57,
    "medium": 0x61C7D9,
    "info": 0x89A69A,
}


def sanitize_discord_text(value: Any, *, max_length: int = 500) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace("@", "@ ")
    return shorten(text, max_length)


def sanitize_discord_alert_text(value: Any, *, max_length: int = 500) -> str:
    return sanitize_discord_text(value, max_length=max_length)


def normalize_discord_alert_key(value: Any, *, default: str, allowed: frozenset[str]) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return normalized if normalized in allowed else default


def clean_discord_alert_sender_name(value: Any) -> str:
    return clean_text(value or DEFAULT_DISCORD_ALERT_SENDER_NAME, "Discord alert sender name", max_length=80) or DEFAULT_DISCORD_ALERT_SENDER_NAME


def clean_discord_alert_route(value: Mapping[str, Any], *, default_sender_name: str) -> DiscordAlertRoute:
    name = clean_text(value.get("name") or DEFAULT_DISCORD_ALERT_ROUTE_NAME, "Discord alert route name", max_length=100) or DEFAULT_DISCORD_ALERT_ROUTE_NAME
    destination = (
        clean_text(value.get("destination") or DEFAULT_DISCORD_ALERT_DESTINATION, "Discord alert route destination", max_length=140)
        or DEFAULT_DISCORD_ALERT_DESTINATION
    )
    webhook_env_var = (
        clean_text(value.get("webhook_env_var") or DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR, "Discord alert webhook env var", max_length=100)
        or DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR
    )
    route_type = normalize_discord_alert_key(
        value.get("route_type"),
        default="webhook",
        allowed=DISCORD_ALERT_ROUTE_TYPES,
    )
    return DiscordAlertRoute(
        name=name,
        destination=destination,
        webhook_env_var=webhook_env_var,
        enabled=query_bool(value.get("enabled"), default=False),
        route_type=route_type,
        sender_name=clean_discord_alert_sender_name(value.get("sender_name") or default_sender_name),
    )


def clean_discord_alert_phrases(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_values = re.split(r"[\n,]+", value)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        raw_values = value
    else:
        raw_values = ()
    phrases: list[str] = []
    seen: set[str] = set()
    for raw_phrase in raw_values:
        phrase = clean_text(raw_phrase, "Discord alert phrase", max_length=90)
        if not phrase:
            continue
        normalized = phrase.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        phrases.append(phrase)
        if len(phrases) >= MAX_DISCORD_ALERT_PHRASES:
            break
    return tuple(phrases)


def clean_discord_alert_rule(value: Mapping[str, Any], *, fallback_route_name: str) -> DiscordAlertRule:
    name = clean_text(value.get("name") or "Corp intel alert", "Discord alert rule name", max_length=100) or "Corp intel alert"
    event_type = normalize_discord_alert_key(
        value.get("event_type"),
        default="intel",
        allowed=DISCORD_ALERT_EVENT_TYPES,
    )
    severity = normalize_discord_alert_key(
        value.get("severity"),
        default="high",
        allowed=DISCORD_ALERT_SEVERITIES,
    )
    phrases = clean_discord_alert_phrases(value.get("phrases"))
    route_name = clean_text(value.get("route_name") or fallback_route_name, "Discord alert route name", max_length=100) or fallback_route_name
    source = clean_text(value.get("source") or "intel_pet", "Discord alert source", max_length=80) or "intel_pet"
    return DiscordAlertRule(
        name=name,
        event_type=event_type,
        severity=severity,
        phrases=phrases,
        route_name=route_name,
        include_matched_text=query_bool(value.get("include_matched_text"), default=False),
        enabled=query_bool(value.get("enabled"), default=False),
        source=source,
    )


def default_discord_alert_settings() -> DiscordAlertSettings:
    route = DiscordAlertRoute(
        name=DEFAULT_DISCORD_ALERT_ROUTE_NAME,
        destination=DEFAULT_DISCORD_ALERT_DESTINATION,
        webhook_env_var=DEFAULT_DISCORD_ALERT_WEBHOOK_ENV_VAR,
        enabled=False,
        sender_name=DEFAULT_DISCORD_ALERT_SENDER_NAME,
    )
    rule = DiscordAlertRule(
        name="Corp help or hostile alert",
        event_type="intel",
        severity="high",
        phrases=("war target", "enemy vessels", "gate camp", "help"),
        route_name=route.name,
        include_matched_text=False,
        enabled=False,
        source="intel_pet",
    )
    return DiscordAlertSettings(
        enabled=False,
        dry_run=True,
        default_sender_name=DEFAULT_DISCORD_ALERT_SENDER_NAME,
        routes=(route,),
        rules=(rule,),
    )


def clean_discord_alert_settings_payload(payload: Mapping[str, Any]) -> DiscordAlertSettings:
    default_settings = default_discord_alert_settings()
    default_sender = clean_discord_alert_sender_name(
        payload.get("default_sender_name") or default_settings.default_sender_name
    )
    raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list):
        raw_routes = []
    routes = tuple(
        clean_discord_alert_route(route, default_sender_name=default_sender)
        for route in raw_routes[:MAX_DISCORD_ALERT_ROUTES]
        if isinstance(route, Mapping)
    )
    if not routes:
        routes = tuple(
            DiscordAlertRoute(
                name=route.name,
                destination=route.destination,
                webhook_env_var=route.webhook_env_var,
                enabled=route.enabled,
                route_type=route.route_type,
                sender_name=default_sender,
            )
            for route in default_settings.routes
        )
    route_names = {route.name for route in routes}
    fallback_route_name = routes[0].name
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        raw_rules = []
    rules = tuple(
        clean_discord_alert_rule(rule, fallback_route_name=fallback_route_name)
        for rule in raw_rules[:MAX_DISCORD_ALERT_RULES]
        if isinstance(rule, Mapping)
    )
    if not rules:
        rules = tuple(default_settings.rules)
    normalized_rules = tuple(
        rule if rule.route_name in route_names else DiscordAlertRule(
            name=rule.name,
            event_type=rule.event_type,
            severity=rule.severity,
            phrases=rule.phrases,
            route_name=fallback_route_name,
            include_matched_text=rule.include_matched_text,
            enabled=rule.enabled,
            source=rule.source,
        )
        for rule in rules
    )
    return DiscordAlertSettings(
        enabled=query_bool(payload.get("enabled"), default=default_settings.enabled),
        dry_run=query_bool(payload.get("dry_run"), default=default_settings.dry_run),
        default_sender_name=default_sender,
        routes=routes,
        rules=normalized_rules,
        updated_at=clean_text(payload.get("updated_at") or "", "Discord alert updated_at", max_length=60),
    )


def load_discord_alert_settings(path: Path = DEFAULT_DISCORD_ALERT_SETTINGS_PATH) -> DiscordAlertSettings:
    if not path.exists():
        return default_discord_alert_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read Discord alert settings: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Discord alert settings file must contain a JSON object.")
    return clean_discord_alert_settings_payload(payload)


def save_discord_alert_settings(
    settings: DiscordAlertSettings,
    path: Path = DEFAULT_DISCORD_ALERT_SETTINGS_PATH,
) -> DiscordAlertSettings:
    saved = DiscordAlertSettings(
        enabled=settings.enabled,
        dry_run=settings.dry_run,
        default_sender_name=settings.default_sender_name,
        routes=settings.routes,
        rules=settings.rules,
        updated_at=now_iso(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(json.dumps(saved.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    temporary_path.replace(path)
    return saved


def select_discord_alert_route(
    settings: DiscordAlertSettings,
    route_name: str = "",
) -> DiscordAlertRoute:
    if route_name:
        for route in settings.routes:
            if route.name == route_name:
                return route
    enabled_routes = [route for route in settings.routes if route.enabled and route.route_type == "webhook"]
    return (enabled_routes or list(settings.routes) or list(default_discord_alert_settings().routes))[0]


def select_discord_alert_rule(
    settings: DiscordAlertSettings,
    rule_name: str = "",
) -> DiscordAlertRule:
    if rule_name:
        for rule in settings.rules:
            if rule.name == rule_name:
                return rule
    enabled_rules = [rule for rule in settings.rules if rule.enabled]
    return (enabled_rules or list(settings.rules) or list(default_discord_alert_settings().rules))[0]


def discord_alert_sample_event(rule: DiscordAlertRule, event_payload: Mapping[str, Any] | None = None) -> DiscordAlertEvent:
    event_payload = event_payload or {}
    first_phrase = rule.phrases[0] if rule.phrases else rule.name
    summary = clean_text(
        event_payload.get("summary") or f"{rule.severity.title()} {rule.event_type} alert: {first_phrase}",
        "Discord alert test summary",
        max_length=180,
    )
    source = clean_text(
        event_payload.get("source") or "local Intel Pet",
        "Discord alert test source",
        max_length=120,
    )
    channel = clean_text(event_payload.get("channel") or "Local", "Discord alert test channel", max_length=80)
    system_name = clean_text(event_payload.get("system_name") or "Dihra", "Discord alert test system", max_length=80)
    matched_text = clean_multiline(
        event_payload.get("matched_text") or f"Sample matched line containing {first_phrase}.",
        "Discord alert test matched text",
        max_length=MAX_DISCORD_ALERT_EVENT_TEXT,
    )
    return DiscordAlertEvent(
        event_type=rule.event_type,
        severity=rule.severity,
        summary=summary,
        source=source,
        channel=channel,
        system_name=system_name,
        matched_text=matched_text,
        observed_at=now_iso(),
    )


def build_discord_alert_preview_payload(
    settings: DiscordAlertSettings,
    *,
    route_name: str = "",
    rule_name: str = "",
    event_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    route = select_discord_alert_route(settings, route_name)
    rule = select_discord_alert_rule(settings, rule_name)
    event = discord_alert_sample_event(rule, event_payload)
    return build_discord_alert_webhook_payload(event, rule, route)


def build_discord_alert_settings_response(
    settings: DiscordAlertSettings,
    *,
    settings_path: Path,
    webhook_configured: bool,
    event_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    route = select_discord_alert_route(settings)
    rule = select_discord_alert_rule(settings)
    return {
        "ok": True,
        "generated_at": now_iso(),
        "settings_file": settings_path.name,
        "webhook_configured": bool(webhook_configured),
        "default_sender": route.sender_name or settings.default_sender_name or DEFAULT_DISCORD_ALERT_SENDER_NAME,
        "settings": settings.to_dict(),
        "active_route": route.to_dict(),
        "active_rule": rule.to_dict(),
        "preview_payload": build_discord_alert_preview_payload(settings, event_payload=event_payload),
        "safety": {
            "webhook_url_stored": False,
            "allowed_mentions": "disabled",
            "matched_text_default": "hidden",
            "automatic_forwarding": False,
            "manual_test_sends": True,
        },
        "sender_modes": [
            {
                "key": "webhook",
                "label": "IntelPet webhook",
                "available": bool(webhook_configured),
                "detail": "Default sender. Uses the configured server webhook and displays as IntelPet.",
            },
            {
                "key": "user_oauth_future",
                "label": "User-owned sender",
                "available": False,
                "detail": "Future route. Needs a Discord bot/user-consent design, not a normal account password.",
            },
        ],
    }


def build_discord_alert_webhook_payload(
    event: DiscordAlertEvent,
    rule: DiscordAlertRule,
    route: DiscordAlertRoute,
) -> dict[str, Any]:
    severity = event.severity or rule.severity or "info"
    fields = [
        {"name": "Severity", "value": severity.title(), "inline": True},
        {"name": "Event", "value": sanitize_discord_alert_text(event.event_type or rule.event_type), "inline": True},
        {"name": "Route", "value": sanitize_discord_alert_text(route.name), "inline": True},
        {"name": "Source", "value": sanitize_discord_alert_text(event.source or "local opt-in alert router"), "inline": False},
    ]
    if event.channel:
        fields.append({"name": "Channel", "value": sanitize_discord_alert_text(event.channel), "inline": True})
    if event.system_name:
        fields.append({"name": "System", "value": sanitize_discord_alert_text(event.system_name), "inline": True})
    if rule.phrases:
        fields.append(
            {
                "name": "Matched Rule",
                "value": sanitize_discord_alert_text(", ".join(rule.phrases), max_length=300),
                "inline": False,
            }
        )
    if rule.include_matched_text and event.matched_text:
        fields.append(
            {
                "name": "Matched Text",
                "value": sanitize_discord_alert_text(event.matched_text, max_length=700),
                "inline": False,
            }
        )

    embed: dict[str, Any] = {
        "title": sanitize_discord_alert_text(event.summary or rule.name, max_length=180),
        "color": DISCORD_ALERT_COLORS.get(severity, DISCORD_ALERT_COLORS["info"]),
        "fields": fields,
        "footer": {"text": "Corp Discord alert router \u00b7 manual opt-in rules"},
        "timestamp": event.observed_at or now_iso(),
    }
    return {
        "username": sanitize_discord_alert_text(route.sender_name or DEFAULT_DISCORD_ALERT_SENDER_NAME, max_length=80),
        "content": sanitize_discord_alert_text(f"[{severity.upper()}] {event.summary or rule.name}", max_length=240),
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }


def clean_discord_post_sender_name(value: Any) -> str:
    return clean_text(value or DEFAULT_DISCORD_POST_SENDER_NAME, "Discord post sender name", max_length=80) or DEFAULT_DISCORD_POST_SENDER_NAME


def clean_discord_post_destination(value: Any) -> str:
    return clean_text(value or DEFAULT_DISCORD_POST_DESTINATION, "Discord post destination label", max_length=140) or DEFAULT_DISCORD_POST_DESTINATION


def clean_discord_post_footer_label(value: Any) -> str:
    return clean_text(value or DEFAULT_DISCORD_POST_DESTINATION, "Discord post footer label", max_length=140) or DEFAULT_DISCORD_POST_DESTINATION


def clean_discord_webhook_url(value: Any) -> str:
    webhook_url = str(value or "").strip()
    if not webhook_url:
        return ""
    validate_discord_webhook_url(webhook_url)
    return webhook_url


def clean_discord_webhook_destination_id(value: Any, *, fallback: str = "") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-_")
    if not text:
        text = fallback
    text = text[:64].strip("-_")
    return text or "default"


def discord_webhook_destination_by_id(
    settings: DiscordPostSettings,
    destination_id: str,
) -> DiscordWebhookDestination | None:
    for destination in settings.webhook_destinations:
        if destination.destination_id == destination_id:
            return destination
    return None


def selected_discord_webhook_destination(settings: DiscordPostSettings) -> DiscordWebhookDestination | None:
    if not settings.webhook_destinations:
        return None
    selected = discord_webhook_destination_by_id(settings, settings.selected_webhook_id)
    return selected or settings.webhook_destinations[0]


def discord_post_settings_with_selected_webhook(settings: DiscordPostSettings) -> DiscordPostSettings:
    selected = selected_discord_webhook_destination(settings)
    if selected is None:
        return settings
    return replace(
        settings,
        selected_webhook_id=selected.destination_id,
        webhook_url=selected.webhook_url,
        forum_posts=selected.forum_posts,
    )


def legacy_discord_webhook_destination(settings: DiscordPostSettings) -> DiscordWebhookDestination | None:
    if not settings.webhook_url:
        return None
    label = clean_discord_post_destination(settings.destination_label)
    destination_id = clean_discord_webhook_destination_id(settings.selected_webhook_id or label)
    return DiscordWebhookDestination(
        destination_id=destination_id,
        label=label,
        webhook_url=settings.webhook_url,
        forum_posts=settings.forum_posts,
    )


def clean_discord_webhook_destinations(
    value: Any,
    *,
    existing: DiscordPostSettings,
) -> tuple[DiscordWebhookDestination, ...]:
    existing_by_id = {destination.destination_id: destination for destination in existing.webhook_destinations}
    if not existing_by_id:
        legacy = legacy_discord_webhook_destination(existing)
        if legacy is not None:
            existing_by_id[legacy.destination_id] = legacy

    raw_items: Iterable[Any]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        raw_items = value
    else:
        raw_items = ()

    destinations: list[DiscordWebhookDestination] = []
    seen_ids: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        raw_label = raw_item.get("label") or raw_item.get("name") or raw_item.get("destination_label")
        fallback_id = clean_discord_webhook_destination_id(raw_label or "destination")
        destination_id = clean_discord_webhook_destination_id(raw_item.get("id") or raw_item.get("destination_id"), fallback=fallback_id)
        existing_destination = existing_by_id.get(destination_id)
        label = clean_discord_post_destination(
            raw_label
            or (existing_destination.label if existing_destination else "")
            or destination_id
        )
        raw_webhook = raw_item.get("webhook_url")
        webhook_url = existing_destination.webhook_url if existing_destination else ""
        if raw_webhook is not None and str(raw_webhook).strip():
            webhook_url = clean_discord_webhook_url(raw_webhook)
        forum_posts = query_bool(
            raw_item.get("forum_posts"),
            default=existing_destination.forum_posts if existing_destination else False,
        )
        if not webhook_url:
            continue
        base_id = destination_id
        suffix = 2
        while destination_id in seen_ids:
            destination_id = clean_discord_webhook_destination_id(f"{base_id}-{suffix}")
            suffix += 1
        seen_ids.add(destination_id)
        destinations.append(
            DiscordWebhookDestination(
                destination_id=destination_id,
                label=label,
                webhook_url=webhook_url,
                forum_posts=forum_posts,
            )
        )

    if not destinations:
        return tuple(existing_by_id.values())
    return tuple(destinations)


def clean_discord_forum_tag_ids(value: Any) -> tuple[str, ...]:
    raw_values: Iterable[Any]
    if isinstance(value, str):
        raw_values = parse_csv(value)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        raw_values = value
    else:
        raw_values = ()
    tag_ids: list[str] = []
    for raw_tag_id in raw_values:
        tag_id = clean_discord_snowflake(raw_tag_id, "discord_forum_tag_id")
        if tag_id and tag_id not in tag_ids:
            tag_ids.append(tag_id)
    return tuple(tag_ids)


def clean_discord_forum_tag_map(value: Any) -> dict[str, tuple[str, ...]]:
    if isinstance(value, str):
        return {
            key: clean_discord_forum_tag_ids(values)
            for key, values in parse_forum_tag_map(value).items()
        }
    if not isinstance(value, Mapping):
        return {}
    tag_map: dict[str, tuple[str, ...]] = {}
    for raw_key, raw_values in value.items():
        key = str(raw_key or "").strip().lower()
        if not key:
            continue
        tag_map[key] = clean_discord_forum_tag_ids(raw_values)
    return {key: values for key, values in tag_map.items() if values}


def forum_tag_map_to_text(tag_map: Mapping[str, Iterable[str]]) -> str:
    entries: list[str] = []
    for key in sorted(tag_map):
        for tag_id in tag_map.get(key, ()):
            if tag_id:
                entries.append(f"{key}:{tag_id}")
    return ",".join(entries)


def default_discord_post_settings() -> DiscordPostSettings:
    return DiscordPostSettings()


def default_discord_fitting_post_settings() -> DiscordPostSettings:
    return DiscordPostSettings(
        destination_label=DEFAULT_DISCORD_FITTING_POST_DESTINATION,
        sender_name=DEFAULT_DISCORD_FITTING_POST_SENDER_NAME,
        forum_posts=True,
    )


def clean_discord_post_settings_payload(
    payload: Mapping[str, Any],
    *,
    existing: DiscordPostSettings | None = None,
) -> DiscordPostSettings:
    existing = existing or default_discord_post_settings()
    clear_webhook = query_bool(payload.get("clear_webhook_url"), default=False)
    raw_webhook = payload.get("webhook_url")
    webhook_url = ""
    if not clear_webhook and raw_webhook is not None and str(raw_webhook).strip():
        webhook_url = clean_discord_webhook_url(raw_webhook)
    raw_public_base_url = payload.get("public_base_url", existing.public_base_url)
    public_base_url = clean_optional_url(raw_public_base_url or "", "public_base_url")
    footer_label = clean_discord_post_footer_label(
        payload.get("footer_label") or payload.get("destination_label") or existing.destination_label
    )
    selected_webhook_id = clean_discord_webhook_destination_id(
        payload.get("selected_webhook_id") or existing.selected_webhook_id
    )
    webhook_destinations = clean_discord_webhook_destinations(
        payload.get("webhook_destinations") or payload.get("destinations"),
        existing=existing,
    )
    selected_destination = None
    if selected_webhook_id:
        selected_destination = next(
            (destination for destination in webhook_destinations if destination.destination_id == selected_webhook_id),
            None,
        )
    webhook_label = clean_discord_post_destination(
        payload.get("webhook_label")
        or payload.get("destination_name")
        or (selected_destination.label if selected_destination else "")
        or footer_label
    )
    selected_webhook_id = selected_webhook_id or clean_discord_webhook_destination_id(webhook_label)
    selected_forum_posts = query_bool(payload.get("forum_posts"), default=existing.forum_posts)
    if webhook_url:
        updated_destinations = [
            destination for destination in webhook_destinations if destination.destination_id != selected_webhook_id
        ]
        updated_destinations.append(
            DiscordWebhookDestination(
                destination_id=selected_webhook_id,
                label=webhook_label,
                webhook_url=webhook_url,
                forum_posts=selected_forum_posts,
            )
        )
        webhook_destinations = tuple(updated_destinations)
    elif clear_webhook and selected_webhook_id:
        webhook_destinations = tuple(
            destination for destination in webhook_destinations if destination.destination_id != selected_webhook_id
        )
    settings = DiscordPostSettings(
        webhook_url=webhook_url,
        destination_label=footer_label,
        sender_name=clean_discord_post_sender_name(payload.get("sender_name") or existing.sender_name),
        public_base_url=public_base_url,
        forum_posts=selected_forum_posts,
        selected_webhook_id=selected_webhook_id,
        webhook_destinations=webhook_destinations,
        forum_tag_ids=clean_discord_forum_tag_ids(payload.get("forum_tag_ids", existing.forum_tag_ids)),
        forum_tag_map=clean_discord_forum_tag_map(payload.get("forum_tag_map", existing.forum_tag_map)),
        updated_at=clean_text(payload.get("updated_at") or existing.updated_at, "Discord post updated_at", max_length=60),
    )
    return discord_post_settings_with_selected_webhook(settings)


def clean_discord_fitting_post_settings_payload(
    payload: Mapping[str, Any],
    *,
    existing: DiscordPostSettings | None = None,
) -> DiscordPostSettings:
    return clean_discord_post_settings_payload(
        payload,
        existing=existing or default_discord_fitting_post_settings(),
    )


def load_discord_post_settings(path: Path = DEFAULT_DISCORD_POST_SETTINGS_PATH) -> DiscordPostSettings:
    if not path.exists():
        return default_discord_post_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read Discord posting settings: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Discord posting settings file must contain a JSON object.")
    return clean_discord_post_settings_payload(payload)


def load_discord_fitting_post_settings(
    path: Path = DEFAULT_DISCORD_FITTING_POST_SETTINGS_PATH,
) -> DiscordPostSettings:
    if not path.exists():
        return default_discord_fitting_post_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read Discord fitting posting settings: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Discord fitting posting settings file must contain a JSON object.")
    return clean_discord_fitting_post_settings_payload(payload)


def save_discord_post_settings(
    settings: DiscordPostSettings,
    path: Path = DEFAULT_DISCORD_POST_SETTINGS_PATH,
) -> DiscordPostSettings:
    saved = replace(settings, updated_at=now_iso())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(json.dumps(saved.to_dict(include_webhook_url=True), indent=2, sort_keys=True), encoding="utf-8")
    temporary_path.replace(path)
    return saved


def save_discord_fitting_post_settings(
    settings: DiscordPostSettings,
    path: Path = DEFAULT_DISCORD_FITTING_POST_SETTINGS_PATH,
) -> DiscordPostSettings:
    return save_discord_post_settings(settings, path)


def redacted_discord_webhook_url(webhook_url: str) -> str:
    if not webhook_url:
        return ""
    parsed = urlparse(webhook_url)
    parts = [part for part in parsed.path.split("/") if part]
    webhook_id = parts[-2] if len(parts) >= 2 else ""
    if not webhook_id:
        return "configured webhook"
    return f"https://{parsed.netloc}/api/webhooks/{webhook_id}/..."


def effective_discord_post_settings(
    settings: DiscordPostSettings,
    *,
    saved_settings_exists: bool,
    server_webhook_url: str,
    server_forum_posts: bool,
    server_forum_tag_ids: Iterable[str],
    server_forum_tag_map: dict[str, tuple[str, ...]],
    server_public_base_url: str,
) -> DiscordPostSettings:
    use_server_defaults = not saved_settings_exists and not settings.updated_at
    destinations = list(settings.webhook_destinations)
    if not destinations and settings.webhook_url:
        legacy = legacy_discord_webhook_destination(settings)
        if legacy is not None:
            destinations.append(legacy)
    if server_webhook_url and (use_server_defaults or not destinations):
        destinations.append(
            DiscordWebhookDestination(
                destination_id="server-default",
                label="Server configured webhook",
                webhook_url=server_webhook_url,
                forum_posts=server_forum_posts,
            )
        )
    selected_webhook_id = settings.selected_webhook_id
    if not selected_webhook_id and destinations:
        selected_webhook_id = destinations[0].destination_id
    effective = DiscordPostSettings(
        webhook_url=settings.webhook_url,
        destination_label=settings.destination_label,
        sender_name=settings.sender_name,
        public_base_url=settings.public_base_url or server_public_base_url,
        forum_posts=server_forum_posts if use_server_defaults else settings.forum_posts,
        selected_webhook_id=selected_webhook_id,
        webhook_destinations=tuple(destinations),
        forum_tag_ids=settings.forum_tag_ids or (tuple(server_forum_tag_ids) if use_server_defaults else ()),
        forum_tag_map=settings.forum_tag_map or (server_forum_tag_map if use_server_defaults else {}),
        updated_at=settings.updated_at,
    )
    return discord_post_settings_with_selected_webhook(effective)


def build_discord_post_settings_response(
    settings: DiscordPostSettings,
    *,
    effective_settings: DiscordPostSettings,
    settings_path: Path,
) -> dict[str, Any]:
    return {
        "ok": True,
        "generated_at": now_iso(),
        "settings_file": settings_path.name,
        "settings": settings.to_dict(),
        "effective_settings": effective_settings.to_dict(),
        "webhook_configured": bool(effective_settings.webhook_url),
        "webhook_url_preview": redacted_discord_webhook_url(effective_settings.webhook_url),
        "safety": {
            "webhook_url_stored_locally": bool(settings.webhook_url),
            "allowed_mentions": "disabled",
            "manual_send_only": True,
            "in_game_market_orders": False,
        },
    }


def clean_direct_discord_post_payload(payload: Mapping[str, Any]) -> DirectDiscordPost:
    post_type = normalize_discord_alert_key(payload.get("post_type"), default="wts", allowed=DISCORD_DIRECT_POST_TYPES)
    category = clean_choice(payload.get("category") or "general", set(LISTING_CATEGORIES), "category")
    title = clean_text(payload.get("title") or "", "Discord direct post title", max_length=120)
    item_name = clean_text(payload.get("item_name") or payload.get("item") or "", "Discord direct post item", max_length=120)
    if not title and not item_name:
        raise ValueError("Direct Discord post needs either a title or an item/service name.")
    return DirectDiscordPost(
        post_type=post_type,
        category=category,
        title=title,
        item_name=item_name,
        quantity=clean_text(payload.get("quantity") or "", "Discord direct post quantity", max_length=80),
        price_text=clean_text(payload.get("price_text") or payload.get("price") or "", "Discord direct post price", max_length=120),
        location=clean_text(payload.get("location") or "", "Discord direct post location", max_length=160),
        contact=clean_text(payload.get("contact") or payload.get("owner") or "", "Discord direct post contact", max_length=100),
        link_url=clean_optional_url(payload.get("link_url") or payload.get("appraisal_url") or "", "Discord direct post link"),
        details=clean_multiline(payload.get("details") or payload.get("notes") or "", "Discord direct post details", max_length=MAX_DISCORD_DIRECT_POST_DETAILS),
    )


def direct_discord_post_title(post: DirectDiscordPost) -> str:
    if post.title:
        return post.title
    title = f"{post.type_label} {post.item_name}".strip()
    if post.quantity:
        title += f" x{post.quantity}"
    if post.location:
        title += f" at {post.location}"
    if post.price_text:
        title += f" | {post.price_text}"
    return title


def direct_discord_post_color(post: DirectDiscordPost) -> int:
    colors = {
        "wts": 0x2E7D32,
        "wtb": 0x1565C0,
        "buyback": 0x7C3AED,
        "market_order": 0xF0BA57,
        "contract": 0xF0BA57,
        "hauling": 0x61C7D9,
        "service": 0x89A69A,
        "announcement": 0x6B7280,
    }
    return colors.get(post.post_type, 0x89A69A)


def resolve_direct_post_forum_tag_ids(
    post: DirectDiscordPost,
    *,
    default_tag_ids: Iterable[str],
    tag_map: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    tag_ids: list[str] = []
    for raw_id in default_tag_ids:
        tag_id = raw_id.strip()
        if tag_id and tag_id not in tag_ids:
            tag_ids.append(tag_id)
    keys = (post.post_type, post.type_label.lower(), post.category, post.category_label.lower())
    for key in keys:
        for tag_id in tag_map.get(key, ()):
            if tag_id and tag_id not in tag_ids:
                tag_ids.append(tag_id)
    return tuple(tag_ids)


def build_direct_discord_post_payload(post: DirectDiscordPost, settings: DiscordPostSettings) -> dict[str, Any]:
    title = sanitize_discord_text(direct_discord_post_title(post), max_length=180)
    fields = [
        {"name": "Type", "value": sanitize_discord_text(post.type_label), "inline": True},
        {"name": "Category", "value": sanitize_discord_text(post.category_label), "inline": True},
    ]
    if post.item_name:
        fields.append({"name": "Item / Service", "value": sanitize_discord_text(post.item_name, max_length=220), "inline": False})
    if post.quantity:
        fields.append({"name": "Quantity", "value": sanitize_discord_text(post.quantity, max_length=120), "inline": True})
    if post.price_text:
        fields.append({"name": "Price / Basis", "value": sanitize_discord_text(post.price_text, max_length=180), "inline": True})
    if post.location:
        fields.append({"name": "Location", "value": sanitize_discord_text(post.location, max_length=180), "inline": False})
    if post.contact:
        fields.append({"name": "Contact", "value": sanitize_discord_text(post.contact, max_length=120), "inline": True})
    if post.link_url:
        fields.append({"name": "Appraisal / Link", "value": f"[Open link]({post.link_url})", "inline": True})
    fields.append(
        {
            "name": "Next Step",
            "value": "Verify terms in EVE, then use contract, trade, market order, or EVE mail manually.",
            "inline": False,
        }
    )
    embed: dict[str, Any] = {
        "title": title,
        "color": direct_discord_post_color(post),
        "fields": fields,
        "footer": {"text": f"{settings.destination_label} \u00b7 manual Discord market post"},
        "timestamp": now_iso(),
    }
    if post.details:
        embed["description"] = sanitize_discord_text(post.details, max_length=1200)
    content_parts = [f"**{title}**"]
    if post.price_text:
        content_parts.append(f"Price/Basis: {sanitize_discord_text(post.price_text, max_length=160)}")
    if post.location:
        content_parts.append(f"Location: {sanitize_discord_text(post.location, max_length=160)}")
    payload: dict[str, Any] = {
        "username": sanitize_discord_text(settings.sender_name or DEFAULT_DISCORD_POST_SENDER_NAME, max_length=80),
        "content": "\n".join(content_parts),
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    if settings.forum_posts:
        payload["thread_name"] = title if len(title) <= DISCORD_THREAD_NAME_MAX_LENGTH else title[: DISCORD_THREAD_NAME_MAX_LENGTH - 3].rstrip() + "..."
        tag_ids = resolve_direct_post_forum_tag_ids(
            post,
            default_tag_ids=settings.forum_tag_ids,
            tag_map=settings.forum_tag_map,
        )
        if tag_ids:
            payload["applied_tags"] = list(tag_ids)
    return payload


def discord_fitting_title(fitting: SharedFitting) -> str:
    title = fitting.display_name if fitting.hull or fitting.fit_name else "Shared fitting"
    return sanitize_discord_text(SPACE_RE.sub(" ", title).strip(), max_length=180)


def discord_fitting_thread_name(fitting: SharedFitting) -> str:
    title = discord_fitting_title(fitting)
    if len(title) <= DISCORD_THREAD_NAME_MAX_LENGTH:
        return title
    return title[: DISCORD_THREAD_NAME_MAX_LENGTH - 3].rstrip() + "..."


def fitting_forum_tag_keys(fitting: SharedFitting) -> tuple[str, ...]:
    keys: list[str] = ["fitting", "fittings", "shared_fitting"]
    for value in (fitting.hull, fitting.fit_name, fitting.tags):
        normalized = SPACE_RE.sub(" ", str(value or "").strip().lower())
        if normalized and normalized not in keys:
            keys.append(normalized)
    for tag in parse_csv(fitting.tags):
        normalized = tag.strip().lower()
        if normalized and normalized not in keys:
            keys.append(normalized)
    return tuple(keys)


def resolve_fitting_forum_tag_ids(
    fitting: SharedFitting,
    *,
    default_tag_ids: Iterable[str],
    tag_map: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    tag_ids: list[str] = []
    for raw_id in default_tag_ids:
        tag_id = raw_id.strip()
        if tag_id and tag_id not in tag_ids:
            tag_ids.append(tag_id)
    for key in fitting_forum_tag_keys(fitting):
        for tag_id in tag_map.get(key, ()):
            if tag_id and tag_id not in tag_ids:
                tag_ids.append(tag_id)
    return tuple(tag_ids)


def discord_fitting_line_summary(fit_note: FitNoteSummary | None) -> str:
    if fit_note is None:
        return "EVE fitting block"
    parts = []
    if fit_note.fitted_lines:
        parts.append(f"{len(fit_note.fitted_lines)} fitted")
    if fit_note.empty_slots:
        parts.append(f"{fit_note.empty_slots} empty")
    if fit_note.cargo_lines:
        parts.append(f"{len(fit_note.cargo_lines)} cargo")
    return ", ".join(parts) or "Parsed EVE fitting block"


def build_discord_fitting_webhook_payload(
    fitting: SharedFitting,
    settings: DiscordPostSettings,
) -> dict[str, Any]:
    fitting_text = clean_fitting_text(fitting.fitting_text)
    if len(fitting_text) > DISCORD_CONTENT_MAX_LENGTH:
        raise ValueError(
            "This fitting block is too long for Discord's 2000 character message limit. "
            "Post the website fitting link or shorten the saved block before sending."
        )
    fit_note = parse_fit_note(fitting_text)
    title = discord_fitting_title(fitting)
    fields = [
        {"name": "Hull", "value": sanitize_discord_text(fitting.hull or "Unknown", max_length=120), "inline": True},
        {
            "name": "Lines",
            "value": discord_fitting_line_summary(fit_note),
            "inline": True,
        },
    ]
    if fitting.tags:
        fields.append({"name": "Tags", "value": sanitize_discord_text(fitting.tags, max_length=180), "inline": True})
    if fitting.submitted_by:
        fields.append({"name": "Submitted By", "value": sanitize_discord_text(fitting.submitted_by, max_length=120), "inline": True})
    if fitting.website_url:
        fields.append({"name": "Website Fit", "value": f"[Open link]({fitting.website_url})", "inline": True})
    embed: dict[str, Any] = {
        "title": title,
        "color": 0x61C7D9,
        "description": "EVE fitting clipboard format for manual import.",
        "fields": fields,
        "footer": {"text": f"{settings.destination_label or DEFAULT_DISCORD_FITTING_POST_DESTINATION} \u00b7 manual EVE fitting import"},
        "timestamp": fitting.updated_at or fitting.created_at or now_iso(),
    }
    payload: dict[str, Any] = {
        "username": sanitize_discord_text(settings.sender_name or DEFAULT_DISCORD_FITTING_POST_SENDER_NAME, max_length=80),
        "content": fitting_text,
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    if settings.forum_posts:
        payload["thread_name"] = discord_fitting_thread_name(fitting)
        tag_ids = resolve_fitting_forum_tag_ids(
            fitting,
            default_tag_ids=settings.forum_tag_ids,
            tag_map=settings.forum_tag_map,
        )
        if tag_ids:
            payload["applied_tags"] = list(tag_ids)
    return payload



def discord_fit_summary(fit_note: FitNoteSummary) -> str:
    slot_text = f"{len(fit_note.fitted_lines)} fitted lines"
    if fit_note.empty_slots:
        slot_text += f", {fit_note.empty_slots} empty slot{'s' if fit_note.empty_slots != 1 else ''}"
    cargo_text = f"{len(fit_note.cargo_lines)} cargo stack{'s' if len(fit_note.cargo_lines) != 1 else ''}"
    lines = [fit_note.display_name, f"{slot_text}; {cargo_text}"]
    if fit_note.cargo_lines:
        lines.append("Cargo: " + shorten("; ".join(fit_note.cargo_lines[:4]), 220))
    return shorten("\n".join(lines), 1000)


def discord_thread_name(listing: MarketListing) -> str:
    name = discord_listing_title(listing)
    name = SPACE_RE.sub(" ", name).strip()
    if len(name) <= DISCORD_THREAD_NAME_MAX_LENGTH:
        return name
    return name[: DISCORD_THREAD_NAME_MAX_LENGTH - 3].rstrip() + "..."


def discord_listing_title(listing: MarketListing) -> str:
    title = f"{listing.label} {listing.item_name} x{listing.quantity:,}"
    if listing.status == "open":
        return title
    return f"{listing.status.upper()} - {title}"


def discord_status_label(listing: MarketListing) -> str:
    if listing.status == "open":
        return "Open"
    if listing.status == "reserved":
        details = "Reserved"
        if listing.reserved_by:
            details += f" by {listing.reserved_by}"
        if listing.reserved_until:
            details += f"\nUntil {listing.reserved_until}"
        return details
    if listing.status == "sold":
        return "Sold"
    if listing.status == "cancelled":
        return "Cancelled"
    return listing.status.title()


def discord_embed_color(listing: MarketListing) -> int:
    if listing.status == "reserved":
        return 0xF0BA57
    if listing.status == "sold":
        return 0x6B7280
    if listing.status == "cancelled":
        return 0xE36F6F
    return 0x2E7D32 if listing.listing_type == "sell" else 0x1565C0


def resolve_forum_tag_ids(
    listing: MarketListing,
    *,
    default_tag_ids: Iterable[str],
    tag_map: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    tag_ids: list[str] = []
    for raw_id in default_tag_ids:
        tag_id = raw_id.strip()
        if tag_id and tag_id not in tag_ids:
            tag_ids.append(tag_id)
    keys = (
        listing.listing_type,
        listing.label.lower(),
        listing.category,
        listing.category_label.lower(),
    )
    for key in keys:
        for tag_id in tag_map.get(key, ()):
            if tag_id and tag_id not in tag_ids:
                tag_ids.append(tag_id)
    return tuple(tag_ids)
