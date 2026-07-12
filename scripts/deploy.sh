#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dis-bot}"
SERVICE_NAME="${SERVICE_NAME:-vipik-discord-bot}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$APP_DIR"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi

"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -m compileall -q \
  "$APP_DIR/core" "$APP_DIR/fun_slesh" "$APP_DIR/scheduled" "$APP_DIR/web_app" "$APP_DIR/main_file.py"

if [ -d "$APP_DIR/tests" ]; then
  "$APP_DIR/.venv/bin/python" -m unittest discover -s "$APP_DIR/tests" -v
fi

systemctl restart "$SERVICE_NAME.service"
systemctl is-active --quiet "$SERVICE_NAME.service"
systemctl status "$SERVICE_NAME.service" --no-pager --lines=30
