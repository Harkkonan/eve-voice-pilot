# Flight Attendant Workbench

Flight Attendant Workbench is a local-only operator panel for the Corp Market / Flight Attendant development and public-site update workflow.

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

Create that file when you want the public-site update button to work:

```json
{
  "ssh_host": "YOUR_VM_PUBLIC_IP",
  "ssh_user": "ubuntu",
  "ssh_key_path": "C:\\Users\\YOUR_WINDOWS_USER\\.ssh\\YOUR_OCI_KEY.key",
  "local_app_host": "127.0.0.1",
  "local_app_port": 8770,
  "vm_app_dir": "/home/ubuntu/apps/eve-voice-pilot",
  "vm_public_base_url": "https://market.brianridderbusch.net"
}
```

The workbench does not show this key path in action logs, and it does not store SSO client secrets, Discord webhooks, admin tokens, EVE access tokens, or refresh tokens.

Action history is stored locally in:

```text
profiles/flight_workbench_actions.jsonl
```

## Public Hosting Config

The Configuration panel can save non-secret public-hosting settings in the same ignored local config file:

- public base URL, such as `https://market.brianridderbusch.net`
- SSO callback URL, such as `https://market.brianridderbusch.net/flight/callback`
- allowed EVE character IDs
- allowed corporation IDs
- allowed alliance IDs
- allow-any-authenticated mode
- public-hosting mode on/off
- trusted member market-write mode on/off

Use `Save Public Config` after editing those fields. `Start Local Site` passes the saved public-hosting settings into the local Corp Market process, so you do not need to paste those non-secret public variables into PowerShell for normal workbench-started local testing. SSO client ID and secret are still read from your private Windows User environment or process environment and are intentionally not edited by the Workbench.

The public deploy button assumes the VM `.env` and Docker Compose deployment are already configured. It does not rotate secrets or edit the EVE Developer application for you.

Keep the EVE Developer portal callback in sync manually. If the saved callback URL is:

```text
https://market.brianridderbusch.net/flight/callback
```

then the EVE Developer application must use that exact callback URL.

## Button Boundaries

The simplified workbench exposes only fixed, allowlisted actions:

- `Verify Local`: runs local Git status, `git diff --check`, static cache diagnostics, and local-site health. If the local site is not running, this tells you to start it before browser testing instead of failing the whole verification.
- `Start Local Site`: starts Corp Market through `scripts/run_corp_market.ps1`.
- `Stop Local Site`: stops the managed local site or a recognized stale local Corp Market listener.
- `Update market.brianridderbusch.net`: connects to the configured VM over SSH, refuses dirty VM worktrees, runs `git pull --ff-only`, rebuilds/restarts `corp-market` and `caddy` with Docker Compose, checks VM-local `/api/health`, and runs the public smoke script against the public URL.
- `View Deploy Logs`: reads recent Docker Compose logs for `corp-market` and `caddy` on the VM.

The workbench does not accept arbitrary shell commands from the browser.

## Routine Public Site Update

After a local change has been tested, committed, pushed to GitHub, and is ready for the public site, use:

```text
Verify Local
Start Local Site
Update market.brianridderbusch.net
```

Use `View Deploy Logs` only when the deploy button reports a problem or the public site behaves unexpectedly.

`Update market.brianridderbusch.net` refuses to continue if the VM checkout has uncommitted local changes. It uses `git pull --ff-only`, so it also refuses merge commits or conflict resolution on the VM. The button updates the Docker Compose deployment and then verifies both the VM-local health endpoint and the public HTTPS site.

## Manual-Only Work

Keep these outside the workbench for now:

- SSO client secret entry or rotation.
- Discord webhook entry or rotation.
- EVE Developers portal callback changes.
- Oracle instance creation, termination, VCN changes, firewall rules, and security list edits.
- First SSH host-key trust prompts.
- Git push, Git reset, cleanup commands, and any Git operation other than the fixed VM fast-forward update button.
- DNS, Cloudflare zone settings, Caddyfile changes, and first-time Docker/Compose installation.

## Security Notes

This is a local operator tool. Do not expose it publicly.

POST actions require an in-memory operator token embedded only in the served local page. The server also refuses non-loopback binds and local browser requests from non-loopback clients. Action logs redact common secret shapes and configured sensitive environment values.

The served page uses a per-run Content Security Policy nonce for its local script and style tags. It also denies framing. This does not make the tool safe for public exposure; it only reduces damage from accidental local HTML/script injection.
