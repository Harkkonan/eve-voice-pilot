# Corp Intel Board

The corp intel board is a read-only dashboard for hostile reports and aid calls in EVE chat logs.

It does not modify the EVE client, send keys, read packets, scrape cache files, or automate gameplay. It only reads opted-in chat log files and turns matching lines into dashboard alerts.

## Local Board

Double-click:

```powershell
Start-EveCorpIntelBoard.bat
```

This starts a local dashboard at:

```text
http://127.0.0.1:8765/
```

The launcher watches these channel patterns by default:

```text
Corp, Corporation, Fleet, Alliance, Local, *Intel*
```

It starts at the end of existing log files, so only new chat lines are processed.

## Shared Corp Board

On the computer hosting the dashboard, run:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --host 0.0.0.0 --port 8765 --ingest-token "change-this-token"
```

Share the host computer's LAN IP address and the token only with corp members who should send intel.

On each opted-in corp member PC, run:

```powershell
.\scripts\run_corp_intel_board.ps1 agent --server "http://HOST-LAN-IP:8765" --token "change-this-token" --pilot "Pilot Name" --channels "Corp,Fleet,Alliance,Local,*Intel*"
```

The agent prints a visible status line showing which server, pilot label, and channel allowlist it is using.

Agents refresh the shared watchlist from the server every 60 seconds by default. This keeps the safer upload model: opted-in pilots send matching intel events, not their full chat logs, while the host can still update the hostile/help terms centrally.

## Dashboard Watchlists

The dashboard has editable watchlists for:

- Hostile pilot names.
- Hostile corporation names.
- Help callout phrases.
- Extra keywords.

Watchlist matches are applied to new chat lines as they arrive. Hostile pilot and corporation matches are marked `high`. Help callout matches are marked `critical`.

The server stores the live watchlist in:

```text
profiles/corp_intel_watchlist.json
```

That file is ignored by Git because it can contain operational corp intel.

The server also keeps recent intel events in a local SQLite database:

```text
profiles/corp_intel_events.sqlite3
```

This lets the board recover recent intel after a restart. By default it keeps seven days of events and the newest 500 events. The database stores sanitized event records, not raw chat logs and not the sender's local chat-log file path.

To change retention:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --retention-days 14 --max-events 1000
```

To run memory-only during a test:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --no-event-db
```

The host browser can edit the watchlist without a token. Remote browsers need an admin token:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --host 0.0.0.0 --port 8765 --ingest-token "change-this-token" --admin-token "change-this-admin-token"
```

## EVE SSO Identity

The board can use EVE SSO to verify dashboard users by character and check their public ESI corporation/alliance membership.

Register an EVE SSO web application in the EVE Developers portal and add this callback URL:

```text
http://HOST-LAN-IP:8765/auth/callback
```

Then run the server with the SSO application values and the corp or alliance ids that should count as trusted:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --host 0.0.0.0 --port 8765 --ingest-token "change-this-token" --sso-client-id "client-id" --sso-client-secret "client-secret" --sso-callback-url "http://HOST-LAN-IP:8765/auth/callback" --allowed-corporation-ids "123456789"
```

You can also use environment variables instead of putting SSO values in the command:

```text
CORP_INTEL_SSO_CLIENT_ID
CORP_INTEL_SSO_CLIENT_SECRET
CORP_INTEL_SSO_CALLBACK_URL
CORP_INTEL_ALLOWED_CORPORATION_IDS
CORP_INTEL_ALLOWED_ALLIANCE_IDS
```

Verified pilot records are stored locally in:

```text
profiles/corp_intel_pilots.sqlite3
```

This first SSO slice uses SSO only to prove character ownership and check current public corporation/alliance identity. It does not store EVE access tokens or refresh tokens.

By default, SSO-verified members can sign in and see their identity status, but remote watchlist edits still require the admin token. To let verified allowlisted members edit watchlists:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --trusted-members-can-edit-watchlist
```

## Safer Testing

Use dry run before uploading from a corp member PC:

```powershell
.\scripts\run_corp_intel_board.ps1 agent --server "http://HOST-LAN-IP:8765" --pilot "Pilot Name" --channels "Corp,Fleet" --dry-run
```

To process existing lines in selected logs for a quick test:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --watch-local --channels "Corp,Fleet" --read-existing
```

## What It Detects

The parser watches for:

- Solar system names from public ESI data.
- Hostile words such as `hostile`, `red`, `neut`, `war target`, `camp`, `bubble`, `cyno`, `dictor`, and `bombers`.
- Aid calls such as `need help`, `need reps`, `need logi`, `tackled`, `pointed`, `scrammed`, and `under attack`.
- Dashboard watchlist terms for hostile pilots, hostile corporations, help callouts, and extra keywords.

Aid calls are marked `critical`. Hostile reports with systems are marked `high`.

## Privacy Notes

- Keep channel allowlists narrow.
- Do not use `--all-channels` unless everyone understands what is being shared.
- The default agent sends only matching intel events, not every chat line.
- Uploaded events do not include the sender's local chat-log file path.
- The event database is local operational data; do not publish or commit it.
- The pilot registry is local operational data; do not publish or commit it.
- SSO tokens are used during login and then discarded. Do not add broad ESI scopes unless a future feature truly needs them.
- Use `--pilot` labels that your corp members are comfortable showing on the board.
- ESI does not provide chat logs; local opt-in agents still need to read each pilot's local chat-log files.
