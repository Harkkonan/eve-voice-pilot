from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot.corp_intel import EveSsoConfig
from eve_voice_pilot.flight_server_routes import (
    FLIGHT_GET_ROUTE_METHODS,
    FLIGHT_POST_ROUTE_METHODS,
    dispatch_flight_get_route,
    dispatch_flight_post_route,
    flight_member_access_error,
    flight_membership_status,
    flight_session_cookie_header,
    flight_session_has_member_access,
    admin_token_write_access_allowed,
    market_write_access_allowed,
    public_hosting_config_errors,
    request_path,
    same_origin_write_allowed,
)


class FakeFlightHandler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _handle_flight_status(self) -> None:
        self.calls.append("status")

    def _handle_flight_logout(self) -> None:
        self.calls.append("logout")


def test_flight_get_route_dispatches_known_paths_only():
    handler = FakeFlightHandler()

    assert dispatch_flight_get_route(handler, "/api/flight/status") is True
    assert dispatch_flight_get_route(handler, "/api/ui-performance") is False

    assert handler.calls == ["status"]
    assert FLIGHT_GET_ROUTE_METHODS["/api/flight/status"] == "_handle_flight_status"
    assert FLIGHT_GET_ROUTE_METHODS["/api/flight/market-types"] == "_handle_flight_market_types"
    assert FLIGHT_GET_ROUTE_METHODS["/api/flight/acquisition/compare"] == "_handle_flight_acquisition_compare"
    assert FLIGHT_GET_ROUTE_METHODS["/api/flight/acquisition/compare/progress"] == "_handle_flight_acquisition_compare_progress"
    assert FLIGHT_GET_ROUTE_METHODS["/api/flight/asset-ledger"] == "_handle_flight_asset_ledger"
    assert FLIGHT_GET_ROUTE_METHODS["/api/flight/mining-yield"] == "_handle_flight_mining_yield"


def test_flight_post_route_keeps_logout_separate_from_appraisal():
    handler = FakeFlightHandler()

    assert dispatch_flight_post_route(handler, "/flight/logout") is True
    assert dispatch_flight_post_route(handler, "/api/flight/appraisal") is False

    assert handler.calls == ["logout"]
    assert FLIGHT_POST_ROUTE_METHODS == {"/flight/logout": "_handle_flight_logout"}


def test_request_path_strips_query_before_dispatch():
    assert request_path("/api/flight/status?max_jumps=7") == "/api/flight/status"


def test_flight_access_helpers_enforce_member_allowlist():
    config = EveSsoConfig(
        client_id="client",
        client_secret="secret",
        callback_url="https://flight.example.test/flight/callback",
        allowed_corporation_ids=(98811080,),
    )

    allowed_session = SimpleNamespace(membership_ok=True)
    denied_session = SimpleNamespace(membership_ok=False)

    assert flight_session_has_member_access(config, allowed_session) is True
    assert flight_session_has_member_access(config, denied_session) is False
    assert flight_member_access_error(config, denied_session) == (
        "This EVE character is not in the configured character/corp/alliance allowlist."
    )
    assert flight_membership_status(config, denied_session)["character_allowlist_count"] == 0


def test_flight_access_helpers_report_character_allowlist():
    config = EveSsoConfig(
        client_id="client",
        client_secret="secret",
        callback_url="https://flight.example.test/flight/callback",
        allowed_character_ids=(2124413713,),
    )

    status = flight_membership_status(config, SimpleNamespace(membership_ok=True))

    assert config.membership_restricted is True
    assert status["allowed"] is True
    assert status["character_allowlist_count"] == 1


def test_public_hosting_helpers_keep_https_sso_and_cookie_rules():
    unsafe_config = EveSsoConfig(callback_url="http://127.0.0.1:8770/flight/callback")

    errors = public_hosting_config_errors(
        public_base_url="http://127.0.0.1:8770",
        sso_config=unsafe_config,
        public_hosting_mode=True,
    )

    assert "--public-base-url must be an https URL" in errors[0]
    assert "EVE SSO client id" in errors[1]
    assert "Secure" in flight_session_cookie_header("session-id", secure=True)
    assert not market_write_access_allowed(
        is_loopback=False,
        public_hosting_mode=True,
        admin_token="",
        auth_header="",
        token_header="",
    )


def test_admin_token_and_same_origin_helpers_cover_public_writes():
    assert admin_token_write_access_allowed(
        admin_token="secret",
        auth_header="Bearer secret",
        token_header="",
    )
    assert admin_token_write_access_allowed(
        admin_token="secret",
        auth_header="",
        token_header="secret",
    )
    assert not admin_token_write_access_allowed(
        admin_token="secret",
        auth_header="",
        token_header="wrong",
    )

    assert same_origin_write_allowed(
        origin_header="http://127.0.0.1:8770",
        referer_header="",
        host_header="127.0.0.1:8770",
        public_base_url="https://flight.example.test",
    )
    assert same_origin_write_allowed(
        origin_header="https://flight.example.test",
        referer_header="",
        host_header="127.0.0.1:8770",
        public_base_url="https://flight.example.test",
    )
    assert not same_origin_write_allowed(
        origin_header="https://attacker.example.test",
        referer_header="",
        host_header="127.0.0.1:8770",
        public_base_url="https://flight.example.test",
    )
    assert not same_origin_write_allowed(
        origin_header="",
        referer_header="",
        host_header="127.0.0.1:8770",
        public_base_url="https://flight.example.test",
    )
