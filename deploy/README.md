# Corp Market / Flight Attendant Deployment Contract

This folder contains public-safe templates for a low-monitoring Oracle VM
deployment.
Do not put real SSO secrets, Discord webhooks, admin tokens, private chat logs,
SQLite databases, or profile files in this folder.

## Runtime Shape

- On a VM/systemd install, the Python app binds to `127.0.0.1:8770`.
- In Docker, the Python app binds to `0.0.0.0` inside the container and is
  exposed only to the Compose network.
- The default production path is Oracle VM + Docker Compose + Caddy on the VM.
- Public HTTPS is provided by Caddy on the VM. Do not host the public site from
  your Windows PC.
- Public hosting mode requires EVE SSO, an HTTPS callback URL, and at least one
  allowed character, corporation, or alliance ID.
- The Workbench remains local-only and must not be reverse-proxied.
- Docker support is for Corp Market / Flight Attendant only. Do not add Voice
  Pilot, Intel Pet, or Workbench to this Compose stack without a fresh review.

Recommended production traffic shape:

```text
Internet
  -> DNS for your domain
  -> Oracle VM public IP
  -> Caddy on 80/443
  -> Docker internal network
  -> corp-market container on 8770
```

## First VM Setup

1. Check out the repository to `/opt/eve-voice-pilot`.
2. Create a virtual environment and install dependencies:

   ```sh
   cd /opt/eve-voice-pilot
   python3 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip
   .venv/bin/python -m pip install -r requirements-web.txt
   ```

   Use `requirements.txt` only on machines that also run the desktop voice app.

3. Copy `deploy/systemd/corp-market.env.example` to
   `/etc/eve-voice-pilot/corp-market.env` and fill in real values on the VM.
4. Copy `deploy/systemd/corp-market.service` to
   `/etc/systemd/system/corp-market.service`.
5. Copy `deploy/caddy/Caddyfile` into your Caddy config and either set
   `CORP_MARKET_PUBLIC_HOST` for Caddy or replace the `market.example.com`
   default.
6. Run the smoke check after every deploy:

   ```sh
   deploy/scripts/smoke-corp-market.sh https://market.example.com
   ```

## Docker Compose Setup

Docker Compose is the preferred VM deployment path, and this checkout keeps it
scoped to the web service.

1. Copy `deploy/docker/.env.example` to `.env` in the repository root and fill
   in the public host, public URL, callback URL, SSO client ID, and at least one
   allowed character, corporation, or alliance ID.
2. Create secret files on the Docker host:

   ```sh
   mkdir -p deploy/docker/secrets
   printf '%s' 'real-sso-client-secret' > deploy/docker/secrets/corp_market_sso_client_secret.txt
   printf '%s' 'optional-admin-token-or-empty' > deploy/docker/secrets/corp_market_admin_token.txt
   printf '%s' 'optional-discord-webhook-or-empty' > deploy/docker/secrets/corp_market_discord_webhook_url.txt
   ```

   On Linux hosts, make those secret files readable by the non-root container
   user and not world-readable:

   ```sh
   sudo chown 100:101 deploy/docker/secrets/corp_market_sso_client_secret.txt \
     deploy/docker/secrets/corp_market_admin_token.txt \
     deploy/docker/secrets/corp_market_discord_webhook_url.txt
   sudo chmod 0400 deploy/docker/secrets/corp_market_sso_client_secret.txt \
     deploy/docker/secrets/corp_market_admin_token.txt \
     deploy/docker/secrets/corp_market_discord_webhook_url.txt
   ```

   The Corp Market image runs as UID `100` / GID `101` by default. Docker
   Compose mounts these secret files from the host, so restrictive root-owned
   files can prevent the container from reading them.

3. Build the static EVE SDE cache into the Docker cache volume before inviting
   testers:

   ```sh
   docker compose --profile tools run --rm cache-refresh
   ```

4. Start the app behind Caddy:

   ```sh
   docker compose up -d corp-market caddy
   ```

5. Run the smoke check from the host:

   ```sh
   . ./.env
   deploy/scripts/smoke-corp-market.sh "$CORP_MARKET_PUBLIC_BASE_URL"
   ```

The Compose app service stores SQLite/settings under the `corp_market_profiles`
volume and generated SDE cache files under the `corp_market_cache` volume. The
cache-refresh service writes to that same cache volume; rerun it after updates
that require a fresh SDE cache.

The app and service wrapper support Docker-style `_FILE` environment variables
for SSO credentials, admin tokens, Discord webhooks, allowlists, and other
string settings. Do not set a non-empty `NAME` and `NAME_FILE` at the same time.

## Optional Cloudflare Tunnel

Cloudflare Tunnel is not the default plan for this project and should not be
used to serve the public site from your Windows PC.

Keep it as an optional VM-side alternative when there is a standard operational
reason:

- you do not want to open inbound `80/443` on the Oracle VM;
- you want Cloudflare to front the VM without exposing the VM's public IP;
- Oracle networking, DNS, or firewall rules are temporarily blocking normal
  Caddy access;
- you want Cloudflare Access, WAF, or similar controls in front of Caddy.

If you use a tunnel, run the tunnel connector on the VM, point it at Caddy or
the internal app on the VM, and keep the same public-hosting, SSO allowlist, and
smoke-check requirements.

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
