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
```

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

### Local Static Data Caches

Flight Attendant uses ESI to learn your actual location, owned blueprints, and materials. It uses CCP's Static Data Export for blueprint recipes and jump-aware route math. Build or refresh the local static caches from PowerShell:

```powershell
python .\scripts\update_industry_recipe_cache.py
```

The generated caches are written to ignored local data:

```text
cache\eve_industry_recipes.json
cache\eve_route_graph.json
```

If these caches are missing, the Flight Attendant tab still requires ESI and will show the data it can safely fetch, but recipe matching, buildability previews, and jump-aware nearby system coverage will stay unavailable.

### Buyer Order Scanner

The Flight Attendant buyer scanner uses your connected ESI location and blueprint list, the local recipe cache, the local route graph, and public ESI market orders. It does not reveal buyer character names because public ESI market orders do not expose those identities. It shows public buy orders by product, price, remaining volume, system, and jumps from your current location.

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

If corp members need to open links from other computers, set a LAN or tunnel URL:

```powershell
.\scripts\run_corp_market.ps1 serve --host 0.0.0.0 --public-base-url "http://HOST-LAN-IP:8770" --discord-webhook-url "https://discord.com/api/webhooks/..."
```

For remote offer creation and status changes, add an admin token:

```powershell
.\scripts\run_corp_market.ps1 serve --host 0.0.0.0 --public-base-url "http://HOST-LAN-IP:8770" --admin-token "change-this-token" --discord-webhook-url "https://discord.com/api/webhooks/..."
```

Loopback browser requests from the host computer can always create and edit offers. Remote reserve clicks are allowed so members can claim offers from Discord links.

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
