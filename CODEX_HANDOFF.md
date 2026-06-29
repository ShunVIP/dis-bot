# CODEX_HANDOFF: ViPik Discord Bot

Этот файл предназначен для Codex/LLM-агента на основном ПК. Сначала прочитай этот документ, затем `docs/TZ_ViPik_Discord_Platform_for_Codex.md`, `README.md`, `AGENT_CONTEXT.md`, `.gitignore`, `KGTD.env.example`, `config.py`, `main_file.py`, `core/runtime_policy.py` и скрипты в `scripts/`.

## Главная цель

Нужно восстановить и продолжить работу над проектом `ShunVIP/dis-bot` без потери секретов, баз данных, моделей и продового состояния на VPS.

Этот архив содержит публичную копию кода из GitHub, handoff-инструкции и пустые placeholders под приватные/runtime-файлы. Он НЕ содержит реальные секреты, реальные базы SQLite, SSH-ключи и обученные модели.

Главное продуктовое ТЗ для дальнейшей разработки лежит в:

- `docs/TZ_ViPik_Discord_Platform_for_Codex.md`

Это ТЗ описывает отдельный сайт/приложение, Discord OAuth2 login, личный кабинет, админ-панель, интеграцию существующего бота, fallback-портал и fallback-чат на случай недоступности Discord.

## Что это за проект

`ViPik Discord Bot` - Discord-бот с slash-командами, экономикой, статистикой, напоминаниями, Steam-интеграцией, WWM knowledge base, системой пародии речи, Markov/GPT-моделями, VPS deploy и локальным bridge для тяжелых моделей.

Проект не является только локальным ботом:

- код редактируется на ПК;
- продовый бот работает на VPS;
- продовые базы лежат на VPS в `/opt/dis-bot/datebase/`;
- `messages.db` на VPS является главным источником сообщений;
- тяжелое GPT-обучение и тяжелые модели должны оставаться на локальном ПК;
- VPS может обращаться к локальному GPT bridge через Tailscale;
- веб-админка должна открываться только приватно, через Tailscale и токен.

## Текущее состояние этого архива

Архив подготовлен на временном ПК из публичного GitHub ZIP. На временном ПК:

- `git` не был найден в PATH;
- локального `.git` в рабочей папке изначально не было;
- реальных секретов и баз в архиве нет;
- пустой `KGTD.env` добавлен специально как шаблон для заполнения;
- пустые папки `datebase/` и `models/` добавлены специально как места для восстановления данных;
- `_private_placeholders/` добавлен как напоминание про SSH-ключи и runtime-файлы;
- рабочая копия находится в папке `dis-bot-main` перед упаковкой.

## Что есть в коде

Основные файлы:

- `docs/TZ_ViPik_Discord_Platform_for_Codex.md` - главное ТЗ на развитие сайта/приложения вокруг бота.
- `main_file.py` - точка входа бота.
- `config.py` - загрузка `KGTD.env`.
- `KGTD.env.example` - шаблон переменных окружения.
- `core/runtime_policy.py` - серверный режим, ограничения, флаги bridge/admin.
- `core/admin_panel.py` - веб-панель.
- `fun_slesh/` - основные slash-модули.
- `scheduled/` - фоновые задачи.
- `wwm_kb/` - база знаний WWM.
- `scripts/` - локальные/VPS скрипты управления.
- `deploy/systemd/` - systemd templates.
- `.github/workflows/deploy.yml` - deploy через GitHub Actions.

## Что обязательно НЕ должно попадать в git

Не коммитить:

- `KGTD.env`
- `.env`
- реальные токены и API keys
- `datebase/`
- `*.db`, `*.sqlite`, `*.sqlite3`
- `models/`
- SSH private keys
- `bot.log`
- `.control_center.local.json`
- `.model_bridge.runtime.json`
- временные deploy/archive файлы

Проверь `.gitignore`: большая часть уже исключена.

## Пустые placeholders в этом архиве

В архив специально добавлены:

- `KGTD.env` - пустой шаблон, все значения пустые.
- `datebase/README.md` и `datebase/.gitkeep` - место под реальные SQLite базы.
- `models/README.md` и `models/.gitkeep` - место под реальные модели.
- `_private_placeholders/README.md` - пояснение, какие приватные файлы отсутствуют.
- `_private_placeholders/.ssh/PUT_SSH_KEY_HERE.txt` - заметка про SSH-ключ, не сам ключ.

Если следующий Codex видит эти файлы, он должен понимать: это не продовые данные, а заготовки.

## Секреты и переменные окружения

Бот читает `KGTD.env` через `config.py`.

Минимально обязательное:

```env
tok=REAL_DISCORD_BOT_TOKEN
```

Опционально/по функциям:

```env
STEAM_API_KEY=REAL_STEAM_API_KEY

REMOTE_MODEL_API_URL=
REMOTE_MODEL_API_TOKEN=

BOT_SERVER_MODE=true
ALLOW_GPT_TRAINING_ON_SERVER=false
ALLOW_FULL_MAINTENANCE_ON_SERVER=false
ALLOW_REMOTE_MODEL_INFERENCE=true
ENABLE_DAILY_MARKOV_RETRAIN_ON_SERVER=false
ENABLE_DAILY_MARKOV_COLLECTION_ON_SERVER=true
DAILY_MARKOV_RETRAIN_HOUR=3
DAILY_MARKOV_RETRAIN_MINUTE=15

WEB_ADMIN_ENABLED=false
WEB_ADMIN_HOST=127.0.0.1
WEB_ADMIN_PORT=8080
WEB_ADMIN_TOKEN=REAL_LONG_RANDOM_TOKEN
WEB_ADMIN_ALLOWED_IPS=
WEB_ADMIN_TITLE=ViPik Bot Control
```

