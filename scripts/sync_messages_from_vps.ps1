param(
    [string]$Host = "206.245.134.221",
    [string]$User = "root",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\disbot_vps_ed25519",
    [string]$RemoteDbPath = "/opt/dis-bot/datebase/messages.db"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $projectRoot "datebase"
$localDbPath = Join-Path $localDir "messages.db"

New-Item -ItemType Directory -Force -Path $localDir | Out-Null

Write-Host "[sync] downloading messages.db from VPS..."
scp -i $KeyPath "${User}@${Host}:${RemoteDbPath}" $localDbPath

Write-Host "[sync] done -> $localDbPath"
