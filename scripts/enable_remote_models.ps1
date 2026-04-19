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
    Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/health" | Out-Null
    Write-Host "[bridge] local health check OK: http://127.0.0.1:8787/health"
} catch {
    throw "Локальный bridge не отвечает на http://127.0.0.1:8787/health"
}

$remoteUrl = "http://${TailscaleIp}:8787"
$remoteScript = @"
set -e
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

$remoteScriptPath = Join-Path $projectRoot ".tmp_enable_remote_models.sh"
[System.IO.File]::WriteAllText($remoteScriptPath, ($remoteScript -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))

try {
    scp -i $KeyPath $remoteScriptPath "${VpsUser}@${VpsHost}:/tmp/dis-bot-enable-remote-models.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось загрузить bridge-скрипт на VPS."
    }

    ssh -i $KeyPath "$VpsUser@$VpsHost" "bash /tmp/dis-bot-enable-remote-models.sh && rm -f /tmp/dis-bot-enable-remote-models.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось обновить настройки bridge на VPS. Проверь SSH-доступ к $VpsUser@$VpsHost."
    }
}
finally {
    Remove-Item -LiteralPath $remoteScriptPath -ErrorAction SilentlyContinue
}
Write-Host "[bridge] remote heavy models enabled on VPS"
