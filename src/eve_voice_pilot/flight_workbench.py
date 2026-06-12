from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import webbrowser

if os.name == "nt":
    import winreg
else:
    winreg = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "profiles" / "flight_workbench.local.json"
DEFAULT_ACTION_LOG_PATH = ROOT / "profiles" / "flight_workbench_actions.jsonl"
DEFAULT_PORT = 8790
DEFAULT_CORP_MARKET_PORT = 8770
DEFAULT_TUNNEL_LOCAL_PORT = 8770
DEFAULT_TUNNEL_REMOTE_PORT = 8770
DEFAULT_LOCAL_APP_HOST = "127.0.0.1"
DEFAULT_VM_APP_DIR = "/home/ubuntu/apps/eve-voice-pilot"
DEFAULT_VM_SERVICE_NAME = "eve-flight.service"
ACTION_OUTPUT_LIMIT = 8000
RECENT_ACTION_LIMIT = 20
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

SENSITIVE_ENV_NAMES = (
    "CORP_MARKET_SSO_CLIENT_ID",
    "CORP_MARKET_SSO_CLIENT_SECRET",
    "EVE_SSO_CLIENT_ID",
    "EVE_SSO_CLIENT_SECRET",
    "CORP_MARKET_ADMIN_TOKEN",
    "CORP_MARKET_DISCORD_WEBHOOK_URL",
    "CORP_MARKET_DISCORD_FORUM_TAG_IDS",
    "CORP_MARKET_DISCORD_FORUM_TAG_MAP",
)
CONFIG_ENV_NAMES = (
    "CORP_MARKET_PUBLIC_BASE_URL",
    "CORP_MARKET_SSO_CALLBACK_URL",
    "CORP_MARKET_ALLOWED_CHARACTER_IDS",
    "CORP_MARKET_ALLOWED_CORPORATION_IDS",
    "CORP_MARKET_ALLOWED_ALLIANCE_IDS",
    "CORP_MARKET_PUBLIC_HOSTING_MODE",
    "CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET",
)
USER_ENV_BRIDGE_NAMES = SENSITIVE_ENV_NAMES
SECRET_NAME_MARKERS = ("SECRET", "TOKEN", "WEBHOOK", "PASSWORD", "AUTHORIZATION", "KEY")
DISCORD_WEBHOOK_RE = re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._-]+")
KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(client[_ -]?secret|admin[_ -]?token|access[_ -]?token|refresh[_ -]?token|webhook[_ -]?url|authorization)"
    r"(\s*[:=]\s*)([^\s\"']+)"
)
REMOTE_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./~+-]+$")
REMOTE_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class WorkbenchError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkbenchConfig:
    config_path: Path = DEFAULT_CONFIG_PATH
    action_log_path: Path = DEFAULT_ACTION_LOG_PATH
    local_app_host: str = DEFAULT_LOCAL_APP_HOST
    local_app_port: int = DEFAULT_CORP_MARKET_PORT
    tunnel_local_port: int = DEFAULT_TUNNEL_LOCAL_PORT
    tunnel_remote_host: str = "127.0.0.1"
    tunnel_remote_port: int = DEFAULT_TUNNEL_REMOTE_PORT
    ssh_host: str = ""
    ssh_user: str = "ubuntu"
    ssh_key_path: str = ""
    vm_app_dir: str = DEFAULT_VM_APP_DIR
    vm_service_name: str = DEFAULT_VM_SERVICE_NAME
    vm_public_base_url: str = ""
    vm_sso_callback_url: str = ""
    vm_allowed_character_ids: tuple[int, ...] = ()
    vm_allowed_corporation_ids: tuple[int, ...] = ()
    vm_allowed_alliance_ids: tuple[int, ...] = ()
    vm_public_hosting_mode: bool = False
    vm_trusted_members_can_write_market: bool = False
    command_timeout_seconds: float = 25.0

    @property
    def local_base_url(self) -> str:
        return f"http://{url_host(self.local_app_host)}:{self.local_app_port}"

    @property
    def tunnel_forward(self) -> str:
        return f"{self.tunnel_local_port}:{ssh_forward_host(self.tunnel_remote_host)}:{self.tunnel_remote_port}"

    @property
    def ssh_target(self) -> str:
        return f"{self.ssh_user}@{self.ssh_host}" if self.ssh_host else ""

    @property
    def vm_configured(self) -> bool:
        return bool(self.ssh_host and self.ssh_user and self.ssh_key_path)

    def public_dict(self) -> dict[str, Any]:
        return {
            "config_path": repo_relative_path(self.config_path),
            "config_exists": self.config_path.exists(),
            "action_log_path": repo_relative_path(self.action_log_path),
            "local_base_url": self.local_base_url,
            "local_app_host": self.local_app_host,
            "local_app_port": self.local_app_port,
            "tunnel_forward": self.tunnel_forward,
            "ssh_configured": self.vm_configured,
            "ssh_host_configured": bool(self.ssh_host),
            "ssh_user": self.ssh_user if self.ssh_user else "",
            "ssh_key_configured": bool(self.ssh_key_path),
            "ssh_key_exists": Path(self.ssh_key_path).expanduser().is_file() if self.ssh_key_path else False,
            "vm_app_dir": self.vm_app_dir,
            "vm_service_name": self.vm_service_name,
            "vm_public_base_url": self.vm_public_base_url,
            "vm_sso_callback_url": self.vm_sso_callback_url,
            "vm_allowed_character_ids": list(self.vm_allowed_character_ids),
            "vm_allowed_corporation_ids": list(self.vm_allowed_corporation_ids),
            "vm_allowed_alliance_ids": list(self.vm_allowed_alliance_ids),
            "vm_public_hosting_mode": self.vm_public_hosting_mode,
            "vm_trusted_members_can_write_market": self.vm_trusted_members_can_write_market,
        }


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    summary: str
    output: str = ""
    returncode: int | None = None
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    label: str
    group: str
    description: str
    runner: Callable[[WorkbenchConfig], CommandResult]
    changes_process: bool = False


@dataclass(frozen=True)
class LocalProcessInfo:
    pid: int
    local_address: str
    local_port: int
    command_line: str


