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
7. The seller or quartermaster marks the listing sold or cancelled from the board.

## Good Offer Habits

- Use the EVE character name that should receive mail.
- Pick a category such as Ships, Modules, PI, Ore, Minerals, or Hauling so Discord forum posts are easier to scan.
- Put the station, structure, or system in `Location`.
- Use normal EVE shorthand in `Unit Price`, such as `750k`, `12.5m`, or `1.2b`.
- Use `Delivery` for pickup-only, delivery-available, high-sec-only, or blue-space notes.
- Keep Discord links private to trusted corp spaces until EVE SSO member gating is added.

## Next Useful Discord Layer

The webhook version is intentionally small. The next layer should add a Discord bot with slash commands that write to the same SQLite listing store:

- `/sell`
- `/want`
- `/reserve`
- `/sold`
- `/market search`
- `/order-fit`
- `/hauling`

That bot should reuse the same manual EVE mail draft pages rather than sending mail or touching the EVE client directly.
