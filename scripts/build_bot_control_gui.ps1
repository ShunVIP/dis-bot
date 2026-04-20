param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$distDir = Join-Path $projectRoot "dist_build"
$buildDir = Join-Path $projectRoot "build"
$specFile = Join-Path $projectRoot "ViPikBotControl.spec"
$finalDistDir = Join-Path $projectRoot "dist"
$finalExe = Join-Path $finalDistDir "ViPikBotControl.exe"
$builtExe = Join-Path $distDir "ViPikBotControl.exe"

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

if (-not (Test-Path $finalDistDir)) {
    New-Item -ItemType Directory -Path $finalDistDir | Out-Null
}

$copiedToFinal = $false
try {
    Copy-Item -LiteralPath $builtExe -Destination $finalExe -Force
    $copiedToFinal = $true
}
catch {
    Write-Host "Не удалось перезаписать $finalExe, возможно файл сейчас открыт." -ForegroundColor Yellow
}

Write-Host ""
if ($copiedToFinal) {
    Write-Host "Готово: $finalExe" -ForegroundColor Green
}
else {
    Write-Host "Готово: $builtExe" -ForegroundColor Green
}
