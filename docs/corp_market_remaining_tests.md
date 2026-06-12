# Corp Market Remaining Tests

This is the named working checklist for the Corp Market Concierge / Flight Attendant remaining test slices.

Use this document when asking "what tests are left?", "what remaining slices do we have?", or "what should I test in game next?" Code validation and in-game/operator validation both matter. A slice is not fully trusted until the code path passes automated/browser checks and the EVE-facing workflow has been sanity-checked by a human pilot.

## Ground Rules

- Keep diagnostics data available for operator review, including `/api/flight/diagnostics` and the in-page static cache preflight.
- Do not put internal test directions back into the member-facing dashboard.
- Do not commit secrets, Discord webhooks, SSO client secrets, access tokens, local databases, generated cache files, or private chat logs.
- Treat every market, hauling, route, PI, reprocessing, and trade result as advisory. The pilot makes every in-game decision manually.
- Re-check CCP/EVE policy before public-release, monetization, new scopes, new token storage, or wider rollout changes.

## Slice Completion Standard

For each slice, aim for both:

- Code proof: focused unit tests, syntax check, `git diff --check`, and rendered browser/mobile smoke for affected pages.
- Pilot proof: a small manual EVE workflow check using real UI confirmation windows, conservative quantities, and no automatic in-game action.

## Remaining Test Slices

### 1. Public Hosting And SSO Gate

Code checks:

- Start with `--public-hosting-mode` and confirm the app refuses unsafe HTTP public URLs.
- Confirm configured callback equals `https://YOUR-DOMAIN/flight/callback`.
- Confirm allowlisted and non-allowlisted characters get the expected access behavior.
- Confirm secure cookies are used for HTTPS public hosting.
- Check `/api/health` and `/api/flight/diagnostics`.

Pilot checks:

- Sign in through EVE SSO from the public URL.
- Confirm the page identifies the correct character and does not expose private IDs.
- Confirm a non-allowed character cannot use the protected workflow.

### 2. Flight Attendant Briefing

Code checks:

- Verify location refresh, ESI Flight Recorder entries, scope disclosure text, and empty/error states.
- Confirm tokens, raw headers, raw ESI responses, character IDs, and private identifiers are not displayed.

Pilot checks:

- Sign in while docked or in a known system.
- Compare the displayed system with the EVE client.
- Confirm Captain's Notes remain local/browser-side and do not imply EVE client control.

### 3. Static Cache Readiness

Code checks:

- Confirm the static cache preflight reports each cache file: recipes, route graph, reprocessing, and planetary industry.
- Confirm missing cache messages include the refresh command and same-host requirement.

Operator checks:

- Run `python .\scripts\update_industry_recipe_cache.py` on the actual serving host.
- Restart or refresh the site and confirm cache readiness updates.

### 4. Industry Library

Code checks:

- Verify owned blueprint reads, asset summaries, recipe cache matching, buildability assumptions, buyer scans, and profitability ranking.
- Confirm blueprint/profitability functions are not present on the Flight Attendant tab.
- Confirm collapsible output panels behave with large result sets.

Pilot checks:

- Compare a few owned blueprints and material stacks against EVE inventory.
- Run one small buyer scan and manually verify the public buy orders in-game or on an external market reference.
- Check profitability output against a small known recipe before trusting larger lists.

### 5. Hauler Routes

Code checks:

- Verify live-location start, manual start override, destination selection, cargo cap, budget cap, route preference, and pod-kill fallback wording.
- Verify Quickbar and CSV exports.
- Verify route diagnostics still explain source, fallback, and pod-kill handling.

Pilot checks:

- Run a small route with a conservative cargo limit.
- Compare route, pickup systems, item volume, buy depth, and destination buy orders in EVE before moving cargo.
- Confirm no route output implies autopilot, warping, order placement, or contract creation.

### 6. Investment Portfolio

Code checks:

- Verify buy-order budget, broker-fee manual field, target fill window, history modes, item scope picker, warning labels, and hub comparison.
- Verify Possible trap, liquidity, buyer concentration, and competition-pressure language.
- Verify portfolio review output still works after public testing directions were removed.

Pilot checks:

- Use a small budget.
- Compare suggested bid, visible competing buy orders, recent history, and destination demand before placing anything.
- Record whether warnings were understandable before any ISK is risked.

