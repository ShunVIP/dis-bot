#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dis-bot}"
BRANCH="${BRANCH:-main}"
REPO_URL="${REPO_URL:-https://github.com/ShunVIP/dis-bot.git}"
SERVICE_NAME="${SERVICE_NAME:-vipik-discord-bot}"
UNIT_NAME="$SERVICE_NAME"

if [[ "$UNIT_NAME" != *.service ]]; then
  UNIT_NAME="${UNIT_NAME}.service"
fi

log() {
  printf '[deploy] %s\n' "$1"
}

run_root() {
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

if ! command -v git >/dev/null 2>&1; then
  log "git is not installed"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  log "python3 is not installed"
  exit 1
fi

mkdir -p "$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
  log "cloning repository into $APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

log "fetching latest changes"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [ ! -d ".venv" ]; then
  log "creating virtual environment"
  python3 -m venv .venv
fi

log "installing dependencies"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

mkdir -p datebase models

if [ ! -f "KGTD.env" ]; then
  log "warning: KGTD.env is missing in $APP_DIR"
fi

if command -v systemctl >/dev/null 2>&1; then
  if run_root systemctl cat "$UNIT_NAME" >/dev/null 2>&1; then
    log "restarting systemd service $UNIT_NAME"
    run_root systemctl daemon-reload
    run_root systemctl restart "$UNIT_NAME"
    run_root systemctl status "$UNIT_NAME" --no-pager --lines=20 || true
  else
    log "systemd service $UNIT_NAME is not installed yet"
  fi
else
  log "systemctl not found; service was not restarted"
fi

log "deploy finished"
