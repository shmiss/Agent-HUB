#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${HERMES_UI_PORT:=8765}"
: "${HERMES_BASE_URL:=http://127.0.0.1:8642/v1}"
: "${HERMES_MODEL:=hermes-agent}"

find_port_owner() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    return
  fi
  python3 - "$port" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
except OSError:
    print(f"port {port} is in use")
finally:
    s.close()
PY
}

if find_port_owner "$HERMES_UI_PORT" | grep -q .; then
  echo "Port $HERMES_UI_PORT is already in use."
  echo
  echo "Current listener:"
  find_port_owner "$HERMES_UI_PORT"
  echo
  echo "You can either:"
  echo "  1. Stop the old process and restart"
  echo "     Example: lsof -ti tcp:$HERMES_UI_PORT | xargs kill"
  echo "  2. Start this UI on another port"
  echo "     Example: HERMES_UI_PORT=8766 HERMES_BASE_URL=$HERMES_BASE_URL HERMES_MODEL=$HERMES_MODEL ./run.sh"
  exit 1
fi

export HERMES_UI_PORT HERMES_BASE_URL HERMES_MODEL
exec python3 server.py
