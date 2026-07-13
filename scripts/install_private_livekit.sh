#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dis-bot}"
ENV_FILE="${ENV_FILE:-$APP_DIR/KGTD.env}"
SERVICE_NAME="${SERVICE_NAME:-vipik-livekit}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root" >&2
  exit 2
fi
for command_name in docker tailscale python3; do
  command -v "$command_name" >/dev/null || { echo "$command_name is required" >&2; exit 2; }
done

TAILSCALE_IP="$(tailscale ip -4 | head -n 1)"
TAILSCALE_DNS="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
[ -n "$TAILSCALE_IP" ] && [ -n "$TAILSCALE_DNS" ] || { echo "Tailscale identity not found" >&2; exit 2; }

read_env() {
  local key="$1"
  [ -f "$ENV_FILE" ] && sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 || true
}
upsert_env() {
  local key="$1" value="$2" temp
  temp="$(mktemp)"
  [ -f "$ENV_FILE" ] && grep -v "^${key}=" "$ENV_FILE" > "$temp" || true
  printf '%s=%s\n' "$key" "$value" >> "$temp"
  install -o bot -g bot -m 0600 "$temp" "$ENV_FILE"
  rm -f "$temp"
}

API_KEY="$(read_env LIVEKIT_API_KEY)"
API_SECRET="$(read_env LIVEKIT_API_SECRET)"
[ -n "$API_KEY" ] || API_KEY="$(openssl rand -hex 12)"
[ -n "$API_SECRET" ] || API_SECRET="$(openssl rand -hex 32)"

cat > /etc/vipik-livekit.yaml <<EOF
port: 7880
bind_addresses:
  - "$TAILSCALE_IP"
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false
  node_ip: "$TAILSCALE_IP"
  interfaces:
    includes:
      - tailscale0
  ips:
    includes:
      - 100.64.0.0/10
room:
  empty_timeout: 300
  departure_timeout: 20
  max_participants: 25
logging:
  level: info
  json: true
keys:
  $API_KEY: $API_SECRET
EOF
chmod 0600 /etc/vipik-livekit.yaml

cat > /etc/sysctl.d/99-vipik-livekit.conf <<'EOF'
net.core.rmem_max=5000000
net.core.wmem_max=5000000
EOF
sysctl -p /etc/sysctl.d/99-vipik-livekit.conf >/dev/null

upsert_env LIVEKIT_API_KEY "$API_KEY"
upsert_env LIVEKIT_API_SECRET "$API_SECRET"
upsert_env LIVEKIT_URL "wss://${TAILSCALE_DNS}:8443"
upsert_env APP_BASE_URL "https://${TAILSCALE_DNS}"

install -m 0644 "$APP_DIR/deploy/systemd/vipik-livekit.service.template" "/etc/systemd/system/${SERVICE_NAME}.service"
chmod 0755 "$APP_DIR/scripts/secure_livekit_firewall.sh"
"$APP_DIR/scripts/secure_livekit_firewall.sh"
docker pull livekit/livekit-server:v1.13.1
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"
tailscale serve --yes --bg --https=443 "http://${TAILSCALE_IP}:3000"
tailscale serve --yes --bg --https=8443 "http://${TAILSCALE_IP}:7880"
systemctl restart vipik-web-app.service
systemctl is-active --quiet "${SERVICE_NAME}.service"
systemctl is-active --quiet vipik-web-app.service

echo "App: https://${TAILSCALE_DNS}"
echo "LiveKit: wss://${TAILSCALE_DNS}:8443"
