# Intel Pet Discord Messaging Plan

Intel Pet does not send Discord messages today. This plan describes the safe path for adding Discord channel messages and direct messages later without silent raw-log forwarding.

The current trust boundary remains local-only:

- chat alerts are read from the local user's EVE chat logs;
- alert history stays in memory for the current pet run;
- EVE SSO location tokens stay in memory only;
- no Discord webhook, bot token, or subscription file is used by the current pet.

## Design Decision

Use two separate Discord delivery paths:

- Channel messages: use a Discord channel webhook, following the Corp Market Concierge pattern.
- Direct messages: use a Discord bot with explicit per-user consent. Webhooks cannot send DMs.

Do not make Discord delivery a general chat-log bridge. The pet should send selected alert summaries only after the local user deliberately enables a route.

## Consent And Privacy Rules

Discord delivery must be off by default.

Every route must be explicit:

- local overlay only;
- channel summary;
- direct message summary;
- channel summary with matched text;
- direct message summary with matched text.

Raw matched text must be off by default. If it is added later, it needs a visible per-route toggle and preview text before sending. It must never forward whole files, backfill old logs silently, or stream every line from a channel.

The default Discord payload should include:

- alert type and severity;
- matched category;
- channel name;
- system names when detected;
- reporting pilot only when already visible in the local alert object;
- age/timestamp;
- a short source label such as `local opt-in Intel Pet`.

The default Discord payload should not include:

- raw chat-log lines;
- unrelated chat context before or after the alert;
- EVE SSO access tokens;
- Discord webhook URLs or bot tokens;
- private profile paths;
- alert history dumps;
- `@everyone`, `@here`, or role/user mentions.

Any user-controlled text sent to Discord should neutralize mentions before delivery.

## Channel Message Slice

The first implementation slice supports channel webhooks only.

Implemented behavior:

1. Add disabled-by-default CLI settings for selected alert types.
2. Read the webhook URL from an environment variable or CLI argument, not from committed files.
3. Validate the webhook URL using the same shape as the Corp Market Concierge helper.
4. Build a summary-only payload from the current alert object.
5. Keep dry-run mode on by default and surface previews in History before enabling live sends.
6. Send only new alerts observed after startup.
7. Rate-limit sends and surface failures in History.

Command shape:

```powershell
$env:INTEL_PET_DISCORD_ALERT_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
.\scripts\run_intel_pet.ps1 --discord-channel-alerts --discord-alert-live
```

The current environment variable is `INTEL_PET_DISCORD_ALERT_WEBHOOK_URL`.

## Direct Message Slice

DMs should wait for a Discord bot integration.

Planned behavior:

1. Add a Discord bot token from an environment variable or local secret store only.
2. Add a local ignored subscription file such as `profiles/intel_pet_discord_subscriptions.json`.
3. Require each Discord user to opt in through a bot command or admin-confirmed mapping.
4. Let each subscriber choose alert types and whether matched text is included.
5. Send DMs only to opted-in Discord users.
6. Keep a small local delivery log with message ids, timestamps, route names, and errors only.
7. Add diagnostics that show enabled routes and recent delivery errors without printing tokens.

Do not infer Discord recipients from EVE character names, corporation membership, or chat participants.

## Safety Checks Before Implementation

Before implementing Discord delivery, re-open the live CCP/EVE policy pages and Discord developer documentation. Record the review in `docs/eve_developer_license_review.md`.

Implementation must preserve these boundaries:

- no EVE client control;
- no packet, memory, or cache scraping;
- no automatic gameplay action;
- no silent raw-log forwarding;
- no public hosting or remote access without a fresh policy/privacy review;
- no committed tokens, webhook URLs, or subscription files.

The implementation should reuse tested helpers where possible and add tests that prove raw alert text is excluded by default.
