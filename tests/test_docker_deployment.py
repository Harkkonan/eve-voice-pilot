from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_web_requirements_exclude_desktop_dependencies():
    requirements = read_text("requirements-web.txt").lower()

    assert "pyjwt[crypto]" in requirements
    assert "sounddevice" not in requirements
    assert "vosk" not in requirements
    assert "websocket-client" not in requirements


def test_pyproject_defines_src_package_and_web_extra():
    pyproject = read_text("pyproject.toml")

    assert 'where = ["src"]' in pyproject
    assert "desktop = [" in pyproject
    assert "web = [" in pyproject
    assert "[tool.setuptools.package-data]" in pyproject
    assert "sounddevice" in pyproject
    assert "PyJWT[crypto]" in pyproject


def test_dockerfile_scopes_runtime_to_corp_market_web_service():
    dockerfile = read_text("Dockerfile")

    assert "FROM python:${PYTHON_VERSION}-slim AS runtime" in dockerfile
    assert "requirements-web.txt" in dockerfile
    assert "CORP_MARKET_HOST=0.0.0.0" in dockerfile
    assert "CORP_MARKET_MARKET_DB_PATH=/data/profiles/corp_market.sqlite3" in dockerfile
    assert "ln -s /data/cache /app/cache" in dockerfile
    assert "ARG APP_UID=100" in dockerfile
    assert "ARG APP_GID=101" in dockerfile
    assert 'addgroup --system --gid "${APP_GID}" evevoice' in dockerfile
    assert 'adduser --system --uid "${APP_UID}"' in dockerfile
    assert "USER evevoice" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "run-corp-market-service.sh" in dockerfile
    assert "sounddevice" not in dockerfile.lower()
    assert "vosk" not in dockerfile.lower()


def test_compose_uses_internal_app_exposure_volumes_and_secrets():
    compose = read_text("compose.yaml")

    assert "CORP_MARKET_SSO_CLIENT_SECRET_FILE: /run/secrets/corp_market_sso_client_secret" in compose
    assert "CORP_MARKET_ADMIN_TOKEN_FILE: /run/secrets/corp_market_admin_token" in compose
    assert "CORP_MARKET_DISCORD_WEBHOOK_URL_FILE: /run/secrets/corp_market_discord_webhook_url" in compose
    assert "CORP_MARKET_ALLOW_ANY_AUTHENTICATED:" in compose
    assert "CORP_MARKET_GOOGLE_SITE_VERIFICATION:" in compose
    assert "CORP_MARKET_GOOGLE_SITE_VERIFICATION_FILE:" in compose
    assert "corp_market_profiles:/data/profiles" in compose
    assert "corp_market_cache:/data/cache" in compose
    assert "cache-refresh:" in compose
    assert "expose:" in compose
    assert '"8770:8770"' not in compose


def test_dockerignore_keeps_local_state_and_secrets_out_of_build_context():
    dockerignore = read_text(".dockerignore")

    for ignored in (
        "profiles/",
        "cache/",
        "models/",
        "local_archives/",
        ".env",
        "deploy/docker/secrets/",
    ):
        assert ignored in dockerignore


def test_gitattributes_keeps_container_entrypoints_lf_only():
    attributes = read_text(".gitattributes")

    assert "*.sh text eol=lf" in attributes
    assert "Dockerfile text eol=lf" in attributes
    assert "compose.yaml text eol=lf" in attributes


def test_caddyfile_supports_vm_default_and_compose_upstream():
    caddyfile = read_text("deploy/caddy/Caddyfile")

    assert "{$CORP_MARKET_PUBLIC_HOST:market.example.com}" in caddyfile
    assert "reverse_proxy {$CORP_MARKET_UPSTREAM:127.0.0.1:8770}" in caddyfile


def test_service_wrapper_supports_file_backed_secrets():
    wrapper = read_text("deploy/scripts/run-corp-market-service.sh")

    assert "value_from_env_or_file" in wrapper
    assert "CORP_MARKET_SSO_CLIENT_SECRET" in wrapper
    assert "CORP_MARKET_ADMIN_TOKEN" in wrapper
    assert "CORP_MARKET_DISCORD_WEBHOOK_URL" in wrapper
    assert "CORP_MARKET_ALLOW_ANY_AUTHENTICATED" in wrapper
    assert "--allow-any-authenticated" in wrapper
    assert "CORP_MARKET_GOOGLE_SITE_VERIFICATION" in wrapper
    assert "--google-site-verification" in wrapper
    assert "--google-site-verification-file" in wrapper
    assert "EVE_SSO_CLIENT_SECRET" in wrapper


def test_docker_env_example_documents_public_access_policy():
    env_example = read_text("deploy/docker/.env.example")

    assert "CORP_MARKET_ALLOW_ANY_AUTHENTICATED=0" in env_example
    assert "CORP_MARKET_GOOGLE_SITE_VERIFICATION=" in env_example
    assert "CORP_MARKET_GOOGLE_SITE_VERIFICATION_FILE=" in env_example
    assert "Public hosting requires either at least one allowlist or allow-any-authenticated mode." in env_example


def test_deploy_docs_cover_linux_secret_file_permissions():
    deploy_readme = read_text("deploy/README.md")
    secrets_readme = read_text("deploy/docker/secrets/README.md")

    for text in (deploy_readme, secrets_readme):
        assert "sudo chown 100:101" in text
        assert "sudo chmod 0400" in text
        assert "UID `100` / GID `101`" in text
