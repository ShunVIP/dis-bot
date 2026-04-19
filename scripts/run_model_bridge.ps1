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
$runtimeConfigPath = Join-Path $projectRoot ".model_bridge.runtime.json"

$pythonExe = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
if (-not (Get-Command $pythonExe -ErrorAction SilentlyContinue) -and $pythonExe -eq "python") {
    throw "Python not found. Install dependencies first or create .venv."
}

$runtimeConfig = @{
    token = $Token
    host  = $BridgeHost
    port  = $BridgePort
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($runtimeConfigPath, $runtimeConfig, (New-Object System.Text.UTF8Encoding($false)))

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$projectRoot;$env:PYTHONPATH" } else { $projectRoot }

& $pythonExe "scripts/model_bridge_server.py"
