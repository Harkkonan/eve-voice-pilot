# Corp Market Concierge

The corp market concierge is a Discord-friendly buy/sell board for corporation members.

It is designed to coordinate human trades, not automate EVE. It does not send keys, click the EVE client, create contracts, place orders, read packets, scrape cache files, or send EVE mail automatically. Buyers and sellers copy the generated mail draft, then send mail, trade, or contract manually in EVE.

Review `docs/eve_developer_license_review.md` before public hosting, monetization, new ESI scopes, or wider rollout.

## Local Board

Double-click:

```powershell
Start-EveCorpMarket.bat
```

This starts the local concierge at:

```text
http://127.0.0.1:8770/
```

The board stores listings in ignored local SQLite data:

```text
profiles/corp_market.sqlite3
```

## Flight Attendant Tab

The local board now includes a `Flight Attendant` tab beside the market board.

The first version is a safe briefing surface:

- It stores captain's notes in the browser only.
- It can use read-only ESI location, assets, and blueprints to show the connected pilot's current system, owned blueprint summary, and material stacks.
- It can compare owned blueprint type IDs with a local static recipe cache before market pricing is added.
- It can use a local SDE route graph to show systems within the selected jump range of the current ESI location.
- It can scan public ESI buy orders for products made by owned blueprints and filter those buyer orders to the selected jump range.
- It includes a `Hauler Routes` tab that compares cheap public material sell orders on or near a selected route with higher public buy orders in the destination system.
- It includes a `Market Acquisition Planner` tab that compares public buy/sell orders with public market history before suggesting public buy-order ceilings, first-order size, and collection range.
- It includes a `Trade P&L` tab that reads recent wallet transactions and wallet journal fee rows to match visible buys and sells into item-level profit, loss, open stock, unmatched sells, and optional matched transaction rows. The tab can narrow history from 1 hour to 30 days and can exclude selected items from the considered income total while still showing their real result.
- It includes an `Ore Reprocessing` tab that uses ESI location, skills, standings, and implants plus local SDE ore data to estimate mineral output from a typed ore amount.
- It includes a `Planetary Industry` planner for comparing PI schematics, material chains, market prices, planet availability, and customs transfer costs before a pilot moves goods manually.
- It keeps disabled placeholders for briefing generation until additional scopes and storage are reviewed.
- It does not warp, click, press keys, create contracts, place orders, read packets, scrape cache files, or react to OCR.
- It keeps the first ESI access token in server memory only; no refresh token or token file is stored by this version.

Treat future Flight Attendant work like a crew member giving advice: the tool can brief the pilot, but the pilot takes every in-game action manually.

### Flight Attendant ESI Setup

Register an EVE SSO web application in the EVE Developers portal with this callback URL for the default local board:

```text
http://127.0.0.1:8770/flight/callback
```

Request these scopes:

```text
esi-location.read_location.v1
esi-assets.read_assets.v1
esi-characters.read_blueprints.v1
esi-skills.read_skills.v1
esi-characters.read_standings.v1
esi-clones.read_implants.v1
esi-universe.read_structures.v1
esi-wallet.read_character_wallet.v1
```

The wallet scope is used by `Trade P&L` for recent market transactions and related market fee rows. The server still keeps the access token in memory only, and the tab does not place, edit, cancel, or update any market orders. Trade P&L exclusions change only the local considered income summary; excluded rows remain visible with their actual profit or loss.

The first Planetary Industry planner slice does not need a new ESI scope because it uses public market data, static SDE schematics, and manual tax settings. Add `esi-planets.manage_planets.v1` only for a later signed-in colony import mode, and label ESI colony data as potentially stale because EVE only refreshes colony layout information after the pilot views the colony in the client. Add `esi-planets.read_customs_offices.v1` only for a later corporation customs-office mode; that endpoint requires Director role and should not be part of the normal member flow.

Start the market board with your SSO app credentials, either through environment variables:

```powershell
$env:CORP_MARKET_SSO_CLIENT_ID = "client-id"
$env:CORP_MARKET_SSO_CLIENT_SECRET = "client-secret"
.\scripts\run_corp_market.ps1 serve --open-browser
```

Or directly on the command line:

