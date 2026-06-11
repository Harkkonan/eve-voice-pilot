from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eve_voice_pilot import flight_workbench


def test_load_config_defaults_when_file_missing(tmp_path):
    config = flight_workbench.load_config(tmp_path / "missing.json")

    assert config.local_app_host == "127.0.0.1"
    assert config.local_app_port == 8770
    assert config.vm_configured is False
    assert config.public_dict()["ssh_key_configured"] is False


def test_load_config_reads_vm_fields_without_secrets_in_public_dict(tmp_path):
    key_path = tmp_path / "oci.key"
    key_path.write_text("private", encoding="utf-8")
    config_path = tmp_path / "flight_workbench.local.json"
    config_path.write_text(
        json.dumps(
            {
                "ssh_host": "203.0.113.10",
                "ssh_user": "ubuntu",
                "ssh_key_path": str(key_path),
                "vm_app_dir": "/home/ubuntu/apps/eve-voice-pilot",
                "vm_service_name": "eve-flight.service",
            }
        ),
        encoding="utf-8",
    )

    config = flight_workbench.load_config(config_path)
    public = config.public_dict()

    assert config.vm_configured is True
    assert public["ssh_host_configured"] is True
    assert public["ssh_key_exists"] is True
    assert "oci.key" not in json.dumps(public)


def test_load_config_rejects_non_loopback_local_targets(tmp_path):
    config_path = tmp_path / "flight_workbench.local.json"
    config_path.write_text(json.dumps({"local_app_host": "0.0.0.0"}), encoding="utf-8")

    with pytest.raises(flight_workbench.WorkbenchError, match="Local app host"):
        flight_workbench.load_config(config_path)

    config_path.write_text(json.dumps({"tunnel_remote_host": "example.com"}), encoding="utf-8")

    with pytest.raises(flight_workbench.WorkbenchError, match="Tunnel remote host"):
        flight_workbench.load_config(config_path)


def test_redact_text_hides_webhooks_tokens_and_extra_values(monkeypatch):
    monkeypatch.setenv("CORP_MARKET_SSO_CLIENT_SECRET", "secret-value-123")

    redacted = flight_workbench.redact_text(
        "client_secret=abc123 https://discord.com/api/webhooks/123456789012345678/token-value secret-value-123 keyfile",
        extra_values=("keyfile",),
    )

    assert "abc123" not in redacted
    assert "token-value" not in redacted
    assert "secret-value-123" not in redacted
    assert "keyfile" not in redacted
    assert "[redacted]" in redacted


