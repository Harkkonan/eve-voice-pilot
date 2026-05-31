# EVE Voice Pilot

EVE Voice Pilot is a small Windows voice-command prototype for EVE Online.

The first version is intentionally cautious:

- One spoken command maps to one key or key chord.
- Practice mode is on by default, so it recognizes commands without sending keys.
- Key sending is blocked unless the active window title contains `EVE`, unless you turn that check off.
- It does not do timed chains, repeats, mouse moves, input broadcasting, or multi-client automation.

## First Run

Double-click `Start-EveVoicePilot.bat`.

The first run creates a local `.venv` folder and installs the two Python packages used for microphone capture and the OpenAI realtime connection.

## OpenAI API Key

This app needs an OpenAI API key. A ChatGPT subscription does not automatically pay for API usage.

Paste your API key into the app. If you check `Remember on this PC`, the app saves it in your Windows user profile using Windows data protection.

## How To Test Safely

1. Leave `Practice mode` turned on.
2. Press `F9`, speak one command, then wait a moment.
3. Check `Last heard`, `Last action`, and the log.
4. When the command matching looks right, turn off `Practice mode`.
5. Put EVE in the foreground before using real key sending.

## Commands

The command list is editable inside the app. Each command has:

- Name: a label you recognize.
- Spoken phrases: one or more phrases separated by commas.
- Key: one key chord, such as `F1`, `V`, or `CTRL+SPACE`.

Your editable command profile is saved at `profiles/my_eve_commands.json`.

## Notes

If EVE is running as administrator and this app is not, Windows may block simulated keypresses. Usually both apps should run normally, without administrator mode.
