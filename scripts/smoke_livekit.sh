#!/usr/bin/env bash
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/dis-bot}"
ENV_FILE="${ENV_FILE:-$APP_DIR/KGTD.env}"
LIVEKIT_SMOKE_URL="${LIVEKIT_SMOKE_URL:-ws://$(tailscale ip -4 | head -n 1):7880}"
ROOM_NAME="vipik-media-smoke-$(date +%s)"
PUBLISHER_LOG="$(mktemp)"
SUBSCRIBER_LOG="$(mktemp)"

cleanup() {
  rm -f "$PUBLISHER_LOG" "$SUBSCRIBER_LOG"
}
trap cleanup EXIT

command -v lk >/dev/null 2>&1 || { echo "LiveKit CLI is required" >&2; exit 2; }
API_KEY="$(sed -n 's/^LIVEKIT_API_KEY=//p' "$ENV_FILE" | tail -n 1)"
API_SECRET="$(sed -n 's/^LIVEKIT_API_SECRET=//p' "$ENV_FILE" | tail -n 1)"
[ -n "$API_KEY" ] && [ -n "$API_SECRET" ] || { echo "LiveKit credentials are missing" >&2; exit 2; }

timeout 18s lk \
  --url "$LIVEKIT_SMOKE_URL" --api-key "$API_KEY" --api-secret "$API_SECRET" \
  room join --identity smoke-publisher --publish-demo --auto-subscribe "$ROOM_NAME" \
  >"$PUBLISHER_LOG" 2>&1 &
PUBLISHER_PID=$!
sleep 3

timeout 10s lk \
  --url "$LIVEKIT_SMOKE_URL" --api-key "$API_KEY" --api-secret "$API_SECRET" \
  room join --identity smoke-subscriber --auto-subscribe "$ROOM_NAME" \
  >"$SUBSCRIBER_LOG" 2>&1
SUBSCRIBER_STATUS=$?
wait "$PUBLISHER_PID"
PUBLISHER_STATUS=$?

echo "publisher_status=$PUBLISHER_STATUS subscriber_status=$SUBSCRIBER_STATUS"
echo "--- publisher ---"
grep -E "participant|track|connect|subscrib|publication|quality" "$PUBLISHER_LOG" || cat "$PUBLISHER_LOG"
echo "--- subscriber ---"
grep -E "participant|track|connect|subscrib|publication|quality" "$SUBSCRIBER_LOG" || cat "$SUBSCRIBER_LOG"

grep -q "participant connected" "$PUBLISHER_LOG" || exit 1
if grep -q "could not connect after timeout" "$PUBLISHER_LOG" "$SUBSCRIBER_LOG"; then
  exit 1
fi
test "$PUBLISHER_STATUS" -eq 124
test "$SUBSCRIBER_STATUS" -eq 124