def test_environment_status_never_returns_secret_values(monkeypatch):
    monkeypatch.setenv("CORP_MARKET_SSO_CLIENT_ID", "client-id")
    monkeypatch.setenv("CORP_MARKET_SSO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("CORP_MARKET_PUBLIC_BASE_URL", "https://flight.example.test")

    status = flight_workbench.environment_status()
    payload = json.dumps(status)

    assert status["sso_ready"] is True
    assert "client-id" not in payload
    assert "client-secret" not in payload
    assert "https://flight.example.test" not in payload


def test_environment_status_uses_redacted_windows_user_env(monkeypatch):
    for name in ("CORP_MARKET_SSO_CLIENT_ID", "CORP_MARKET_SSO_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)

    def fake_user_env(name):
        return {
            "CORP_MARKET_SSO_CLIENT_ID": "user-client-id",
            "CORP_MARKET_SSO_CLIENT_SECRET": "user-client-secret",
        }.get(name, "")

    monkeypatch.setattr(flight_workbench, "read_user_env_value", fake_user_env)

    status = flight_workbench.environment_status()
    payload = json.dumps(status)

    assert status["sso_ready"] is True
    assert "user-client-id" not in payload
    assert "user-client-secret" not in payload
    assert any(row["name"] == "CORP_MARKET_SSO_CLIENT_ID" and row["source"] == "Windows User" for row in status["rows"])


def test_child_environment_with_user_vars_bridges_allowlisted_user_env(monkeypatch):
    monkeypatch.delenv("CORP_MARKET_SSO_CLIENT_ID", raising=False)

    def fake_user_env(name):
        return "user-client-id" if name == "CORP_MARKET_SSO_CLIENT_ID" else ""

    monkeypatch.setattr(flight_workbench, "read_user_env_value", fake_user_env)

    child_env = flight_workbench.child_environment_with_user_vars(("CORP_MARKET_SSO_CLIENT_ID",))

    assert child_env["CORP_MARKET_SSO_CLIENT_ID"] == "user-client-id"


def test_run_action_rejects_unknown_action():
    with pytest.raises(flight_workbench.WorkbenchError):
        flight_workbench.run_action("git pull", flight_workbench.WorkbenchConfig())


def test_git_status_action_uses_fixed_command(monkeypatch):
    observed = {}

    def fake_run_command(args, **kwargs):
        observed["args"] = args
        return flight_workbench.CommandResult(ok=True, summary="ok", output="## master...origin/master")

    monkeypatch.setattr(flight_workbench, "run_command", fake_run_command)

    result = flight_workbench.run_action("git_status", flight_workbench.WorkbenchConfig())

    assert result.ok is True
    assert observed["args"] == ["git", "status", "--short", "--branch"]


def test_local_server_start_bridges_user_env_to_child_process(monkeypatch):
    observed = {}

    monkeypatch.setattr(
        flight_workbench,
        "check_local_health",
        lambda config: {"ok": False, "url": "http://127.0.0.1:8770/api/health", "detail": "offline"},
    )
    monkeypatch.setattr(
        flight_workbench,
        "child_environment_with_user_vars",
        lambda: {"CORP_MARKET_SSO_CLIENT_ID": "user-client-id"},
    )

    def fake_start_managed_process(name, args, *, log_name, env=None):
        observed["env"] = env
        return flight_workbench.CommandResult(ok=True, summary="started")

    monkeypatch.setattr(flight_workbench, "start_managed_process", fake_start_managed_process)

    result = flight_workbench.local_server_start(flight_workbench.WorkbenchConfig())

    assert result.ok is True
    assert observed["env"]["CORP_MARKET_SSO_CLIENT_ID"] == "user-client-id"


def test_tunnel_start_requires_vm_config():
    result_error = None
    try:
        flight_workbench.tunnel_start(flight_workbench.WorkbenchConfig())
    except flight_workbench.WorkbenchError as exc:
        result_error = str(exc)

    assert result_error == "VM SSH is not configured in profiles/flight_workbench.local.json."


def test_tunnel_start_rejects_non_loopback_remote_host(tmp_path):
    key_path = tmp_path / "oci.key"
    key_path.write_text("private", encoding="utf-8")
    config = flight_workbench.WorkbenchConfig(
        ssh_host="203.0.113.10",
        ssh_user="ubuntu",
        ssh_key_path=str(key_path),
        tunnel_remote_host="example.com",
    )

    with pytest.raises(flight_workbench.WorkbenchError, match="Tunnel remote host"):
        flight_workbench.tunnel_start(config)


def test_vm_git_status_rejects_unsafe_remote_path(tmp_path):
    key_path = tmp_path / "oci.key"
    key_path.write_text("private", encoding="utf-8")
    config = flight_workbench.WorkbenchConfig(
        ssh_host="203.0.113.10",
        ssh_user="ubuntu",
        ssh_key_path=str(key_path),
        vm_app_dir="/home/ubuntu/apps/eve-voice-pilot; rm -rf /",
    )

    with pytest.raises(flight_workbench.WorkbenchError):
        flight_workbench.vm_git_status(config)


def test_vm_service_status_echoes_readable_state(tmp_path, monkeypatch):
    observed = {}
    key_path = tmp_path / "oci.key"
    key_path.write_text("private", encoding="utf-8")
    config = flight_workbench.WorkbenchConfig(
        ssh_host="203.0.113.10",
        ssh_user="ubuntu",
        ssh_key_path=str(key_path),
        vm_service_name="eve-flight.service",
    )

    def fake_run_ssh_command(config, remote_command, *, timeout_seconds=None):
        observed["remote_command"] = remote_command
        return flight_workbench.CommandResult(ok=True, summary="checked")

    monkeypatch.setattr(flight_workbench, "run_ssh_command", fake_run_ssh_command)

    result = flight_workbench.vm_service_status(config)

    assert result.ok is True
    assert "state=$(systemctl is-active eve-flight.service || true)" in observed["remote_command"]
    assert 'echo "eve-flight.service: $state"' in observed["remote_command"]


def test_vm_update_and_restart_uses_fixed_fast_forward_deploy_command(tmp_path, monkeypatch):
    observed = {}
    key_path = tmp_path / "oci.key"
    key_path.write_text("private", encoding="utf-8")
    config = flight_workbench.WorkbenchConfig(
        ssh_host="203.0.113.10",
        ssh_user="ubuntu",
        ssh_key_path=str(key_path),
        vm_app_dir="/home/ubuntu/apps/eve-voice-pilot",
        vm_service_name="eve-flight.service",
    )

    def fake_run_ssh_command(config, remote_command, *, timeout_seconds=None):
        observed["remote_command"] = remote_command
        observed["timeout_seconds"] = timeout_seconds
        return flight_workbench.CommandResult(ok=True, summary="updated")

    monkeypatch.setattr(flight_workbench, "run_ssh_command", fake_run_ssh_command)

    result = flight_workbench.vm_update_and_restart(config)

    assert result.ok is True
    assert "git pull --ff-only origin" in observed["remote_command"]
    assert "git status --porcelain" in observed["remote_command"]
    assert ".venv/bin/python -m pip install -r requirements.txt" in observed["remote_command"]
    assert "systemctl restart eve-flight.service" in observed["remote_command"]
    assert "eve-flight.service: $state" in observed["remote_command"]
    assert "curl -fsS" not in observed["remote_command"]
    assert observed["timeout_seconds"] >= 90.0


def test_vm_update_and_verify_adds_git_and_health_checks(tmp_path, monkeypatch):
    observed = {}
    key_path = tmp_path / "oci.key"
    key_path.write_text("private", encoding="utf-8")
    config = flight_workbench.WorkbenchConfig(
        ssh_host="203.0.113.10",
        ssh_user="ubuntu",
        ssh_key_path=str(key_path),
        vm_app_dir="/home/ubuntu/apps/eve-voice-pilot",
        vm_service_name="eve-flight.service",
    )

    def fake_run_ssh_command(config, remote_command, *, timeout_seconds=None):
        observed["remote_command"] = remote_command
        observed["timeout_seconds"] = timeout_seconds
        return flight_workbench.CommandResult(ok=True, summary="updated")

    monkeypatch.setattr(flight_workbench, "run_ssh_command", fake_run_ssh_command)

    result = flight_workbench.vm_update_and_verify(config)

    assert result.ok is True
    assert "git pull --ff-only origin" in observed["remote_command"]
    assert 'echo "Git status:"' in observed["remote_command"]
    assert "git status --short --branch" in observed["remote_command"]
    assert 'echo "Health:"' in observed["remote_command"]
    assert "curl -fsS http://127.0.0.1:8770/api/health" in observed["remote_command"]
    assert "for attempt in 1 2 3 4 5 6 7 8 9 10" in observed["remote_command"]
    assert "sleep 1" in observed["remote_command"]
    assert observed["timeout_seconds"] >= 120.0


def test_vm_update_and_restart_rejects_unsafe_remote_values(tmp_path):
    key_path = tmp_path / "oci.key"
    key_path.write_text("private", encoding="utf-8")
    config = flight_workbench.WorkbenchConfig(
        ssh_host="203.0.113.10",
        ssh_user="ubuntu",
        ssh_key_path=str(key_path),
        vm_app_dir="/home/ubuntu/apps/eve-voice-pilot",
        vm_service_name="eve-flight.service; reboot",
    )

    with pytest.raises(flight_workbench.WorkbenchError):
        flight_workbench.vm_update_and_restart(config)


def test_operator_origin_rules_accept_same_origin_and_reject_other_origin():
    assert flight_workbench.origin_is_allowed("http://127.0.0.1:8790", "", "127.0.0.1:8790") is True
    assert flight_workbench.origin_is_allowed("https://evil.example", "", "127.0.0.1:8790") is False


def test_summarize_git_status_uses_plain_git_state_words():
    assert flight_workbench.summarize_git_status(
        flight_workbench.CommandResult(ok=True, summary="ok", output="## master...origin/master")
    ) == "Clean"
    assert flight_workbench.summarize_git_status(
        flight_workbench.CommandResult(ok=True, summary="ok", output="## master...origin/master [ahead 1]\n M README.md")
    ) == "Dirty, Ahead"
    assert flight_workbench.summarize_git_status(
        flight_workbench.CommandResult(ok=False, summary="failed", output="")
    ) == "Check failed"


def test_make_csp_nonce_uses_standard_base64_charset():
    nonce = flight_workbench.make_csp_nonce()

    assert len(nonce) >= 20
    assert all(char.isalnum() or char in "+/=" for char in nonce)


def test_build_http_server_refuses_non_loopback_bind():
    state = flight_workbench.WorkbenchState(flight_workbench.WorkbenchConfig(), "token", "nonce")

    with pytest.raises(flight_workbench.WorkbenchError):
        flight_workbench.build_http_server("0.0.0.0", 8790, state)


def test_post_action_requires_operator_token_and_accepts_valid_token(tmp_path, monkeypatch):
    config = flight_workbench.WorkbenchConfig(action_log_path=tmp_path / "actions.jsonl")
    state = flight_workbench.WorkbenchState(config, "operator-token", "nonce")
    server = flight_workbench.build_http_server("127.0.0.1", 0, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/actions/git_status"

    try:
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url, method="POST"), timeout=5)
        assert exc.value.code == 403

        monkeypatch.setattr(
            flight_workbench,
            "run_action",
            lambda action_id, config: flight_workbench.CommandResult(ok=True, summary=f"ran {action_id}"),
        )
        response = urlopen(Request(url, method="POST", headers={"X-Workbench-Token": "operator-token"}), timeout=5)
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["ok"] is True
        assert payload["summary"] == "ran git_status"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_security_headers_use_nonce_and_deny_framing(tmp_path):
    config = flight_workbench.WorkbenchConfig(action_log_path=tmp_path / "actions.jsonl")
    state = flight_workbench.WorkbenchState(config, "operator-token", "YWJjMTIz")
    server = flight_workbench.build_http_server("127.0.0.1", 0, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    try:
        response = urlopen(Request(url, method="GET"), timeout=5)
        body = response.read().decode("utf-8")
        csp = response.headers["Content-Security-Policy"]

        assert 'nonce="YWJjMTIz"' in body
        assert "script-src 'nonce-YWJjMTIz'" in csp
        assert "style-src 'nonce-YWJjMTIz'" in csp
        assert "'unsafe-inline'" not in csp
        assert response.headers["X-Frame-Options"] == "DENY"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_render_dashboard_includes_initial_action_metadata():
    html = flight_workbench.render_dashboard(flight_workbench.WorkbenchConfig(), "operator-token", "YWJjMTIz")

    assert 'nonce="YWJjMTIz"' in html
    assert "const initialActions =" in html
    assert "Git Status" in html
    assert "operator-token" in html


def test_append_action_log_redacts_output(tmp_path, monkeypatch):
    monkeypatch.setattr(flight_workbench, "DEFAULT_ACTION_LOG_PATH", tmp_path / "unused.jsonl")
    config = flight_workbench.WorkbenchConfig(
        action_log_path=tmp_path / "actions.jsonl",
        ssh_key_path="C:\\Users\\Example\\.ssh\\oci_test.key",
    )

    flight_workbench.append_action_log(
        config,
        {
            "action_id": "vm_logs_tail",
            "ok": True,
            "summary": "client_secret=abc123",
            "output": "C:\\Users\\Example\\.ssh\\oci_test.key",
            "generated_at": "2026-06-10T00:00:00Z",
        },
    )

    text = config.action_log_path.read_text(encoding="utf-8")
    assert "abc123" not in text
    assert "oci_test.key" not in text


def test_status_payload_contains_expected_cards(monkeypatch):
    monkeypatch.setattr(flight_workbench, "read_user_env_value", lambda name: "")
    monkeypatch.setattr(
        flight_workbench,
        "check_local_health",
        lambda config: {"ok": False, "url": "http://127.0.0.1:8770/api/health", "detail": "offline"},
    )
    monkeypatch.setattr(
        flight_workbench,
        "local_git_status",
        lambda config: flight_workbench.CommandResult(ok=True, summary="ok", output="## master...origin/master"),
    )
    monkeypatch.setattr(flight_workbench, "recent_action_log", lambda config: [])

    payload = flight_workbench.build_status_payload(flight_workbench.WorkbenchConfig())

    assert payload["local_server"]["health"]["ok"] is False
    assert payload["ssh_tunnel"]["configured"] is False
    assert payload["environment"]["sso_ready"] is False
    assert any(action["id"] == "vm_service_restart" for action in payload["actions"])
    assert any(action["id"] == "vm_update_restart" for action in payload["actions"])
    assert any(action["id"] == "vm_update_verify" for action in payload["actions"])
