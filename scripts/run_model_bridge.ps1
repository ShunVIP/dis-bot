param(
    [string]$Token,
    [string]$BridgeHost = "127.0.0.1",
    [int]$BridgePort = 8787
)

$ErrorActionPreference = "Stop"

if (-not $Token) {
    throw "Pass -Token for REMOTE_MODEL_API_TOKEN"
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$pythonExe = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
if (-not (Get-Command $pythonExe -ErrorAction SilentlyContinue) -and $pythonExe -eq "python") {
    throw "Python not found. Install dependencies first or create .venv."
}

$env:REMOTE_MODEL_API_TOKEN = $Token
$env:REMOTE_MODEL_API_HOST = $BridgeHost
$env:REMOTE_MODEL_API_PORT = "$BridgePort"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$projectRoot;$env:PYTHONPATH" } else { $projectRoot }

& $pythonExe "scripts/model_bridge_server.py"
