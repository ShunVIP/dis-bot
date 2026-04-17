#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dis-bot}"
REPO_URL="${REPO_URL:-https://github.com/ShunVIP/dis-bot.git}"
RUN_USER="${RUN_USER:-bot}"
SERVICE_NAME="${SERVICE_NAME:-vipik-discord-bot}"
BRANCH="${BRANCH:-main}"
SKIP_CLONE="${SKIP_CLONE:-0}"
ENABLE_PULL_TIMER="${ENABLE_PULL_TIMER:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_PATH="$REPO_ROOT/deploy/systemd/vipik-discord-bot.service.template"
TMP_SERVICE="/tmp/${SERVICE_NAME}.service"
TMP_UPDATE_SERVICE="/tmp/${SERVICE_NAME}-update.service"
TMP_UPDATE_TIMER="/tmp/${SERVICE_NAME}-update.timer"

log() {
  printf '[bootstrap] %s\n' "$1"
}

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    log "root or sudo is required for: $*"
    exit 1
  fi
}

run_as_user() {
  if [ "$(id -u)" -eq 0 ]; then
    runuser -u "$RUN_USER" -- "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "$RUN_USER" "$@"
  else
    log "sudo is required to run as $RUN_USER"
    exit 1
  fi
}

if command -v apt-get >/dev/null 2>&1; then
  log "installing system packages via apt"
  run_root apt-get update
  run_root apt-get install -y git python3 python3-venv python3-pip
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
  log "creating user $RUN_USER"
  run_root useradd --system --create-home --shell /bin/bash "$RUN_USER"
fi

if [ "$SKIP_CLONE" != "1" ] && [ ! -d "$APP_DIR/.git" ]; then
  log "cloning repository into $APP_DIR"
  run_root mkdir -p "$(dirname "$APP_DIR")"
  run_root git clone "$REPO_URL" "$APP_DIR"
fi

run_root mkdir -p "$APP_DIR"
run_root chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

log "deploying latest code"
run_as_user env APP_DIR="$APP_DIR" REPO_URL="$REPO_URL" BRANCH="$BRANCH" SERVICE_NAME="$SERVICE_NAME" SKIP_GIT_PULL="$SKIP_CLONE" bash "$APP_DIR/scripts/deploy.sh"

if [ ! -f "$TEMPLATE_PATH" ] && [ -f "$APP_DIR/deploy/systemd/vipik-discord-bot.service.template" ]; then
  TEMPLATE_PATH="$APP_DIR/deploy/systemd/vipik-discord-bot.service.template"
fi

if [ ! -f "$TEMPLATE_PATH" ]; then
  log "service template not found"
  exit 1
fi

sed \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  "$TEMPLATE_PATH" > "$TMP_SERVICE"

run_root mv "$TMP_SERVICE" "/etc/systemd/system/${SERVICE_NAME}.service"

UPDATE_SERVICE_TEMPLATE="$REPO_ROOT/deploy/systemd/vipik-discord-bot-update.service.template"
UPDATE_TIMER_TEMPLATE="$REPO_ROOT/deploy/systemd/vipik-discord-bot-update.timer.template"

if [ ! -f "$UPDATE_SERVICE_TEMPLATE" ] && [ -f "$APP_DIR/deploy/systemd/vipik-discord-bot-update.service.template" ]; then
  UPDATE_SERVICE_TEMPLATE="$APP_DIR/deploy/systemd/vipik-discord-bot-update.service.template"
fi

if [ ! -f "$UPDATE_TIMER_TEMPLATE" ] && [ -f "$APP_DIR/deploy/systemd/vipik-discord-bot-update.timer.template" ]; then
  UPDATE_TIMER_TEMPLATE="$APP_DIR/deploy/systemd/vipik-discord-bot-update.timer.template"
fi

if [ -f "$UPDATE_SERVICE_TEMPLATE" ]; then
  sed -e "s|__APP_DIR__|$APP_DIR|g" "$UPDATE_SERVICE_TEMPLATE" > "$TMP_UPDATE_SERVICE"
  run_root mv "$TMP_UPDATE_SERVICE" "/etc/systemd/system/${SERVICE_NAME}-update.service"
fi

if [ -f "$UPDATE_TIMER_TEMPLATE" ]; then
  sed -e "s|vipik-discord-bot|$SERVICE_NAME|g" "$UPDATE_TIMER_TEMPLATE" > "$TMP_UPDATE_TIMER"
  run_root mv "$TMP_UPDATE_TIMER" "/etc/systemd/system/${SERVICE_NAME}-update.timer"
fi

run_root systemctl daemon-reload
run_root systemctl enable "${SERVICE_NAME}.service"
run_root systemctl restart "${SERVICE_NAME}.service"
if [ "$ENABLE_PULL_TIMER" = "1" ] && [ -f "/etc/systemd/system/${SERVICE_NAME}-update.timer" ]; then
  run_root systemctl enable "${SERVICE_NAME}-update.timer"
  run_root systemctl restart "${SERVICE_NAME}-update.timer"
fi

log "bootstrap completed"
log "create $APP_DIR/KGTD.env before expecting the bot to connect successfully"
