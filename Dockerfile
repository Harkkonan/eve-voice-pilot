# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APP_UID=100
ARG APP_GID=101

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    CORP_MARKET_ROOT=/app \
    CORP_MARKET_PYTHON=python \
    CORP_MARKET_HOST=0.0.0.0 \
    CORP_MARKET_PORT=8770 \
    CORP_MARKET_MARKET_DB_PATH=/data/profiles/corp_market.sqlite3 \
    CORP_MARKET_DISCORD_ALERT_SETTINGS_PATH=/data/profiles/corp_discord_alert_settings.json \
    CORP_MARKET_DISCORD_POST_SETTINGS_PATH=/data/profiles/corp_discord_post_settings.json \
    CORP_MARKET_DISCORD_FITTING_POST_SETTINGS_PATH=/data/profiles/corp_fitting_discord_post_settings.json

WORKDIR /app

RUN addgroup --system --gid "${APP_GID}" evevoice \
    && adduser --system --uid "${APP_UID}" --ingroup evevoice --home /nonexistent --no-create-home evevoice \
    && mkdir -p /data/profiles /data/cache /app/scripts /app/deploy/scripts \
    && ln -s /data/profiles /app/profiles \
    && ln -s /data/cache /app/cache \
    && chown -R evevoice:evevoice /data

COPY requirements-web.txt /tmp/requirements-web.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements-web.txt

COPY src ./src
COPY scripts/update_industry_recipe_cache.py ./scripts/update_industry_recipe_cache.py
COPY deploy/scripts/run-corp-market-service.sh ./deploy/scripts/run-corp-market-service.sh
RUN chmod +x ./deploy/scripts/run-corp-market-service.sh

USER evevoice

EXPOSE 8770
VOLUME ["/data/profiles", "/data/cache"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8770/api/health', timeout=3).read()"

CMD ["./deploy/scripts/run-corp-market-service.sh"]
