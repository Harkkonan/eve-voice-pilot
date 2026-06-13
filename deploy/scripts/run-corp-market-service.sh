#!/usr/bin/env bash
set -euo pipefail

ROOT="${CORP_MARKET_ROOT:-/opt/eve-voice-pilot}"
PYTHON="${CORP_MARKET_PYTHON:-${ROOT}/.venv/bin/python}"

args=(
  -m eve_voice_pilot.corp_market serve
  --host "${CORP_MARKET_HOST:-127.0.0.1}"
  --port "${CORP_MARKET_PORT:-8770}"
  --market-db-path "${CORP_MARKET_MARKET_DB_PATH:-/var/lib/eve-voice-pilot/profiles/corp_market.sqlite3}"
  --public-base-url "${CORP_MARKET_PUBLIC_BASE_URL:?CORP_MARKET_PUBLIC_BASE_URL is required}"
  --sso-callback-url "${CORP_MARKET_SSO_CALLBACK_URL:?CORP_MARKET_SSO_CALLBACK_URL is required}"
  --sso-client-id "${CORP_MARKET_SSO_CLIENT_ID:?CORP_MARKET_SSO_CLIENT_ID is required}"
  --sso-client-secret "${CORP_MARKET_SSO_CLIENT_SECRET:?CORP_MARKET_SSO_CLIENT_SECRET is required}"
  --discord-alert-settings-path "${CORP_MARKET_DISCORD_ALERT_SETTINGS_PATH:-/var/lib/eve-voice-pilot/profiles/corp_discord_alert_settings.json}"
  --discord-post-settings-path "${CORP_MARKET_DISCORD_POST_SETTINGS_PATH:-/var/lib/eve-voice-pilot/profiles/corp_discord_post_settings.json}"
  --discord-fitting-post-settings-path "${CORP_MARKET_DISCORD_FITTING_POST_SETTINGS_PATH:-/var/lib/eve-voice-pilot/profiles/corp_fitting_discord_post_settings.json}"
  --public-hosting-mode
)

add_optional_arg() {
  local flag="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    args+=("$flag" "$value")
  fi
}

add_optional_arg --allowed-character-ids "${CORP_MARKET_ALLOWED_CHARACTER_IDS:-}"
add_optional_arg --allowed-corporation-ids "${CORP_MARKET_ALLOWED_CORPORATION_IDS:-}"
add_optional_arg --allowed-alliance-ids "${CORP_MARKET_ALLOWED_ALLIANCE_IDS:-}"
add_optional_arg --admin-token "${CORP_MARKET_ADMIN_TOKEN:-}"
add_optional_arg --discord-webhook-url "${CORP_MARKET_DISCORD_WEBHOOK_URL:-}"
add_optional_arg --discord-forum-tag-ids "${CORP_MARKET_DISCORD_FORUM_TAG_IDS:-}"
add_optional_arg --discord-forum-tag-map "${CORP_MARKET_DISCORD_FORUM_TAG_MAP:-}"

if [[ "${CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET:-0}" == "1" ]]; then
  args+=(--trusted-members-can-write-market)
fi

exec "$PYTHON" "${args[@]}"
