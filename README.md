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
- `--max-jumps 10`: distance mode, using `data/eve_trade_targets.json`.
- `--targets "Amarr,Dodixie,Hek"`: one-off destination list for distance mode.
- `--sort-by profit`: rank by total profit instead of ISK per jump.
- `--highsec-only`: skip routes that dip below 0.5 security.

Always check the orders in EVE before hauling. The helper reads live EVE Workbench market data, but buy and sell orders can fill or move between the suggestion and your undock.
