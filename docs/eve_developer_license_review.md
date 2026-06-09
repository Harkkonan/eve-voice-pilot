# EVE Developer License Review

Last verified: 2026-06-08

Authoritative source:

- EVE Developer License Agreement: https://developers.eveonline.com/license-agreement
- EVE developer license docs: https://developers.eveonline.com/docs/resources/license/
- EVE third-party policies: https://support.eveonline.com/hc/en-us/articles/8564030965660-Third-Party-Policies
- EVE SSO docs: https://developers.eveonline.com/docs/services/sso/
- ESI API explorer: https://developers.eveonline.com/api-explorer
- ESI Swagger spec: https://esi.evetech.net/latest/swagger.json

This file is a project review aid, not legal advice and not a substitute for the live agreement. Re-open the official source before each meaningful release, public hosting change, monetization change, or new ESI/SSO capability.

## Review Cadence

- Review before creating or changing an EVE developer application.
- Review before sharing the board outside a small trusted test group.
- Review before adding new ESI scopes, storing tokens, using CCP marks/assets, or changing privacy behavior.
- Review at least quarterly while the project is actively used.

## Current Project Posture

The corp intel board and hosted Flight Attendant/Corp Market surfaces are designed to stay on the conservative side of CCP's third-party-tool rules:

- It is read-only with respect to the EVE client.
- It does not send keys, click, read the EVE screen, read packets, scrape cache files, inspect process memory, or automate gameplay.
- It reads opted-in local chat logs and sends only matching intel events by default.
- The EVE Intel Pet overlay is local-only and informational: it reads the user's own EVE chat logs, shows matching lines to that user, and does not share them by default.
- Optional Intel Pet Discord voice notes are explicit user-authored note sends only, use a dedicated local ignored webhook settings file, disable Discord mentions, and do not automatically forward chat alerts.
- The Intel Pet alert history is in-memory only for the current pet run and is not written to disk or shared with Discord, ESI, or the corp intel board.
- Optional Intel Pet location cheer uses only the read-only `esi-location.read_location.v1` scope after explicit EVE SSO consent, keeps the access token in memory only while the pet is running, and shows local animation only.
- Optional Intel Pet voice command sending is local-only, off by default, requires explicit user opt-in, uses exact matches from the existing EVE Voice Pilot command profile, and keeps the active EVE-window guard on by default.
- It uses EVE SSO to prove character ownership and public ESI to check corporation/alliance membership.
- It signature-verifies SSO access tokens and discards EVE access/refresh tokens after login.
- It stores local operational records only: verified pilot identity, token hashes, watchlist settings, and sanitized intel events.
- The hosted Flight Attendant remains advisory only: it can read opted-in ESI data and public market orders, but the pilot performs all in-game hauling, buying, selling, contracts, and mail manually.
- Public Flight Attendant hosting should use HTTPS, EVE SSO, and a configured corporation/alliance allowlist.
- It should remain non-commercial unless a future review confirms the exact monetization method is allowed.

## License Topics To Re-check

### Application Purpose

Confirm the tool still supports EVE players' use of EVE and does not drift into unrelated data use, gambling, betting, raffles, lotteries, sweepstakes, or similar activity.

### Player Consent And Privacy

Confirm each pilot intentionally runs the local agent and understands which channels and matching terms are shared. Avoid full-log collection unless there is a separate explicit consent review.

### No Cheating Or Abuse

Confirm the tool is not being used for phishing, spam, malware, item theft, scams, denial-of-service behavior, hidden player tracking, or gameplay automation.

### Non-commercial Use

Confirm the project is not charging access fees, gating premium features, selling EVE-derived products, or otherwise monetizing outside the agreement's allowed boundaries.

### ESI And SSO Use

Confirm the app uses only needed scopes, verifies SSO tokens, keeps the client secret private, respects ESI rate limits/cache guidance, and keeps the developer account email current for CCP notices.

### CCP Marks And Notices

If the project uses EVE, CCP, or related logos/images/marks in the UI or docs, confirm the required notices and branding limits still apply. Avoid implying CCP endorsement.

## Current Safe Defaults

- Prefer empty SSO scopes for identity-only login.
- Prefer `--require-sso-dashboard` for shared boards.
- Prefer `--require-verified-ingest` plus per-pilot agent tokens over a shared ingest token.
- Keep channel allowlists narrow.
- Keep the board private to the corporation unless a broader review is done.
- Keep alert overlays informational and local-only unless there is a separate consent and policy review.
- Keep Discord note sharing opt-in, deliberate, and separate from automatic chat alert forwarding.
- Do not add automatic overlay-driven client input, screen-reading gameplay decisions, bot-like behavior, input broadcasting, stored rapid keystroke patterns, or automated reactions.
- Keep voice-driven client input off by default, exact-command only, active-window guarded by default, and manually configured by the local user.

