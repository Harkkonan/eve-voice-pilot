# Corp Market / Flight Attendant Deployment Contract

This folder contains public-safe templates for a low-monitoring VM deployment.
Do not put real SSO secrets, Discord webhooks, admin tokens, private chat logs,
SQLite databases, or profile files in this folder.

## Runtime Shape

- The Python app binds to `127.0.0.1:8770`.
- Public HTTPS is provided by Caddy or Cloudflare Tunnel in front of the app.
- Public hosting mode requires EVE SSO, an HTTPS callback URL, and at least one
  allowed character, corporation, or alliance ID.
- The Workbench remains local-only and must not be reverse-proxied.

## First VM Setup

1. Check out the repository to `/opt/eve-voice-pilot`.
2. Create a virtual environment and install dependencies:

   ```sh
   cd /opt/eve-voice-pilot
   python3 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip
   .venv/bin/python -m pip install -r requirements.txt
   ```

3. Copy `deploy/systemd/corp-market.env.example` to
   `/etc/eve-voice-pilot/corp-market.env` and fill in real values on the VM.
4. Copy `deploy/systemd/corp-market.service` to
   `/etc/systemd/system/corp-market.service`.
5. Copy `deploy/caddy/Caddyfile` into your Caddy config and replace
   `market.example.com`.
6. Run the smoke check after every deploy:

   ```sh
   deploy/scripts/smoke-corp-market.sh https://market.example.com
   ```

## Backup Contract

Back up the ignored operational state, not the Git checkout:

- `profiles/corp_market.sqlite3*`
- `profiles/corp_discord_alert_settings.json`
- `profiles/corp_discord_post_settings.json`
- `profiles/corp_fitting_discord_post_settings.json`
- generated cache files under `cache/` if they are expensive to rebuild

Use `deploy/scripts/backup-corp-market.sh` for daily snapshots and
`deploy/scripts/restore-corp-market.sh` for a manual restore drill.

## Release Gate

Before promoting a release:

```sh
git diff --check
.venv/bin/python -m pytest
deploy/scripts/smoke-corp-market.sh "$CORP_MARKET_PUBLIC_BASE_URL"
```

Record any fresh EVE policy review in `docs/eve_developer_license_review.md`
before a meaningful public hosting, privacy, monetization, or SSO-scope change.
