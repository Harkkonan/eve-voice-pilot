#!/usr/bin/env bash
set -euo pipefail

ROOT="${CORP_MARKET_ROOT:-/opt/eve-voice-pilot}"
PYTHON="${CORP_MARKET_PYTHON:-${ROOT}/.venv/bin/python}"

value_from_env_or_file() {
  local name="$1"
  local default="${2:-}"
  local file_name="${name}_FILE"
  local value="${!name-}"
  local file_path="${!file_name-}"

  if [[ -n "$value" && -n "$file_path" ]]; then
    echo "Set either ${name} or ${file_name}, not both." >&2
    exit 64
  fi
  if [[ -n "$file_path" ]]; then
    if [[ ! -r "$file_path" ]]; then
      echo "Could not read ${file_name}: ${file_path}" >&2
      exit 66
    fi
    value="$(tr -d '\r\n' < "$file_path")"
  fi
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '%s' "$default"
  fi
}

require_value() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "${name} is required. Set ${name} or ${name}_FILE." >&2
    exit 64
  fi
}

public_base_url="$(value_from_env_or_file CORP_MARKET_PUBLIC_BASE_URL)"
sso_callback_url="$(value_from_env_or_file CORP_MARKET_SSO_CALLBACK_URL)"
sso_client_id="$(value_from_env_or_file CORP_MARKET_SSO_CLIENT_ID)"
sso_client_secret="$(value_from_env_or_file CORP_MARKET_SSO_CLIENT_SECRET)"
if [[ -z "$sso_client_id" ]]; then
  sso_client_id="$(value_from_env_or_file EVE_SSO_CLIENT_ID)"
fi
if [[ -z "$sso_client_secret" ]]; then
  sso_client_secret="$(value_from_env_or_file EVE_SSO_CLIENT_SECRET)"
fi

require_value CORP_MARKET_PUBLIC_BASE_URL "$public_base_url"
require_value CORP_MARKET_SSO_CALLBACK_URL "$sso_callback_url"
require_value CORP_MARKET_SSO_CLIENT_ID "$sso_client_id"
require_value CORP_MARKET_SSO_CLIENT_SECRET "$sso_client_secret"

args=(
  -m eve_voice_pilot.corp_market serve
  --host "${CORP_MARKET_HOST:-127.0.0.1}"
  --port "${CORP_MARKET_PORT:-8770}"
  --market-db-path "${CORP_MARKET_MARKET_DB_PATH:-/var/lib/eve-voice-pilot/profiles/corp_market.sqlite3}"
  --public-base-url "$public_base_url"
  --sso-callback-url "$sso_callback_url"
  --sso-client-id "$sso_client_id"
  --sso-client-secret "$sso_client_secret"
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

add_optional_arg --allowed-character-ids "$(value_from_env_or_file CORP_MARKET_ALLOWED_CHARACTER_IDS)"
add_optional_arg --allowed-corporation-ids "$(value_from_env_or_file CORP_MARKET_ALLOWED_CORPORATION_IDS)"
add_optional_arg --allowed-alliance-ids "$(value_from_env_or_file CORP_MARKET_ALLOWED_ALLIANCE_IDS)"
add_optional_arg --admin-token "$(value_from_env_or_file CORP_MARKET_ADMIN_TOKEN)"
add_optional_arg --discord-webhook-url "$(value_from_env_or_file CORP_MARKET_DISCORD_WEBHOOK_URL)"
add_optional_arg --discord-forum-tag-ids "$(value_from_env_or_file CORP_MARKET_DISCORD_FORUM_TAG_IDS)"
add_optional_arg --discord-forum-tag-map "$(value_from_env_or_file CORP_MARKET_DISCORD_FORUM_TAG_MAP)"
add_optional_arg --google-site-verification "$(value_from_env_or_file CORP_MARKET_GOOGLE_SITE_VERIFICATION)"
add_optional_arg --google-site-verification-file "$(value_from_env_or_file CORP_MARKET_GOOGLE_SITE_VERIFICATION_FILE)"

if [[ "${CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET:-0}" == "1" ]]; then
  args+=(--trusted-members-can-write-market)
fi
if [[ "${CORP_MARKET_ALLOW_ANY_AUTHENTICATED:-0}" == "1" ]]; then
  args+=(--allow-any-authenticated)
fi

exec "$PYTHON" "${args[@]}"
