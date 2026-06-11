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

The pet includes a small original pixel-art spaceship. It uses eight transparent PNG ship frames and a separate original robot-miner gag frame set under:

```text
src/eve_voice_pilot/static/intel-pet/
```

The overlay swaps those frames with Tkinter only. Turrets and engines animate when an alert appears, the ship flies happily on configured system arrivals and mission milestones, and it flies around shooting when a local game-log kill is detected. The ship also runs a short idle cycle every five minutes.

Alert bubbles use a subtle Aura-style life-simulation backing layer: faint trace lines, cell pulses, and a scan sweep behind the message. It is only local visual polish and does not add any new data source or ESI scope.

The `Stout robot miner` behavior is a local visual gag: the ship briefly folds into a compact industrial robot, swings a pickaxe, fires eye lasers, and returns to ship form. It does not add an ESI scope, share data, read new files, click, send keys, or automate EVE.

The robot-miner frames are transparent 160x128 PNG renders derived from local CC0 source packs kept under the ignored `local_archives/intel_pet_cc0_packs/` folder. The generator uses Quaternius Animated Mech Pack as the base mech form, Quaternius LowPoly Robot for the squat robot silhouette, OpenGameArt Stylized Low Poly Tools axe meshes as the dual mining-pick props, and Quaternius Sci-Fi Essentials for sensor/industrial details. Raw downloaded packs are not committed. To regenerate the committed frames after placing the sources locally:

```powershell
.\.venv\Scripts\python.exe scripts\generate_intel_pet_robot_miner_assets.py
```

Use `Options` > `Behaviors` > `Test` beside an alert kind to run that selected animation immediately on the overlay without waiting for a chat, location, combat, or mission event.

Alert bubbles stay visible for 15 seconds by default. If a newer alert arrives before that timer ends, the bubble switches to the newer message and the 15-second timer starts again.

## Add Extra Local Alerts

You can add extra keywords or help phrases from the command line:

```powershell
.\scripts\run_intel_pet.ps1 `
  --pilot-name "Your Character Name" `
  --keyword "buy order" `
  --help-phrase "need evac"
```

The current version still does not automatically forward chat alerts to Discord. Deliberate voice notes can be sent to a dedicated Discord notes channel when you opt in and configure a notes webhook.

## Optional Discord Voice Notes

Discord voice notes let you say a phrase such as:

```text
Aura take a note gate camp near the Amarr undock
```

You can also say `Aura take a note` by itself. The pet will arm note capture, collect recognized note fragments, then send after 2 seconds with no new words. Say `send note` to close and send immediately, or `cancel note` to cancel an armed note.

Set this up from `Options` > `Notes`, or start the pet with a notes webhook:

```powershell
.\scripts\run_intel_pet.ps1 `
  --enable-voice-listener `
  --enable-discord-notes `
  --discord-note-webhook-url "https://discord.com/api/webhooks/..."
```

The notes webhook is saved only if you save it from `Options` > `Notes`, and it goes to:

```text
profiles/intel_pet_discord_notes.json
```

That file is ignored by git. It is separate from normal Intel Pet settings, and it is not included in Intel Pet settings export/import. Discord note messages disable Discord mentions with `allowed_mentions: {"parse": []}`.

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

The default pet speech engine is Windows local speech. You can also choose cached OpenAI or ElevenLabs pet speech. Cached clips are stored under the ignored `cache\speech\` folder and replayed locally:

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

ElevenLabs pet speech uses a local environment variable and the `Voice / voice id` field in `Options` > `Voice`:

```text
INTEL_PET_ELEVENLABS_API_KEY
ELEVENLABS_API_KEY
ELEVEN_LABS_API_KEY
```

You can also turn spoken pet messages on or off in `Options` > `Voice`.

### Ballad Voice Studio

Use `Options` > `Voice` to tune the pet voice:

- `Voice preset` fills in a style and a safe sample line.
- `Preview Voice` saves the current voice settings and plays the preview sentence when the cache is ready.
- `Cache Preview` prepares the current preview sentence without playing it.
- `Regenerate Preview` rebuilds that sample if you changed engine, voice, or style and want a fresh clip.
- `Spoken alert types` controls which alert kinds are spoken when `Speak pet messages` is on.

The preview cache is for the deliberate sample sentence only. It does not cache raw chat alerts or alert history.

## Optional Voice Command Listener

The pet can also listen for the same phrases configured in EVE Voice Pilot and show what command matched. It starts in practice mode: it does not send keys, click, move the mouse, or control EVE.

To try practice mode:

```powershell
.\scripts\run_intel_pet.ps1 --enable-voice-listener
```

When it recognizes a configured phrase, the bubble shows the heard phrase, the matched command, and the keybind it would use in the full Voice Pilot app. The message ends with:

```text
Practice only. No key sent.
```

To allow exact matched voice commands to send their configured keybinds:

```powershell
.\scripts\run_intel_pet.ps1 `
  --enable-voice-listener `
  --allow-voice-command-sending
