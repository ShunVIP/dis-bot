param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

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

function Show-Header {
    Clear-Host
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "       ЦЕНТР УПРАВЛЕНИЯ VIPIK BOT        " -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "Папка проекта: $projectRoot"
    Write-Host ""
    Write-Host "Локальная работа:"
    Write-Host "1. Установить зависимости локально"
    Write-Host "2. Запустить бота локально"
    Write-Host "3. Синхронизировать messages.db с VPS"
    Write-Host "4. Обучить модели локально"
    Write-Host "5. Отправить легкие модели и базы на VPS"
    Write-Host ""
    Write-Host "Bridge тяжелой GPT-модели:"
    Write-Host "6. Пошагово: как работает и как настроить bridge"
    Write-Host "7. Запустить bridge только локально на ПК"
    Write-Host "8. Остановить локальный bridge на ПК"
    Write-Host "9. Включить тяжелые модели с ПК для VPS"
    Write-Host "10. Выключить тяжелые модели с ПК для VPS"
    Write-Host ""
    Write-Host "Git и VPS:"
    Write-Host "11. Показать git status"
    Write-Host "12. Commit и push в Git"
    Write-Host "13. Установить ежедневную синхронизацию messages.db на ПК"
    Write-Host "14. Поставить бота на новый VPS с нуля"
    Write-Host "15. Показать статус бота на VPS"
    Write-Host ""
    Write-Host "Справка:"
    Write-Host "16. Открыть README"
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
                cmd /c train_models.bat
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
                $ip = Read-Host "Введи Tailscale IP твоего ПК"
                $token = Read-Host "Введи токен bridge"
                if (-not $ip -or -not $token) {
                    throw "Нужны и IP, и токен."
                }
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
                powershell -ExecutionPolicy Bypass -File ".\scripts\install_bot_on_vps.ps1" -Host $host -User $user -RemoteAppDir $appDir
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
        "0" {
            break
        }
        default {
            Write-Host "Неизвестный пункт меню." -ForegroundColor Yellow
            Pause-Continue
        }
    }
}
