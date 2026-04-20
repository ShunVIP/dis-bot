param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$distDir = Join-Path $projectRoot "dist"
$buildDir = Join-Path $projectRoot "build"
$specFile = Join-Path $projectRoot "ViPikBotControl.spec"

if (Test-Path $distDir) {
    Remove-Item -LiteralPath $distDir -Recurse -Force
}
if (Test-Path $buildDir) {
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
if (Test-Path $specFile) {
    Remove-Item -LiteralPath $specFile -Force
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "ViPikBotControl" `
    --distpath "$distDir" `
    --workpath "$buildDir" `
    "scripts\bot_control_gui.py"

Write-Host ""
Write-Host "Готово: $distDir\ViPikBotControl.exe" -ForegroundColor Green
