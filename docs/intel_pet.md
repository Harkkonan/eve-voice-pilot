# EVE Intel Pet

EVE Intel Pet is a small local overlay that watches your own EVE chat logs and shows an always-on-top alert when a new chat line matches something important.

It is intentionally personal and local-first:

- It reads only the EVE chat log folder on your computer.
- It starts at the end of existing files by default, so old chat is not replayed.
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

The overlay itself stays small: just the ship, a speech bubble, and an `Options` button. Click `Options` to open the full settings window.

In the `Alerts` tab, add, change, or remove:

- your pilot names for mention alerts;
- help phrases for critical calls;
- extra keywords for watch terms.

Changes are saved immediately to:

```text
profiles/intel_pet_settings.json
```

The live matcher refreshes as soon as a setting is saved, so you do not need to restart the pet.

## Alert History

The `History` tab in `Options` shows recent chat alerts and location cheer events from this pet run.

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
