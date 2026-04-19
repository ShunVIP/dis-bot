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

$env:REMOTE_MODEL_API_TOKEN = $Token
$env:REMOTE_MODEL_API_HOST = $BridgeHost
$env:REMOTE_MODEL_API_PORT = "$BridgePort"

python scripts/model_bridge_server.py
