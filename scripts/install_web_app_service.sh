#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dis-bot}"
RUN_USER="${RUN_USER:-bot}"
SERVICE_NAME="${SERVICE_NAME:-vipik-web-app}"
APP_PORT="${APP_PORT:-3000}"
APP_HOST="${APP_HOST:-}"

if [ -z "$APP_HOST" ] && command -v tailscale >/dev/null 2>&1; then
  APP_HOST="$(tailscale ip -4 | head -n 1)"
fi
if [ -z "$APP_HOST" ]; then
  echo "Tailscale IPv4 was not found; set APP_HOST explicitly" >&2
  exit 2
fi

template="$APP_DIR/deploy/systemd/vipik-web-app.service.template"
target="/etc/systemd/system/$SERVICE_NAME.service"
sed \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  -e "s|__APP_HOST__|$APP_HOST|g" \
  -e "s|__APP_PORT__|$APP_PORT|g" \
  "$template" > "$target"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME.service"
systemctl is-active --quiet "$SERVICE_NAME.service"
curl --fail --silent --show-error "http://$APP_HOST:$APP_PORT/health" >/dev/null
systemctl status "$SERVICE_NAME.service" --no-pager --lines=25
