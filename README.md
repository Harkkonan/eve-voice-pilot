# EVE Voice Pilot

EVE Voice Pilot is a small Windows voice-command prototype for EVE Online.

The first version is intentionally cautious:

- One spoken command maps to one key or key chord.
- Keybinds can include left/right modifiers, like `LEFT SHIFT+P`, `LEFT CTRL+LEFT SHIFT`, or `LEFT SHIFT AND P`.
- Practice mode is on by default, so it recognizes commands without sending keys.
- Key sending is blocked unless the active window title contains `EVE`, unless you turn that check off.
- Exact command phrases can fire from partial live transcription before the final transcript is ready.
- Live command matching is strict. Use clear phrases like `open map` instead of short one-word aliases for important actions.
- It does not do timed chains, repeats, mouse moves, input broadcasting, or multi-client automation.

## First Run

Double-click `Start-EveVoicePilot.bat`.

The first run creates a local `.venv` folder, installs the Python packages, and downloads the small local speech model into `models\`.

## Speech Engine

The `Speech engine` setting has two choices:

- `Local (offline)`: uses the downloaded Vosk model. This is the default, does not use OpenAI credits, and should feel quick once the model is loaded.
- `OpenAI realtime`: uses OpenAI transcription. Use this if you want to compare recognition quality or use it as a fallback.

For `OpenAI realtime`, paste your API key into the app. A ChatGPT subscription does not automatically pay for API usage. If you check `Remember on this PC`, the app saves it in your Windows user profile using Windows data protection.

## Microphone Check

Windows voice training does not affect this app. Pick your headset mic in `Microphone`, click `Test Mic`, and speak normally.

If the test says the level is low, try a different listed microphone or raise the Windows input volume. If it says the mic has a low sample rate, pick a higher-quality headset input if one is available.

## How To Test Safely

1. Leave `Practice mode` turned on.
2. Press `PAUSE` or click `Arm Listening`.
3. Speak commands. After each command, the app automatically listens again.
4. Press `PAUSE` again or click `Pause` when you want it to stop listening.
5. Check `Last heard`, `Last action`, and the log.
6. When the command matching looks right, turn off `Practice mode`.
7. Put EVE in the foreground before using real key sending.

## Armed Listening

`Arm Listening` means the app is actively waiting for voice commands. It keeps the selected speech engine ready and restarts listening after each command, which is faster than clicking Start every time.

Use `Pause` when you are done. While armed, the app uses the microphone. It uses API credits only when `OpenAI realtime` is selected.

The recommended arm/pause hotkey is `PAUSE`, so EVE can keep `F9` for Solar System Map.

## Commands

The command list is editable inside the app. Each command has:

- Name: a label you recognize.
- Spoken phrases: one or more phrases separated by commas.
- Key: one key chord, such as `F1`, `V`, or `CTRL+SPACE`.
- Hold seconds: how long to hold the keybind before release. `0.10` is a good starting point.
- Speak response: optional. When enabled, the app plays a short cached Windows voice response after a successful key send.
- Response suffix: a short style tag such as `Aura`. Commands without a suffix do not speak.
- Response text: optional. If left blank, the app generates a short confirmation from the command name.

Click a command list column header to sort the visible list. Sorting is only for viewing; it does not change the saved command order.

Your editable command profile is saved at `profiles/my_eve_commands.json`.

Voice responses can use `OpenAI cached` or `Windows local`. `OpenAI cached` uses `gpt-4o-mini-tts`, the `ballad` voice, and a power-ballad starship-AI style by default. Clips are generated as `.wav` files in `cache\speech\`, then replayed locally during gameplay. They do not play for silence, invalid phrases, practice mode, or blocked sends.

Use `Regenerate Voice Clips` after changing the response voice or style.

## Keybind Standard

The recommended EVE keybind list is in `docs/eve_voice_keybind_standard.md`. A sortable CSV is in `data/eve_voice_keybind_standard.csv`.

The matching app profile is `profiles/eve_voice_standard.json`. It remaps medium slots to `Alt+1` through `Alt+8` instead of the EVE default `Alt+F1` through `Alt+F8`, because `Alt+F4` is a risky Windows close-window shortcut.

## OCR Watcher

The OCR watcher is a command-line helper that reads one screen rectangle and sends a key chord when the watched text value changes. It does not send on the first stable read; the first value becomes the baseline.

It uses `pytesseract`, which also needs the Tesseract Windows app installed. If Tesseract is not on `PATH`, pass the full `tesseract.exe` path with `--tesseract-cmd`.

For easier setup, double-click `Start-EveOcrWatcherGui.bat` or run:

```powershell
.\scripts\run_ocr_watcher_gui.ps1
```

The GUI has settings fields, preset buttons, test buttons, live mouse coordinates, and an output log. Use `Select Region` to drag a rectangle around the text on screen. Use `Show Region` to draw a temporary overlay on the screen area being watched, and `Preview Region` to open the actual screen crop. Start with `Read Once` to see what OCR returns, then use `Start Dry Run` to confirm changes are detected before using `Start Live Watch`. The `Set Top Left` and `Set Bottom Right` buttons capture your mouse position after 3 seconds to help tune the screen region.

Dry-run a region first:

```powershell
.\scripts\run_ocr_watcher.ps1 --region "100,200,260,40" --hotkey "CTRL+SHIFT+F9" --pattern "([0-9,]+)" --dry-run
```

Read once without watching:

```powershell
.\scripts\run_ocr_watcher.ps1 --region "100,200,260,40" --hotkey "CTRL+SHIFT+F9" --pattern "([0-9,]+)" --once
```

Send for real after the value changes:

```powershell
.\scripts\run_ocr_watcher.ps1 --region "100,200,260,40" --hotkey "CTRL+SHIFT+F9" --pattern "([0-9,]+)" --stable-samples 2 --cooldown 1.5
```

By default, key sending is blocked unless the active window title contains `EVE`. Use `--window-title-contains ""` only if you intentionally want to disable that guard.

## Corp Intel Board

The corp intel board is a read-only dashboard for EVE chat-log intel. It watches selected chat channels for hostile reports, enemy sightings, system names, and calls for aid, then shows them at `http://127.0.0.1:8765/`.

