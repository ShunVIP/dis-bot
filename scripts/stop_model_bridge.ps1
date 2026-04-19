$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot ".model_bridge.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "[bridge] pid file not found"
    exit 0
}

$pidValue = (Get-Content $pidFile | Select-Object -First 1).Trim()
if ($pidValue) {
    $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $pidValue -Force
        Write-Host "[bridge] stopped PID $pidValue"
    } else {
        Write-Host "[bridge] process already stopped"
    }
}

Remove-Item $pidFile -ErrorAction SilentlyContinue
