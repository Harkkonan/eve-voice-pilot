#!/usr/bin/env sh
set -eu

BASE_URL="${1:?usage: smoke-corp-market.sh https://market.example.com}"
BASE_URL="${BASE_URL%/}"

health="$(curl -fsS "${BASE_URL}/api/health")"
printf '%s' "$health" | grep -q '"ok": true'
robots="$(curl -fsS "${BASE_URL}/robots.txt")"
printf '%s' "$robots" | grep -q 'User-agent: \*'
printf '%s' "$robots" | grep -q "Sitemap: ${BASE_URL}/sitemap.xml"
sitemap="$(curl -fsS "${BASE_URL}/sitemap.xml")"
printf '%s' "$sitemap" | grep -q "<loc>${BASE_URL}/</loc>"

headers="$(mktemp)"
body="$(mktemp)"
cleanup() {
  rm -f "$headers" "$body"
}
trap cleanup EXIT

curl -fsS -D "$headers" -o "$body" "${BASE_URL}/"

grep -qi '^content-security-policy:' "$headers"
grep -qi '^x-content-type-options: nosniff' "$headers"
grep -qi '^x-frame-options: DENY' "$headers"

case "$BASE_URL" in
  https://*) grep -qi '^strict-transport-security:' "$headers" ;;
esac

grep -qi '<title>EVE Flight Attendant | Corp Market Concierge</title>' "$body"
grep -qi '<meta name="description"' "$body"
grep -qi "<link rel=\"canonical\" href=\"${BASE_URL}/\">" "$body"
grep -qi 'EVE Online Flight Attendant' "$body"

printf 'Corp Market smoke passed for %s\n' "$BASE_URL"
