param(
    [string]$Host = "206.245.134.221",
    [string]$User = "root",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\disbot_vps_ed25519",
    [string]$RemoteAppDir = "/opt/dis-bot",
    [switch]$IncludeGpt
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$bundlePath = Join-Path $projectRoot "training_bundle.tar.gz"
$modelsDir = Join-Path $projectRoot "models"

if (-not (Test-Path $modelsDir)) {
    throw "models directory not found: $modelsDir"
}

Write-Host "[sync] creating training bundle..."
if ($IncludeGpt) {
    Write-Host "[sync] include GPT models: yes"
    tar -czf $bundlePath `
        -C $projectRoot `
        models `
        datebase/persona.db `
        datebase/parody_filters.db `
        datebase/parody_ratings.db
}
else {
    Write-Host "[sync] include GPT models: no"
    if (Test-Path $bundlePath) {
        Remove-Item -LiteralPath $bundlePath -Force
    }
    $tempRoot = Join-Path $projectRoot ".sync_models_tmp"
    $tempModels = Join-Path $tempRoot "models"
    if (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $tempModels | Out-Null
    Get-ChildItem -Path $modelsDir -File | Copy-Item -Destination $tempModels
    tar -czf $bundlePath `
        -C $tempRoot `
        models `
        -C $projectRoot `
        datebase/persona.db `
        datebase/parody_filters.db `
        datebase/parody_ratings.db
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}

Write-Host "[sync] uploading bundle to VPS..."
scp -i $KeyPath $bundlePath "${User}@${Host}:${RemoteAppDir}/"

Write-Host "[sync] extracting bundle on VPS..."
ssh -i $KeyPath "${User}@${Host}" "cd ${RemoteAppDir} && tar -xzf training_bundle.tar.gz && rm -f training_bundle.tar.gz && systemctl restart vipik-discord-bot"

Remove-Item -LiteralPath $bundlePath
Write-Host "[sync] done"