```powershell
.\scripts\run_corp_market.ps1 serve --sso-client-id "client-id" --sso-client-secret "client-secret" --open-browser
```

Do not commit SSO secrets. Keep them in your local shell, Windows environment, or another private secret store.

### Public Flight Attendant Hosting

For a corp-clickable Flight Attendant link, use HTTPS, EVE SSO, and a corporation or alliance allowlist. Re-open the live CCP/EVE policy pages before sharing the link beyond a small trusted test group.

Register the hosted callback URL in the EVE Developers portal:

```text
https://YOUR-DOMAIN-OR-TUNNEL/flight/callback
```

Then start the local server behind your HTTPS tunnel or reverse proxy:

```powershell
$env:CORP_MARKET_SSO_CLIENT_ID = "client-id"
$env:CORP_MARKET_SSO_CLIENT_SECRET = "client-secret"
$env:CORP_MARKET_PUBLIC_BASE_URL = "https://YOUR-DOMAIN-OR-TUNNEL"
$env:CORP_MARKET_SSO_CALLBACK_URL = "https://YOUR-DOMAIN-OR-TUNNEL/flight/callback"
$env:CORP_MARKET_ALLOWED_CORPORATION_IDS = "123456789"
.\scripts\run_corp_market.ps1 serve --public-hosting-mode
```

Public hosting mode refuses to start unless the public base URL and callback URL use HTTPS, EVE SSO is configured, and at least one allowed corporation or alliance id is present. Flight Attendant access tokens remain in server memory only; no refresh token or token file is stored by this version.

Use the diagnostics endpoint after startup:

```text
https://YOUR-DOMAIN-OR-TUNNEL/api/flight/diagnostics
```

The Flight Attendant tab also shows a **Static Cache Preflight** panel. Check it before inviting testers or after deploying a new server/container. If it reports a missing cache, run the cache refresh on the same host that serves the website, then restart or refresh the page.

Remote market listing writes are locked down in public hosting mode. Add an admin token for operator-only writes, or add `--trusted-members-can-write-market` if allowlisted EVE SSO members should be able to create, reserve, and update market listings from the shared site:

```powershell
$env:CORP_MARKET_ADMIN_TOKEN = "change-this-token"
.\scripts\run_corp_market.ps1 serve --public-hosting-mode
```

### Local Static Data Caches

Flight Attendant uses ESI to learn your actual location, owned blueprints, materials, reprocessing skills, standings, and implants. It uses CCP's Static Data Export for blueprint recipes, jump-aware route math, ore portions, mineral outputs, and NPC station reprocessing values. Build or refresh the local static caches from PowerShell:

```powershell
python .\scripts\update_industry_recipe_cache.py
```

Run this command on the actual web host, VM, or container that is serving the site. The generated files are ignored local data, so pushing Git commits does not copy them to a separate host. A missing `cache\eve_planetary_industry.json` will make the Planetary Industry tab report `Planetary cache file is missing.` until the refresh runs there.

The generated caches are written to ignored local data:

```text
cache\eve_industry_recipes.json
cache\eve_route_graph.json
cache\eve_reprocessing.json
cache\eve_planetary_industry.json
```

If these caches are missing, the Flight Attendant tab still requires ESI and will show the data it can safely fetch, but recipe matching, buildability previews, jump-aware nearby system coverage, ore reprocessing estimates, and planetary schematic and chain planning will stay unavailable. Refresh the cache after updates that add static fields such as `volume_m3`, `max_production_limit`, required skills, job time, ore portions, station reprocessing values, PI schematic inputs and outputs, PI commodity tiers, customs-tax base values, or SDE planet records.

### Planetary Industry Planner

Planetary Industry support is being added one safe layer at a time. The Flight Attendant tab can rank PI schematics from the SDE against public market orders and manual tax assumptions. The current strategy layer shows a plain-language summary, profitable-only and price-check filters, profit-per-day ranking, an input shopping list, output sell targets, and a separate import/export customs breakdown.

The material-chain target field can walk a target such as `Microfiber Shielding` or `Viral Agent` backward through PI schematics. The chain view shows P1/P0 requirements for one target schematic batch, which planet types provide the P0 resources, buy-direct cost, make-from-bought-inputs value, output sell target value, and customs fees for each material movement. Same-planet notes show when every raw input can be sourced on one planet type and how much intermediate transfer customs can be avoided.

