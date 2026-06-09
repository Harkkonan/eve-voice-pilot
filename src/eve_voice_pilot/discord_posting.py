from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse
from urllib.request import Request, urlopen


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


def validate_discord_webhook_url(webhook_url: str, *, deps: DiscordPostingDependencies) -> None:
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or parsed.netloc not in {"discord.com", "discordapp.com"}:
        raise deps.market_error("Discord webhook URL must start with https://discord.com/api/webhooks/...")
    if not DISCORD_WEBHOOK_PATH_RE.match(parsed.path):
        raise deps.market_error(
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
