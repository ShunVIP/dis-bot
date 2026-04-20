# ViPik Discord Bot

Discord-бот для одного сервера с играми, экономикой, статистикой, пародией на стиль речи, Steam, напоминаниями и автоматизацией.

Если проект открывает другой ИИ или новый человек, сначала прочитай:

- [AGENT_CONTEXT.md](D:/dis-bot/AGENT_CONTEXT.md)

Этот репозиторий уже не про "просто локальный бот". У проекта есть рабочая прод-схема:

- код редактируется на ПК;
- бот постоянно живёт на VPS;
- `messages.db` собирается на VPS и считается главным источником сообщений;
- безопасный `Markov` дообучается на VPS автоматически;
- тяжёлая `GPT`-модель обучается только на ПК;
- если bridge включён, VPS использует тяжёлую модель с ПК через `Tailscale`.

## Быстрый смысл

Если тебе нужно понять проект за 2 минуты:

1. Бот в проде работает на VPS как `systemd`-сервис `vipik-discord-bot`.
2. Код деплоится отдельно от тяжёлых моделей и баз.
3. На VPS нельзя запускать тяжёлое GPT-обучение.
4. На ПК есть `Центр управления ботом.bat` и `ViPik Bot Control GUI.bat` — главные интерфейсы обслуживания.
5. Веб-панель на VPS нужна только для просмотра статуса и безопасного переключения удалённой тяжёлой модели.

## Для другого агента / другого аккаунта

Если этот проект открывает другой LLM-агент, другой аккаунт ChatGPT/Codex/Claude Code или новый человек, ему нужно знать следующее:

- не коммитить секреты, токены, `KGTD.env`, базы и `models/`;
- не пытаться обучать GPT на VPS;
- не ломать ежедневный цикл `сбор сообщений -> Markov` на VPS;
- не считать локальную `messages.db` источником правды, если она не была синхронизирована с VPS;
- не тащить тяжёлую GPT-модель на VPS;
- любые изменения в деплое, bridge, `systemd`, `Tailscale`, runtime policy и env-флагах требуют аккуратности;
- главные локальные интерфейсы обслуживания — [Центр управления ботом.bat](D:/dis-bot/%D0%A6%D0%B5%D0%BD%D1%82%D1%80%20%D1%83%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F%20%D0%B1%D0%BE%D1%82%D0%BE%D0%BC.bat) и [ViPik Bot Control GUI.bat](D:/dis-bot/ViPik%20Bot%20Control%20GUI.bat).

Короткий operational contract:

- локально: обучение GPT, git, sync базы, управление bridge;
- на VPS: прод-бот, `messages.db`, безопасный `Markov`, веб-панель, systemd;
- через bridge: только inference тяжёлой GPT-модели с ПК.

## Архитектура

### Где что живёт

- ПК:
  - разработка кода;
  - локальный `git`;
  - локальное GPT-обучение;
  - локальные модели в `models/`;
  - запуск bridge-сервера модели;
  - `Центр управления ботом.bat`;
  - `ViPik Bot Control GUI.bat`.

- VPS:
  - прод-бот;
  - `KGTD.env`;
  - боевые SQLite-базы;
  - ежедневный сбор сообщений;
  - ежедневный безопасный `Markov`;
  - веб-панель администратора;
  - `systemd`-сервис.

### Источник правды по данным

- Главный источник сообщений: `messages.db` на VPS.
- ПК не должен вести "свою отдельную правду" по сообщениям.
- На ПК база должна регулярно подтягиваться с VPS.

### Что делает bridge

Bridge нужен только для тяжёлой GPT-модели.

Схема:

- модель физически лежит на ПК;
- бот на VPS при необходимости обращается к API на ПК;
- связь идёт через `Tailscale`;
- если ПК выключен или bridge отключён, бот продолжает работать без тяжёлой GPT.

## Безопасность

### Что не должно попадать в git

Нельзя коммитить:

- `KGTD.env`
- любые реальные токены и API-ключи
- `datebase/*.db`
- `models/`
- приватные SSH-ключи
- временные архивы деплоя
- логи
- локальные state-файлы центра управления

### Что уже исключено

Смотри [`.gitignore`](D:/dis-bot/.gitignore).

Отдельно уже игнорируются:

- `.control_center.local.json`
- `models/`
- `datebase/`
- `KGTD.env`

