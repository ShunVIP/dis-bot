param(
    [Parameter(Mandatory = $true)]
    [string]$TailscaleIp,
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$VpsHost = "206.245.134.221",
    [string]$VpsUser = "root",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\disbot_vps_ed25519",
    [string]$RemoteEnvPath = "/opt/dis-bot/KGTD.env"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

& (Join-Path $projectRoot "scripts\start_model_bridge.ps1") -Token $Token

$remoteUrl = "http://${TailscaleIp}:8787"
$remoteScript = @"
python3 - <<'PY'
from pathlib import Path

env_path = Path("$RemoteEnvPath")
content = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
updates = {
    "REMOTE_MODEL_API_URL": "$remoteUrl",
    "REMOTE_MODEL_API_TOKEN": "$Token",
}

out = []
seen = set()
for line in content:
    if "=" in line:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)

for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("updated", env_path)
PY
systemctl restart vipik-discord-bot
systemctl is-active vipik-discord-bot
"@

$remoteScript | ssh -i $KeyPath "$VpsUser@$VpsHost" "bash -s"
Write-Host "[bridge] remote heavy models enabled on VPS"