```

Command sending stays guarded by the active-window check by default. The pet only sends when the active window title contains:

```text
EVE
```

You can change the required title text:

```powershell
.\scripts\run_intel_pet.ps1 `
  --enable-voice-listener `
  --allow-voice-command-sending `
  --voice-target-title "EVE"
```

To return to practice mode:

```powershell
.\scripts\run_intel_pet.ps1 --enable-voice-listener --no-voice-command-sending
```

The listener uses your saved EVE Voice Pilot command profile when available, then falls back to the sample profile. You can choose the speech engine, microphone, local Vosk model, local Whisper model, and call sign in `Options` > `Voice`.

Use `Local (offline)` plus the recommended Vosk model for command phrases. Use `Whisper local dictation` when you want better note-style language recognition; it is slower, does not do fast partial command firing, and still routes final text through the same exact-match command checks. OpenAI realtime transcription needs a saved EVE Voice Pilot API key on this PC, or one of the same local environment variables used for spoken pet messages. Local/offline transcription needs the Vosk model from setup.

### Local Recognition Models

Setup installs the lightweight local model:

```text
models\vosk-model-small-en-us-0.15
```

If local recognition is too weak, install the recommended larger model:

```powershell
.\scripts\download-vosk-model.ps1 -ModelName vosk-model-en-us-0.22-lgraph
```

Then open `Options` > `Voice` and choose:

```text
Recommended lgraph (vosk-model-en-us-0.22-lgraph)
```

The status line shows whether the selected model is installed. Model files stay under the ignored local `models\` folder and should not be committed.

For optional local Whisper dictation:

```powershell
.\scripts\install-whisper-dictation.ps1
```

Then choose:

```text
Whisper local dictation
```

The first Whisper use may download the selected model into the normal user model cache. `tiny.en` is fastest, `base.en` is the default, and `small.en` or `medium.en` are slower experiments for better dictation fidelity. The pet records one short temporary phrase WAV, transcribes it locally, deletes the temporary WAV, and does not save transcripts unless you deliberately send a Discord note.

### Voice Lab

Use `Options` > `Voice Lab` to search, add, duplicate, change, remove, preview, and dry-run voice commands from the pet.

The lab saves commands to your personal EVE Voice Pilot profile. If the pet is currently reading the bundled sample profile, the first edit is saved to:

```text
profiles\my_eve_commands.json
```

The command search filters by command name, phrase, keybind, and action text. `Duplicate` copies the selected command into a new editable command, which is useful when making safer variants of an existing phrase. The selected-command preview summarizes the keybind, phrases, and spoken response text before you save changes.

The dry-run phrase test never sends keys. It only shows what the listener would hear, which command would match, and whether the live listener would stay in practice mode.

Use `Phrase Quality` to find phrases that are likely to confuse local recognition before command sending is enabled. It reports duplicate phrases across commands, highly similar phrases, and short single-word phrases. The report is advisory only: it does not remove phrases or turn on fuzzy matching.

Use `Recent heard phrases` in Voice Lab when the listener heard you but did not match the command you expected. Select the command, select or type the phrase, then use `Add Heard To Selected` or `Add Test To Selected`. This only adds an exact phrase to the local command profile; it does not turn on fuzzy matching or send keys from the lab.

Use `Recognition Lab` when local voice recognition feels wrong. Turn off `Listen for voice commands`, press `Record Local Diagnostic`, say one phrase, and review the local transcript, volume/RMS, stop reason, selected microphone, model path, grammar size, exact-match result, nearest configured command phrases, and ambiguity guidance. The lab does not send keys and does not save the recording.

## Optional ESI Location Cheer

Location cheer makes the ship fly happily when your connected EVE character reaches `Dihra`, `Amarr`, or `Jita`.

This mode is opt-in. It asks EVE SSO for only:

```text
esi-location.read_location.v1
```

The pet keeps the ESI access token in memory only while it is running. It sends that token only to the official ESI host, does not write token files, store refresh tokens, send your location to Discord, share it with the corp intel board, or control the EVE client.

Register this local callback URL in your EVE Developers application:

```text
http://127.0.0.1:8788/intel-pet/callback
```

Keep the SSO values in your local PowerShell environment, then start the pet:

```powershell
$env:INTEL_PET_SSO_CLIENT_ID = "client-id"
$env:INTEL_PET_SSO_CLIENT_SECRET = "client-secret"
.\scripts\run_intel_pet.ps1 --enable-location-cheer
```

You can also pass the SSO client id on the command line while keeping the secret in the environment:

```powershell
$env:INTEL_PET_SSO_CLIENT_SECRET = "client-secret"
.\scripts\run_intel_pet.ps1 --enable-location-cheer --sso-client-id "client-id"
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