The Planning Rules panel includes a `Tax Field Guide` for Owner tax %, NPC tax %, Customs Code Expertise, Sales tax %, and Broker fee %, plus a collapsible `PI Field Answers` guide with 20 common operational reminders. The field guide explains where to find the rates in game and what can or cannot be auto-filled later from ESI. ESI can help with signed-in location, public market prices, and character skill levels such as Accounting or Customs Code Expertise; arbitrary planet owner POCO tax and exact broker fee for a chosen order location should remain manual unless a later role-gated corporation customs-office mode is added.

The planner uses these public-data planning modes and leaves signed-in colony layout as future work:

- manual public-data mode: choose a hub, output tier, and tax settings, then compare PI schematics using public market orders and SDE schematic data;
- factory-planet mode: treat all inputs as bought/imported and all outputs as exported/sold;
- extraction or hybrid mode: compare buying inputs with making lower-tier inputs yourself, while still showing the opportunity value of self-supplied materials;
- signed-in colony mode later: read colonies with `esi-planets.manage_planets.v1`, while warning that ESI colony layout data may be stale until the pilot opens the colony in EVE.

Profit math must always show customs movement cost, not hide it inside a generic fee. Use this shape:

```text
net profit = output sale value - input value - import customs cost - export customs cost - sales tax - optional broker fee
```

The customs rows are intentionally separate:

- `Import from customs`: input quantity times the PI tier import base and the effective import rate.
- `Export to customs`: output quantity times the PI tier export base and the effective export rate.
- `Customs transfer cost`: import plus export, shown beside net profit.

The current static cache records the normal PI taxable base values used for customs estimates:

```text
P0: 5 ISK
P1: 400 ISK
P2: 7,200 ISK
P3: 60,000 ISK
P4: 1,200,000 ISK
```

The first site tab should stay advisory only. It should not create colonies, move goods, open customs offices, place market orders, send mail, or automate any EVE client action. The useful decision is practical and manual: which PI chain looks worth setting up or feeding after customs, market tax, and hauling reality are visible.

### Ore Reprocessing Calculator

The `Ore Reprocessing` tab estimates mineral output from an ore type and ore-unit amount. It can automatically use:

- current system, station, or structure from ESI location;
- Reprocessing, Reprocessing Efficiency, and ore-processing skill levels from ESI skills;
- NPC corporation or faction standing from ESI standings for NPC station tax reduction;
- known Zainou Beancounter reprocessing implants from ESI implants;
- ore portion size, material outputs, and NPC station base yield/tax from the local SDE cache;
- public Jita buy orders from ESI to compare the immediate value of the processed materials against the original unprocessed ore stack.

For NPC stations, the calculator applies the station's SDE reprocessing efficiency and station take, then reduces station take by the connected pilot's ESI standing where possible. The location selector can use the pilot's current ESI location or list NPC stations from the local SDE cache whose owner corporation or faction standing is over 1.5 in the pilot's ESI standings. Those station options include the standings-adjusted processing fee and can be sorted by best net yield, lowest processing fee, or highest standing. The SDE cache does not include every station display name, so the selector may show the solar system, owner, and station id until a specific station is selected for calculation.

For Upwell structures, ESI can resolve the current structure name/owner if the pilot has access, but it does not expose the active reprocessing rig, facility tax, service settings, or structure bonus. Use the manual override fields for those structure values.

This tab is advisory only. It never starts a reprocessing job, moves items, presses keys, places orders, or writes to the EVE client.

Jita values are immediate liquidation estimates against visible public buy-order depth in Jita. If buy depth does not cover every output material or the whole ore stack, the tab labels the value as partial.

Batch paste mode includes a paste manifest before calculation. It shows accepted ore stacks, total units, merged duplicate ore lines, ignored comment lines, and the first parsing issues so testers can catch inventory-copy mistakes before trusting the assay.

### Buyer Order Scanner

The Flight Attendant buyer scanner uses your connected ESI location and blueprint list, the local recipe cache, the local route graph, and public ESI market orders. It does not reveal buyer character names because public ESI market orders do not expose those identities. It shows public buy orders by product, price, remaining volume, system, and jumps from your current location.

