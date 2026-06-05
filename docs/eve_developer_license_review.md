# EVE Developer License Review

Last verified: 2026-06-05

Authoritative source:

- EVE Developer License Agreement: https://developers.eveonline.com/license-agreement
- EVE developer license docs: https://developers.eveonline.com/docs/resources/license/
- EVE SSO docs: https://developers.eveonline.com/docs/services/sso/

This file is a project review aid, not legal advice and not a substitute for the live agreement. Re-open the official source before each meaningful release, public hosting change, monetization change, or new ESI/SSO capability.

## Review Cadence

- Review before creating or changing an EVE developer application.
- Review before sharing the board outside a small trusted test group.
- Review before adding new ESI scopes, storing tokens, using CCP marks/assets, or changing privacy behavior.
- Review at least quarterly while the project is actively used.

## Current Project Posture

The corp intel board and hosted Flight Attendant/Corp Market surfaces are designed to stay on the conservative side of CCP's third-party-tool rules:

- It is read-only with respect to the EVE client.
- It does not send keys, click, read packets, scrape cache files, inspect process memory, or automate gameplay.
- It reads opted-in local chat logs and sends only matching intel events by default.
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
- Do not add reaction alerts, overlay control, client input, OCR-driven gameplay decisions, or bot-like behavior.

## Review Log

- 2026-06-03: Created project review doc from the official EVE Developer License Agreement and EVE developer docs. Current corp intel board design remains read-only, opt-in, non-commercial, and SSO/ESI based.
- 2026-06-05: Re-opened the official EVE Developer License Agreement, EVE SSO docs, ESI rate-limit docs, and EVE third-party policy page for Flight Attendant public-hosting hardening. The implementation remains member-gated, non-commercial, read-only/advisory, and manual for all in-game actions.
