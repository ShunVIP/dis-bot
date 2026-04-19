param(
    [string]$VpsHost = "206.245.134.221",
    [string]$VpsUser = "root",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\disbot_vps_ed25519",
    [string]$RemoteEnvPath = "/opt/dis-bot/KGTD.env"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

$remoteScript = @"
set -e
python3 - <<'PY'
from pathlib import Path

env_path = Path("$RemoteEnvPath")
content = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
updates = {
    "REMOTE_MODEL_API_URL": "",
    "REMOTE_MODEL_API_TOKEN": "",
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

$remoteScriptPath = Join-Path $projectRoot ".tmp_disable_remote_models.sh"
[System.IO.File]::WriteAllText($remoteScriptPath, ($remoteScript -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))

try {
    scp -i $KeyPath $remoteScriptPath "${VpsUser}@${VpsHost}:/tmp/dis-bot-disable-remote-models.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось загрузить bridge-скрипт на VPS."
    }

    ssh -i $KeyPath "$VpsUser@$VpsHost" "bash /tmp/dis-bot-disable-remote-models.sh && rm -f /tmp/dis-bot-disable-remote-models.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось отключить bridge на VPS. Проверь SSH-доступ к $VpsUser@$VpsHost."
    }
}
finally {
    Remove-Item -LiteralPath $remoteScriptPath -ErrorAction SilentlyContinue
}
& (Join-Path $projectRoot "scripts\stop_model_bridge.ps1")
Write-Host "[bridge] remote heavy models disabled"
