param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$BridgeHost = "0.0.0.0",
    [int]$BridgePort = 8787
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot ".model_bridge.pid"
$portPattern = ":{0}" -f $BridgePort
$runtimeConfigPath = Join-Path $projectRoot ".model_bridge.runtime.json"

if (Test-Path $pidFile) {
    $existingPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($existingPid) {
        $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "[bridge] already running with PID $existingPid"
            exit 0
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

$portOwner = netstat -ano | Select-String $portPattern | Select-String "LISTENING" | Select-Object -First 1
if ($portOwner) {
    $existingPid = (($portOwner.ToString() -split '\s+') | Select-Object -Last 1).Trim()
    if ($existingPid) {
        $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "[bridge] port $BridgePort already used by PID $existingPid, stopping stale process"
            Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
    }
}

$runScript = Join-Path $projectRoot "scripts\run_model_bridge.ps1"
$runtimeConfig = @{
    token = $Token
    host  = $BridgeHost
    port  = $BridgePort
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($runtimeConfigPath, $runtimeConfig, (New-Object System.Text.UTF8Encoding($false)))

$proc = Start-Process -FilePath "powershell.exe" `
    -ArgumentList @(
        "-NoExit",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $runScript,
        "-Token",
        $Token,
        "-BridgeHost",
        $BridgeHost,
        "-BridgePort",
        "$BridgePort"
    ) `
    -WorkingDirectory $projectRoot `
    -PassThru

Set-Content -Path $pidFile -Value $proc.Id
Write-Host "[bridge] launched in a new window, PID $($proc.Id), host=$BridgeHost, port=$BridgePort"
