#!/usr/bin/env bash
set -euo pipefail

add_rule() {
  local command_name="$1" protocol="$2" port="$3"
  command -v "$command_name" >/dev/null 2>&1 || return 0
  if ! "$command_name" -C INPUT ! -i tailscale0 -p "$protocol" --dport "$port" -j DROP 2>/dev/null; then
    "$command_name" -I INPUT 1 ! -i tailscale0 -p "$protocol" --dport "$port" -j DROP
  fi
}

for firewall in iptables ip6tables; do
  add_rule "$firewall" tcp 7881
  add_rule "$firewall" udp 7882
done
