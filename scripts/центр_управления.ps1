param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$localConfigPath = Join-Path $projectRoot ".control_center.local.json"

function Get-LocalSettings {
    if (-not (Test-Path $localConfigPath)) {
        return @{}
    }

    try {
        $raw = Get-Content -LiteralPath $localConfigPath -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return @{}
        }

        $data = ConvertFrom-Json $raw -AsHashtable
        if ($null -eq $data) {
            return @{}
        }
        return $data
    }
    catch {
        return @{}
    }
}

function Save-LocalSettings([hashtable]$Settings) {
    $json = $Settings | ConvertTo-Json -Depth 5
    Set-Content -LiteralPath $localConfigPath -Value $json -Encoding UTF8
}

function Get-SavedTailscaleIp {
    $settings = Get-LocalSettings
    $savedIp = ""
    if ($settings.ContainsKey("tailscale_ip")) {
        $savedIp = [string]$settings["tailscale_ip"]
    }

    if (-not [string]::IsNullOrWhiteSpace($savedIp)) {
        return $savedIp.Trim()
    }

    $tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if (-not $tailscaleCmd) {
        return ""
    }

    try {
        $detectedIp = (& $tailscaleCmd.Source ip -4 2>$null | Select-Object -First 1).Trim()
        return $detectedIp
    }
    catch {
        return ""
    }
}

function Save-TailscaleIp([string]$Ip) {
    if ([string]::IsNullOrWhiteSpace($Ip)) {
        return
    }

    $settings = Get-LocalSettings
    $settings["tailscale_ip"] = $Ip.Trim()
    Save-LocalSettings $settings
}

function Prompt-WithDefault([string]$Prompt, [string]$DefaultValue) {
    if ([string]::IsNullOrWhiteSpace($DefaultValue)) {
        return (Read-Host $Prompt)
    }

    $value = Read-Host "$Prompt [$DefaultValue]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }

    return $value.Trim()
}

function Pause-Continue {
    Write-Host ""
    Read-Host "Нажми Enter чтобы вернуться в меню"
}

function Ask-YesNo([string]$Prompt, [bool]$DefaultYes = $true) {
    $hint = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    $raw = Read-Host "$Prompt $hint"
    if ([string]::IsNullOrWhiteSpace($raw)) { return $DefaultYes }
    return $raw.Trim().ToLower() -in @("y","yes","д","да")
}

function Run-Cmd([scriptblock]$Action) {
    try {
        & $Action
    }
    catch {
        Write-Host ""
        Write-Host "Ошибка: $($_.Exception.Message)" -ForegroundColor Red
    }
    Pause-Continue
}

function Show-Bridge-Guide {
    Clear-Host
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "         НАСТРОЙКА ТЯЖЕЛОГО BRIDGE        " -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Bridge нужен только если хочешь, чтобы VPS использовал"
    Write-Host "тяжелую GPT-модель, которая физически лежит на твоем ПК."
    Write-Host ""
    Write-Host "Что должно быть готово:"
    Write-Host "1. Тяжелая модель уже есть на ПК."
    Write-Host "2. На ПК установлен Tailscale."
    Write-Host "3. На VPS установлен Tailscale и сервер видит твой ПК."
    Write-Host "4. ПК включен, когда нужен GPT-режим."
    Write-Host ""
    Write-Host "Как это включается:"
    Write-Host "1. Узнай Tailscale IP своего ПК."
    Write-Host "2. Придумай токен bridge."
    Write-Host "3. В мастер-меню выбери включение тяжелых моделей."
    Write-Host "4. Введи Tailscale IP и токен."
    Write-Host ""
    Write-Host "Как это выключается:"
    Write-Host "- В мастер-меню выбери выключение тяжелых моделей."
    Write-Host "- Или просто выключи ПК."
    Write-Host ""
    Write-Host "Важно:"
    Write-Host "- Bridge не нужен для обычной работы бота."
    Write-Host "- GPT-обучение делается локально на ПК."
    Write-Host "- VPS хранит боевые базы и легкие модели."
    Pause-Continue
}