### Важные ограничения проекта

- GPT-обучение на VPS запрещено по умолчанию.
- Полная тяжёлая `/профилактика` на VPS запрещена по умолчанию.
- Веб-панель нельзя открывать публично без `Tailscale` и токена.
- Bridge нельзя считать публичным API.

## Структура проекта

### Основные файлы

- [main_file.py](D:/dis-bot/main_file.py) — точка входа бота.
- [config.py](D:/dis-bot/config.py) — чтение переменных окружения.
- [README.md](D:/dis-bot/README.md) — эта документация.
- [KGTD.env.example](D:/dis-bot/KGTD.env.example) — пример env-файла.

### Основные папки

- [fun_slesh](D:/dis-bot/fun_slesh) — slash-команды и логика модулей.
- [core](D:/dis-bot/core) — runtime policy, админ-панель и базовая логика.
- [scheduled](D:/dis-bot/scheduled) — фоновые задачи.
- [scripts](D:/dis-bot/scripts) — локальные/VPS служебные скрипты.
- [deploy/systemd](D:/dis-bot/deploy/systemd) — шаблоны systemd.
- [datebase](D:/dis-bot/datebase) — SQLite-базы.
- [models](D:/dis-bot/models) — локальные модели и артефакты.

## Переменные окружения

Бот читает `KGTD.env`.

Минимальный шаблон:

```env
tok=YOUR_DISCORD_BOT_TOKEN
STEAM_API_KEY=YOUR_STEAM_API_KEY

REMOTE_MODEL_API_URL=
REMOTE_MODEL_API_TOKEN=

BOT_SERVER_MODE=true
ALLOW_GPT_TRAINING_ON_SERVER=false
ALLOW_FULL_MAINTENANCE_ON_SERVER=false
ALLOW_REMOTE_MODEL_INFERENCE=true

ENABLE_DAILY_MARKOV_RETRAIN_ON_SERVER=true
ENABLE_DAILY_MARKOV_COLLECTION_ON_SERVER=true
DAILY_MARKOV_RETRAIN_HOUR=3
DAILY_MARKOV_RETRAIN_MINUTE=15

WEB_ADMIN_ENABLED=false
WEB_ADMIN_HOST=127.0.0.1
WEB_ADMIN_PORT=8080
WEB_ADMIN_TOKEN=
WEB_ADMIN_ALLOWED_IPS=
WEB_ADMIN_TITLE=ViPik Bot Control
```

### Главные флаги

- `BOT_SERVER_MODE=true` — включает серверный режим.
- `ALLOW_GPT_TRAINING_ON_SERVER=false` — блокирует GPT-обучение на VPS.
- `ALLOW_FULL_MAINTENANCE_ON_SERVER=false` — блокирует тяжёлую профилактику на VPS.
- `ALLOW_REMOTE_MODEL_INFERENCE=true` — разрешает использовать bridge.
- `ENABLE_DAILY_MARKOV_RETRAIN_ON_SERVER=true` — ежедневный `Markov`.
- `ENABLE_DAILY_MARKOV_COLLECTION_ON_SERVER=true` — ежедневный добор сообщений.
- `WEB_ADMIN_ENABLED=true` — включает веб-панель.
- `WEB_ADMIN_ALLOWED_IPS=` — IP/CIDR allowlist для веб-панели.

## Что работает автоматически

### На VPS

- бот под `systemd`;
- ежедневный добор новых сообщений;
- ежедневный безопасный `Markov`;
- фоновые Discord-задачи;
- веб-панель;
- использование bridge, если он включён.

### На ПК

- локальное GPT-обучение;
- синхронизация `messages.db` с VPS;
- включение/выключение bridge;
- git workflow;
- отправка лёгких артефактов на VPS.

## Центр управления ботом

Главный локальный интерфейс:

- [Центр управления ботом.bat](D:/dis-bot/%D0%A6%D0%B5%D0%BD%D1%82%D1%80%20%D1%83%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F%20%D0%B1%D0%BE%D1%82%D0%BE%D0%BC.bat)
- [ViPik Bot Control GUI.bat](D:/dis-bot/ViPik%20Bot%20Control%20GUI.bat)

Внутри он использует:

