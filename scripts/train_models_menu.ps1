param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Ask-Choice {
    param(
        [string]$Prompt,
        [string[]]$Allowed
    )

    $allowedText = ($Allowed -join "/")
    $value = Read-Host "$Prompt ($allowedText)"
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Нужно выбрать один из вариантов: $allowedText"
    }

    $value = $value.Trim()
    if ($Allowed -notcontains $value) {
        throw "Неизвестный вариант: $value"
    }

    return $value
}

$syncChoice = Ask-Choice "Сначала скачать свежую messages.db с VPS? 1=Да, 2=Нет" @("1", "2")
if ($syncChoice -eq "1") {
    powershell -ExecutionPolicy Bypass -File ".\scripts\sync_messages_from_vps.ps1"
}

Write-Host ""
$scope = Ask-Choice "Для кого обучать? 1=Для всех пользователей, 2=Для одного пользователя" @("1", "2")
if ($scope -eq "1") {
    powershell -ExecutionPolicy Bypass -File ".\scripts\train_local.ps1" -All
    exit $LASTEXITCODE
}

$userId = Read-Host "Введи Discord user id"
if ([string]::IsNullOrWhiteSpace($userId)) {
    throw "Discord user id не может быть пустым."
}

powershell -ExecutionPolicy Bypass -File ".\scripts\train_local.ps1" -UserId $userId.Trim()
exit $LASTEXITCODE