Double-click `Start-EveCorpIntelBoard.bat` for the local board. It watches `Corp`, `Corporation`, `Fleet`, `Alliance`, `Local`, and `*Intel*` channel names by default, and starts at the end of existing logs so only new chat is processed.

Run the shared server on a trusted host:

```powershell
.\scripts\run_corp_intel_board.ps1 serve --host 0.0.0.0 --port 8765 --ingest-token "change-this-token"
```

Run an opt-in corp member agent:

```powershell
.\scripts\run_corp_intel_board.ps1 agent --server "http://HOST-LAN-IP:8765" --token "change-this-token" --pilot "Pilot Name" --channels "Corp,Fleet,Alliance,Local,*Intel*"
```

The agent sends only matching intel events by default, not every chat line. Keep channel allowlists narrow. More detail is in `docs/corp_intel_board.md`.

The dashboard also has editable watchlists for hostile pilots, hostile corporations, help callouts, and extra keywords. The live watchlist is stored in ignored local data at `profiles/corp_intel_watchlist.json`; remote agents refresh it from the shared server every 60 seconds.

Recent intel events persist locally in ignored SQLite data at `profiles/corp_intel_events.sqlite3`, with seven-day retention by default.

Optional EVE SSO login verifies dashboard users by character and public ESI corporation/alliance membership. The returned SSO access token is signature-checked against EVE's JWKS before the character identity is trusted. Pilot records persist locally in ignored SQLite data at `profiles/corp_intel_pilots.sqlite3`; access and refresh tokens are not stored.

For a shared corp-hosted board, add `--require-sso-dashboard` plus `--allowed-corporation-ids` or `--allowed-alliance-ids` so the dashboard and JSON APIs require an allowlisted EVE SSO session.

After SSO login, trusted members can create per-pilot agent upload tokens from the dashboard and run the local agent with `--agent-token`. The server stores only token hashes, uses the token for authenticated watchlist refreshes, and stamps matching uploads with the verified EVE character identity.

### EVE Intel Pet

Double-click `Start-EveIntelPet.bat` for a local-only always-on-top alert overlay. It watches your own EVE chat logs, starts at the end of existing files, and warns on new help/hostile/keyword lines. It includes a small original pixel-art ship that animates on alerts and every five minutes while idle. Add your character name for mention alerts:

```powershell
.\scripts\run_intel_pet.ps1 --pilot-name "Your Character Name"
```

When idle, the overlay shows only the ship and a small `Options` button. Alert bubbles show only the message text, then hide again. Use `Options` to add, change, or remove pilot-name mention alerts, help phrases, and local alert keywords while it is running. The Options window also has an in-memory history tab for recent pet alerts.

Optional `--enable-location-cheer` uses read-only ESI location with `esi-location.read_location.v1` so the ship flies happily when you reach Dihra, Amarr, or Jita. The pet keeps the access token in memory only while running and does not share your location.

Local settings can live in ignored profile data at `profiles/intel_pet_settings.json`. More detail is in `docs/intel_pet.md`.

## Notes

If EVE is running as administrator and this app is not, Windows may block simulated keypresses. Usually both apps should run normally, without administrator mode.

## Trade Planner Agent

The trade planner is a separate command-line helper that uses EVE Workbench's sell-buy trade tool data to suggest distribution runs. It does not log in, place orders, or touch the game client.

Double-click `Start-EveTradeAgent.bat` for prompt mode.

Run it from PowerShell:

```powershell
.\scripts\run_trade_agent.ps1 --from "Jita" --to "Amarr" --volume 10000 --top 8
```

Or let it scan the editable target list and keep only routes within a jump limit:

