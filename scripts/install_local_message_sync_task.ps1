param(
    [string]$TaskName = "ViPik Sync Messages DB",
    [string]$DailyAt = "07:30"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$syncScriptPath = Join-Path $projectRoot "scripts\sync_messages_from_vps.ps1"

if (-not (Test-Path $syncScriptPath)) {
    throw "sync_messages_from_vps.ps1 not found: $syncScriptPath"
}

$time = [datetime]::ParseExact($DailyAt, "HH:mm", $null)
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$syncScriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Daily sync of messages.db from VPS for local Markov and ML training" `
    -Force | Out-Null

Write-Host "[task] installed: $TaskName at $DailyAt"
