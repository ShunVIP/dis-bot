param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$BridgeHost = "0.0.0.0",
    [int]$BridgePort = 8787
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot ".model_bridge.pid"

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

$runScript = Join-Path $projectRoot "scripts\run_model_bridge.ps1"
$proc = Start-Process powershell -ArgumentList @(
    "-ExecutionPolicy", "Bypass",
    "-File", $runScript,
    "-Token", $Token,
    "-BridgeHost", $BridgeHost,
    "-BridgePort", $BridgePort
) -PassThru -WindowStyle Normal

Set-Content -Path $pidFile -Value $proc.Id
Write-Host "[bridge] started, PID $($proc.Id), host=$BridgeHost, port=$BridgePort"
