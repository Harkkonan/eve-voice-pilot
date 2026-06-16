# EVE Voice Pilot

EVE Voice Pilot is a small Windows voice-command prototype for EVE Online.

The first version is intentionally cautious:

- One spoken command maps to one key or key chord.
- Keybinds can include left/right modifiers, like `LEFT SHIFT+P`, `LEFT CTRL+LEFT SHIFT`, or `LEFT SHIFT AND P`.
- Practice mode is on by default, so it recognizes commands without sending keys.
- Key sending is blocked unless the active window title contains `EVE`, unless you turn that check off.
- Exact command phrases can fire from partial live transcription before the final transcript is ready.
- Live command matching is strict. Use clear phrases like `open map` instead of short one-word aliases for important actions.
- It does not do timed chains, repeats, mouse moves, screen reading, input broadcasting, or multi-client automation.

## First Run

Double-click `Start-EveVoicePilot.bat`.

The first run creates a local `.venv` folder, installs the Python packages, and downloads the small local speech model into `models\`.

## Speech Engine

The `Speech engine` setting has three choices:

- `Local (offline)`: uses the downloaded Vosk model. This is the default, does not use OpenAI credits, and should feel quick once the model is loaded.
- `Whisper local dictation`: uses optional local Whisper through `faster-whisper`. Use it for note-style speech or testing better language recognition. It does not support fast partial command firing, and command sending still requires an exact configured phrase match.
- `OpenAI realtime`: uses OpenAI transcription. Use this if you want to compare recognition quality or use it as a fallback.

For better offline command recognition, install the recommended larger Vosk model:

```powershell
.\scripts\download-vosk-model.ps1 -ModelName vosk-model-en-us-0.22-lgraph
```

Then choose `Recommended lgraph (vosk-model-en-us-0.22-lgraph)` in `Local model`.

For optional local Whisper dictation, install the extra package:

```powershell
.\scripts\install-whisper-dictation.ps1
```

The first Whisper use may download the `base.en` model into the normal user model cache. The app records a short temporary WAV for the phrase, transcribes it locally, and deletes the temporary file.

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

Voice responses can use `OpenAI cached`, `ElevenLabs cached`, or `Windows local`. Cached cloud voices generate `.wav` files in `cache\speech\`, then replay locally during gameplay. They do not play for silence, invalid phrases, practice mode, or blocked sends.

Use `Regenerate Voice Clips` after changing the response voice or style.

## Keybind Standard

The recommended EVE keybind list is in `docs/eve_voice_keybind_standard.md`. A sortable CSV is in `data/eve_voice_keybind_standard.csv`.

The matching app profile is `profiles/eve_voice_standard.json`. It remaps medium slots to `Alt+1` through `Alt+8` instead of the EVE default `Alt+F1` through `Alt+F8`, because `Alt+F4` is a risky Windows close-window shortcut.

## Corp Intel Board

Retired. The shared corp-hosted chat-log dashboard and remote upload agent are no longer active project surfaces. `Start-EveCorpIntelBoard.bat`, `scripts\run_corp_intel_board.ps1`, and `python -m eve_voice_pilot.corp_intel` now stop with a retirement notice instead of starting a server or agent.

The shared chat-log parsing and SSO helper code remains only for compatibility with EVE Intel Pet and Flight Attendant. Do not start a new Corp Intel Board deployment without a fresh privacy and EVE policy review.

See `docs/retired_features.md` for the retirement note.

### EVE Intel Pet

Double-click `Start-EveIntelPet.bat` for a local-only always-on-top alert overlay. It watches your own EVE chat logs and local game logs, starts at the end of existing files, and warns on new help/hostile/keyword lines. The ship also flies around shooting when a fresh game-log line looks like an NPC or player kill. It includes a small original pixel-art ship that animates on alerts and every five minutes while idle, plus a local-only stout robot miner gag behavior for future trigger hooks. Add your character name for mention alerts:

```powershell
.\scripts\run_intel_pet.ps1 --pilot-name "Your Character Name"
```

When idle, the overlay shows only the ship and a small `Options` button. Drag the ship or the `Options` button to move it. Alert bubbles show only the message text, stay up for 15 seconds, then hide again unless a newer alert arrives first. Use `Options` to add, change, or remove pilot-name mention alerts, help phrases, and local alert keywords while it is running. The Options window also lets you choose per-alert ship behaviors with small animated previews and has an in-memory history tab for recent pet alerts.

Optional `--enable-location-cheer` uses read-only ESI location with `esi-location.read_location.v1` so the ship flies happily when you reach Dihra, Amarr, or Jita. The pet keeps the access token in memory only while running and does not share your location.

Optional Discord voice notes let you say phrases such as `Aura take a note gate camp near Amarr` and post that deliberate note to a configured Discord notes-channel webhook. Notes are off by default, use a separate ignored local settings file at `profiles/intel_pet_discord_notes.json`, and disable Discord mentions.

Chat alerts still come from local EVE `Chatlogs`, not ESI. When location cheer is enabled, the SSO character name is used as the default local chat-log `Listener` filter; pass `--all-listeners` to watch matching channels for every local character log.

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

The corp market concierge is being reshaped into a Discord alert-router settings site for corporation coordination. The first tab saves local Discord alert route/rule settings, previews the exact payload, and can send a manual test through the configured webhook. The default sender is `IntelPet`, matched text stays off by default, and no raw-log forwarding is wired yet.

The legacy market board still exists below that first tab. It can post offers or requests to a Discord channel through a webhook, sync listing status changes back to that Discord post, then give buyers and sellers a copyable EVE mail draft. It does not send EVE mail, create contracts, place orders, or automate the game client.

Double-click `Start-EveCorpMarket.bat` for the local board at `http://127.0.0.1:8770/`.