## Review Log

- 2026-06-03: Created project review doc from the official EVE Developer License Agreement and EVE developer docs. Current corp intel board design remains read-only, opt-in, non-commercial, and SSO/ESI based.
- 2026-06-05: Re-opened the official EVE Developer License Agreement, EVE SSO docs, ESI rate-limit docs, and EVE third-party policy page for Flight Attendant public-hosting hardening. The implementation remains member-gated, non-commercial, read-only/advisory, and manual for all in-game actions.
- 2026-06-05: Re-opened the official EVE Developer License Agreement, EVE ESI overview/best-practices docs, and EVE third-party policy page before adding public market-history-backed acquisition planning. The planner uses public market orders/history, adds no new SSO scope, caches ESI responses, and remains advisory/manual only.
- 2026-06-05: Re-opened the official EVE Developer License Agreement, developer license docs, and ESI Swagger/API docs before adding the ore reprocessing calculator. The feature adds read-only implant and structure-info scopes, keeps tokens in server memory only, uses SDE cache data for ore/station constants, and remains advisory/manual only.
- 2026-06-05: Re-opened the official EVE Developer License Agreement, developer license docs, EVE third-party policy page, and ESI Swagger/API docs before adding Trade P&L. The feature adds a read-only wallet scope, analyzes recent wallet transactions and market fee journal rows only after pilot SSO consent, keeps tokens in server memory only, and remains advisory/manual only.
- 2026-06-05: Re-opened the official EVE Developer License Agreement, developer license docs, EVE third-party policy page, API Explorer, and ESI overview/best-practices docs before adding standings-ranked NPC reprocessing station selection. The feature reuses existing read-only location, skills, standings, and implant scopes, stores no standings or station-choice data beyond browser local storage, uses SDE cache station constants, and remains advisory/manual only.
- 2026-06-05: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding EVE Intel Pet. The first slice is local-only, reads the user's own EVE chat logs, shows informational alerts for matching new lines, adds no ESI scope, shares nothing by default, and does not control the EVE client.
- 2026-06-06: Re-opened the official EVE Developer License Agreement, developer license docs, EVE SSO docs, EVE third-party policy page, ESI rate-limit/best-practices docs, and the live ESI Swagger spec before adding optional Intel Pet location cheer. The feature adds only `esi-location.read_location.v1`, polls slowly, keeps tokens in memory only, shares no location data, and remains local informational animation only.
- 2026-06-06: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet alert history and a sleeker local overlay. The history is in-memory only, capped to recent alerts, cleared when the pet closes, and remains local informational display only.
- 2026-06-06: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding opt-in Intel Pet voice command sending. The feature is local-only, disabled by default, requires exact spoken command matches from the existing EVE Voice Pilot profile, sends only the configured key/chord for that command, keeps the active-window guard enabled by default, and does not add screen-reading decisions, client memory/cache/packet access, input broadcasting, or automated reaction chains.
- 2026-06-06: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding the Intel Pet Voice Lab command editor. The editor saves local personal command-profile changes, keeps dry-run phrase tests from sending keys, preserves exact-match command behavior, and does not add multi-step automation, input broadcasting, screen-reading decisions, or automatic gameplay reactions.
- 2026-06-06: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet Ballad Voice Studio. The feature is local voice-style tuning for spoken pet messages, caches only explicit preview/sample text in the ignored local speech cache, does not cache raw chat alerts or alert history, adds no ESI scope, and does not control the EVE client.
- 2026-06-06: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet Phrase Trainer. The feature keeps recent heard voice phrases in memory only for the current pet run, requires a manual click to add a phrase to a selected local command, preserves exact-match command behavior, and does not add fuzzy automation, input broadcasting, screen-reading decisions, or automatic gameplay reactions.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet Recognition Lab. The feature records one local diagnostic phrase on demand, displays transcript, volume, microphone, model, grammar, and exact-match details in the local options window only, does not save audio or transcripts, adds no ESI scope, and does not control the EVE client.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet local recognition model selection. The feature only changes which local Vosk model the opt-in listener uses, stores the selected model path in ignored local settings, adds no ESI scope, does not save audio or transcripts, and does not change exact-match command or active-window guard behavior.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet per-alert spoken-message controls. The feature only lets the local user mute or allow spoken playback by alert type, reduces accidental local disclosure of alert text, adds no ESI scope, shares nothing externally, and does not control the EVE client.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet settings export/import. The feature exports only cleaned local Intel Pet settings, imports only after manual file selection, excludes raw chat logs, alert history, EVE SSO tokens, Discord webhooks, and Voice Lab command profiles, adds no ESI scope, shares nothing externally, and does not control the EVE client.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet Diagnostics. The feature shows a local troubleshooting summary of settings, watched folders, filters, voice configuration, ESI location status, and in-memory counts; it excludes raw chat lines, alert message text, tokens, webhooks, and recordings, adds no ESI scope, shares nothing externally, and does not control the EVE client.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before documenting the Intel Pet Discord messaging plan. The plan is not a runtime integration; it requires future opt-in routes, summary-only defaults, explicit matched-text toggles, no silent raw-log forwarding, no committed tokens or webhook URLs, no new ESI scope, and no EVE client control.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before changing the Corp Market first tab toward a Discord alert-router settings surface. This first slice is planning-only in the browser plus summary-only payload helpers; it does not enable automatic Discord sending, adds no ESI scope, keeps market webhook secrets out of committed files, disables Discord mentions, and preserves the no raw-log-forwarding and no EVE client control boundaries.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, EVE SSO docs, and EVE third-party policy page before adding generated Flight Attendant scope justifications and per-tab scope disclosures. The change adds no new ESI scope or gameplay behavior; it makes current read-only scope use clearer by tab and preserves manual pilot action for hauling, buying, selling, reprocessing, and market orders.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding the Shared Fittings tab. The change stores user-pasted EVE fitting clipboard blocks and optional website fitting links in ignored local SQLite data, adds no ESI scope, does not read the EVE client, and preserves manual copy/import behavior for pilots.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before enabling persisted Discord alert-router settings and manual test sends from the Corp Market first tab. The feature saves only cleaned route/rule settings in ignored local JSON, uses the existing private webhook configuration for the default IntelPet sender, disables Discord mentions, keeps matched text opt-in, adds no ESI scope, does not automatically forward Intel Pet chat yet, and does not control the EVE client.
- 2026-06-07: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding Intel Pet Discord voice notes. The feature sends only deliberate voice-note text after an explicit note phrase, stores the notes-channel webhook in ignored local settings, disables Discord mentions, adds no ESI scope, does not automatically forward chat alerts, and does not control the EVE client.
- 2026-06-08: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page during a security/privacy release audit. The hardening removes stored repeated key sends and mouse-button voice sends, refuses non-loopback corp intel serving without SSO membership and ingest gates, keeps public Corp Market/Flight Attendant reads member-gated, and pins Intel Pet location bearer tokens to the official ESI host. The project remains non-commercial, read-only/advisory where ESI is involved, opt-in for local data sharing, and manual for all gameplay actions.
- 2026-06-08: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before removing the old screen-reading watcher. The removal deletes the runnable watcher modules, launchers, scripts, tests, README instructions, and screen-capture/text-recognition Python dependencies so EVE gameplay state is not read from the screen.
- 2026-06-08: Re-opened the official EVE Developer License Agreement, developer license docs, EVE third-party policy page, ESI overview, and ESI rate-limit docs before adding the Flight Attendant ESI Flight Recorder. The feature adds no ESI scope, changes no authentication behavior, stores no tokens or raw ESI responses, and shows only browser-session-local friendly summaries of recent authorized checks.
- 2026-06-08: Re-opened the official EVE Developer License Agreement, developer license docs, EVE third-party policy page, ESI overview, and ESI rate-limit docs before making reprocessing implant and structure-name scopes explicit opt-in. The normal Flight Attendant login now keeps `esi-clones.read_implants.v1` and `esi-universe.read_structures.v1` out of the default scope request, shows a separate reprocessing opt-in prompt with the exact scopes, stores no new token data, and remains read-only/advisory.
- 2026-06-09: Re-opened the official EVE Developer License Agreement, developer license docs, and EVE third-party policy page before adding manual Shared Fittings Discord forum posting. The feature stores the Fittings webhook only in ignored local JSON, sends only user-saved fitting clipboard blocks after an explicit button press, disables Discord mentions, adds no ESI scope, does not read the EVE client, and leaves all fitting import/simulation action manual in EVE.
