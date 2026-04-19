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

Start-Sleep -Seconds 2
try {
    Invoke-WebRequest -UseBasicParsing "http://${TailscaleIp}:8787/health" | Out-Null
    Write-Host "[bridge] Tailscale access check OK: http://${TailscaleIp}:8787/health"
} catch {
    Write-Warning "Bridge запущен локально, но по Tailscale IP пока недоступен."
    Write-Warning "Скорее всего, Windows Firewall блокирует вход на порт 8787."
    Write-Warning "Запусти PowerShell от имени администратора и выполни:"
    Write-Warning "New-NetFirewallRule -DisplayName 'dis-bot model bridge 8787' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8787"
}

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

($remoteScript -replace "`r`n", "`n") | ssh -i $KeyPath "$VpsUser@$VpsHost" "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось обновить настройки bridge на VPS. Проверь SSH-доступ к $VpsUser@$VpsHost."
}
Write-Host "[bridge] remote heavy models enabled on VPS"
