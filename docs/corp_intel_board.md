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

Aid calls are marked `critical`. Hostile reports with systems are marked `high`.

## Privacy Notes

- Keep channel allowlists narrow.
- Do not use `--all-channels` unless everyone understands what is being shared.
- The default agent sends only matching intel events, not every chat line.
- Use `--pilot` labels that your corp members are comfortable showing on the board.