Public ESI market orders are cached in the local server process for 5 minutes. This keeps repeated buyer, profitability, and hauling scans from hammering the same market-order endpoint and lines up with CCP's market-order cache/rate-limit direction.

### Market Acquisition Planner

The `Market Acquisition Planner` tab is for strategic public buy-order placement. It uses the connected pilot's current system, a chosen downstream demand system, public regional market orders, public regional market history, the route graph, and a user-entered broker-fee estimate.

It recommends:

- a safe bid ceiling after estimated broker fees and downstream sales tax;
- a small suggested starting bid;
- a first-order unit count capped by budget, destination buy-order depth, and recent market-history volume;
- a narrow or wider buy-order range;
- visible history warnings.

The item scope picker is shared with `Hauler Routes`. When the local SDE market data is available, each top-level market category and subcategory is labeled from that cache, shows the published item count, and includes collapsed item checkboxes with a show-more control. Use a whole category when you want broad coverage, or expand a subcategory such as Bombs and select exact item types when you want a narrow scan. The planner defaults to a 50,000,000 ISK budget and accepts manual budgets from 1 ISK through 10,000,000,000 ISK. Common materials uses a smaller top-industry-input scan in the hosted planner so broad scans are less likely to time out; use market categories or exact items for a more targeted family of items.

History warnings are intentionally plain-language. A `Possible trap` signal means the top-of-book spread is not supported by recent market history, the competing buy side is already above the safe ceiling, or another market-history/current-order mismatch needs manual checking. It is not proof of bad intent by another player. Treat it as a reason to verify the item in EVE before posting a buy order.

Acquisition recommendations, hauler route opportunities, and Planetary Industry input/output lists include Quickbar copy buttons. Those buttons copy plain market item names, one per line, for EVE's Market Quickbar import flow. They do not copy quantities, place orders, write EVE settings files, or touch the EVE client; the pilot still opens the market window, uses `Quickbar > Import Quickbar`, chooses the add/import option, and verifies the item list manually in EVE.

This tab does not place, update, or cancel market orders. The pilot still creates every buy order manually in EVE.

### Blueprint Profitability

Version 1 is intentionally focused on manufacturing recipes. It does not yet rank reactions, invention, copying, or research jobs.

The profitability board uses:

- owned character blueprints from ESI, including BPO/BPC, runs, ME, and TE;
- owned materials from ESI assets;
- SDE manufacturing product, material, max copy run, skill, and base job-time data;
- public market buy orders for expected sale value;
- public market sell orders for missing-material and replacement pricing;
- public Jita buy orders for the raw resource value if the required materials were liquidated instead of built;
- Accounting skill for sales-tax estimates.

Profit cards show after-tax true profit first, then wallet gain, TE-adjusted one-run job time, and estimated profit per hour. Math details keep the before-tax values and the underlying blueprint, material, skill, and job-time assumptions visible.

Future modules should be added one calculator at a time:

- reactions;
- invention, including probability and output BPC assumptions;
- copying and research decisions;
- facility, rig, system-cost-index, and broader industry-skill modifiers.

### Hauler Route Scanner

The `Hauler Routes` tab uses your connected ESI location as the route start. Pick a destination such as Jita, Amarr, Hek, Rens, Dodixie, or Dihra, then scan common build materials, whole market categories, or exact item types that can be bought from public sell orders on or near the route and sold into public buy orders in the destination system.

The scan ranks opportunities by after-tax profit using your Accounting skill. It walks reachable public sell-order depth against destination public buy-order depth until the profitable depth, cargo capacity, or purchase-budget cap runs out, then checks public market history for the matched pickup and destination regions. Results show weighted average pickup/destination prices, matched order counts, pickup systems, pickup cost, remaining budget, sales-tax drag, after-tax profit per extra jump, after-tax profit per m3, and plain-language history warnings such as `Possible trap` or `Caution`. It is an advisory board only: it does not buy items, sell items, create contracts, move the ship, or prove docking access at the listed structure.

Cargo capacity is applied when the local recipe cache includes `volume_m3`. Run the static cache refresh again after this update if the hauler tab says volume is unknown:

