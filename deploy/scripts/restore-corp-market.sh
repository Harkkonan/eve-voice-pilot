#!/usr/bin/env sh
set -eu

ARCHIVE="${1:?usage: restore-corp-market.sh BACKUP.tar.gz [STATE_DIR]}"
STATE_DIR="${2:-/var/lib/eve-voice-pilot}"
CACHE_DIR="${3:-/var/cache/eve-voice-pilot}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$STATE_DIR"
tar -C "$TMP_DIR" -xzf "$ARCHIVE"
SNAPSHOT_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

if [ -d "${SNAPSHOT_DIR}/profiles" ]; then
  mkdir -p "${STATE_DIR}/profiles"
  cp -a "${SNAPSHOT_DIR}/profiles/." "${STATE_DIR}/profiles/"
fi

if [ -d "${SNAPSHOT_DIR}/cache" ]; then
  mkdir -p "$CACHE_DIR"
  cp -a "${SNAPSHOT_DIR}/cache/." "$CACHE_DIR/"
fi

printf 'Restored %s into %s\n' "$ARCHIVE" "$STATE_DIR"
