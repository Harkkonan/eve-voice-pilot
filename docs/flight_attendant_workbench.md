# Flight Attendant Workbench

Flight Attendant Workbench is a local-only operator panel for managing the Corp Market / Flight Attendant development and VM workflow.

It is not a corp-facing page. It binds to `127.0.0.1` by default and should not be put behind a tunnel, reverse proxy, router port forward, or public hosting setup.

## Start It

Double-click:

```powershell
Start-EveFlightWorkbench.bat
```

Or run:

```powershell
.\scripts\run_flight_workbench.ps1 serve --open-browser
```

The default workbench URL is:

```text
http://127.0.0.1:8790/
```

## Local Config

VM and SSH details are read from an ignored local file:

```text
profiles/flight_workbench.local.json
```

Create that file when you want VM buttons to work:

```json
{
  "ssh_host": "YOUR_VM_PUBLIC_IP",
  "ssh_user": "ubuntu",
  "ssh_key_path": "C:\\Users\\YOUR_WINDOWS_USER\\.ssh\\YOUR_OCI_KEY.key",
  "local_app_host": "127.0.0.1",
  "local_app_port": 8770,
  "tunnel_local_port": 8770,
  "tunnel_remote_host": "127.0.0.1",
  "tunnel_remote_port": 8770,
  "vm_app_dir": "/home/ubuntu/apps/eve-voice-pilot",
  "vm_service_name": "eve-flight.service"
}
```

The workbench does not show this key path in action logs, and it does not store SSO client secrets, Discord webhooks, admin tokens, EVE access tokens, or refresh tokens.

Action history is stored locally in:

```text
profiles/flight_workbench_actions.jsonl
```

## Button Boundaries

The first version allows fixed, allowlisted actions only:

- Start or stop the local Corp Market server through `scripts/run_corp_market.ps1`.
- Start or stop a managed SSH tunnel from the saved config.
- Check local `/api/health` and `/api/flight/diagnostics`.
- Check static cache preflight.
- Run local `git status --short --branch` and `git diff --check`.
- Run fixed SSH checks for VM health, service status, service restart, service logs, and VM Git status.

The workbench does not accept arbitrary shell commands from the browser.

## Manual-Only Work

Keep these outside the workbench for now:

- SSO client secret entry or rotation.
- Discord webhook entry or rotation.
- EVE Developers portal callback changes.
- Oracle instance creation, termination, VCN changes, firewall rules, and security list edits.
- First SSH host-key trust prompts.
- Git push, Git pull, Git reset, and cleanup commands.
- Public hosting and tunnel-token setup.

## Security Notes

This is a local operator tool. Do not expose it publicly.

POST actions require an in-memory operator token embedded only in the served local page. The server also refuses non-loopback binds and local browser requests from non-loopback clients. Action logs redact common secret shapes and configured sensitive environment values.