```powershell
.\scripts\run_trade_agent.ps1 --from "Jita" --max-jumps 10 --volume 5000
```

Useful options:

- `--from "System"`: where you are now.
- `--to "System"`: exact destination.
- `--route "Amarr,Jita,Hek,Rens,Amarr"`: check a full hub loop in one command.
- `--max-jumps 10`: distance mode, using `data/eve_trade_targets.json`.
- `--targets "Amarr,Dodixie,Hek"`: one-off destination list for distance mode.
- `--budget 8000000`: cap suggestions to the ISK you can spend.
- `--item-domain industrial`: only show minerals, materials, ores, PI goods, and ammunition/charges.
- `--prefer materials`: boost material-style goods above mineral/ore fillers when ranking.
- `--sort-by profit`: rank by total profit instead of ISK per jump.
- `--format compact`: print a short table with quantity, spend, profit, ROI, cargo, and order depth.
- `--highsec-only`: skip routes that dip below 0.5 security.

Example hub loop with an 8m ISK budget and 11,000 m3 cargo:

```powershell
.\scripts\run_trade_agent.ps1 --route "Amarr,Jita,Hek,Rens,Amarr" --volume 11000 --budget 8000000 --item-domain industrial --prefer materials --format compact --top 5 --sort-by profit
```

Always check the orders in EVE before hauling. The helper reads live EVE Workbench market data, but buy and sell orders can fill or move between the suggestion and your undock.

## Corp Market Concierge

The corp market concierge is a Discord-friendly buy/sell board for corporation members. It posts offers or requests to a Discord channel through a webhook, syncs listing status changes back to that Discord post, then gives buyers and sellers a copyable EVE mail draft. It does not send EVE mail, create contracts, place orders, or automate the game client.

Double-click `Start-EveCorpMarket.bat` for the local board at `http://127.0.0.1:8770/`.

To post new offers into Discord, create a Discord channel webhook and run:

```powershell
.\scripts\run_corp_market.ps1 serve --discord-webhook-url "https://discord.com/api/webhooks/..."
```

If the Discord target is a forum channel, add `--discord-forum-posts` so each offer creates a forum post/thread. Optional forum tag mapping can use listing types or categories, such as `--discord-forum-tag-map "sell:WTS_TAG_ID,want:WTB_TAG_ID,ships:SHIPS_TAG_ID,pi:PI_TAG_ID"`.

Reserve, sold, cancelled, and reopen changes update the original Discord webhook message for listings created with this version or later. Renaming forum threads or changing forum tags after creation will need a Discord bot later.

For a shared LAN test, set the public link base that Discord members should open:

```powershell
.\scripts\run_corp_market.ps1 serve --host 0.0.0.0 --public-base-url "http://HOST-LAN-IP:8770" --discord-webhook-url "https://discord.com/api/webhooks/..."
```

For an Internet-accessible Flight Attendant link, use `--public-hosting-mode` with an HTTPS public base URL, EVE SSO credentials, and `--allowed-corporation-ids` or `--allowed-alliance-ids`. This keeps hosted Flight Attendant access member-only and tightens remote market writes.

Listings are stored in ignored local SQLite data at `profiles/corp_market.sqlite3`. More detail is in `docs/corp_market_concierge.md`.

The Flight Attendant tab includes a `Static Cache Preflight` panel. Run `python .\scripts\update_industry_recipe_cache.py` on the same machine or container that serves the website before inviting testers; generated cache files under `cache\` are ignored and are not copied by Git pushes.

The Flight Attendant tab also includes a `Market Acquisition Planner`. It compares public ESI market orders with public market history to suggest cautious public buy-order ceilings, first-order size, and collection range. `Possible trap` warnings mean the current order spread is not well supported by recent history or is too thin to trust without checking in EVE.

The `Trade P&L` tab reads recent ESI wallet transactions and market fee journal rows to match visible buys and sells into item-level profit, loss, open stock, unmatched sells, and optional matched transaction rows. Its history filter can be narrowed from 1 hour up to 30 days, and its considered income view can use an accounting lens plus item exclusions such as Pyerite while still showing the real underlying profit or loss. It is read-only and does not place or edit market orders.

The `Ore Reprocessing` tab estimates mineral output from an ore amount using ESI location, skills, standings, and implants plus local SDE ore/station data. It can rank NPC reprocessing stations where your ESI standing is over 1.5, include the standings-adjusted processing fee, let you sort by net yield, processing fee, or standing, and compare the Jita buy-order value of the processed materials with the Jita buy-order value of the unprocessed ore stack. Upwell structure rigs, taxes, and bonuses still need manual overrides because ESI does not expose those settings.

The `Planetary Industry` tab uses SDE PI schematics, manual customs-tax settings, and public market prices to rank PI chains by profit per day after import customs cost, export customs cost, sales tax, and optional broker fee. It includes profitable-only and price-check filters, an input shopping list, output sell targets, and clear customs breakdowns. It remains advisory only: pilots still create colonies, move goods, and place orders manually.
