# LOCAL_AUDIT_SUMMARY

Дата подготовки: 2026-06-18.

## Что было сделано

- Репозиторий `ShunVIP/dis-bot` временно был открыт публично владельцем.
- Код был скачан как GitHub ZIP, потому что на временном ПК `git` не найден в PATH.
- ZIP был распакован в локальную папку `dis-bot-main`.
- Проведен обзор структуры, конфигурации, `.gitignore`, deploy scripts, GitHub Actions и runtime-зависимостей.
- Добавлены handoff-файлы:
  - `CODEX_HANDOFF.md`
  - `OLD_PC_MIGRATION_CHECKLIST.md`
  - `CODEX_START_PROMPT.md`
  - `LOCAL_AUDIT_SUMMARY.md`
  - `RESTORE_EMPTY_ASSETS.md`
  - `docs/TZ_ViPik_Discord_Platform_for_Codex.md`

## Главное новое ТЗ

Добавлено полноценное ТЗ:

- `docs/TZ_ViPik_Discord_Platform_for_Codex.md`

Оно связывает исходный документ `TZ_Discord_Platform_for_Codex_FULL.docx` с реальным проектом `ShunVIP/dis-bot` и фиксирует продуктовую цель: отдельный сайт/приложение, Discord OAuth2 login, личный кабинет, админ-панель, интеграция текущего бота, fallback-портал и fallback-чат/общение на случай, когда Discord недоступен.

## Локальное состояние временного ПК

- Рабочая папка: `C:\Users\MSI\OneDrive\Документы\bot`
- Исходный ZIP: `dis-bot-main.zip`
- Распакованный проект: `dis-bot-main`
- Изначально `.git` в рабочей папке не было.
- Команда `git` не найдена.
- Реальных секретов, баз и моделей в архиве нет.

## Вывод по секретам

В GitHub-коде не найден реальный `KGTD.env`, Discord token, Steam key, SSH private key или реальные SQLite базы.

Но в коде публично видны operational details:

- VPS host по умолчанию: `206.245.134.221`
- app dir: `/opt/dis-bot`
- service: `vipik-discord-bot`
- SSH key path: `%USERPROFILE%\.ssh\disbot_vps_ed25519`
- default bridge token в GUI: `secretkeyvipik`
- admin URL default: `http://100.90.24.117:8080/`

Рекомендация: после аудита вернуть GitHub repo в private и заменить bridge/admin tokens, если они совпадают с дефолтами.

## Что содержит GitHub

- Код бота.
- Скрипты деплоя.
- systemd templates.
- GitHub Actions workflow.
- Шаблон `KGTD.env.example`.
- Документацию `README.md` и `AGENT_CONTEXT.md`.

## Что GitHub намеренно не содержит

- `KGTD.env`
- `datebase/`
- `models/`
- `*.db`
- SSH keys
- logs
- local GUI/bridge state

## Минимальный запуск

Для запуска нужен `KGTD.env` с `tok`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main_file.py
```

Не запускать локально, если продовый бот уже работает с тем же token и нет понимания последствий.