```powershell
python .\scripts\update_industry_recipe_cache.py
```

## Discord Channel Posting

The first version uses a Discord channel webhook. A new offer created from the local board can post a Discord message with a link to the offer page. The offer page has a copyable EVE mail draft.

Create a Discord webhook for the market channel, then run:

```powershell
.\scripts\run_corp_market.ps1 serve --discord-webhook-url "https://discord.com/api/webhooks/..."
```

For a normal text channel, that is enough.

For a Discord forum or media channel, add forum mode so Discord creates a new post/thread for each offer:

```powershell
.\scripts\run_corp_market.ps1 serve --discord-webhook-url "https://discord.com/api/webhooks/..." --discord-forum-posts
```

To apply Discord forum tags automatically, copy the tag ids from Discord developer mode and map listing types or categories:

```powershell
.\scripts\run_corp_market.ps1 serve --discord-webhook-url "https://discord.com/api/webhooks/..." --discord-forum-posts --discord-forum-tag-map "sell:WTS_TAG_ID,want:WTB_TAG_ID,ships:SHIPS_TAG_ID,pi:PI_TAG_ID,ore:ORE_TAG_ID"
```

The webhook URL must come from **Channel Settings > Integrations > Webhooks > Copy Webhook URL**. Do not use the Discord channel link or a forum post link.

If corp members need to open links from other computers on a trusted LAN, set a LAN URL:

```powershell
.\scripts\run_corp_market.ps1 serve --host 0.0.0.0 --public-base-url "http://HOST-LAN-IP:8770" --discord-webhook-url "https://discord.com/api/webhooks/..."
```

Use the public-hosting mode above for an Internet-accessible tunnel or domain.

For remote offer creation and status changes, add an admin token:

```powershell
.\scripts\run_corp_market.ps1 serve --host 0.0.0.0 --public-base-url "http://HOST-LAN-IP:8770" --admin-token "change-this-token" --discord-webhook-url "https://discord.com/api/webhooks/..."
```

Loopback browser requests from the host computer can create and edit offers in local/LAN mode. In public hosting mode, remote writes require the market admin token or trusted allowlisted SSO member write access.

## First-Version Workflow

1. Create a `corp-market` or `quartermaster-market` Discord channel.
2. Create a Discord webhook for that channel.
3. Start the concierge with `--discord-webhook-url`.
4. Add a `For sale` or `Want to buy` offer on the local board.
5. The Discord channel receives a rich listing with a mail-draft link.
6. A member opens the link, clicks `Copy Mail`, and sends the pasted mail manually in EVE.
7. The seller or quartermaster marks the listing reserved, sold, cancelled, or reopened from the board.
8. If the listing was posted with this version or later, the concierge edits the original Discord post with the new status.

## Good Offer Habits

- Use the EVE character name that should receive mail.
- Pick a category such as Ships, Modules, PI, Ore, Minerals, or Hauling so Discord forum posts are easier to scan.
- Put the station, structure, or system in `Location`.
- Paste EVE/EFT fit blocks directly into `Notes`. Blocks that start with `[Ship, Fit Name]` are detected as fit notes, summarized in Discord, and preserved in the generated EVE mail draft.
- Add a `Fit Image URL` when you have an in-game simulator screenshot. The screenshot helps builders visually check the fit, but the EFT text block is still the best source for copying/importing the fit into EVE.
- Use normal EVE shorthand in `Unit Price`, such as `750k`, `12.5m`, or `1.2b`.
- Use `Delivery` for pickup-only, delivery-available, high-sec-only, or blue-space notes.
- Keep Discord links private to trusted corp spaces until EVE SSO member gating is added.

## Next Useful Discord Layer

The webhook version is intentionally small. It can create Discord listings and sync status changes back to the original webhook post. It cannot rename existing forum threads or change forum tags after creation; that should wait for a Discord bot with channel/thread permissions.

The next bot layer should add slash commands that write to the same SQLite listing store:

- `/sell`
- `/want`
- `/reserve`
- `/sold`
- `/market search`
- `/order-fit`
- `/hauling`

That bot should reuse the same manual EVE mail draft pages rather than sending mail or touching the EVE client directly.