_process_lock = threading.Lock()
_managed_processes: dict[str, subprocess.Popen[Any]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_csp_nonce() -> str:
    return base64.b64encode(secrets.token_bytes(18)).decode("ascii")


def clean_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 65535) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def clean_float(value: Any, default: float, *, minimum: float = 1.0, maximum: float = 120.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def clean_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def clean_text(value: Any, default: str = "", *, max_length: int = 500) -> str:
    if value is None:
        return default
    text = str(value or "").strip()
    if len(text) > max_length:
        return text[:max_length]
    return text


def clean_int_list(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = re.split(r"[,\s]+", value)
    elif isinstance(value, Iterable):
        raw_items = list(value)
    else:
        raw_items = [value]
    ids: list[int] = []
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            item_id = int(text)
        except ValueError as exc:
            raise WorkbenchError(f"Allowlist IDs must be positive numbers; got {text!r}.") from exc
        if item_id <= 0:
            raise WorkbenchError(f"Allowlist IDs must be positive numbers; got {text!r}.")
        if item_id not in ids:
            ids.append(item_id)
    return tuple(ids)


def int_list_text(values: Iterable[int]) -> str:
    return ",".join(str(int(value)) for value in values if int(value) > 0)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> WorkbenchConfig:
    data: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkbenchError(f"Could not read workbench config: {exc}") from exc
        if not isinstance(parsed, dict):
            raise WorkbenchError("Workbench config must be a JSON object.")
        data = parsed
    local_app_host = clean_text(data.get("local_app_host"), DEFAULT_LOCAL_APP_HOST, max_length=80)
    tunnel_remote_host = clean_text(data.get("tunnel_remote_host"), "127.0.0.1", max_length=80)
    require_loopback_host(local_app_host, "Local app host")
    require_loopback_host(tunnel_remote_host, "Tunnel remote host")
    return WorkbenchConfig(
        config_path=path,
        action_log_path=DEFAULT_ACTION_LOG_PATH,
        local_app_host=local_app_host,
        local_app_port=clean_int(data.get("local_app_port"), DEFAULT_CORP_MARKET_PORT),
        tunnel_local_port=clean_int(data.get("tunnel_local_port"), DEFAULT_TUNNEL_LOCAL_PORT),
        tunnel_remote_host=tunnel_remote_host,
        tunnel_remote_port=clean_int(data.get("tunnel_remote_port"), DEFAULT_TUNNEL_REMOTE_PORT),
        ssh_host=clean_text(data.get("ssh_host"), "", max_length=180),
        ssh_user=clean_text(data.get("ssh_user"), "ubuntu", max_length=80),
        ssh_key_path=clean_text(data.get("ssh_key_path"), "", max_length=500),
        vm_app_dir=clean_text(data.get("vm_app_dir"), DEFAULT_VM_APP_DIR, max_length=500),
        vm_service_name=clean_text(data.get("vm_service_name"), DEFAULT_VM_SERVICE_NAME, max_length=120),
        vm_public_base_url=clean_text(data.get("vm_public_base_url"), "", max_length=240),
        vm_sso_callback_url=clean_text(
            data.get("vm_sso_callback_url"),
            "",
            max_length=280,
        ),
        vm_allowed_character_ids=clean_int_list(data.get("vm_allowed_character_ids")),
        vm_allowed_corporation_ids=clean_int_list(data.get("vm_allowed_corporation_ids")),
        vm_allowed_alliance_ids=clean_int_list(data.get("vm_allowed_alliance_ids")),
        vm_public_hosting_mode=clean_bool(data.get("vm_public_hosting_mode"), False),
        vm_trusted_members_can_write_market=clean_bool(data.get("vm_trusted_members_can_write_market"), False),
        command_timeout_seconds=clean_float(data.get("command_timeout_seconds"), 25.0),
    )


def read_config_data(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbenchError(f"Could not read workbench config: {exc}") from exc
    if not isinstance(parsed, dict):
        raise WorkbenchError("Workbench config must be a JSON object.")
    return parsed


def save_config_data(path: Path, data: dict[str, Any]) -> None:
    ensure_ignored_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def update_vm_public_config(path: Path, payload: dict[str, Any]) -> WorkbenchConfig:
    data = read_config_data(path)
    data.update(
        {
            "vm_public_base_url": clean_text(payload.get("vm_public_base_url"), "", max_length=240),
            "vm_sso_callback_url": clean_text(payload.get("vm_sso_callback_url"), "", max_length=280),
            "vm_allowed_character_ids": list(clean_int_list(payload.get("vm_allowed_character_ids"))),
            "vm_allowed_corporation_ids": list(clean_int_list(payload.get("vm_allowed_corporation_ids"))),
            "vm_allowed_alliance_ids": list(clean_int_list(payload.get("vm_allowed_alliance_ids"))),
            "vm_public_hosting_mode": clean_bool(payload.get("vm_public_hosting_mode"), False),
            "vm_trusted_members_can_write_market": clean_bool(
                payload.get("vm_trusted_members_can_write_market"),
                False,
            ),
        }
    )
    candidate = load_config_from_data(path, data)
    validate_vm_public_config(candidate)
    save_config_data(path, data)
    return candidate


def load_config_from_data(path: Path, data: dict[str, Any]) -> WorkbenchConfig:
    temp_path = path
    local_app_host = clean_text(data.get("local_app_host"), DEFAULT_LOCAL_APP_HOST, max_length=80)
    tunnel_remote_host = clean_text(data.get("tunnel_remote_host"), "127.0.0.1", max_length=80)
    require_loopback_host(local_app_host, "Local app host")
    require_loopback_host(tunnel_remote_host, "Tunnel remote host")
    return WorkbenchConfig(
        config_path=temp_path,
        action_log_path=DEFAULT_ACTION_LOG_PATH,
        local_app_host=local_app_host,
        local_app_port=clean_int(data.get("local_app_port"), DEFAULT_CORP_MARKET_PORT),
        tunnel_local_port=clean_int(data.get("tunnel_local_port"), DEFAULT_TUNNEL_LOCAL_PORT),
        tunnel_remote_host=tunnel_remote_host,
        tunnel_remote_port=clean_int(data.get("tunnel_remote_port"), DEFAULT_TUNNEL_REMOTE_PORT),
        ssh_host=clean_text(data.get("ssh_host"), "", max_length=180),
        ssh_user=clean_text(data.get("ssh_user"), "ubuntu", max_length=80),
        ssh_key_path=clean_text(data.get("ssh_key_path"), "", max_length=500),
        vm_app_dir=clean_text(data.get("vm_app_dir"), DEFAULT_VM_APP_DIR, max_length=500),
        vm_service_name=clean_text(data.get("vm_service_name"), DEFAULT_VM_SERVICE_NAME, max_length=120),
        vm_public_base_url=clean_text(data.get("vm_public_base_url"), "", max_length=240),
        vm_sso_callback_url=clean_text(
            data.get("vm_sso_callback_url"),
            "",
            max_length=280,
        ),
        vm_allowed_character_ids=clean_int_list(data.get("vm_allowed_character_ids")),
        vm_allowed_corporation_ids=clean_int_list(data.get("vm_allowed_corporation_ids")),
        vm_allowed_alliance_ids=clean_int_list(data.get("vm_allowed_alliance_ids")),
        vm_public_hosting_mode=clean_bool(data.get("vm_public_hosting_mode"), False),
        vm_trusted_members_can_write_market=clean_bool(data.get("vm_trusted_members_can_write_market"), False),
        command_timeout_seconds=clean_float(data.get("command_timeout_seconds"), 25.0),
    )


def repo_relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def is_loopback_host(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def require_loopback_host(host: str, label: str) -> None:
    if not is_loopback_host(host):
        raise WorkbenchError(f"{label} must be 127.0.0.1, localhost, or ::1.")


def url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def ssh_forward_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def client_is_loopback(address: str) -> bool:
    return address in {"127.0.0.1", "::1"} or address.startswith("127.")


def origin_is_allowed(origin_header: str, referer_header: str, host_header: str) -> bool:
    candidates = [origin_header, referer_header]
    allowed_host = str(host_header or "").split(",", 1)[0].strip().lower()
    if not any(candidates):
        return True
    for candidate in candidates:
        if not candidate:
            continue
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        if parsed.netloc.lower() == allowed_host:
            return True
    return False


def name_is_sensitive(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SECRET_NAME_MARKERS)


def read_user_env_value(name: str) -> str:
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _value_type = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return str(value or "").strip()


def child_environment_with_user_vars(names: Iterable[str] = USER_ENV_BRIDGE_NAMES) -> dict[str, str]:
    env = os.environ.copy()
    for name in names:
        if env.get(name):
            continue
        user_value = read_user_env_value(name)
        if user_value:
            env[name] = user_value
    return env


def redacted_env_values() -> list[str]:
    values = []
    for name, value in os.environ.items():
        if value and (name in SENSITIVE_ENV_NAMES or name_is_sensitive(name)):
            values.append(value)
    for name in USER_ENV_BRIDGE_NAMES:
        value = read_user_env_value(name)
        if value and (name in SENSITIVE_ENV_NAMES or name_is_sensitive(name)):
            values.append(value)
    return sorted(values, key=len, reverse=True)


def redact_text(value: Any, extra_values: Iterable[str] = ()) -> str:
    text = str(value or "")
    text = DISCORD_WEBHOOK_RE.sub("[redacted-discord-webhook]", text)
    text = KEY_VALUE_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", text)
    for secret_value in list(extra_values) + redacted_env_values():
        if secret_value and len(secret_value) >= 6:
            text = text.replace(secret_value, "[redacted]")
    return text


def compact_output(output: str, *, limit: int = ACTION_OUTPUT_LIMIT) -> str:
    clean_output = output.strip()
    if len(clean_output) <= limit:
        return clean_output
    return clean_output[:limit] + "\n[output truncated]"


def run_command(
    args: list[str],
    *,
    cwd: Path = ROOT,
    timeout_seconds: float = 25.0,
    extra_secret_values: Iterable[str] = (),
) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(ok=False, summary=f"Command not found: {args[0]}", output=str(exc))
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(part for part in (exc.stdout or "", exc.stderr or "") if part)
        return CommandResult(
            ok=False,
            summary=f"Timed out after {timeout_seconds:g} seconds.",
            output=compact_output(redact_text(output, extra_secret_values)),
            returncode=None,
        )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    clean = compact_output(redact_text(output, extra_secret_values))
    return CommandResult(
        ok=completed.returncode == 0,
        summary="Command completed." if completed.returncode == 0 else f"Command exited {completed.returncode}.",
        output=clean,
        returncode=completed.returncode,
    )


def ensure_ignored_dirs() -> None:
    (ROOT / "profiles").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)


def process_is_running(name: str) -> bool:
    with _process_lock:
        process = _managed_processes.get(name)
        if process is None:
            return False
        if process.poll() is None:
            return True
        _managed_processes.pop(name, None)
        return False


def managed_process_status(name: str) -> dict[str, Any]:
    with _process_lock:
        process = _managed_processes.get(name)
        if process is None:
            return {"managed": False, "running": False, "pid": None}
        returncode = process.poll()
        running = returncode is None
        if not running:
            _managed_processes.pop(name, None)
        return {"managed": True, "running": running, "pid": process.pid if running else None, "returncode": returncode}


def run_windows_process_query_for_port(port: int, *, timeout_seconds: float = 8.0) -> str:
    if os.name != "nt":
        return ""
    script = f"""
$items = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" -ErrorAction SilentlyContinue
    [PSCustomObject]@{{
        pid = [int]$_.OwningProcess
        local_address = [string]$_.LocalAddress
        local_port = [int]$_.LocalPort
        command_line = [string]$process.CommandLine
    }}
}}
$items | ConvertTo-Json -Compress
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def parse_local_process_query(raw: str) -> list[LocalProcessInfo]:
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    rows = parsed if isinstance(parsed, list) else [parsed]
    processes: list[LocalProcessInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("pid") or 0)
            local_port = int(row.get("local_port") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        processes.append(
            LocalProcessInfo(
                pid=pid,
                local_address=str(row.get("local_address") or ""),
                local_port=local_port,
                command_line=str(row.get("command_line") or ""),
            )
        )
    return processes


def local_processes_for_port(port: int, *, timeout_seconds: float = 8.0) -> list[LocalProcessInfo]:
    return parse_local_process_query(run_windows_process_query_for_port(port, timeout_seconds=timeout_seconds))


def is_corp_market_process(process: LocalProcessInfo) -> bool:
    command_line = process.command_line.lower()
    return "eve_voice_pilot.corp_market" in command_line or "run_corp_market.ps1" in command_line


def is_configured_ssh_tunnel_process(process: LocalProcessInfo, config: WorkbenchConfig) -> bool:
    if not config.vm_configured:
        return False
    command_line = process.command_line
    command_line_lower = command_line.lower()
    if "ssh" not in command_line_lower or config.ssh_target.lower() not in command_line_lower:
        return False
    forward_pattern = rf'(?i)(?:^|\s)-L\s*"?{re.escape(config.tunnel_forward)}"?(?:\s|$)'
    return bool(re.search(forward_pattern, command_line))


def corp_market_processes_for_config(
    config: WorkbenchConfig,
    *,
    timeout_seconds: float = 8.0,
) -> list[LocalProcessInfo]:
    if os.name != "nt":
        return []
    return [
        process
        for process in local_processes_for_port(config.local_app_port, timeout_seconds=timeout_seconds)
        if is_corp_market_process(process)
    ]


def ssh_tunnel_processes_for_config(
    config: WorkbenchConfig,
    *,
    timeout_seconds: float = 8.0,
) -> list[LocalProcessInfo]:
    if os.name != "nt" or not config.vm_configured:
        return []
    return [
        process
        for process in local_processes_for_port(config.tunnel_local_port, timeout_seconds=timeout_seconds)
        if is_configured_ssh_tunnel_process(process, config)
    ]


def local_and_tunnel_processes_for_config(
    config: WorkbenchConfig,
    *,
    timeout_seconds: float = 8.0,
) -> tuple[list[LocalProcessInfo], list[LocalProcessInfo]]:
    if os.name != "nt":
        return [], []
    if config.local_app_port == config.tunnel_local_port:
        processes = local_processes_for_port(config.local_app_port, timeout_seconds=timeout_seconds)
        local_processes = [process for process in processes if is_corp_market_process(process)]
        tunnel_processes = [
            process
            for process in processes
            if config.vm_configured and is_configured_ssh_tunnel_process(process, config)
        ]
        return local_processes, tunnel_processes
    return (
        corp_market_processes_for_config(config, timeout_seconds=timeout_seconds),
        ssh_tunnel_processes_for_config(config, timeout_seconds=timeout_seconds),
    )


def public_process_summary(processes: Iterable[LocalProcessInfo]) -> list[dict[str, Any]]:
    return [
        {
            "pid": process.pid,
            "local_address": process.local_address,
            "local_port": process.local_port,
            "recognized": is_corp_market_process(process),
        }
        for process in processes
    ]


def stop_windows_process_tree(pid: int, *, timeout_seconds: float = 10.0) -> CommandResult:
    if os.name != "nt":
        return CommandResult(ok=False, summary="Windows process-tree stop is not available on this platform.")
    return run_command(["taskkill", "/PID", str(int(pid)), "/T", "/F"], timeout_seconds=timeout_seconds)


def start_managed_process(
    name: str,
    args: list[str],
    *,
    log_name: str,
    env: dict[str, str] | None = None,
) -> CommandResult:
    ensure_ignored_dirs()
    with _process_lock:
        existing = _managed_processes.get(name)
        if existing is not None and existing.poll() is None:
            return CommandResult(ok=True, summary=f"{name} is already managed by this workbench.", data={"pid": existing.pid})
        log_path = ROOT / "logs" / log_name
        log_file = log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                args,
                cwd=str(ROOT),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                shell=False,
            )
        finally:
            log_file.close()
        _managed_processes[name] = process
        return CommandResult(
            ok=True,
            summary=f"Started {name}.",
            output=f"Log: {repo_relative_path(log_path)}",
            data={"pid": process.pid, "log_path": repo_relative_path(log_path)},
        )


def stop_managed_process(name: str) -> CommandResult:
    with _process_lock:
        process = _managed_processes.get(name)
        if process is None or process.poll() is not None:
            _managed_processes.pop(name, None)
            return CommandResult(ok=False, summary=f"{name} is not managed by this workbench.")
        pid = process.pid
    if os.name == "nt":
        stop_result = stop_windows_process_tree(pid)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)
        summary = f"Stopped {name} process tree." if stop_result.ok else f"Stopped {name}."
    else:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)
            summary = f"Stopped {name} after forcing the managed process to exit."
        else:
            summary = f"Stopped {name}."
    with _process_lock:
        _managed_processes.pop(name, None)
    return CommandResult(ok=True, summary=summary, data={"pid": pid})


def fetch_json(url: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "FlightWorkbench/0.1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": f"HTTP {exc.code}"}
    except URLError as exc:
        return {"ok": False, "error": str(exc.reason)}
    except TimeoutError:
        return {"ok": False, "error": "Timed out."}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Endpoint did not return JSON."}
    if isinstance(parsed, dict):
        return parsed
    return {"ok": False, "error": "Endpoint returned an unexpected payload."}


def check_local_health(config: WorkbenchConfig, *, timeout_seconds: float = 2.5) -> dict[str, Any]:
    require_loopback_host(config.local_app_host, "Local app host")
    payload = fetch_json(f"{config.local_base_url}/api/health", timeout_seconds=timeout_seconds)
    return {
        "ok": bool(payload.get("ok")),
        "url": f"{config.local_base_url}/api/health",
        "detail": "Corp Market is answering." if payload.get("ok") else str(payload.get("error") or "Not reachable."),
    }


def check_flight_diagnostics(config: WorkbenchConfig) -> dict[str, Any]:
    require_loopback_host(config.local_app_host, "Local app host")
    payload = fetch_json(f"{config.local_base_url}/api/flight/diagnostics", timeout_seconds=4.0)
    return {
        "ok": bool(payload.get("ok")),
        "url": f"{config.local_base_url}/api/flight/diagnostics",
        "payload": payload,
    }


def environment_status() -> dict[str, Any]:
    rows = []
    for name in SENSITIVE_ENV_NAMES + CONFIG_ENV_NAMES:
        process_configured = bool(os.environ.get(name))
        user_configured = bool(read_user_env_value(name))
        configured = process_configured or user_configured
        source = "Process" if process_configured else ("Windows User" if user_configured else "")
        rows.append(
            {
                "name": name,
                "configured": configured,
                "process_configured": process_configured,
                "user_configured": user_configured,
                "source": source,
                "secret": name in SENSITIVE_ENV_NAMES or name_is_sensitive(name),
                "value": f"Set in {source}" if configured else "",
            }
        )
    sso_ready = bool(
        (
            os.environ.get("CORP_MARKET_SSO_CLIENT_ID")
            or os.environ.get("EVE_SSO_CLIENT_ID")
            or read_user_env_value("CORP_MARKET_SSO_CLIENT_ID")
            or read_user_env_value("EVE_SSO_CLIENT_ID")
        )
        and (
            os.environ.get("CORP_MARKET_SSO_CLIENT_SECRET")
            or os.environ.get("EVE_SSO_CLIENT_SECRET")
            or read_user_env_value("CORP_MARKET_SSO_CLIENT_SECRET")
            or read_user_env_value("EVE_SSO_CLIENT_SECRET")
        )
    )
    return {
        "sso_ready": sso_ready,
        "rows": rows,
        "note": "Values are intentionally not displayed.",
    }


def local_git_status(config: WorkbenchConfig) -> CommandResult:
    return run_command(["git", "status", "--short", "--branch"], timeout_seconds=config.command_timeout_seconds)


def git_diff_check(config: WorkbenchConfig) -> CommandResult:
    return run_command(["git", "diff", "--check"], timeout_seconds=config.command_timeout_seconds)


def summarize_git_status(result: CommandResult) -> str:
    if not result.ok:
        return "Check failed"
    lines = [line for line in result.output.splitlines() if line.strip()]
    branch_line = lines[0] if lines else ""
    dirty = any(not line.startswith("##") for line in lines)
    states = []
    if dirty:
        states.append("dirty")
    else:
        states.append("clean")
    if "[ahead" in branch_line:
        states.append("ahead")
    if "[behind" in branch_line:
        states.append("behind")
    return ", ".join(states).title()


def local_health_action(config: WorkbenchConfig) -> CommandResult:
    health = check_local_health(config)
    diagnostics = check_flight_diagnostics(config) if health["ok"] else {"ok": False, "skipped": True}
    return CommandResult(
        ok=bool(health["ok"]),
        summary="Local Corp Market health checked." if health["ok"] else "Local Corp Market is not reachable.",
        output=json.dumps({"health": health, "diagnostics": diagnostics}, indent=2),
        data={"health": health, "diagnostics": diagnostics},
    )


def cache_preflight_action(config: WorkbenchConfig) -> CommandResult:
    from eve_voice_pilot.corp_market import build_static_cache_diagnostics

    diagnostics = build_static_cache_diagnostics()
    return CommandResult(
        ok=bool(diagnostics.get("ok")),
        summary="Static caches are ready." if diagnostics.get("ok") else "One or more static caches are missing.",
        output=json.dumps(diagnostics, indent=2),
        data=diagnostics,
    )


def local_server_start(config: WorkbenchConfig) -> CommandResult:
    require_loopback_host(config.local_app_host, "Local app host")
    health = check_local_health(config)
    if health["ok"]:
        processes = corp_market_processes_for_config(config)
        if processes and not process_is_running("local server"):
            pids = ", ".join(str(process.pid) for process in processes)
            return CommandResult(
                ok=True,
                summary="Corp Market is already answering, but it is not managed by this Workbench.",
                output=f"Recognized local Corp Market listener PID(s): {pids}\nUse Stop Local Server first if you need a fresh reload.",
                data={"processes": public_process_summary(processes)},
            )
        return CommandResult(ok=True, summary="Corp Market is already answering on the configured local URL.")
    script = ROOT / "scripts" / "run_corp_market.ps1"
    args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "serve",
        "--host",
        config.local_app_host,
        "--port",
        str(config.local_app_port),
    ]
    child_env = child_environment_with_user_vars()
    child_env.update(public_env_from_config(config))
    return start_managed_process(
        "local server",
        args,
        log_name="flight_workbench_corp_market.log",
        env=child_env,
    )


def local_server_stop(config: WorkbenchConfig) -> CommandResult:
    require_loopback_host(config.local_app_host, "Local app host")
    summaries: list[str] = []
    stopped_pids: list[int] = []

    managed = managed_process_status("local server")
    if managed.get("running"):
        result = stop_managed_process("local server")
        summaries.append(result.summary)
        pid = result.data.get("pid") if result.data else None
        if isinstance(pid, int):
            stopped_pids.append(pid)
    else:
        with _process_lock:
            _managed_processes.pop("local server", None)

    remaining = [process for process in corp_market_processes_for_config(config) if process.pid not in stopped_pids]
    for process in remaining:
        result = stop_windows_process_tree(process.pid)
        if result.ok:
            stopped_pids.append(process.pid)
            summaries.append(f"Stopped Corp Market listener PID {process.pid}.")
        else:
            summaries.append(f"Could not stop Corp Market listener PID {process.pid}: {result.summary}")

    if stopped_pids:
        return CommandResult(
            ok=True,
            summary="Stopped local Corp Market server.",
            output="\n".join(summaries),
            data={"stopped_pids": stopped_pids},
        )

    health = check_local_health(config)
    if health["ok"]:
        tunnel_processes = ssh_tunnel_processes_for_config(config)
        if tunnel_processes:
            pids = ", ".join(str(process.pid) for process in tunnel_processes)
            return CommandResult(
                ok=False,
                summary="Corp Market is answering through the configured SSH tunnel.",
                output=(
                    f"Port {config.local_app_port} is owned by SSH tunnel PID(s): {pids}.\n"
                    "Use Stop Managed Tunnel to close the tunnel, then start the local server again."
                ),
                data={"tunnel_processes": public_process_summary(tunnel_processes)},
            )
        return CommandResult(
            ok=False,
            summary="Corp Market is answering, but no safe matching local process was found to stop.",
            output="This usually means the port is owned by an unexpected process. Close the old server window manually, then start again.",
        )
    return CommandResult(ok=False, summary="No local Corp Market server is running on the configured URL.")


def validate_ssh_config(config: WorkbenchConfig) -> None:
    if not config.vm_configured:
        raise WorkbenchError("VM SSH is not configured in profiles/flight_workbench.local.json.")
    if not HOST_RE.match(config.ssh_host):
        raise WorkbenchError("VM host contains unsupported characters.")
    if not USER_RE.match(config.ssh_user):
        raise WorkbenchError("VM user contains unsupported characters.")
    key_path = Path(config.ssh_key_path).expanduser()
    if not key_path.is_file():
        raise WorkbenchError("SSH key file was not found.")


def validate_remote_path(value: str, label: str) -> str:
    clean = value.strip()
    if not clean or not REMOTE_SAFE_PATH_RE.match(clean):
        raise WorkbenchError(f"{label} contains unsupported characters.")
    return clean


def validate_remote_name(value: str, label: str) -> str:
    clean = value.strip()
    if not clean or not REMOTE_SAFE_NAME_RE.match(clean):
        raise WorkbenchError(f"{label} contains unsupported characters.")
    return clean


def require_https_url(value: str, label: str) -> str:
    clean = value.strip().rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise WorkbenchError(f"{label} must be an HTTPS URL.")
    return clean


def validate_vm_public_config(config: WorkbenchConfig) -> None:
    if not config.vm_public_hosting_mode:
        return
    public_base_url = require_https_url(config.vm_public_base_url, "VM public base URL")
    callback_url = require_https_url(config.vm_sso_callback_url, "VM SSO callback URL")
    expected_callback = f"{public_base_url.rstrip('/')}/flight/callback"
    if callback_url != expected_callback:
        raise WorkbenchError(f"VM SSO callback URL should be {expected_callback}.")
    if not (
        config.vm_allowed_character_ids
        or config.vm_allowed_corporation_ids
        or config.vm_allowed_alliance_ids
    ):
        raise WorkbenchError("Public hosting needs at least one allowed character, corporation, or alliance ID.")


def public_env_from_config(config: WorkbenchConfig) -> dict[str, str]:
    if not config.vm_public_hosting_mode:
        return {
            "CORP_MARKET_PUBLIC_BASE_URL": "",
            "CORP_MARKET_SSO_CALLBACK_URL": "",
            "CORP_MARKET_PUBLIC_HOSTING_MODE": "0",
            "CORP_MARKET_ALLOWED_CHARACTER_IDS": "",
            "CORP_MARKET_ALLOWED_CORPORATION_IDS": "",
            "CORP_MARKET_ALLOWED_ALLIANCE_IDS": "",
            "CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET": "0",
        }
    validate_vm_public_config(config)
    return {
        "CORP_MARKET_PUBLIC_BASE_URL": require_https_url(config.vm_public_base_url, "VM public base URL"),
        "CORP_MARKET_SSO_CALLBACK_URL": require_https_url(config.vm_sso_callback_url, "VM SSO callback URL"),
        "CORP_MARKET_PUBLIC_HOSTING_MODE": "1",
        "CORP_MARKET_ALLOWED_CHARACTER_IDS": int_list_text(config.vm_allowed_character_ids),
        "CORP_MARKET_ALLOWED_CORPORATION_IDS": int_list_text(config.vm_allowed_corporation_ids),
        "CORP_MARKET_ALLOWED_ALLIANCE_IDS": int_list_text(config.vm_allowed_alliance_ids),
        "CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET": "1"
        if config.vm_trusted_members_can_write_market
        else "0",
    }


def ssh_base_args(config: WorkbenchConfig) -> list[str]:
    validate_ssh_config(config)
    return [
        "ssh",
        "-i",
        str(Path(config.ssh_key_path).expanduser()),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "ServerAliveInterval=30",
        config.ssh_target,
    ]


def run_ssh_command(config: WorkbenchConfig, remote_command: str, *, timeout_seconds: float | None = None) -> CommandResult:
    args = ssh_base_args(config) + [remote_command]
    return run_command(
        args,
        timeout_seconds=timeout_seconds or config.command_timeout_seconds,
        extra_secret_values=(config.ssh_key_path,),
    )


def vm_health(config: WorkbenchConfig) -> CommandResult:
    return run_ssh_command(config, "hostname && uptime && free -h")


def vm_service_status(config: WorkbenchConfig) -> CommandResult:
    service = validate_remote_name(config.vm_service_name, "VM service name")
    return run_ssh_command(
        config,
        (
            f"state=$(systemctl is-active {service} || true); "
            f'echo "{service}: $state"; '
            f"systemctl status --no-pager --lines=24 {service}"
        ),
        timeout_seconds=max(20.0, config.command_timeout_seconds),
    )


def vm_service_restart(config: WorkbenchConfig) -> CommandResult:
    service = validate_remote_name(config.vm_service_name, "VM service name")
    return run_ssh_command(
        config,
        (
            'if [ -x "$HOME/bin/eve-flight-restart" ]; then '
            '"$HOME/bin/eve-flight-restart"; '
            f"else sudo -n systemctl restart {service}; fi"
        ),
        timeout_seconds=max(30.0, config.command_timeout_seconds),
    )


def build_vm_public_env_command(config: WorkbenchConfig) -> str:
    service = validate_remote_name(config.vm_service_name, "VM service name")
    updates = public_env_from_config(config)
    payload = json.dumps(updates, sort_keys=True)
    script = f"""
import json
from pathlib import Path

path = Path("/home/ubuntu/.eve-flight-env")
updates = json.loads({payload!r})
env = {{}}
comments = []
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            comments.append(line)
            continue
        if clean.startswith("export "):
            clean = clean[len("export "):].strip()
        if "=" not in clean:
            comments.append(line)
            continue
        key, value = clean.split("=", 1)
        env[key.strip()] = value.strip()
env.update(updates)
ordered = [
    "CORP_MARKET_SSO_CLIENT_ID",
    "CORP_MARKET_SSO_CLIENT_SECRET",
    "CORP_MARKET_PUBLIC_BASE_URL",
    "CORP_MARKET_SSO_CALLBACK_URL",
    "CORP_MARKET_PUBLIC_HOSTING_MODE",
    "CORP_MARKET_ALLOWED_CHARACTER_IDS",
    "CORP_MARKET_ALLOWED_CORPORATION_IDS",
    "CORP_MARKET_ALLOWED_ALLIANCE_IDS",
    "CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET",
]
lines = [line for line in comments if line.strip().startswith("#")]
for key in ordered:
    if key in env:
        lines.append(f"{{key}}={{env[key]}}")
for key in sorted(env):
    if key not in ordered:
        lines.append(f"{{key}}={{env[key]}}")
path.write_text("\\n".join(lines).rstrip() + "\\n", encoding="utf-8")
path.chmod(0o600)
print("Updated /home/ubuntu/.eve-flight-env")
print("Public base URL:", updates.get("CORP_MARKET_PUBLIC_BASE_URL", ""))
print("Callback URL:", updates.get("CORP_MARKET_SSO_CALLBACK_URL", ""))
print("Allowed character IDs:", updates.get("CORP_MARKET_ALLOWED_CHARACTER_IDS", ""))
print("Allowed corporation IDs:", updates.get("CORP_MARKET_ALLOWED_CORPORATION_IDS", ""))
print("Allowed alliance IDs:", updates.get("CORP_MARKET_ALLOWED_ALLIANCE_IDS", ""))
"""
    return (
        "set -e; "
        f"python3 - <<'PY'\n{script}\nPY\n"
        "sudo -n systemctl daemon-reload; "
        f"sudo -n systemctl restart {service}; "
        f"state=$(systemctl is-active {service} || true); "
        f'echo "{service}: $state"; '
        "sleep 2; "
        'echo "Health:"; '
        "curl -fsS http://127.0.0.1:8770/api/health; "
        "echo; "
        'echo "Flight diagnostics require an allowlisted SSO browser session in public-hosting mode."'
    )


def vm_apply_public_config(config: WorkbenchConfig) -> CommandResult:
    validate_vm_public_config(config)
    remote_command = build_vm_public_env_command(config)
    return run_ssh_command(
        config,
        remote_command,
        timeout_seconds=max(45.0, config.command_timeout_seconds),
    )


def vm_update_and_restart(config: WorkbenchConfig) -> CommandResult:
    app_dir = validate_remote_path(config.vm_app_dir, "VM app directory")
    service = validate_remote_name(config.vm_service_name, "VM service name")
    remote_command = build_vm_update_command(app_dir, service, verify=False)
    return run_ssh_command(
        config,
        remote_command,
        timeout_seconds=max(90.0, config.command_timeout_seconds),
    )


def vm_update_and_verify(config: WorkbenchConfig) -> CommandResult:
    app_dir = validate_remote_path(config.vm_app_dir, "VM app directory")
    service = validate_remote_name(config.vm_service_name, "VM service name")
    remote_command = build_vm_update_command(app_dir, service, verify=True)
    return run_ssh_command(
        config,
        remote_command,
        timeout_seconds=max(120.0, config.command_timeout_seconds),
    )


def build_vm_update_command(app_dir: str, service: str, *, verify: bool) -> str:
    restart_command = (
        'if [ -x "$HOME/bin/eve-flight-restart" ]; then '
        '"$HOME/bin/eve-flight-restart"; '
        f"else sudo -n systemctl restart {service}; fi"
    )
    command = (
        "set -e; "
        f"cd {app_dir}; "
        'branch="$(git rev-parse --abbrev-ref HEAD)"; '
        'echo "Branch: $branch"; '
        'if [ -n "$(git status --porcelain)" ]; then '
        'echo "VM working tree has local changes; refusing to update."; '
        "git status --short; "
        "exit 3; "
        "fi; "
        'git pull --ff-only origin "$branch"; '
        "if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi; "
        ".venv/bin/python -m pip install -r requirements.txt; "
        f"{restart_command}; "
        f"state=$(systemctl is-active {service} || true); "
        f'echo "{service}: $state"; '
        f"systemctl status --no-pager --lines=18 {service}"
    )
    if verify:
        command += (
            "; "
            'echo "Git status:"; '
            "git status --short --branch; "
            'echo "Health:"; '
            "for attempt in 1 2 3 4 5 6 7 8 9 10; do "
            "if curl -fsS http://127.0.0.1:8770/api/health 2>/dev/null; then break; fi; "
            'if [ "$attempt" = "10" ]; then echo "Health check did not answer after 10 seconds."; exit 7; fi; '
            "sleep 1; "
            "done"
        )
    return command


def vm_logs_tail(config: WorkbenchConfig) -> CommandResult:
    service = validate_remote_name(config.vm_service_name, "VM service name")
    return run_ssh_command(
        config,
        (
            'if [ -x "$HOME/bin/eve-flight-logs" ]; then '
            '"$HOME/bin/eve-flight-logs" | tail -n 120; '
            f"else journalctl -u {service} -n 120 --no-pager; fi"
        ),
        timeout_seconds=max(30.0, config.command_timeout_seconds),
    )


def vm_git_status(config: WorkbenchConfig) -> CommandResult:
    app_dir = validate_remote_path(config.vm_app_dir, "VM app directory")
    return run_ssh_command(config, f"cd {app_dir} && git status --short --branch")


def vm_public_readiness(config: WorkbenchConfig) -> CommandResult:
    app_dir = validate_remote_path(config.vm_app_dir, "VM app directory")
    service = validate_remote_name(config.vm_service_name, "VM service name")
    script = build_vm_public_readiness_script(service)
    remote_command = f"cd {app_dir} && .venv/bin/python -c {shlex.quote(script)}"
    return run_ssh_command(
        config,
        remote_command,
        timeout_seconds=max(35.0, config.command_timeout_seconds),
    )


def build_vm_public_readiness_script(service: str) -> str:
    return f"""
import json
import shutil
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

SERVICE = {service!r}
issues = []


def run(args):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")


def yes_no(value):
    return "yes" if value else "no"


def truthy(value):
    return str(value or "").strip().lower() in {{"1", "true", "yes", "on"}}


def read_flight_env():
    env = {{}}
    try:
        with open("/home/ubuntu/.eve-flight-env", encoding="utf-8") as handle:
            for line in handle:
                clean = line.strip()
                if not clean or clean.startswith("#"):
                    continue
                if clean.startswith("export "):
                    clean = clean[len("export "):].strip()
                if "=" not in clean:
                    continue
                key, value = clean.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError as exc:
        print(f"Flight env file: error: {{exc}}")
        issues.append("Flight env file is not readable.")
    return env


print("VM public hosting readiness")
service_state = run(["systemctl", "is-active", SERVICE]).stdout.strip()
print(f"{{SERVICE}}: {{service_state or 'unknown'}}")
if service_state != "active":
    issues.append(f"{{SERVICE}} is not active.")

try:
    with urlopen("http://127.0.0.1:8770/api/health", timeout=3) as response:
        health = json.loads(response.read().decode("utf-8", errors="replace"))
    print(f"Local app health: {{yes_no(bool(health.get('ok')))}}")
    if not health.get("ok"):
        issues.append("Local app health is not OK.")
except (OSError, HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
    print(f"Local app health: error: {{exc}}")
    issues.append("Local app health endpoint is not reachable.")

flight_env = read_flight_env()
diagnostics = None
try:
    with urlopen("http://127.0.0.1:8770/api/flight/diagnostics", timeout=5) as response:
        diagnostics = json.loads(response.read().decode("utf-8", errors="replace"))
except HTTPError as exc:
    if exc.code == 403:
        print("Flight diagnostics: protected by public-hosting SSO (HTTP 403)")
    else:
        print(f"Flight diagnostics: error: {{exc}}")
        issues.append("Flight diagnostics endpoint is not reachable from the VM.")
except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
    print(f"Flight diagnostics: error: {{exc}}")
    issues.append("Flight diagnostics endpoint is not reachable from the VM.")

if diagnostics is not None:
    hosting = diagnostics.get("hosting") or {{}}
    sso = diagnostics.get("sso") or {{}}
    public_mode = bool(hosting.get("public_hosting_mode"))
    public_https = bool(hosting.get("public_base_url_https"))
    callback_match = bool(hosting.get("callback_matches_public_base"))
    sso_ready = bool(sso.get("configured"))
    restricted = bool(sso.get("membership_restricted"))
    print(f"Server mode: {{hosting.get('server_mode_label') or 'unknown'}}")
    print(f"Public base URL: {{hosting.get('public_base_url') or 'missing'}}")
    print(f"Expected callback: {{hosting.get('expected_callback_url') or 'missing'}}")
    print(f"Public hosting mode: {{yes_no(public_mode)}}")
    print(f"Public URL uses HTTPS: {{yes_no(public_https)}}")
    print(f"Callback matches public base: {{yes_no(callback_match)}}")
    print(f"SSO configured: {{yes_no(sso_ready)}}")
    print(f"Member allowlist configured: {{yes_no(restricted)}}")
    if not public_mode:
        issues.append("Public hosting mode is not enabled.")
    if not public_https:
        issues.append("Public base URL is missing or not HTTPS.")
    if not callback_match:
        issues.append("SSO callback does not match the public base URL.")
    if not sso_ready:
        issues.append("SSO client ID/secret are not configured.")
    if not restricted:
        issues.append("Corp/alliance member allowlist is not configured.")
else:
    public_base = flight_env.get("CORP_MARKET_PUBLIC_BASE_URL", "")
    callback_url = flight_env.get("CORP_MARKET_SSO_CALLBACK_URL", "")
    expected_callback = f"{{public_base.rstrip('/')}}/flight/callback" if public_base else ""
    public_mode = truthy(flight_env.get("CORP_MARKET_PUBLIC_HOSTING_MODE"))
    public_https = public_base.startswith("https://")
    callback_match = bool(callback_url and expected_callback and callback_url.rstrip("/") == expected_callback.rstrip("/"))
    sso_ready = bool(
        flight_env.get("CORP_MARKET_SSO_CLIENT_ID")
        and flight_env.get("CORP_MARKET_SSO_CLIENT_SECRET")
    )
    restricted = bool(
        flight_env.get("CORP_MARKET_ALLOWED_CHARACTER_IDS")
        or flight_env.get("CORP_MARKET_ALLOWED_CORPORATION_IDS")
        or flight_env.get("CORP_MARKET_ALLOWED_ALLIANCE_IDS")
    )
    print("Server mode: inferred from /home/ubuntu/.eve-flight-env")
    print(f"Public base URL: {{public_base or 'missing'}}")
    print(f"Expected callback: {{expected_callback or 'missing'}}")
    print(f"Public hosting mode: {{yes_no(public_mode)}}")
    print(f"Public URL uses HTTPS: {{yes_no(public_https)}}")
    print(f"Callback matches public base: {{yes_no(callback_match)}}")
    print(f"SSO configured: {{yes_no(sso_ready)}}")
    print(f"Member allowlist configured: {{yes_no(restricted)}}")
    if not public_mode:
        issues.append("Public hosting mode is not enabled.")
    if not public_https:
        issues.append("Public base URL is missing or not HTTPS.")
    if not callback_match:
        issues.append("SSO callback does not match the public base URL.")
    if not sso_ready:
        issues.append("SSO client ID/secret are not configured.")
    if not restricted:
        issues.append("Corp/alliance member allowlist is not configured.")

caddy_path = shutil.which("caddy")
if caddy_path:
    version = run(["caddy", "version"]).stdout.strip().splitlines()
    caddy_state = run(["systemctl", "is-active", "caddy"]).stdout.strip()
    print(f"Caddy binary: {{caddy_path}}")
    print(f"Caddy version: {{version[0] if version else 'unknown'}}")
    print(f"caddy.service: {{caddy_state or 'unknown'}}")
    if caddy_state != "active":
        issues.append("caddy.service is not active.")
else:
    print("Caddy binary: missing")
    issues.append("Caddy is not installed.")

if issues:
    print("Missing before public HTTPS:")
    for issue in issues:
        print(f"- {{issue}}")
    sys.exit(1)

print("Ready for public HTTPS hosting checks.")
"""


def tunnel_start(config: WorkbenchConfig) -> CommandResult:
    validate_ssh_config(config)
    if process_is_running("ssh tunnel"):
        return CommandResult(ok=True, summary="SSH tunnel is already managed by this workbench.")
    require_loopback_host(config.tunnel_remote_host, "Tunnel remote host")
    forward = config.tunnel_forward
    args = ssh_base_args(config)[:-1] + [
        "-N",
        "-L",
        forward,
        config.ssh_target,
    ]
    return start_managed_process("ssh tunnel", args, log_name="flight_workbench_tunnel.log")


def tunnel_stop(config: WorkbenchConfig) -> CommandResult:
    result = stop_managed_process("ssh tunnel")
    if result.ok:
        return result
    with _process_lock:
        _managed_processes.pop("ssh tunnel", None)
    tunnel_processes = ssh_tunnel_processes_for_config(config)
    if not tunnel_processes:
        return result
    summaries: list[str] = []
    stopped_pids: list[int] = []
    for process in tunnel_processes:
        stop_result = stop_windows_process_tree(process.pid)
        if stop_result.ok:
            stopped_pids.append(process.pid)
            summaries.append(f"Stopped SSH tunnel PID {process.pid}.")
        else:
            summaries.append(f"Could not stop SSH tunnel PID {process.pid}: {stop_result.summary}")
    if stopped_pids:
        return CommandResult(
            ok=True,
            summary="Stopped SSH tunnel.",
            output="\n".join(summaries),
            data={"stopped_pids": stopped_pids},
        )
    return CommandResult(
        ok=False,
        summary="Could not stop the configured SSH tunnel.",
        output="\n".join(summaries),
        data={"tunnel_processes": public_process_summary(tunnel_processes)},
    )


def action_definitions() -> dict[str, ActionDefinition]:
    actions = [
        ActionDefinition("local_health", "Check Local Health", "Health Checks", "Fetch /api/health and diagnostics.", local_health_action),
        ActionDefinition("cache_preflight", "Check Static Caches", "Health Checks", "Read local cache preflight status.", cache_preflight_action),
        ActionDefinition("git_status", "Git Status", "Git", "Show local Git branch and dirty state.", local_git_status),
        ActionDefinition("git_diff_check", "Diff Whitespace Check", "Git", "Run git diff --check.", git_diff_check),
        ActionDefinition("local_server_start", "Start Local Server", "Local Server", "Start Corp Market through the existing wrapper.", local_server_start, True),
        ActionDefinition("local_server_stop", "Stop Local Server", "Local Server", "Stop the managed server or a recognized stale Corp Market listener.", local_server_stop, True),
        ActionDefinition("tunnel_start", "Start SSH Tunnel", "SSH Tunnel", "Start the configured local SSH tunnel.", tunnel_start, True),
        ActionDefinition("tunnel_stop", "Stop Managed Tunnel", "SSH Tunnel", "Stop only the tunnel started by this workbench.", tunnel_stop, True),
        ActionDefinition("vm_health", "VM Health", "VM App Service", "Run hostname, uptime, and memory checks over SSH.", vm_health),
        ActionDefinition("vm_service_status", "Service Status", "VM App Service", "Read systemd service status over SSH.", vm_service_status),
        ActionDefinition("vm_service_restart", "Restart VM Service", "VM App Service", "Restart the configured app service over SSH.", vm_service_restart, True),
        ActionDefinition("vm_apply_public_config", "Apply VM Public Config", "VM App Service", "Write saved public hosting settings to the VM env file and restart the service.", vm_apply_public_config, True),
        ActionDefinition("vm_update_restart", "Update VM App", "VM App Service", "Fast-forward VM Git checkout, install requirements, and restart the service.", vm_update_and_restart, True),
        ActionDefinition("vm_update_verify", "Update VM + Verify", "VM App Service", "Update the VM app, restart the service, check Git status, and fetch health.", vm_update_and_verify, True),
        ActionDefinition("vm_logs_tail", "Tail VM Logs", "VM App Service", "Read recent service logs over SSH.", vm_logs_tail),
        ActionDefinition("vm_git_status", "VM Git Status", "VM App Service", "Show Git status in the VM app directory.", vm_git_status),
        ActionDefinition("vm_public_readiness", "Public Readiness", "VM App Service", "Check VM public-hosting readiness without changing Oracle, DNS, or secrets.", vm_public_readiness),
    ]
    return {action.action_id: action for action in actions}


def public_action_definitions() -> list[dict[str, Any]]:
    return [
        {
            "id": action.action_id,
            "label": action.label,
            "group": action.group,
            "description": action.description,
            "changes_process": action.changes_process,
        }
        for action in action_definitions().values()
    ]


def run_action(action_id: str, config: WorkbenchConfig) -> CommandResult:
    action = action_definitions().get(action_id)
    if action is None:
        raise WorkbenchError("Action is not in the workbench allowlist.")
    return action.runner(config)


def command_result_payload(action_id: str, result: CommandResult) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "ok": result.ok,
        "summary": result.summary,
        "output": result.output,
        "returncode": result.returncode,
        "data": result.data or {},
        "generated_at": now_iso(),
    }


def append_action_log(config: WorkbenchConfig, payload: dict[str, Any]) -> None:
    ensure_ignored_dirs()
    entry = {
        "action_id": payload.get("action_id"),
        "ok": bool(payload.get("ok")),
        "summary": redact_text(payload.get("summary", ""), extra_values=(config.ssh_key_path,)),
        "output": compact_output(redact_text(payload.get("output", ""), extra_values=(config.ssh_key_path,)), limit=3000),
        "returncode": payload.get("returncode"),
        "generated_at": payload.get("generated_at") or now_iso(),
    }
    with config.action_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def recent_action_log(config: WorkbenchConfig, limit: int = RECENT_ACTION_LIMIT) -> list[dict[str, Any]]:
    if not config.action_log_path.exists():
        return []
    try:
        lines = config.action_log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def build_status_payload(config: WorkbenchConfig) -> dict[str, Any]:
    health = check_local_health(config, timeout_seconds=0.8)
    git_status = local_git_status(config)
    local_processes, tunnel_processes = local_and_tunnel_processes_for_config(config, timeout_seconds=2.0)
    return {
        "ok": True,
        "generated_at": now_iso(),
        "config": config.public_dict(),
        "local_server": {
            "managed": managed_process_status("local server"),
            "health": health,
            "processes": public_process_summary(local_processes),
        },
        "ssh_tunnel": {
            "managed": managed_process_status("ssh tunnel"),
            "configured": config.vm_configured,
            "forward": config.tunnel_forward,
            "processes": public_process_summary(tunnel_processes),
        },
        "git": {
            "ok": git_status.ok,
            "summary": git_status.summary,
            "display": summarize_git_status(git_status),
            "output": git_status.output,
        },
        "environment": environment_status(),
        "actions": public_action_definitions(),
        "recent_actions": recent_action_log(config),
        "manual_only": [
            "Rotate SSO secrets and Discord webhooks outside this workbench.",
            "Change EVE Developer callbacks in the EVE Developers portal.",
            "Create, terminate, or firewall Oracle resources in the Oracle console.",
            "Push Git commits only from your normal Git workflow.",
        ],
    }


def escape_attr(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def render_dashboard(config: WorkbenchConfig, operator_token: str, csp_nonce: str) -> str:
    token = escape_attr(operator_token)
    nonce = escape_attr(csp_nonce)
    initial_actions_json = json.dumps(public_action_definitions()).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Flight Attendant Workbench</title>
  <style nonce="{nonce}">
    :root {{
      color-scheme: light;
      --bg: #f6f7f5;
      --ink: #18201d;
      --muted: #5d6861;
      --line: #d9dfda;
      --panel: #ffffff;
      --soft: #eef2ef;
      --green: #16785a;
      --red: #ba3d32;
      --amber: #a96800;
      --blue: #1f5f99;
      --shadow: 0 16px 42px rgba(24, 32, 29, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      border-bottom: 1px solid var(--line);
      background: #fbfcfb;
    }}
    .wrap {{
      width: min(1280px, calc(100% - 36px));
      margin: 0 auto;
    }}
    .topbar {{
      min-height: 76px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(1.35rem, 2.5vw, 2.1rem);
      line-height: 1.1;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin-top: 7px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    main {{
      padding: 24px 0 34px;
    }}
    .status-strip {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .tile, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .tile {{
      min-height: 88px;
      padding: 15px;
    }}
    .tile span {{
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .tile strong {{
      display: block;
      margin-top: 8px;
      font-size: 1.08rem;
      line-height: 1.25;
    }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
      gap: 18px;
      align-items: start;
    }}
    .panel {{
      margin-bottom: 18px;
      overflow: hidden;
    }}
    .panel h2 {{
      margin: 0;
      font-size: 1rem;
      line-height: 1.25;
    }}
    .panel-head {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: #fbfcfb;
    }}
    .panel-body {{
      padding: 16px 18px 18px;
    }}
    .actions {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .action-group {{
      padding: 12px 0;
      border-top: 1px solid var(--soft);
    }}
    .action-group:first-child {{
      padding-top: 0;
      border-top: 0;
    }}
    .action-group h3 {{
      margin: 0 0 10px;
      font-size: 0.88rem;
      line-height: 1.2;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
    }}
    button {{
      min-height: 40px;
      border: 1px solid #b9c3bc;
      border-radius: 8px;
      background: #ffffff;
      color: var(--ink);
      font: 700 0.92rem/1.1 "Segoe UI", system-ui, sans-serif;
      cursor: pointer;
      padding: 10px 12px;
      text-align: left;
    }}
    button:hover {{ border-color: var(--green); color: var(--green); }}
    button:disabled {{ cursor: wait; color: #87908a; border-color: #d7ddd8; }}
    .primary {{
      background: var(--green);
      border-color: var(--green);
      color: #fff;
      text-align: center;
    }}
    .primary:hover {{ background: #0f684c; color: #fff; }}
    .danger {{ border-color: #e2b5ae; color: var(--red); }}
    .muted {{
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.45;
    }}
    .rows {{
      display: grid;
      gap: 8px;
    }}
    .row {{
      display: grid;
      grid-template-columns: minmax(160px, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 9px 0;
      border-bottom: 1px solid var(--soft);
      font-size: 0.92rem;
    }}
    .row:last-child {{ border-bottom: 0; }}
    .form-grid {{
      display: grid;
      gap: 10px;
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--soft);
    }}
    label.field {{
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 700;
    }}
    input[type="text"] {{
      width: 100%;
      border: 1px solid #b9c3bc;
      border-radius: 7px;
      padding: 8px 10px;
      font: 0.9rem/1.3 "Segoe UI", system-ui, sans-serif;
      color: var(--ink);
      background: #fff;
    }}
    .check-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 0.86rem;
      font-weight: 700;
    }}
    code, pre {{
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 0.86rem;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 800;
      border: 1px solid transparent;
      white-space: nowrap;
    }}
    .ok {{ color: var(--green); background: #e8f4ee; border-color: #b8ddcd; }}
    .warn {{ color: var(--amber); background: #fff5df; border-color: #e9cf91; }}
    .bad {{ color: var(--red); background: #fff0ed; border-color: #eab8ae; }}
    .neutral {{ color: var(--blue); background: #eaf2f8; border-color: #bfd4e8; }}
    .output {{
      min-height: 220px;
      max-height: 520px;
      overflow: auto;
      background: #111713;
      color: #e8f1ea;
      border-radius: 8px;
      padding: 14px;
      white-space: pre-wrap;
      line-height: 1.45;
    }}
    .action-log {{
      display: grid;
      gap: 10px;
      max-height: 360px;
      overflow: auto;
    }}
    .log-entry {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfb;
    }}
    .log-entry strong {{ display: block; margin-bottom: 4px; }}
    .small {{ font-size: 0.8rem; color: var(--muted); }}
    @media (max-width: 920px) {{
      .status-strip, .grid {{ grid-template-columns: 1fr; }}
      .actions {{ grid-template-columns: 1fr; }}
      .topbar {{ align-items: flex-start; flex-direction: column; padding: 18px 0; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap topbar">
      <div>
        <h1>Flight Attendant Workbench</h1>
        <div class="subtitle">Local operator panel for server, tunnel, VM, Git, SSO, and health checks.</div>
      </div>
      <button class="primary" id="refresh-button" type="button">Refresh Status</button>
    </div>
  </header>
  <main class="wrap">
    <section class="status-strip" aria-label="Workbench status">
      <div class="tile"><span>Local Server</span><strong id="local-status">Checking</strong></div>
      <div class="tile"><span>SSH Tunnel</span><strong id="tunnel-status">Checking</strong></div>
      <div class="tile"><span>SSO Env</span><strong id="sso-status">Checking</strong></div>
      <div class="tile"><span>Git</span><strong id="git-status">Checking</strong></div>
    </section>
    <section class="grid">
      <div>
        <div class="panel">
          <div class="panel-head"><h2>Actions</h2><span class="pill neutral">Allowlisted</span></div>
          <div class="panel-body">
            <div id="action-groups"></div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Last Result</h2><span id="result-status" class="pill neutral" aria-live="polite">Idle</span></div>
          <div class="panel-body">
            <pre id="action-output" class="output" aria-live="polite">No action has run yet.</pre>
          </div>
        </div>
      </div>
      <aside>
        <div class="panel">
          <div class="panel-head"><h2>Configuration</h2><span id="config-status" class="pill neutral">Local</span></div>
          <div class="panel-body">
            <div class="rows" id="config-rows"></div>
            <form id="public-config-form" class="form-grid">
              <label class="field">Public base URL
                <input id="vm-public-base-url" name="vm_public_base_url" type="text" autocomplete="off" placeholder="https://flight.example.com">
              </label>
              <label class="field">SSO callback URL
                <input id="vm-sso-callback-url" name="vm_sso_callback_url" type="text" autocomplete="off" placeholder="https://flight.example.com/flight/callback">
              </label>
              <label class="field">Allowed character IDs
                <input id="vm-allowed-character-ids" name="vm_allowed_character_ids" type="text" autocomplete="off" placeholder="2124413713, 123456789">
              </label>
              <label class="field">Allowed corporation IDs
                <input id="vm-allowed-corporation-ids" name="vm_allowed_corporation_ids" type="text" autocomplete="off" placeholder="1000045">
              </label>
              <label class="field">Allowed alliance IDs
                <input id="vm-allowed-alliance-ids" name="vm_allowed_alliance_ids" type="text" autocomplete="off">
              </label>
              <label class="check-row">
                <input id="vm-public-hosting-mode" name="vm_public_hosting_mode" type="checkbox">
                Public hosting mode
              </label>
              <label class="check-row">
                <input id="vm-trusted-members-can-write-market" name="vm_trusted_members_can_write_market" type="checkbox">
                Allow trusted members to write market listings
              </label>
              <button id="save-public-config" type="submit">Save Public Config</button>
            </form>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>SSO / Env</h2><span id="env-status" class="pill neutral">Hidden</span></div>
          <div class="panel-body">
            <div class="rows" id="env-rows"></div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Recent Actions</h2><span id="log-count" class="pill neutral">0</span></div>
          <div class="panel-body">
            <div class="action-log" id="action-log"></div>
          </div>
        </div>
      </aside>
    </section>
  </main>
  <script nonce="{nonce}">
    const operatorToken = "{token}";
    const initialActions = {initial_actions_json};
    const actionOutput = document.querySelector("#action-output");
    const resultStatus = document.querySelector("#result-status");
    const actionGroups = document.querySelector("#action-groups");

    function pillClass(ok) {{
      return ok ? "pill ok" : "pill bad";
    }}

    function setText(selector, value) {{
      const element = document.querySelector(selector);
      if (element) element.textContent = value;
    }}

    function row(label, value, cls = "pill neutral") {{
      return `<div class="row"><span>${{escapeHtml(label)}}</span><span class="${{cls}}">${{escapeHtml(value)}}</span></div>`;
    }}

    function idListText(values) {{
      return Array.isArray(values) ? values.join(",") : "";
    }}

    function setInputValue(selector, value) {{
      const element = document.querySelector(selector);
      if (element) element.value = value ?? "";
    }}

    function setChecked(selector, value) {{
      const element = document.querySelector(selector);
      if (element) element.checked = Boolean(value);
    }}

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
    }}

    function renderActions(actions) {{
      const byGroup = new Map();
      for (const action of actions || []) {{
        if (!byGroup.has(action.group)) byGroup.set(action.group, []);
        byGroup.get(action.group).push(action);
      }}
      actionGroups.innerHTML = Array.from(byGroup.entries()).map(([group, items]) => `
        <section class="action-group">
          <h3>${{escapeHtml(group)}}</h3>
          <div class="actions">
            ${{items.map(action => `<button type="button" data-action="${{escapeHtml(action.id)}}">${{escapeHtml(action.label)}}</button>`).join("")}}
          </div>
        </section>
      `).join("");
    }}

    function renderStatus(data) {{
      const health = data.local_server?.health || {{}};
      const localManaged = data.local_server?.managed || {{}};
      const localProcesses = data.local_server?.processes || [];
      const localLabel = localManaged.running
        ? `Managed PID ${{localManaged.pid}}`
        : (health.ok && localProcesses.length ? `Answering PID ${{localProcesses.map(process => process.pid).join(", ")}}` : (health.ok ? "Answering" : "Offline"));
      setText("#local-status", localLabel);
      document.querySelector("#local-status").style.color = health.ok ? "var(--green)" : "var(--red)";
      const tunnel = data.ssh_tunnel || {{}};
      const tunnelManaged = tunnel.managed || {{}};
      setText("#tunnel-status", tunnelManaged.running ? `Managed PID ${{tunnelManaged.pid}}` : (tunnel.configured ? "Configured" : "Not configured"));
      setText("#sso-status", data.environment?.sso_ready ? "Configured" : "Missing");
      document.querySelector("#sso-status").style.color = data.environment?.sso_ready ? "var(--green)" : "var(--amber)";
      setText("#git-status", data.git?.display || (data.git?.ok ? "Checked" : "Check failed"));
      renderActions(data.actions || []);

      const config = data.config || {{}};
      document.querySelector("#config-rows").innerHTML = [
        row("Config file", config.config_exists ? "Found" : "Missing", config.config_exists ? "pill ok" : "pill warn"),
        row("Local URL", config.local_base_url || ""),
        row("Tunnel", config.tunnel_forward || ""),
        row("SSH host", config.ssh_host_configured ? "Set" : "Missing", config.ssh_host_configured ? "pill ok" : "pill warn"),
        row("SSH key", config.ssh_key_exists ? "Found" : (config.ssh_key_configured ? "Missing file" : "Missing"), config.ssh_key_exists ? "pill ok" : "pill warn"),
        row("VM service", config.vm_service_name || ""),
        row("Public mode", config.vm_public_hosting_mode ? "On" : "Off", config.vm_public_hosting_mode ? "pill ok" : "pill warn")
      ].join("");
      setInputValue("#vm-public-base-url", config.vm_public_base_url || "");
      setInputValue("#vm-sso-callback-url", config.vm_sso_callback_url || "");
      setInputValue("#vm-allowed-character-ids", idListText(config.vm_allowed_character_ids));
      setInputValue("#vm-allowed-corporation-ids", idListText(config.vm_allowed_corporation_ids));
      setInputValue("#vm-allowed-alliance-ids", idListText(config.vm_allowed_alliance_ids));
      setChecked("#vm-public-hosting-mode", config.vm_public_hosting_mode);
      setChecked("#vm-trusted-members-can-write-market", config.vm_trusted_members_can_write_market);

      const envRows = data.environment?.rows || [];
      document.querySelector("#env-rows").innerHTML = envRows.map(item => row(item.name, item.value || (item.configured ? "Set" : "Missing"), item.configured ? "pill ok" : "pill warn")).join("");
      const logs = data.recent_actions || [];
      setText("#log-count", String(logs.length));
      document.querySelector("#action-log").innerHTML = logs.length ? logs.slice().reverse().map(entry => `
        <div class="log-entry">
          <strong>${{escapeHtml(entry.action_id || "action")}} <span class="${{entry.ok ? "pill ok" : "pill bad"}}">${{entry.ok ? "OK" : "Check"}}</span></strong>
          <div>${{escapeHtml(entry.summary || "")}}</div>
          <div class="small">${{escapeHtml(entry.generated_at || "")}}</div>
        </div>
      `).join("") : `<div class="muted">No actions logged yet.</div>`;
    }}

    async function refreshStatus() {{
      const response = await fetch("/api/status", {{headers: {{"Accept": "application/json"}}}});
      const data = await response.json();
      renderStatus(data);
    }}

    async function runAction(actionId) {{
      resultStatus.textContent = "Running";
      resultStatus.className = "pill warn";
      actionOutput.textContent = `Running ${{actionId}}`;
      for (const button of document.querySelectorAll("button")) button.disabled = true;
      try {{
        const response = await fetch(`/api/actions/${{encodeURIComponent(actionId)}}`, {{
          method: "POST",
          headers: {{"Accept": "application/json", "X-Workbench-Token": operatorToken}}
        }});
        const data = await response.json();
        resultStatus.textContent = data.ok ? "OK" : "Check";
        resultStatus.className = data.ok ? "pill ok" : "pill bad";
        actionOutput.textContent = [data.summary || "", data.output || ""].filter(Boolean).join("\\n\\n") || JSON.stringify(data, null, 2);
      }} catch (error) {{
        resultStatus.textContent = "Error";
        resultStatus.className = "pill bad";
        actionOutput.textContent = error.message || String(error);
      }} finally {{
        for (const button of document.querySelectorAll("button")) button.disabled = false;
        await refreshStatus();
      }}
    }}

    async function savePublicConfig(event) {{
      event.preventDefault();
      resultStatus.textContent = "Saving";
      resultStatus.className = "pill warn";
      const payload = {{
        vm_public_base_url: document.querySelector("#vm-public-base-url").value,
        vm_sso_callback_url: document.querySelector("#vm-sso-callback-url").value,
        vm_allowed_character_ids: document.querySelector("#vm-allowed-character-ids").value,
        vm_allowed_corporation_ids: document.querySelector("#vm-allowed-corporation-ids").value,
        vm_allowed_alliance_ids: document.querySelector("#vm-allowed-alliance-ids").value,
        vm_public_hosting_mode: document.querySelector("#vm-public-hosting-mode").checked,
        vm_trusted_members_can_write_market: document.querySelector("#vm-trusted-members-can-write-market").checked
      }};
      try {{
        const response = await fetch("/api/config/vm-public", {{
          method: "POST",
          headers: {{"Accept": "application/json", "Content-Type": "application/json", "X-Workbench-Token": operatorToken}},
          body: JSON.stringify(payload)
        }});
        const data = await response.json();
        resultStatus.textContent = data.ok ? "OK" : "Check";
        resultStatus.className = data.ok ? "pill ok" : "pill bad";
        actionOutput.textContent = data.summary || JSON.stringify(data, null, 2);
      }} catch (error) {{
        resultStatus.textContent = "Error";
        resultStatus.className = "pill bad";
        actionOutput.textContent = error.message || String(error);
      }} finally {{
        await refreshStatus();
      }}
    }}

    document.addEventListener("click", event => {{
      const button = event.target.closest("button[data-action]");
      if (button) runAction(button.dataset.action);
    }});
    document.querySelector("#public-config-form").addEventListener("submit", savePublicConfig);
    document.querySelector("#refresh-button").addEventListener("click", refreshStatus);
    renderActions(initialActions);
    refreshStatus();
  </script>
</body>
</html>"""


class WorkbenchState:
    def __init__(self, config: WorkbenchConfig, operator_token: str, csp_nonce: str) -> None:
        self.config = config
        self.operator_token = operator_token
        self.csp_nonce = csp_nonce


def build_http_server(host: str, port: int, state: WorkbenchState) -> ThreadingHTTPServer:
    if not is_loopback_host(host):
        raise WorkbenchError("Flight Attendant Workbench only binds to 127.0.0.1, localhost, or ::1.")

    class WorkbenchHandler(BaseHTTPRequestHandler):
        server_version = "FlightWorkbench/0.1"

        def do_GET(self) -> None:
            if not self._require_loopback():
                return
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._send_html(render_dashboard(state.config, state.operator_token, state.csp_nonce))
                return
            if path == "/api/status":
                self._send_json(build_status_payload(state.config))
                return
            if path == "/api/health":
                self._send_json({"ok": True, "generated_at": now_iso()})
                return
            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            if not self._require_loopback():
                return
            if not self._require_operator_token():
                return
            if not origin_is_allowed(
                self.headers.get("Origin", ""),
                self.headers.get("Referer", ""),
                self.headers.get("Host", ""),
            ):
                self._send_json({"ok": False, "error": "Origin was not allowed."}, status=403)
                return
            path = urlparse(self.path).path
            if path == "/api/config/vm-public":
                try:
                    payload = self._read_json_body()
                    state.config = update_vm_public_config(state.config.config_path, payload)
                except WorkbenchError as exc:
                    self._send_json({"ok": False, "summary": str(exc)}, status=400)
                    return
                except Exception as exc:
                    self._send_json({"ok": False, "summary": f"Could not save public config: {exc}"}, status=400)
                    return
                append_action_log(
                    state.config,
                    {
                        "ok": True,
                        "action": "save_public_config",
                        "summary": "Saved public hosting config locally.",
                    },
                )
                self._send_json(
                    {
                        "ok": True,
                        "summary": "Saved public hosting config locally. Use Apply VM Public Config to write it to the VM service.",
                        "config": state.config.public_dict(),
                    }
                )
                return
            prefix = "/api/actions/"
            if not path.startswith(prefix):
                self.send_error(404, "Not found")
                return
            action_id = path.removeprefix(prefix).strip("/")
            try:
                result = run_action(action_id, state.config)
            except WorkbenchError as exc:
                payload = {
                    "action_id": action_id,
                    "ok": False,
                    "summary": str(exc),
                    "output": "",
                    "generated_at": now_iso(),
                }
            except Exception as exc:  # defensive boundary for local operator UI
                payload = {
                    "action_id": action_id,
                    "ok": False,
                    "summary": f"Action failed: {exc}",
                    "output": "",
                    "generated_at": now_iso(),
                }
            else:
                payload = command_result_payload(action_id, result)
            append_action_log(state.config, payload)
            self._send_json(payload, status=200 if payload.get("ok") else 400)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 64_000:
                raise WorkbenchError("Request body was empty or too large.")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise WorkbenchError("Request body must be JSON.") from exc
            if not isinstance(parsed, dict):
                raise WorkbenchError("Request body must be a JSON object.")
            return parsed

        def _require_loopback(self) -> bool:
            if client_is_loopback(str(self.client_address[0])):
                return True
            self._send_json({"ok": False, "error": "Workbench accepts local browser requests only."}, status=403)
            return False

        def _require_operator_token(self) -> bool:
            token = self.headers.get("X-Workbench-Token", "")
            if secrets.compare_digest(token, state.operator_token):
                return True
            self._send_json({"ok": False, "error": "Missing or invalid workbench token."}, status=403)
            return False

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, markup: str, *, status: int = 200) -> None:
            body = markup.encode("utf-8")
            self.send_response(status)
            self._send_security_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'self'; connect-src 'self'; style-src 'nonce-{state.csp_nonce}'; script-src 'nonce-{state.csp_nonce}'; base-uri 'none'; frame-ancestors 'none'",
            )

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write(f"[flight-workbench] {self.address_string()} - {format % args}\n")

    return ThreadingHTTPServer((host, port), WorkbenchHandler)


def run_server(args: argparse.Namespace) -> int:
    config = load_config(args.config_path)
    token = secrets.token_urlsafe(32)
    state = WorkbenchState(config=config, operator_token=token, csp_nonce=make_csp_nonce())
    server = build_http_server(args.host, args.port, state)
    url = f"http://{url_host(args.host)}:{args.port}/"
    print(f"Flight Attendant Workbench listening at {url}")
    print(f"Config file: {config.config_path}")
    print("Operator token is held in memory and required for button actions.")
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Flight Attendant Workbench.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the localhost-only workbench.")
    serve.add_argument("--host", default="127.0.0.1", help="Loopback bind address.")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Workbench port.")
    serve.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH, help="Ignored local JSON config path.")
    serve.add_argument("--open-browser", action="store_true", help="Open the workbench in your default browser.")
    serve.set_defaults(func=run_server)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except WorkbenchError as exc:
        print(f"Flight workbench error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
