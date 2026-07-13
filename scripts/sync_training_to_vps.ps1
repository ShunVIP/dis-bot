param(
    [string]$VpsHost = "206.245.134.221",
    [string]$VpsUser = "root",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\disbot_vps_ed25519",
    [string]$RemoteAppDir = "/opt/dis-bot"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$bundlePath = Join-Path $projectRoot "training_bundle.tar.gz"
$modelsDir = Join-Path $projectRoot "models"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $modelsDir)) {
    throw "models directory not found: $modelsDir"
}

$pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }
Write-Host "[sync] refreshing versioned ML manifest..."
& $pythonExe (Join-Path $PSScriptRoot "build_ml_manifest.py") --require-artifacts
if ($LASTEXITCODE -ne 0) {
    throw "ML manifest build failed with exit code $LASTEXITCODE"
}

Write-Host "[sync] creating training bundle..."
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
    datebase/parody_filters.db `
    datebase/parody_ratings.db
Remove-Item -LiteralPath $tempRoot -Recurse -Force

Write-Host "[sync] uploading bundle to VPS..."
scp -i $KeyPath $bundlePath "${VpsUser}@${VpsHost}:${RemoteAppDir}/"

Write-Host "[sync] extracting bundle on VPS..."
ssh -i $KeyPath "${VpsUser}@${VpsHost}" "cd ${RemoteAppDir} && tar -xzf training_bundle.tar.gz && rm -f training_bundle.tar.gz && systemctl restart vipik-discord-bot"

Remove-Item -LiteralPath $bundlePath
Write-Host "[sync] done"
