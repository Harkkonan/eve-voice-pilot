# Corp Intel Board

Retired: 2026-06-13

The Corp Intel Board dashboard, shared server, and remote upload agent are retired. The launcher, PowerShell script, and direct Python module entrypoint now stop with a retirement notice instead of starting the old dashboard or upload agent.

No new local, LAN, or public Corp Intel Board deployment should be started from this repository. Do not publish old setup instructions, archived chat-log-derived data, watchlists, pilot registries, ingest tokens, SSO secrets, or retained event databases.

The `src/eve_voice_pilot/corp_intel.py` module may still exist because EVE Intel Pet and Flight Attendant import shared helper types and parsing utilities from it. Those compatibility imports do not make the Board an active feature.

See `docs/retired_features.md` for the retirement record.
