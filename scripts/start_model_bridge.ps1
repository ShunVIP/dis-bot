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

$pythonExe = if (Test-Path (Join-Path $projectRoot ".venv\Scripts\python.exe")) {
    (Join-Path $projectRoot ".venv\Scripts\python.exe")
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python not found. Install dependencies first or create .venv."
    }
    $pythonCommand.Source
}

$env:REMOTE_MODEL_API_TOKEN = $Token
$env:REMOTE_MODEL_API_HOST = $BridgeHost
$env:REMOTE_MODEL_API_PORT = "$BridgePort"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$projectRoot;$env:PYTHONPATH" } else { $projectRoot }

$proc = Start-Process -FilePath $pythonExe `
    -ArgumentList "scripts/model_bridge_server.py" `
    -WorkingDirectory $projectRoot `
    -PassThru `
    -WindowStyle Hidden

Set-Content -Path $pidFile -Value $proc.Id
Write-Host "[bridge] started, PID $($proc.Id), host=$BridgeHost, port=$BridgePort"
