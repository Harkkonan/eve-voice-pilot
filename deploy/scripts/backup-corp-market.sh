#!/usr/bin/env sh
set -eu

STATE_DIR="${1:-/var/lib/eve-voice-pilot}"
BACKUP_DIR="${2:-/var/backups/eve-voice-pilot}"
CACHE_DIR="${3:-/var/cache/eve-voice-pilot}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_DIR}/corp-market-${STAMP}"

mkdir -p "$DEST"

if [ -d "${STATE_DIR}/profiles" ]; then
  cp -a "${STATE_DIR}/profiles" "$DEST/"
fi

if [ -d "${STATE_DIR}/cache" ]; then
  cp -a "${STATE_DIR}/cache" "$DEST/"
elif [ -d "$CACHE_DIR" ]; then
  mkdir -p "${DEST}/cache"
  cp -a "${CACHE_DIR}/." "${DEST}/cache/"
fi

tar -C "$BACKUP_DIR" -czf "${DEST}.tar.gz" "corp-market-${STAMP}"
rm -rf "$DEST"

printf '%s\n' "${DEST}.tar.gz"
