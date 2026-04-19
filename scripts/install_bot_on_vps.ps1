param(
    [string]$VpsHost = "206.245.134.221",
    [string]$VpsUser = "root",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\disbot_vps_ed25519",
    [string]$RemoteAppDir = "/opt/dis-bot",
    [string]$RunUser = "bot",
    [string]$ServiceName = "vipik-discord-bot"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$bundlePath = Join-Path $projectRoot "vps_install_bundle.tar.gz"

if (Test-Path $bundlePath) {
    Remove-Item -LiteralPath $bundlePath -Force
}

Write-Host "[vps] собираю установочный пакет..."
tar -czf $bundlePath `
    --exclude=.git `
    --exclude=.venv `
    --exclude=__pycache__ `
    --exclude=models `
    --exclude=bot.log `
    --exclude=KGTD.env `
    --exclude='datebase/*.db' `
    -C $projectRoot .

Write-Host "[vps] загружаю пакет на сервер..."
scp -i $KeyPath $bundlePath "${VpsUser}@${VpsHost}:/tmp/dis-bot-install.tar.gz"
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось загрузить установочный пакет на VPS. Проверь SSH-доступ к $VpsUser@$VpsHost."
}

$remoteScript = @"
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y python3 python3-venv python3-pip git tar

if ! id "$RunUser" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$RunUser"
fi

mkdir -p "$RemoteAppDir"
tar -xzf /tmp/dis-bot-install.tar.gz -C "$RemoteAppDir"
rm -f /tmp/dis-bot-install.tar.gz
chown -R "$RunUser:$RunUser" "$RemoteAppDir"

if command -v python3 >/dev/null 2>&1; then
python3 - <<'PY'
elif command -v python >/dev/null 2>&1; then
python - <<'PY'
else
  echo "python is not installed on VPS" >&2
  exit 1
fi
from pathlib import Path
app_dir = Path("$RemoteAppDir")
template = app_dir / "deploy" / "systemd" / "vipik-discord-bot.service.template"
target = Path("/etc/systemd/system/$ServiceName.service")
content = template.read_text(encoding="utf-8")
content = content.replace("__APP_DIR__", "$RemoteAppDir").replace("__RUN_USER__", "$RunUser")
target.write_text(content, encoding="utf-8")
print("written", target)
PY

runuser -u "$RunUser" -- python3 -m venv "$RemoteAppDir/.venv"
"$RemoteAppDir/.venv/bin/pip" install --upgrade pip
"$RemoteAppDir/.venv/bin/pip" install -r "$RemoteAppDir/requirements.txt"

mkdir -p "$RemoteAppDir/datebase" "$RemoteAppDir/models"
touch "$RemoteAppDir/KGTD.env"
chown "$RunUser:$RunUser" "$RemoteAppDir/KGTD.env"
chmod 600 "$RemoteAppDir/KGTD.env"

systemctl daemon-reload
systemctl enable "$ServiceName.service"
systemctl restart "$ServiceName.service" || true
systemctl status "$ServiceName.service" --no-pager --lines=20 || true
"@

Write-Host "[vps] запускаю первичную установку..."
($remoteScript -replace "`r`n", "`n") | ssh -i $KeyPath "${VpsUser}@${VpsHost}" "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось завершить установку на VPS. Проверь SSH-доступ к $VpsUser@$VpsHost."
}

Remove-Item -LiteralPath $bundlePath -Force
Write-Host "[vps] готово. Заполни $RemoteAppDir/KGTD.env на сервере и перезапусти сервис."