Важно: если включается bridge, `REMOTE_MODEL_API_TOKEN` должен быть новым случайным токеном, а не публичным дефолтом из GUI.

## Что нужно забрать со старого ПК

Смотри также `OLD_PC_MIGRATION_CHECKLIST.md`.

Главные артефакты:

- рабочий репозиторий с `.git`;
- реальный `KGTD.env`, если он был локально;
- `%USERPROFILE%\.ssh\disbot_vps_ed25519`;
- `%USERPROFILE%\.ssh\known_hosts`, если нужно;
- локальный `datebase/`, если есть свежие копии;
- локальный `models/`, особенно `models/gpt/`;
- `.control_center.local.json`;
- `.model_bridge.runtime.json`, если bridge был запущен;
- собранный `dist/ViPikBotControl.exe`, если нужен GUI без Python;
- Tailscale состояние/аккаунт/адрес ПК;
- GitHub credentials/token, если локальный git использовал auth.

## Что нужно проверить на VPS

Ожидаемые значения из скриптов:

- host по умолчанию: `206.245.134.221`
- user по умолчанию: `root`
- app dir: `/opt/dis-bot`
- service: `vipik-discord-bot`
- env: `/opt/dis-bot/KGTD.env`
- главная база сообщений: `/opt/dis-bot/datebase/messages.db`

На VPS проверить:

```bash
systemctl status vipik-discord-bot --no-pager --lines=50
ls -la /opt/dis-bot
ls -la /opt/dis-bot/datebase
test -f /opt/dis-bot/KGTD.env && echo "KGTD.env exists"
```

Не выводить содержимое `KGTD.env` в чат целиком.

## Восстановление на основном ПК

Рекомендуемый порядок:

1. Установить Git, Python 3.12+, Tailscale, OpenSSH client.
2. Получить/открыть реальный git clone `ShunVIP/dis-bot`.
3. Сравнить этот архив с актуальным clone.
4. Вернуть локальные секреты и runtime data, но не коммитить их.
5. Создать `.venv` и установить зависимости:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

6. Проверить, что `KGTD.env` существует.
7. Проверить SSH к VPS:

```powershell
ssh -i "$env:USERPROFILE\.ssh\disbot_vps_ed25519" root@206.245.134.221 "systemctl is-active vipik-discord-bot"
```

8. Синхронизировать `messages.db`, если нужно:

```powershell
.\scripts\sync_messages_from_vps.ps1
```

9. Запускать бота локально только если понятно, что не будет конфликта с продовым ботом.

## Важные риски

- Не запускать второй экземпляр бота с тем же Discord token без понимания последствий.
- Не перезаписывать VPS `KGTD.env` пустым шаблоном.
- Не считать локальный `messages.db` источником правды, пока он не синхронизирован с VPS.
- Не обучать тяжелую GPT-модель на VPS.
- Не открывать веб-админку публично.
- Не использовать публичный дефолтный bridge token.

## Что сделать Codex первым делом на основном ПК

1. Проверить `git status --short --branch`.
2. Проверить `git remote -v`.
3. Проверить наличие `KGTD.env`, `datebase/`, `models/`, `.ssh/disbot_vps_ed25519`.
4. Не печатать секреты.
5. Составить план восстановления.
6. Только после подтверждения пользователя выполнять команды, которые меняют VPS, systemd, env или GitHub secrets.

## Discord menu UX status, 2026-06-20

The user explicitly decided to reduce visible Discord slash commands. Public Discord `/` autocomplete should show only:

- `/команды`
- `/админ`

Current implementation:

- `main_file.py` defines `PUBLIC_MENU_COMMANDS = {"команды", "админ"}`.
- `main_file.py::collapse_slash_commands_to_menu()` snapshots all loaded app commands into `bot.menu_catalog_commands`, removes every public command except `/команды` and `/админ` from `bot.tree`, and stores hidden names in `bot.menu_hidden_command_names`.
- `setup_hook()` calls `collapse_slash_commands_to_menu()` after `load_slash_modules()` and before `bot.tree.sync()`.
- `fun_slesh/menu.py` now owns the live menu UX. The old `/меню` became `/команды`, and old `/меню_админ` became `/админ`.
- `fun_slesh/menu.py` has section buttons and modals/selects for many user flows: economy, reputation, stats, birthdays, games, random, parody, Steam, search/WWM, reminders.

Important continuation notes:

- After running the bot with a real token, Discord sync should remove old public slash commands. Global command propagation may take time.
- Not every hidden command has a perfect button/modal flow yet. Admin, maintenance, diagnostics, and complex flows still need a second pass.
- Keep old command callback methods where possible and call them from menu actions. Do not duplicate business logic into buttons unless necessary.
- If a section button fails at runtime, check the target cog method name and signature in the original `fun_slesh/*` module.
- Runtime was not fully verified here because this temporary PC does not have the real `KGTD.env`, real databases, Discord token, or installed dependencies. Syntax check passed for `fun_slesh/menu.py` and `main_file.py`.
