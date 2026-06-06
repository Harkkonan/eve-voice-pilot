# EVE Intel Pet

EVE Intel Pet is a small local overlay that watches your own EVE chat logs and shows an always-on-top alert when a new chat line matches something important.

It is intentionally personal and local-first:

- It reads only the EVE chat log folder on your computer.
- It starts at the end of existing files by default, so old chat is not replayed.
- It does not connect to Discord, ESI, or the corp intel board in the first version.
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

## Ship Animation

The pet includes a small original pixel-art spaceship. It uses eight transparent PNG frames under:

```text
src/eve_voice_pilot/static/intel-pet/
```

The overlay swaps those frames with Tkinter only. Turrets and engines animate when an alert appears, and the ship runs a short idle cycle every five minutes.

## Add Extra Local Alerts

You can add extra keywords or help phrases from the command line:

```powershell
.\scripts\run_intel_pet.ps1 `
  --pilot-name "Your Character Name" `
  --keyword "buy order" `
  --help-phrase "need evac"
```

The first version has no Discord push. That keeps the trust boundary simple while we prove the overlay is useful.

## Manage Alerts In The Overlay

Click `Alerts` in the pet window to add, change, or remove:

- your pilot names for mention alerts;
- help phrases for critical calls;
- extra keywords for watch terms.

Changes are saved immediately to:

```text
profiles/intel_pet_settings.json
```

The live matcher refreshes as soon as a setting is saved, so you do not need to restart the pet.

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
  "alert_seconds": 18
}
```

Do not commit this file. It can contain private character names and operational keywords.

## Console Mode

For testing without the overlay:

```powershell
.\scripts\run_intel_pet.ps1 --pilot-name "Your Character Name" --console
```

Use `--read-existing` only when you deliberately want to replay existing chat logs for testing.