function Show-Quick-Scenarios {
    Clear-Host
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "          ЧТО НАЖИМАТЬ И В КАКОМ ПОРЯДКЕ  " -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1. Первый запуск на личном ПК:"
    Write-Host "   1 -> установить зависимости"
    Write-Host "   2 -> запустить бота локально"
    Write-Host ""
    Write-Host "2. Обычная локальная GPT-работа:"
    Write-Host "   3 -> скачать свежую messages.db с VPS"
    Write-Host "   4 -> обучить локально ^(внутри будет выбор: только GPT / только Markov+Persona / всё^)"
    Write-Host "   5 -> отправить легкие модели и базы на VPS"
    Write-Host ""
    Write-Host "3. Если хочешь включить тяжелую GPT с ПК для VPS:"
    Write-Host "   6 -> прочитать краткую справку по bridge"
    Write-Host "   9 -> включить тяжелые модели с ПК для VPS"
    Write-Host "   10 -> выключить, когда больше не нужно"
    Write-Host ""
    Write-Host "4. Если VPS потерян и нужен новый:"
    Write-Host "   14 -> поставить бота на новый VPS с нуля"
    Write-Host "   потом заполнить KGTD.env на сервере"
    Write-Host "   потом 5 -> отправить легкие модели и базы"
    Write-Host ""
    Write-Host "5. Если просто работаешь с кодом:"
    Write-Host "   11 -> посмотреть git status"
    Write-Host "   12 -> commit и push"
    Write-Host "   18 -> обновить проект с GitHub"
    Write-Host ""
    Write-Host "6. Если хочешь читать подробную инструкцию:"
    Write-Host "   16 -> открыть README"
    Pause-Continue
}

function Show-Header {
    Clear-Host
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "       ЦЕНТР УПРАВЛЕНИЯ VIPIK BOT        " -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "Папка проекта: $projectRoot"
    Write-Host ""
    Write-Host "Сначала обычно нажимают:"
    Write-Host "17. Что нажимать по шагам"
    Write-Host ""
    Write-Host "Локальная работа:"
    Write-Host "1. Установить зависимости локально        - сделать первый запуск"
    Write-Host "2. Запустить бота локально                - проверить, что бот стартует"
    Write-Host "3. Синхронизировать messages.db с VPS     - забрать свежую базу сообщений"
    Write-Host "4. Обучить модели локально                - внутри будет выбор режима обучения"
    Write-Host "5. Отправить легкие модели и базы на VPS  - обновить сервер"
    Write-Host ""
    Write-Host "Bridge тяжелой GPT-модели:"
    Write-Host "6. Пошагово: как работает и как настроить bridge"
    Write-Host "7. Запустить bridge только локально на ПК - просто поднять bridge"
    Write-Host "8. Остановить локальный bridge на ПК      - выключить bridge"
    Write-Host "9. Включить тяжелые модели с ПК для VPS   - связать VPS с ПК"
    Write-Host "10. Выключить тяжелые модели с ПК для VPS - разорвать связь"
    Write-Host ""
    Write-Host "Git и VPS:"
    Write-Host "11. Показать git status                   - посмотреть изменения"
    Write-Host "12. Commit и push в Git                   - отправить код в GitHub"
    Write-Host "13. Установить ежедневную синхронизацию messages.db на ПК"
    Write-Host "14. Поставить бота на новый VPS с нуля    - аварийное восстановление"
    Write-Host "15. Показать статус бота на VPS           - проверить сервер"
    Write-Host "18. Обновить проект из GitHub             - подтянуть последние изменения"
    Write-Host ""
    Write-Host "Справка:"
    Write-Host "16. Открыть README                        - подробная инструкция"
    Write-Host "0. Выход"
    Write-Host ""
}

