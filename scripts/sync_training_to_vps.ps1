param(
    [string]$Host = "206.245.134.221",
    [string]$User = "root",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\disbot_vps_ed25519",
    [string]$RemoteAppDir = "/opt/dis-bot"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$bundlePath = Join-Path $projectRoot "training_bundle.tar.gz"

Write-Host "[sync] creating training bundle..."
tar -czf $bundlePath `
    -C $projectRoot `
    models `
    datebase/persona.db `
    datebase/parody_filters.db `
    datebase/parody_ratings.db

Write-Host "[sync] uploading bundle to VPS..."
scp -i $KeyPath $bundlePath "${User}@${Host}:${RemoteAppDir}/"

Write-Host "[sync] extracting bundle on VPS..."
ssh -i $KeyPath "${User}@${Host}" "cd ${RemoteAppDir} && tar -xzf training_bundle.tar.gz && rm -f training_bundle.tar.gz && systemctl restart vipik-discord-bot"

Remove-Item -LiteralPath $bundlePath
Write-Host "[sync] done"
