from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from eve_voice_pilot.corp_intel import EveSsoConfig, VerifiedPilot


FLIGHT_SESSION_COOKIE_NAME = "corp_market_flight_session"

FLIGHT_GET_ROUTE_METHODS: dict[str, str] = {
    "/api/flight/status": "_handle_flight_status",
    "/api/flight/diagnostics": "_handle_flight_diagnostics",
    "/api/flight/systems": "_handle_flight_systems",
    "/api/flight/industry": "_handle_flight_industry",
    "/api/flight/buyers": "_handle_flight_buyers",
    "/api/flight/buyers/progress": "_handle_flight_buyers_progress",
    "/api/flight/profitability": "_handle_flight_profitability",
    "/api/flight/hauling": "_handle_flight_hauling",
    "/api/flight/hauling/compare": "_handle_flight_hauling_compare",
    "/api/flight/hauling/progress": "_handle_flight_hauling_progress",
    "/api/flight/acquisition": "_handle_flight_acquisition",
    "/api/flight/acquisition/progress": "_handle_flight_acquisition_progress",
    "/api/flight/trade-pnl": "_handle_flight_trade_pnl",
    "/api/flight/planetary": "_handle_flight_planetary",
    "/api/flight/reprocessing": "_handle_flight_reprocessing",
    "/api/flight/reprocessing-locations": "_handle_flight_reprocessing_locations",
    "/flight/login": "_handle_flight_login",
    "/flight/callback": "_handle_flight_callback",
    "/flight/logout": "_handle_flight_logout",
}

FLIGHT_POST_ROUTE_METHODS: dict[str, str] = {
    "/flight/logout": "_handle_flight_logout",
}


class FlightSessionLike(Protocol):
    membership_ok: bool


def request_path(raw_path: str) -> str:
    return urlparse(raw_path).path


def dispatch_route(handler: object, path: str, route_methods: Mapping[str, str]) -> bool:
    method_name = route_methods.get(path)
    if method_name is None:
        return False
    getattr(handler, method_name)()
    return True


def dispatch_flight_get_route(handler: object, path: str) -> bool:
    return dispatch_route(handler, path, FLIGHT_GET_ROUTE_METHODS)


def dispatch_flight_post_route(handler: object, path: str) -> bool:
    return dispatch_route(handler, path, FLIGHT_POST_ROUTE_METHODS)


def flight_session_has_member_access(config: EveSsoConfig, session: FlightSessionLike | None) -> bool:
    if session is None:
        return False
    if not config.membership_restricted:
        return True
    return bool(session.membership_ok)


def verified_pilot_has_member_access(config: EveSsoConfig, pilot: VerifiedPilot) -> bool:
    if not config.membership_restricted:
        return True
    return bool(pilot.membership_ok)


def flight_membership_status(config: EveSsoConfig, session: FlightSessionLike | None) -> dict[str, Any]:
    required = config.membership_restricted
    allowed = flight_session_has_member_access(config, session) if session else None
    if not required:
        message = "No corp or alliance allowlist is configured."
    elif session is None:
        message = "Sign in with an allowlisted corporation or alliance character."
    elif allowed:
        message = "Signed-in character is in the configured corp/alliance allowlist."
    else:
        message = "Signed-in character is not in the configured corp/alliance allowlist."
    return {
        "required": required,
        "allowed": allowed,
        "corporation_allowlist_count": len(config.allowed_corporation_ids),
        "alliance_allowlist_count": len(config.allowed_alliance_ids),
        "trusted_members_can_write_market": bool(config.trusted_members_can_edit),
        "message": message,
    }


def flight_member_access_error(config: EveSsoConfig, session: FlightSessionLike | None) -> str:
    if session is None:
        return "Connect ESI before using Flight Attendant."
    if config.membership_restricted and not session.membership_ok:
        return "This EVE character is not in the configured corp/alliance allowlist."
    return ""


def is_https_url(url: str) -> bool:
    return urlparse(str(url or "")).scheme.lower() == "https"


def should_secure_flight_cookie(public_base_url: str) -> bool:
    return is_https_url(public_base_url)


def default_flight_callback_url(*, public_base_url: str, url_host: str, port: int) -> str:
    if public_base_url and urlparse(public_base_url).scheme.lower() in {"http", "https"}:
        return f"{public_base_url.rstrip('/')}/flight/callback"
    return f"http://{url_host}:{port}/flight/callback"


def public_hosting_config_errors(*, public_base_url: str, sso_config: EveSsoConfig, public_hosting_mode: bool) -> list[str]:
    if not public_hosting_mode:
        return []
    errors: list[str] = []
    if not is_https_url(public_base_url):
        errors.append("--public-base-url must be an https URL in public hosting mode")
    if not sso_config.enabled:
        errors.append("EVE SSO client id, client secret, and callback URL are required")
    elif not is_https_url(sso_config.callback_url):
        errors.append("--sso-callback-url must be an https URL in public hosting mode")
    if not sso_config.membership_restricted:
        errors.append("configure --allowed-corporation-ids or --allowed-alliance-ids for member-only access")
    return errors


def market_write_access_allowed(
    *,
    is_loopback: bool,
    public_hosting_mode: bool,
    admin_token: str,
    auth_header: str,
    token_header: str,
    trusted_member: bool = False,
) -> bool:
    if trusted_member:
        return True
    if admin_token:
        return auth_header == f"Bearer {admin_token}" or token_header == admin_token or (is_loopback and not public_hosting_mode)
    if public_hosting_mode:
        return False
    return is_loopback


def admin_token_write_access_allowed(*, admin_token: str, auth_header: str, token_header: str) -> bool:
    if not admin_token:
        return False
    return auth_header == f"Bearer {admin_token}" or token_header == admin_token


def same_origin_write_allowed(
    *,
    origin_header: str,
    referer_header: str,
    host_header: str,
    public_base_url: str,
) -> bool:
    allowed_hosts = {normalize_host_header(host_header)}
    public_host = url_host(public_base_url)
    if public_host:
        allowed_hosts.add(public_host)
    allowed_hosts.discard("")
    if not allowed_hosts:
        return False

    origin_host = url_host(origin_header)
    if origin_host:
        return origin_host in allowed_hosts
    referer_host = url_host(referer_header)
    if referer_host:
        return referer_host in allowed_hosts
    return False


def normalize_host_header(host_header: str) -> str:
    return str(host_header or "").split(",", 1)[0].strip().lower()


def url_host(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    return parsed.netloc.lower()


def request_cookie(handler: BaseHTTPRequestHandler, name: str) -> str:
    raw_cookie = handler.headers.get("Cookie", "")
    for part in raw_cookie.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return ""


def flight_session_cookie_header(session_id: str, *, secure: bool = False) -> str:
    header = (
        f"{FLIGHT_SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={60 * 60}"
    )
    return f"{header}; Secure" if secure else header


def clear_flight_session_cookie_header(*, secure: bool = False) -> str:
    header = f"{FLIGHT_SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    return f"{header}; Secure" if secure else header
