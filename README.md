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

The first run creates a local `.venv` folder and installs the two Python packages used for microphone capture and the OpenAI realtime connection.

## OpenAI API Key

This app needs an OpenAI API key. A ChatGPT subscription does not automatically pay for API usage.

Paste your API key into the app. If you check `Remember on this PC`, the app saves it in your Windows user profile using Windows data protection.

## Microphone Check

Windows voice training does not affect this app. Pick your headset mic in `Microphone`, click `Test Mic`, and speak normally.

If the test says the level is low, try a different listed microphone or raise the Windows input volume. If it says the mic has a low sample rate, pick a higher-quality headset input if one is available.

## How To Test Safely

1. Leave `Practice mode` turned on.
2. Press `F9` or click `Arm Listening`.
3. Speak commands. After each command, the app automatically listens again.
4. Press `F9` again or click `Pause` when you want it to stop listening.
5. Check `Last heard`, `Last action`, and the log.
6. When the command matching looks right, turn off `Practice mode`.
7. Put EVE in the foreground before using real key sending.

## Armed Listening

`Arm Listening` means the app is actively waiting for voice commands. It keeps the OpenAI connection warm and restarts listening after each command, which is faster than clicking Start every time.

Use `Pause` when you are done. While armed, the app uses the microphone and may use API credits even when no command is spoken.

The recommended arm/pause hotkey is `PAUSE`, so EVE can keep `F9` for Solar System Map.

## Commands

The command list is editable inside the app. Each command has:

- Name: a label you recognize.
- Spoken phrases: one or more phrases separated by commas.
- Key: one key chord, such as `F1`, `V`, or `CTRL+SPACE`.
- Hold seconds: how long to hold the keybind before release. `0.10` is a good starting point.

Your editable command profile is saved at `profiles/my_eve_commands.json`.

## Keybind Standard

The recommended EVE keybind list is in `docs/eve_voice_keybind_standard.md`. A sortable CSV is in `data/eve_voice_keybind_standard.csv`.

The matching app profile is `profiles/eve_voice_standard.json`. It remaps medium slots to `Alt+1` through `Alt+8` instead of the EVE default `Alt+F1` through `Alt+F8`, because `Alt+F4` is a risky Windows close-window shortcut.

## Notes

If EVE is running as administrator and this app is not, Windows may block simulated keypresses. Usually both apps should run normally, without administrator mode.
