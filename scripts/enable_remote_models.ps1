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
$runtimeConfigPath = Join-Path $projectRoot ".model_bridge.runtime.json"

if (Test-Path $runtimeConfigPath) {
    try {
        $runtimeConfig = Get-Content -LiteralPath $runtimeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $runtimeToken = [string]$runtimeConfig.token
        if (-not [string]::IsNullOrWhiteSpace($runtimeToken)) {
            if ($Token -and $Token -ne $runtimeToken) {
                Write-Host "[bridge] token from running bridge differs from manual input, using runtime token" -ForegroundColor Yellow
            }
            $Token = $runtimeToken
        }
    }
    catch {
        Write-Host "[bridge] warning: failed to read runtime token, using manual input" -ForegroundColor Yellow
    }
}

Write-Host "[bridge] checking existing local bridge..."
$healthOk = $false
for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
        Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/health" | Out-Null
        $healthOk = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $healthOk) {
    throw "Локальный bridge не отвечает. Сначала запусти пункт 7 и оставь окно bridge открытым."
}
Write-Host "[bridge] local health check OK: http://127.0.0.1:8787/health"

try {
    $probeBody = @{ user_id = 0 } | ConvertTo-Json -Compress
    Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "http://127.0.0.1:8787/model_exists" `
        -Headers @{ "X-Model-Token" = $Token } `
        -ContentType "application/json" `
        -Body $probeBody | Out-Null
    Write-Host "[bridge] local auth check OK"
} catch {
    throw "Локальный bridge не прошёл проверку токена на /model_exists. Запусти bridge заново через пункт 7 с тем же токеном."
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
