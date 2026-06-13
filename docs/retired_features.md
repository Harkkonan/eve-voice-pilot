# Retired Features

## Corp Intel Board

Retired: 2026-06-13

The Corp Intel Board dashboard, shared server, and remote upload agent are removed from the active product surface. The BAT/PowerShell launchers and direct Python module entrypoint now stop with a retirement notice instead of starting a dashboard or upload agent.

The shared `src/eve_voice_pilot/corp_intel.py` module may remain in the tree because EVE Intel Pet and Flight Attendant import chat-log parsing, SSO, and small compatibility helpers from it. Those imports do not make the Board an active feature.

Do not restart, republish, or document a new shared chat-log board without fresh owner approval plus privacy and EVE policy review.

## Chatlog Knowledge Base

Retired: 2026-06-05

The generated `docs/chatlog-knowledge/` static site, its generator script, and its tests were removed from the active project because the output was derived from local EVE chat logs.

Any preserved copy should stay local under ignored `local_archives/`. Do not commit or publish archived chat-log-derived material without fresh owner approval plus privacy and EVE policy review.

The later Corp Intel Board retirement is tracked above. Shared helper code may remain for other tools, but the Board itself is no longer an active surface.