- [scripts/центр_управления.ps1](D:/dis-bot/scripts/%D1%86%D0%B5%D0%BD%D1%82%D1%80_%D1%83%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F.ps1)
- [scripts/bot_control_gui.py](D:/dis-bot/scripts/bot_control_gui.py)

### GUI-версия

Если не хочешь работать через `cmd`, запускай:

- [ViPik Bot Control GUI.bat](D:/dis-bot/ViPik%20Bot%20Control%20GUI.bat)
- `dist/ViPikBotControl.exe`

Это однооконная оболочка для самых частых действий:

- запуск/остановка bridge;
- связка VPS с ПК;
- отключение тяжёлой модели;
- `git pull`;
- синхронизация `messages.db`;
- отправка лёгких моделей и баз на VPS;
- открытие меню обучения;
- статус VPS;
- открытие веб-панели.

Сборка `.exe`:

- [scripts/build_bot_control_gui.ps1](D:/dis-bot/scripts/build_bot_control_gui.ps1)

Готовый файл после сборки:

- `dist/ViPikBotControl.exe`

### Актуальные пункты меню

- `1` — установить зависимости локально
- `2` — запустить бота локально
- `3` — синхронизировать `messages.db` с VPS
- `4` — обучить модели локально
- `5` — отправить лёгкие модели и базы на VPS
- `6` — справка по bridge
- `7` — запустить bridge только локально
- `8` — остановить локальный bridge
- `9` — включить тяжёлые модели с ПК для VPS
- `10` — выключить тяжёлые модели с ПК для VPS
- `19` — запустить комплект из 2 окон
- `11` — показать `git status`
- `12` — commit + push
- `13` — поставить ежедневную синхронизацию базы на ПК
- `14` — поставить бота на новый VPS с нуля
- `15` — показать статус бота на VPS
- `16` — открыть `README`
- `17` — сценарии “что нажимать по шагам”
- `18` — обновить проект из GitHub

### Что делать обычно

#### Если нужен локальный GPT-training

1. `3` — скачать свежую `messages.db`
2. `4` — выбрать:
   - скачать базу ещё раз или нет;
   - `Только GPT`;
   - для всех или для одного пользователя

#### Если нужен bridge

1. `9` — включить тяжёлые модели с ПК для VPS
2. если больше не нужен:
   - `10`

#### Если нужны последние правки

1. `18` — подтянуть обновления

## Локальное обучение

### Что реально происходит при GPT-training

- обучение идёт на ПК, не на VPS;
- используется локальная копия `messages.db`;
- обученная модель остаётся на ПК;
- если bridge включён, VPS потом использует её автоматически.

### Какие скрипты за это отвечают

- [train_models.bat](D:/dis-bot/train_models.bat)
- [scripts/train_models_menu.ps1](D:/dis-bot/scripts/train_models_menu.ps1)
- [scripts/train_local.ps1](D:/dis-bot/scripts/train_local.ps1)
- [scripts/train_local.py](D:/dis-bot/scripts/train_local.py)

### Режимы обучения

- `Только GPT`
- `Только Markov и Persona`
- `Всё вместе`

## Bridge тяжёлой модели

### Что нужно для работы

- ПК включён;
- `Tailscale` включён;
- bridge поднят локально;
- в боте на VPS включено использование удалённой модели.

### Файлы bridge

- [scripts/model_bridge_server.py](D:/dis-bot/scripts/model_bridge_server.py)
- [scripts/start_model_bridge.ps1](D:/dis-bot/scripts/start_model_bridge.ps1)
- [scripts/stop_model_bridge.ps1](D:/dis-bot/scripts/stop_model_bridge.ps1)
- [scripts/enable_remote_models.ps1](D:/dis-bot/scripts/enable_remote_models.ps1)
- [scripts/disable_remote_models.ps1](D:/dis-bot/scripts/disable_remote_models.ps1)

### Важный принцип

Bridge не переносит модель на VPS. Он только даёт VPS возможность спросить модель на твоём ПК.

## Веб-панель

### Для чего она нужна

Веб-панель — это не замена центру управления. Это безопасная VPS-панель для:

- просмотра статуса;
- просмотра защитной политики;
- включения/выключения использования удалённой тяжёлой модели;
- быстрого контроля прод-режима.

### Что нельзя переносить в веб-панель

Нельзя безопасно перенести туда:

- локальное обучение GPT;
- sync `messages.db` на ПК;
- локальный `git pull/commit/push`;
- локальный запуск bridge;
- всё, что должно выполняться именно на твоём ПК.

