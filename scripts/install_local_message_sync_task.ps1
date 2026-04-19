param(
    [string]$TaskName = "ViPik Sync Messages DB",
    [string]$DailyAt = "07:30"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$batPath = Join-Path $projectRoot "sync_messages.bat"

if (-not (Test-Path $batPath)) {
    throw "sync_messages.bat not found: $batPath"
}

$time = [datetime]::ParseExact($DailyAt, "HH:mm", $null)
$action = New-ScheduledTaskAction -Execute $batPath
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Daily sync of messages.db from VPS for local GPT training" `
    -Force | Out-Null

Write-Host "[task] installed: $TaskName at $DailyAt"