The top of the window shows a compact live status strip for alert terms, voice-command mode, Discord notes, ESI location, and in-memory history.

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

Each row has a small animated preview next to the selector. The behavior choices include short alert, happy flight, combat burst, long flight, long shooting, long combo, stout robot miner, calm wiggle, and no animation. Behavior changes are saved immediately to the same local settings file.

In the `Voice` tab, turn spoken pet messages on or off and choose the voice engine/style. You can also enable the voice-command listener there. `Allow command sending` is off by default; when it is on, the active-window guard is still on by default.

In the `Notes` tab, the current voice-note phrase preview follows your saved call sign and trigger phrase. Use `Use Tap Tap Trigger` if you want to put `tap tap` first while keeping any other note triggers.

In the `Voice Lab` tab, edit the command profile and test phrases without sending keys. Command changes are local profile changes and are shared with the main EVE Voice Pilot app.

Use `Export Settings` and `Import Settings` at the bottom of the options window to back up or move Intel Pet settings. Importing refreshes the live matcher and saves the cleaned settings to the normal ignored local profile.

Exported settings include your alert terms, pilot names, alert behavior choices, spoken alert choices, and voice preferences. They do not include raw chat logs, alert history, EVE SSO tokens, Discord webhooks, or the separate Voice Lab command profile. Treat exported files as private because pilot names and watched terms can still reveal how you use the pet.

## Diagnostics

The `Diagnostics` tab summarizes the pet's current local runtime state: settings file, watched folders, channel and listener filters, polling mode, game-log cheer status, ESI location-cheer state, voice listener state, selected local model, command profile path, and in-memory history counts.

Use `Copy Diagnostics` when troubleshooting. The copied report is summary-only: it does not include raw chat lines, alert message text, EVE SSO tokens, Discord webhooks, or voice recordings.

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
  "spoken_alert_kinds": {
    "mention": true,
    "help": true,
    "hostile": true,
    "keyword": true,
    "location": true,
    "combat": true,
    "mission": true
  },
  "response_engine": "Windows local",
  "response_voice": "ballad",
  "response_style": "Speak as a starship AI with a dramatic 1980s power ballad cadence: soaring, confident, a little theatrical, but concise and clear. Do not sing; speak the line.",
  "voice_preview_text": "Intel Pet voice online. Systems are green.",
  "enable_voice_listener": false,
  "voice_engine": "Local (offline)",
  "voice_model_path": "",
  "voice_input_device": "",
  "voice_call_sign": "merlin",
  "allow_voice_command_sending": false,
  "require_voice_target_window": true,
  "voice_target_title": "EVE",
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
