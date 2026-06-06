# EVE Intel Pet

EVE Intel Pet is a small local overlay that watches your own EVE chat and game logs. It shows an always-on-top alert when a new chat line matches something important, and the ship celebrates when a fresh game-log line looks like a kill or mission milestone.

It is intentionally personal and local-first:

- It reads only the EVE chat and game log folders on your computer.
- It starts at the end of existing files by default, so old chat, combat, and mission log lines are not replayed.
- It does not connect to Discord or the corp intel board.
- It connects to ESI only if you deliberately start optional location cheer mode.
- It does not control the EVE client, read memory, inspect packets, or automate gameplay.
- Its ship pet is original generated pixel art, not extracted EVE client art or a copied official ship.

## Start It

Double-click:

```powershell
Start-EveIntelPet.bat
```

Or run it with your character name so it can alert when someone mentions you:

```powershell
.\scripts\run_intel_pet.ps1 --pilot-name "Your Character Name"
```

By default it watches `Corp`, `Corporation`, `Fleet`, `Alliance`, `Local`, and `*Intel*` channel names.

The pet reads local EVE chat-log files from the `Chatlogs` folder. EVE SSO does not provide chat messages. When optional location cheer is enabled, the pet uses the SSO character name as a local chat-log `Listener` filter, so signing in as Dandin watches Dandin's matching local chat logs by default.

To watch every local character's matching channel logs even when location cheer is enabled:

```powershell
.\scripts\run_intel_pet.ps1 --enable-location-cheer --all-listeners
```

To force one or more local chat-log listeners without SSO:

```powershell
.\scripts\run_intel_pet.ps1 `
  --listener-name "Dandin Ridderston" `
  --listener-name "Liet-kynes Ridderston"
```

Channels outside the default allowlist, such as `Rookie Help` or private corp community channels, need `--channels` or `--all-channels`.

## Ship Animation

The pet includes a small original pixel-art spaceship. It uses eight transparent PNG frames under:

```text
src/eve_voice_pilot/static/intel-pet/
```

The overlay swaps those frames with Tkinter only. Turrets and engines animate when an alert appears, the ship flies happily on configured system arrivals and mission milestones, and it flies around shooting when a local game-log kill is detected. The ship also runs a short idle cycle every five minutes.

Alert bubbles stay visible for 15 seconds by default. If a newer alert arrives before that timer ends, the bubble switches to the newer message and the 15-second timer starts again.

## Add Extra Local Alerts

You can add extra keywords or help phrases from the command line:

```powershell
.\scripts\run_intel_pet.ps1 `
  --pilot-name "Your Character Name" `
  --keyword "buy order" `
  --help-phrase "need evac"
```

The first version has no Discord push. That keeps the trust boundary simple while we prove the overlay is useful.

## Local Combat Kill Cheer

The pet watches your local EVE `Gamelogs` folder for new kill-looking lines such as `has been destroyed`, `you destroyed`, or `final blow`. When it sees one, the ship does a short frantic flight-and-shoot animation and the bubble shows the cleaned game-log message.

This is local only:

- it does not use ESI for combat;
- it does not inspect the EVE client, packets, memory, or cache files;
- it starts at the end of existing game logs by default;
- it only reacts to new lines written while the pet is running.

To disable combat cheering:

```powershell
.\scripts\run_intel_pet.ps1 --no-combat-cheer
```

If your EVE game logs live somewhere unusual:

```powershell
.\scripts\run_intel_pet.ps1 --game-log-dir "C:\Path\To\EVE\logs\Gamelogs"
```

## Local Mission Comments

The pet also watches the same local EVE `Gamelogs` folder for new mission-looking lines such as `Mission accepted`, `Mission completed`, or `Mission objectives complete`. When it sees one, the bubble shows a short lore-flavored comment and the History tab keeps the cleaned game-log line that triggered it.

Mission comments are local only:

- they do not use ESI;
- they do not require any new ESI scope;
- they do not inspect the EVE client, packets, memory, or cache files;
- they start at the end of existing game logs by default;
- they only react to new lines written while the pet is running.

To disable mission comments:

```powershell
.\scripts\run_intel_pet.ps1 --no-mission-cheer
```

## Optional Spoken Pet Messages

The pet can speak the same alert, location, combat, and mission text it already shows in its bubble. This is opt-in and read-only: spoken pet messages do not listen for voice commands and do not send keys to EVE.

To enable it from the command line:

```powershell
.\scripts\run_intel_pet.ps1 --speak-alerts
```

The default pet speech engine is Windows local speech. You can also choose the cached OpenAI voice path used by EVE Voice Pilot:

```powershell
.\scripts\run_intel_pet.ps1 `
  --speak-alerts `
  --response-engine "OpenAI cached" `
  --response-voice "ballad"
```

OpenAI pet speech uses the saved EVE Voice Pilot API key on this PC, or one of these local environment variables:

```text
INTEL_PET_OPENAI_API_KEY
OPENAI_API_KEY
EVE_VOICE_OPENAI_API_KEY
```

