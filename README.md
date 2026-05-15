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
4. На ПК главный интерфейс обслуживания — `ViPikBotControl.exe`.
5. Веб-панель на VPS нужна только для просмотра статуса и безопасного переключения удалённой тяжёлой модели.

## Для другого агента / другого аккаунта

Если этот проект открывает другой LLM-агент, другой аккаунт ChatGPT/Codex/Claude Code или новый человек, ему нужно знать следующее:

- не коммитить секреты, токены, `KGTD.env`, базы и `models/`;
- не пытаться обучать GPT на VPS;
- не ломать ежедневный цикл `сбор сообщений -> Markov` на VPS;
- не считать локальную `messages.db` источником правды, если она не была синхронизирована с VPS;
- не тащить тяжёлую GPT-модель на VPS;
- любые изменения в деплое, bridge, `systemd`, `Tailscale`, runtime policy и env-флагах требуют аккуратности;
- главный локальный интерфейс обслуживания — `dist/ViPikBotControl.exe` или прямой запуск [scripts/bot_control_gui.py](D:/dis-bot/scripts/bot_control_gui.py).

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
  - `dist/ViPikBotControl.exe`;
  - [scripts/bot_control_gui.py](D:/dis-bot/scripts/bot_control_gui.py).

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
  В том числе:
  - `toxicity.py` — реакция на токсичные сообщения;
  - `social_chat.py` — лёгкая разговорная болтовня бота в чате;
  - `parody_engine.py` — пародии, Markov, Persona и GPT.
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

## GUI управления

Главный локальный интерфейс:

- `dist/ViPikBotControl.exe`
- [scripts/bot_control_gui.py](D:/dis-bot/scripts/bot_control_gui.py)

Это основной однооконный интерфейс обслуживания проекта. Старый batch-центр больше не считается главным способом работы.

### Что умеет GUI

- вкладки `Обзор`, `Документация` и `Лог`;
- одна главная кнопка `Включить GPT модели` / `Выключить GPT модели`;
- одна главная кнопка `Скачать свежую DB и обучить GPT`;
- одна главная кнопка `Обновить статусы`;
- быстрые индикаторы `Bridge`, `VPS`, `Бот`, `Команды`, `База сегодня`, `Markov сегодня`, `Tailscale`, `GPT мост`;
- `git status`, `git pull`, `commit + push`;
- установка локальных зависимостей;
- запуск бота локально;
- отправка лёгких моделей и баз на VPS;
- установка ежедневной sync-задачи;
- установка бота на новый VPS;
- открытие веб-панели;
- окно настроек подключения отдельной кнопкой, а не отдельной вкладкой.

### Сборка `.exe`

- [scripts/build_bot_control_gui.ps1](D:/dis-bot/scripts/build_bot_control_gui.ps1)

Готовый файл после сборки:

- `dist/ViPikBotControl.exe`

### Что делать обычно

#### Если нужен локальный GPT-training

1. В GUI нажать `Скачать свежую DB и обучить GPT`.
2. Выбрать: для всех или для одного пользователя.
3. Обучение пойдёт на ПК и его лог появится во вкладке `Лог`.

#### Если нужен bridge

1. В GUI нажать `Включить GPT модели`.
2. Если больше не нужен — нажать `Выключить GPT модели`.

#### Если нужны последние правки

1. В GUI нажать `Git pull`

## Локальное обучение

### Что реально происходит при GPT-training

- обучение идёт на ПК, не на VPS;
- используется локальная копия `messages.db`;
- обученная модель остаётся на ПК;
- если bridge включён, VPS потом использует её автоматически.

### Какие скрипты за это отвечают

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

Веб-панель — это не замена локальному GUI. Это безопасная VPS-панель для:

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

- кнопку `Git pull` в GUI

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

Через GUI:

- кнопка `Поставить бота на новый VPS`

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

1. обновить проект через `Git pull`
2. проверить `.venv`
3. проверить `messages.db`
4. снова нажать `Скачать свежую DB и обучить GPT`

### Если не работает bridge

1. убедиться, что `Tailscale` включён;
2. убедиться, что ПК включён;
3. в GUI:
   - `Включить GPT модели` чтобы поднять bridge и связать VPS;
   - `Выключить GPT модели` чтобы разорвать связь и погасить bridge.

### Если потерян VPS

1. поднять новый VPS;
2. в GUI нажать `Поставить бота на новый VPS`;
3. заполнить `KGTD.env`;
4. восстановить лёгкие артефакты;
5. включить bridge, если нужен GPT-режим.
