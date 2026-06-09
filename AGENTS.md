# EVE Voice Pilot Agent Instructions

This repo is a Windows-first Python project for EVE Online helpers:

- `EVE Voice Pilot`: a cautious voice-command app that maps one spoken command to one key or key chord.
- `Corp Intel Board`: an opt-in, read-only chat-log intel dashboard.
- `Corp Market Concierge` / `Flight Attendant`: Discord-friendly corp coordination and read-only ESI planning helpers.
- `Trade Agent`: EVE Workbench and ESI-backed route/trade recommendations.

These instructions are for Codex and other coding agents working in this repository.

## Public-Release Posture

Build as if the repository may be published publicly.

- Keep the project non-commercial unless the owner has completed a fresh policy review.
- Do not commit secrets, API keys, Discord webhooks, SSO client secrets, access tokens, private chat logs, generated chat-log artifacts, local archives, generated caches, local SQLite databases, local settings, downloaded models, or personal EVE profile files.
- Treat `docs/eve_developer_license_review.md` as a review checklist, not as the source of truth.
- Re-open the live CCP/EVE policy pages before each meaningful release, public hosting change, monetization change, new ESI/SSO scope, new client-input capability, or feature that changes privacy behavior.
- Record meaningful policy checks in `docs/eve_developer_license_review.md` with the review date and reason.
- Preserve notices and avoid implying CCP endorsement when using EVE, CCP, or related names, data, marks, or imagery.

Useful official references:

- EVE Developer License Agreement: https://developers.eveonline.com/license-agreement
- EVE developer license docs: https://developers.eveonline.com/docs/resources/license/
- EVE third-party policies: https://support.eveonline.com/hc/en-us/articles/8564030965660-Third-Party-Policies

## Safety Boundaries

This project should stay on the conservative side of EVE third-party-tool rules.

- Voice commands must remain one spoken command to one key or key chord.
- Do not add timed chains, stored rapid keystroke patterns, input broadcasting, multi-client automation, mouse movement/click automation, or bot-like gameplay loops.
- Do not read EVE process memory, scrape cache files, inspect packets, reverse engineer the client, or modify the game client.
- Do not add screen-reading features for EVE gameplay state such as stopped, moving, warping, targets, overview state, or timers unless the owner explicitly requests a fresh policy and design review.
- Do not add automatic in-game mail, contracts, market orders, asset moves, fleet actions, warps, targeting, module cycling, or other gameplay actions.
- Keep key sending guarded by the active-window check unless the user explicitly chooses otherwise in a clear local setting.
- ESI and SSO features should use the minimum scopes needed, respect rate limits/cache behavior, verify identity where applicable, and avoid storing access/refresh tokens unless there is a specific reviewed reason.

## Privacy And Data Handling

- Prefer opt-in local operation over silent collection.
- Keep raw chat logs, private channel text, player-by-player transcripts, invite links, tokens, and personal paths out of committed docs and generated public artifacts.
- Public or shareable reports should be explicitly public-safe and should redact private links and sensitive details.
- Store local operational state under ignored paths such as `profiles/*.sqlite3`, `profiles/my_eve_commands.json`, `cache/`, `models/`, and `local_archives/`.
- If adding a new local state file, update `.gitignore` and verify it with `git status --ignored` or `git check-ignore` before the feature is considered done.

## Project Conventions

- Default shell is PowerShell on Windows.
- Prefer the repo's existing simple standard-library style before adding dependencies.
- Use `rg` / `rg --files` for search.
- Use `apply_patch` for manual edits.
- Keep user-facing text beginner-friendly and practical.
- Keep code comments sparse and useful.
- Stay inside the current checkout unless the user explicitly names another path.
- When offering Brian a teaching question or learning opportunity in this project, append it to the ignored local ledger at `local_archives/codex_learning_opportunities.md` with the question, work context, why it matters, relevant files or commands, and transcript lookup hints. Keep tracked docs public-safe.

## Common Commands

Setup:

```powershell
.\scripts\setup.ps1
```

Run the voice app:

```powershell
.\Start-EveVoicePilot.bat
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Focused test pass for voice-command core:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_core.py
```

Run `git diff --check` before committing documentation or code changes.

## Git Rules

- Explain Git status in plain language: `dirty` means there are local uncommitted changes, `ahead` means local commits are not pushed, and `behind` means remote commits are not present locally.
- Do not push, pull, reset, clean, rebase, or rewrite history unless the user explicitly asks.
- Before editing, inspect `git status --short --branch`.
- If unrelated files are dirty, leave them untouched and unstaged.
- Stage only files that belong to the completed task.
- Do not commit generated packages, local settings, logs, crash dumps, extracted game assets, private saves, secrets, failed-check work, unclear WIP, or changes whose ownership is unclear.
- After a meaningful completed slice, run appropriate checks and create a local checkpoint commit if the staged change is safe and self-contained.

## Area Notes

- `src/eve_voice_pilot/app.py`, `commands.py`, `input_sender.py`, `local_transcription.py`, `transcription.py`, and `speech_responses.py` are the core voice app surface.
- `profiles/eve_voice_standard.json`, `data/eve_voice_keybind_standard.csv`, and `docs/eve_voice_keybind_standard.md` should stay in sync when changing default voice commands or recommended EVE keybinds.
- `src/eve_voice_pilot/corp_intel.py` should remain read-only against the EVE client and opt-in for pilots.
- `src/eve_voice_pilot/corp_market.py` contains the corp market concierge and Flight Attendant helpers; keep Discord/ESI flows explicit, manual where gameplay handoff is involved, and careful with tokens.
- The old `docs/chatlog-knowledge/` static site, generator, and tests were retired on 2026-06-05 because they were derived from local EVE chat logs. Preserve old copies only in ignored `local_archives/`; do not reintroduce chat-log-derived public artifacts without fresh privacy/policy review and owner approval.