### 7. Trade Asset Ledger

Code checks:

- Verify read-only ledger preview, managed container naming rules, ESI asset bridge, and hauler/portfolio handoff text.
- Confirm no hand-edit UI appears for managed rows.

Pilot checks:

- Create or identify containers using the documented naming patterns.
- Refresh ledger and compare rows with EVE assets.
- Confirm ready-to-haul labels match the actual container contents.

### 8. Bulk Appraisal

Code checks:

- Verify pasted fits, inventory lists, view-contents text, BPC warnings, unresolved lines, low-confidence rows, hub pricing, and Discord-safe export text.
- Confirm raw pasted text is not stored.

Pilot checks:

- Paste one known inventory sample.
- Compare quick-sell and replace/buy estimates against the selected hub.
- Confirm BPC and low-confidence warnings are easy to notice.

### 9. Planetary Industry

Code checks:

- Verify schematic ranking, chain planning, customs-fee math, tax field guide, Quickbar copy, and quote-check output.
- Confirm no colony-management or live EVE client control is implied.

Pilot checks:

- Use one small customs preview in EVE and compare it against the quote checker.
- Compare one simple P1/P2 chain against known planet or factory setup assumptions.
- Confirm manual tax fields match what the EVE UI shows.

### 10. Reprocessing

Code checks:

- Verify manual station/structure overrides, implant opt-in boundary, standings-based station choices, batch paste manifest, mineral output, and after-tax comparison.
- Confirm optional implant/structure scopes are only requested through explicit opt-in.

Pilot checks:

- Compare one small ore batch against an in-game reprocessing preview.
- Check a manual facility-yield override against the actual structure or station.
- Confirm the app does not claim it can see private structure names without opt-in.

### 11. Mining Yield

Code checks:

- Verify mining-ledger opt-in, cached ledger rows, manual session hours, local timer, and no live mining telemetry.
- Confirm the tab does not use inventory deltas, screen reading, laser cycle data, or EVE client state.

Pilot checks:

- Run a small manual mining block and record session time.
- Compare ledger totals after ESI cache delay.
- Confirm labels make it clear this is cached daily ledger math, not live tracking.

### 12. Trade P&L

Code checks:

- Verify wallet transaction/journal scope behavior, consideration rules, expected-vs-actual matching, open stock valuation, and exclusions.
- Confirm sensitive wallet details are summarized appropriately.

Pilot checks:

- Compare one known buy/sell pair with wallet history.
- Check taxes, fees, and remaining stock against EVE wallet and assets.
- Confirm the report is useful before relying on it for accounting.

### 13. Discord Workflows

Code checks:

- Verify market posts, direct Discord posts, fitting posts, alert routes, preview payloads, redacted webhook previews, and manual send controls.
- Confirm webhook URLs never appear in rendered UI, tests, docs, or committed files.

Operator checks:

- Send one controlled message to a private Discord destination.
- Confirm mentions are disabled unless explicitly intended.
- Confirm the posted text is clear enough for corp members.

### 14. Mobile And Browser Usability

Code checks:

- Desktop and mobile render without horizontal overflow.
- Workspace selector and tab rail both work.
- Large output panels stay collapsible and scrollable.
- No framework overlay or blank dashboard state.

Pilot checks:

- Open the public URL in at least two browsers.
- Try the main read-only flows on a phone-sized viewport.
- Note any labels, buttons, or tables that are hard to read before adding more features.

### 15. Performance And Bandwidth

Code checks:

- Confirm public ESI order/history/price calls use local server caches where intended.
- Confirm long scans show progress and can fail cleanly.
- Confirm broad market scans have safe limits.

Operator checks:

- Time a route scan, portfolio scan, buyer scan, and profitability scan.
- Record slow steps and whether results are still useful.
- Prefer narrower scans or cached summaries before adding more raw API calls.

## Parking Lot

- Role-gated corporation or alliance administration mode.
- Persistent token storage, only after a separate security review.
- Corp-owned structure market pricing, only with a clear scope/access design.
- Signed-in PI colony layout reads, only with careful stale-data warnings.
- A polished public onboarding/help page outside the main app, if corp members need it.