### Как включать безопасно

Для `Tailscale-only` режима:

```env
WEB_ADMIN_ENABLED=true
WEB_ADMIN_HOST=100.90.24.117
WEB_ADMIN_PORT=8080
WEB_ADMIN_TOKEN=LONG_RANDOM_TOKEN
WEB_ADMIN_ALLOWED_IPS=
```

Или строже:

```env
WEB_ADMIN_ALLOWED_IPS=100.69.97.40/32
```

### Как открывать

Открывать из устройства в твоём `tailnet`:

```text
http://100.90.24.117:8080/
```

Если включён посторонний браузерный VPN, панель может не открываться. Для неё нужен `Tailscale`, а не сторонний VPN-прокси.

## Git workflow

### Базовые команды

```powershell
git status
git add .
git commit -m "Сообщение"
git push origin main
```

### Перед push проверять

- нет ли `KGTD.env`
- нет ли `.db`
- нет ли `models/`
- нет ли приватных ключей
- нет ли временных архивов/мусора

### Что делать перед работой после чужих изменений

Используй:

- `18` в центре управления

или:

```powershell
git pull origin main
```

## VPS с нуля

### Требуется

- Linux VPS
- SSH-доступ
- локальный ключ SSH
- этот репозиторий на ПК

### Основной путь

Через центр управления:

- `14` — поставить бота на новый VPS с нуля

Вручную:

- [scripts/install_bot_on_vps.ps1](D:/dis-bot/scripts/install_bot_on_vps.ps1)

### Что делает установщик

- ставит Python и пакеты;
- создаёт пользователя;
- кладёт код;
- создаёт `.venv`;
- ставит зависимости;
- создаёт `systemd`;
- включает сервис;
- создаёт пустой `KGTD.env`.

## Деплой и синхронизация

### Что деплоится через git

- код
- скрипты
- документация
- runtime policy

### Что не деплоится через git

- тяжёлые модели
- боевые базы
- реальные секреты

### Лёгкие артефакты на VPS

Для отправки лёгких моделей и баз:

- [scripts/sync_training_to_vps.ps1](D:/dis-bot/scripts/sync_training_to_vps.ps1)

По умолчанию GPT туда не уходит.

## Полезные файлы

- [scripts/sync_messages_from_vps.ps1](D:/dis-bot/scripts/sync_messages_from_vps.ps1)
- [scripts/install_local_message_sync_task.ps1](D:/dis-bot/scripts/install_local_message_sync_task.ps1)
- [scripts/install_bot_on_vps.ps1](D:/dis-bot/scripts/install_bot_on_vps.ps1)
- [scripts/sync_training_to_vps.ps1](D:/dis-bot/scripts/sync_training_to_vps.ps1)
- [scripts/train_local.py](D:/dis-bot/scripts/train_local.py)
- [scripts/train_models_menu.ps1](D:/dis-bot/scripts/train_models_menu.ps1)
- [core/runtime_policy.py](D:/dis-bot/core/runtime_policy.py)
- [core/admin_panel.py](D:/dis-bot/core/admin_panel.py)
- [deploy/systemd/vipik-discord-bot.service.template](D:/dis-bot/deploy/systemd/vipik-discord-bot.service.template)

## Известные ограничения

- голосовые audio-функции Discord требуют `PyNaCl`;
- тяжёлые GPT-модели не рассчитаны на хранение и обучение на текущем VPS;
- bridge не заменяет полноценный локальный training;
- источник правды по сообщениям должен быть один, и сейчас это VPS;
- веб-панель не должна становиться публичной интернет-панелью.

## Что делать, если что-то сломалось

### Если не работает локальный training

1. `18` — обновить проект
2. проверить `.venv`
3. проверить `messages.db`
4. повторить `4`

### Если не работает bridge

1. убедиться, что `Tailscale` включён;
2. убедиться, что ПК включён;
3. в центре:
   - `9` чтобы включить;
   - `10` чтобы выключить;
4. если надо, перезапустить bridge локально через `7` и `8`.

### Если потерян VPS

1. поднять новый VPS;
2. `14` в центре управления;
3. заполнить `KGTD.env`;
4. восстановить лёгкие артефакты;
5. включить bridge, если нужен GPT-режим.
