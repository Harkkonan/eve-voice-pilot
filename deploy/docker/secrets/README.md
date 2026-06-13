# Docker Secret Files

Create these files on the Docker host before running `docker compose up`.
The directory is ignored by Git except for this README.

Required:

```text
corp_market_sso_client_secret.txt
```

Optional but referenced by `compose.yaml`; create an empty file if disabled:

```text
corp_market_admin_token.txt
corp_market_discord_webhook_url.txt
```

On Linux hosts, make the files readable by the non-root container user:

```sh
sudo chown 100:101 deploy/docker/secrets/corp_market_sso_client_secret.txt \
  deploy/docker/secrets/corp_market_admin_token.txt \
  deploy/docker/secrets/corp_market_discord_webhook_url.txt
sudo chmod 0400 deploy/docker/secrets/corp_market_sso_client_secret.txt \
  deploy/docker/secrets/corp_market_admin_token.txt \
  deploy/docker/secrets/corp_market_discord_webhook_url.txt
```

The Corp Market image runs as UID `100` / GID `101` by default.

Do not commit real SSO secrets, admin tokens, or Discord webhook URLs.