To post new offers or send Discord alert tests, create a Discord channel webhook and run:

```powershell
.\scripts\run_corp_market.ps1 serve --discord-webhook-url "https://discord.com/api/webhooks/..."
```

If the Discord target is a forum channel, add `--discord-forum-posts` so each offer creates a forum post/thread. Optional forum tag mapping can use listing types or categories, such as `--discord-forum-tag-map "sell:WTS_TAG_ID,want:WTB_TAG_ID,ships:SHIPS_TAG_ID,pi:PI_TAG_ID"`.

Reserve, sold, cancelled, and reopen changes update the original Discord webhook message for listings created with this version or later. Renaming forum threads or changing forum tags after creation will need a Discord bot later.

For a shared LAN test, set the public link base that Discord members should open:

```powershell
.\scripts\run_corp_market.ps1 serve --host 0.0.0.0 --public-base-url "http://HOST-LAN-IP:8770" --discord-webhook-url "https://discord.com/api/webhooks/..."
```

For an Internet-accessible Flight Attendant link, use `--public-hosting-mode` with an HTTPS public base URL and EVE SSO credentials. Add `--allowed-character-ids`, `--allowed-corporation-ids`, or `--allowed-alliance-ids` for member-only access, or use `--allow-any-authenticated` for a public beta where any valid EVE SSO character can use read/planning features. Remote market writes remain locked down unless you configure the admin token or trusted allowlisted member writes.

Listings are stored in ignored local SQLite data at `profiles/corp_market.sqlite3`. More detail is in `docs/corp_market_concierge.md`.

The `Shared Fittings` tab stores user-pasted EVE fitting clipboard blocks in the same ignored local SQLite database. Each entry can include optional tags, a submitter, and a website fitting link; pilots still copy/import the fit manually in EVE. The tab can also save a separate ignored Discord webhook settings file at `profiles/corp_fitting_discord_post_settings.json` for a `Fittings` forum channel, then manually post the exact EVE clipboard-format fitting block with mentions disabled.

The Flight Attendant tab includes a `Static Cache Preflight` panel. Run `python .\scripts\update_industry_recipe_cache.py` on the same machine or container that serves the website before inviting testers; generated cache files under `cache\` are ignored and are not copied by Git pushes.

The Flight Attendant tab also includes a `Market Acquisition Planner`. It compares public ESI market orders with public market history to suggest cautious public buy-order ceilings, first-order size, and collection range. `Possible trap` warnings mean the current order spread is not well supported by recent history or is too thin to trust without checking in EVE.

### Flight Attendant Workbench

Double-click `Start-EveFlightWorkbench.bat` for a local-only operator panel at `http://127.0.0.1:8790/`.

The workbench is separate from the corp-facing Flight Attendant site. Its public button set is intentionally small: verify the local machine, start or stop the local site, update `market.brianridderbusch.net` through the VM Docker Compose deployment, and view deploy logs. It keeps an ignored local action log and does not expose arbitrary shell commands in the browser. Configure VM access in ignored local JSON at `profiles/flight_workbench.local.json`. Details are in `docs/flight_attendant_workbench.md`.

The `Trade P&L` tab reads recent ESI wallet transactions and market fee journal rows to match visible buys and sells into item-level profit, loss, open stock, unmatched sells, and optional matched transaction rows. Its history filter can be narrowed from 1 hour up to 30 days, and its considered income view can use an accounting lens plus consideration rules: count every item, ignore SDE-labeled materials and inputs, ignore a custom list, or combine materials with a custom list. Ignored items stay visible with their real profit or loss; they only stop changing the considered result. Inventory mode can add a public Fuzzwork Jita 4-4 estimate for open stock; realized P&L remains matched wallet truth. Acquisition Planner runs save local expected bid/profit snapshots in the ignored market SQLite database so P&L can compare actual matched results against the latest plan for that item. Trade P&L also saves seen wallet transaction rows locally and replays older buys/sells before the selected window so sells can match older buy lots without counting old buys as current cashflow. It is read-only and does not place or edit market orders.

The `Ore Reprocessing` tab estimates mineral output from an ore amount using ESI location, skills, standings, and implants plus local SDE ore/station data. It can rank NPC reprocessing stations where your ESI standing is over 1.5, include the standings-adjusted processing fee, let you sort by net yield, processing fee, or standing, and compare the Jita buy-order value of the processed materials with the Jita buy-order value of the unprocessed ore stack. Upwell structure rigs, taxes, and bonuses still need manual overrides because ESI does not expose those settings.

The `Planetary Industry` tab uses SDE PI schematics, manual customs-tax settings, and public market prices to rank PI chains by profit per day after import customs cost, export customs cost, sales tax, and optional broker fee. It includes profitable-only and price-check filters, an input shopping list, output sell targets, and clear customs breakdowns. It remains advisory only: pilots still create colonies, move goods, and place orders manually.