You can also turn spoken pet messages on or off in `Options` > `Voice`.

## Optional Voice Command Practice

The pet can also listen for the same phrases configured in EVE Voice Pilot and show what command matched. This first pet integration is practice-only: it does not send keys, click, move the mouse, or control EVE.

To try it:

```powershell
.\scripts\run_intel_pet.ps1 --enable-voice-listener
```

When it recognizes a configured phrase, the bubble shows the heard phrase, the matched command, and the keybind it would use in the full Voice Pilot app. The message ends with:

```text
Practice only. No key sent.
```

The listener uses your saved EVE Voice Pilot command profile when available, then falls back to the sample profile. You can choose the speech engine, microphone, and call sign in `Options` > `Voice`.

OpenAI realtime transcription needs a saved EVE Voice Pilot API key on this PC, or one of the same local environment variables used for spoken pet messages. Local/offline transcription needs the Vosk model from setup.

## Optional ESI Location Cheer

Location cheer makes the ship fly happily when your connected EVE character reaches `Dihra`, `Amarr`, or `Jita`.

This mode is opt-in. It asks EVE SSO for only:

```text
esi-location.read_location.v1
```

The pet keeps the ESI access token in memory only while it is running. It does not write token files, store refresh tokens, send your location to Discord, share it with the corp intel board, or control the EVE client.

Register this local callback URL in your EVE Developers application:

```text
http://127.0.0.1:8788/intel-pet/callback
```

Then start the pet with your local SSO app values:

```powershell
.\scripts\run_intel_pet.ps1 `
  --enable-location-cheer `
  --sso-client-id "client-id" `
  --sso-client-secret "client-secret"
```

You can also keep the SSO values in your local PowerShell environment:

```powershell
$env:INTEL_PET_SSO_CLIENT_ID = "client-id"
$env:INTEL_PET_SSO_CLIENT_SECRET = "client-secret"
.\scripts\run_intel_pet.ps1 --enable-location-cheer
```

To choose different happy systems:

```powershell
.\scripts\run_intel_pet.ps1 `
  --enable-location-cheer `
  --happy-system "Dihra" `
  --happy-system "Amarr" `
  --happy-system "Jita"
```

## Manage Alerts In The Overlay

The overlay itself stays small. When idle, it shows only the ship and a small `Options` button. The speech bubble appears only for actual alert or arrival messages, then physically disappears again.

Drag the ship to move the pet around your screen. You can also drag the `Options` button; a normal click still opens settings.

Click `Options` to open the full settings window.

In the `Alerts` tab, add, change, or remove:

- your pilot names for mention alerts;
- help phrases for critical calls;
- extra keywords for watch terms.

Changes are saved immediately to:

```text
profiles/intel_pet_settings.json
```

The live matcher refreshes as soon as a setting is saved, so you do not need to restart the pet.

In the `Behaviors` tab, choose the ship animation for each alert type:

- pilot mention;
- help call;
- hostile intel;
- keyword match;
- system arrival;
- kill cheer;
- mission milestone.

Each row has a small animated preview next to the selector. The behavior choices include short alert, happy flight, combat burst, long flight, long shooting, long combo, calm wiggle, and no animation. Behavior changes are saved immediately to the same local settings file.

In the `Voice` tab, turn spoken pet messages on or off and choose the voice engine/style. You can also enable the practice voice-command listener there. The listener reports matched Voice Pilot commands but does not send keys.

## Alert History

The `History` tab in `Options` shows recent chat alerts and location cheer events from this pet run.

It also shows which local chat-log files are being watched, including the EVE log `Listener` character for each file. The History tab updates live while it is open.

History is local and in-memory only:

- it is cleared when the pet closes;
- it is not written to `profiles/intel_pet_settings.json`;
- it is not sent to Discord, ESI, or the corp intel board.

## Optional Local Settings File

You can also keep local settings in ignored profile data at:

```text
profiles/intel_pet_settings.json
```

Example:

```json
{
  "pilot_names": ["Your Character Name"],
  "extra_keywords": ["buy order"],
  "help_phrases": ["need evac"],
  "show_message_text": true,
  "alert_seconds": 15,
  "speak_alerts": false,
  "response_engine": "Windows local",
  "response_voice": "ballad",
  "response_style": "Speak as a starship AI with a dramatic 1980s power ballad cadence: soaring, confident, a little theatrical, but concise and clear. Do not sing; speak the line.",
  "enable_voice_listener": false,
  "voice_engine": "Local (offline)",
  "voice_input_device": "",
  "voice_call_sign": "merlin",
  "alert_behaviors": {
    "mention": "alert",
    "help": "alert",
    "hostile": "alert",
    "keyword": "alert",
    "location": "happy",
    "combat": "combat",
    "mission": "long_move"
  }
}
```

Do not commit this file. It can contain private character names and operational keywords.

## Console Mode

For testing without the overlay:

```powershell
.\scripts\run_intel_pet.ps1 --pilot-name "Your Character Name" --console
```

Use `--read-existing` only when you deliberately want to replay existing chat logs for testing.
