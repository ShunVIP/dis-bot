param(
    [string]$Token,
    [string]$Host = "127.0.0.1",
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

if (-not $Token) {
    throw "Pass -Token for REMOTE_MODEL_API_TOKEN"
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$env:REMOTE_MODEL_API_TOKEN = $Token
$env:REMOTE_MODEL_API_HOST = $Host
$env:REMOTE_MODEL_API_PORT = "$Port"

python scripts/model_bridge_server.py
