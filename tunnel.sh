#!/usr/bin/env bash
# Start the dashboard and expose it on a public https:// URL via a Cloudflare
# quick tunnel. Every argument is passed straight through to `python -m exotel`.
#
#   ./tunnel.sh --demo
#   ./tunnel.sh --source http://10.210.60.121 --user stridemate --password secret
#
# The tunnel is ephemeral: no Cloudflare account, no DNS, no open ports. It dies
# with this script. The URL is random but NOT secret, so the dashboard's token
# is what actually protects the feed - it is appended to the URL printed below.

set -euo pipefail

PORT="${EXOTEL_PORT:-8787}"
PY="${PYTHON:-python3}"

if ! command -v cloudflared >/dev/null 2>&1; then
  cat >&2 <<'MSG'
cloudflared is not installed.

  macOS         brew install cloudflared
  Debian/Ubuntu see https://pkg.cloudflare.com
  other         https://github.com/cloudflare/cloudflared/releases

Running without a tunnel is fine too - just use:  python -m exotel --demo
MSG
  exit 1
fi

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then kill "$SERVER_PID" 2>/dev/null || true; fi
  if [[ -n "${TUNNEL_PID:-}" ]]; then kill "$TUNNEL_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

LOG="$(mktemp -t exotel-server)"
TLOG="$(mktemp -t exotel-tunnel)"

"$PY" -m exotel --port "$PORT" "$@" >"$LOG" 2>&1 &
SERVER_PID=$!

# Wait for the server to announce its token.
TOKEN=""
for _ in $(seq 1 60); do
  TOKEN="$(grep -m1 '^EXOTEL_TOKEN=' "$LOG" 2>/dev/null | cut -d= -f2- || true)"
  [[ -n "$TOKEN" ]] && break
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "server exited:" >&2; cat "$LOG" >&2; exit 1; }
  sleep 0.25
done
[[ -z "$TOKEN" ]] && { echo "server never printed a token:" >&2; cat "$LOG" >&2; exit 1; }

cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate >"$TLOG" 2>&1 &
TUNNEL_PID=$!

# Wait for the quick tunnel hostname.
URL=""
for _ in $(seq 1 120); do
  URL="$(grep -om1 'https://[a-z0-9-]*\.trycloudflare\.com' "$TLOG" || true)"
  [[ -n "$URL" ]] && break
  kill -0 "$TUNNEL_PID" 2>/dev/null || { echo "cloudflared exited:" >&2; tail -20 "$TLOG" >&2; exit 1; }
  sleep 0.5
done
[[ -z "$URL" ]] && { echo "no tunnel URL after 60s:" >&2; tail -20 "$TLOG" >&2; exit 1; }

FULL="${URL}/?k=${TOKEN}"
printf '%s\n' "$FULL" > .tunnel-url

echo
echo "  Public dashboard"
echo "  $FULL"
echo
echo "  Local            http://127.0.0.1:${PORT}/?k=${TOKEN}"
echo "  Server log       $LOG"
echo
echo "  Anyone with that link can watch the feed. The tunnel and the token both"
echo "  disappear when you stop this script (Ctrl-C)."
echo

# QR code for handing the link to a phone, if a generator is around.
if command -v qrencode >/dev/null 2>&1; then
  qrencode -t ANSIUTF8 "$FULL"
fi

wait "$SERVER_PID"
