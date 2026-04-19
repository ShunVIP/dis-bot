$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot ".model_bridge.pid"
$runtimeConfigPath = Join-Path $projectRoot ".model_bridge.runtime.json"
$bridgePort = 8787

if (-not (Test-Path $pidFile)) {
    Write-Host "[bridge] pid file not found"
} else {
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
}

$portOwner = netstat -ano | Select-String (":{0}" -f $bridgePort) | Select-String "LISTENING" | Select-Object -First 1
if ($portOwner) {
    $portPid = (($portOwner.ToString() -split '\s+') | Select-Object -Last 1).Trim()
    if ($portPid) {
        $proc = Get-Process -Id $portPid -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $portPid -Force -ErrorAction SilentlyContinue
            Write-Host "[bridge] stopped stale port owner PID $portPid"
        }
    }
}

Remove-Item $runtimeConfigPath -ErrorAction SilentlyContinue