while ($true) {
    Show-Header
    $choice = Read-Host "Выбери действие"

    switch ($choice) {
        "1" {
            Run-Cmd {
                if (-not (Test-Path ".venv")) {
                    python -m venv .venv
                }
                .\.venv\Scripts\python.exe -m pip install --upgrade pip
                .\.venv\Scripts\python.exe -m pip install -r requirements.txt
            }
        }
        "2" {
            Run-Cmd {
                if (-not (Test-Path ".venv\Scripts\python.exe")) {
                    throw "Сначала выполни пункт 1."
                }
                & .\.venv\Scripts\python.exe main_file.py
            }
        }
        "3" {
            Run-Cmd {
                powershell -ExecutionPolicy Bypass -File ".\scripts\sync_messages_from_vps.ps1"
            }
        }
        "4" {
            Run-Cmd {
                powershell -ExecutionPolicy Bypass -File ".\scripts\train_models_menu.ps1"
            }
        }
        "5" {
            Run-Cmd {
                $includeGpt = Ask-YesNo "Включать GPT-модели в отправку?" $false
                if ($includeGpt) {
                    powershell -ExecutionPolicy Bypass -File ".\scripts\sync_training_to_vps.ps1" -IncludeGpt
                }
                else {
                    powershell -ExecutionPolicy Bypass -File ".\scripts\sync_training_to_vps.ps1"
                }
            }
        }
        "6" {
            Show-Bridge-Guide
        }
        "7" {
            Run-Cmd {
                $token = Read-Host "Введи токен bridge"
                if (-not $token) {
                    throw "Нужен токен bridge."
                }
                powershell -ExecutionPolicy Bypass -File ".\scripts\start_model_bridge.ps1" -Token $token
            }
        }
        "8" {
            Run-Cmd {
                powershell -ExecutionPolicy Bypass -File ".\scripts\stop_model_bridge.ps1"
            }
        }
        "9" {
            Run-Cmd {
                $defaultIp = Get-SavedTailscaleIp
                $ip = Prompt-WithDefault "Введи Tailscale IP твоего ПК" $defaultIp
                $token = Read-Host "Введи токен bridge"
                if (-not $ip -or -not $token) {
                    throw "Нужны и IP, и токен."
                }
                Save-TailscaleIp $ip
                powershell -ExecutionPolicy Bypass -File ".\scripts\enable_remote_models.ps1" -TailscaleIp $ip -Token $token
            }
        }
        "10" {
            Run-Cmd {
                powershell -ExecutionPolicy Bypass -File ".\scripts\disable_remote_models.ps1"
            }
        }
        "11" {
            Run-Cmd {
                git status
            }
        }
        "12" {
            Run-Cmd {
                git status
                if (-not (Ask-YesNo "Добавить все изменения в git?" $true)) {
                    return
                }
                git add .
                $message = Read-Host "Введи сообщение коммита"
                if ([string]::IsNullOrWhiteSpace($message)) {
                    throw "Сообщение коммита не может быть пустым."
                }
                git commit -m $message
                git push origin main
            }
        }
        "13" {
            Run-Cmd {
                $time = Read-Host "Во сколько ставить ежедневную синхронизацию? Формат HH:mm"
                if ([string]::IsNullOrWhiteSpace($time)) {
                    $time = "07:30"
                }
                powershell -ExecutionPolicy Bypass -File ".\scripts\install_local_message_sync_task.ps1" -DailyAt $time
            }
        }
        "14" {
            Run-Cmd {
                $host = Read-Host "IP нового VPS"
                if ([string]::IsNullOrWhiteSpace($host)) {
                    $host = "206.245.134.221"
                }
                $user = Read-Host "Пользователь SSH"
                if ([string]::IsNullOrWhiteSpace($user)) {
                    $user = "root"
                }
                $appDir = Read-Host "Каталог установки на VPS"
                if ([string]::IsNullOrWhiteSpace($appDir)) {
                    $appDir = "/opt/dis-bot"
                }
                powershell -ExecutionPolicy Bypass -File ".\scripts\install_bot_on_vps.ps1" -VpsHost $host -VpsUser $user -RemoteAppDir $appDir
            }
        }
        "15" {
            Run-Cmd {
                ssh -i "$env:USERPROFILE\.ssh\disbot_vps_ed25519" root@206.245.134.221 "systemctl status vipik-discord-bot --no-pager -n 40"
            }
        }
        "16" {
            Run-Cmd {
                Start-Process notepad.exe "$projectRoot\README.md"
            }
        }
        "17" {
            Show-Quick-Scenarios
        }
        "18" {
            Run-Cmd {
                git pull origin main
            }
        }
        "0" {
            break
        }
        default {
            Write-Host "Неизвестный пункт меню." -ForegroundColor Yellow
            Pause-Continue
        }
    }
}
