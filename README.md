# ViPik Discord Platform

ViPik — self-hosted Discord-бот с приватной веб-панелью и собственным web/app. Проект объединяет серверные функции, общие пользовательские данные, чат/DM, игровую статистику, экономику, модерацию и локальные ML-пайплайны без платных API.

## Главные архитектурные правила

- Пародии работают **только через Markov**. GPT, Transformers, Persona-режим и model bridge удалены.
- Тяжёлое обучение и пакетная обработка выполняются на основном ПК; VPS хранит данные, лёгкие артефакты и выполняет inference.
- Исключение — безопасные лёгкие модели, которые можно обучить на VPS без заметной нагрузки, если данные нельзя экспортировать.
- ML-модель не получает право применять санкции без достаточной проверенной разметки.
- Пользовательские данные имеют один канонический store; Discord, админка и web/app не должны вести независимые копии.
- Используются только бесплатные/self-hosted компоненты.

## Что уже работает

### Discord-бот

- Markov-пародии двух качеств: `мем` и `разум`;
- сбор корпуса сообщений с фильтрами каналов;
- профиль стиля, вычисляемый из статистики корпуса без отдельной Persona-модели;
- рейтинги удачных и неудачных Markov-фраз;
- активности, игровые сессии, привычки и игровые хокку на шаблонах/статистике;
- ежедневные и периодические итоги сервера;
- экономика, репутация, роли, дни рождения, напоминания;
- Steam, Riot/LoL и Wuthering Waves-интеграции;
- токсичность: рабочие правила + отдельный ML-классификатор в `shadow`-режиме;
- социальные ответы и троллинг используют только Markov.

### Приватная админ-панель

- вход через Discord OAuth и проверка серверных прав;
- IP allowlist, защищённая сессия и безопасные write-запросы;
- единый реестр функций и каналов;
- просмотр известных баз и настроек;
- управление днями рождения через общий store;
- просмотр shadow-предсказаний токсичности и ручная разметка уровня `0..3`;
- условный перезапуск только после настроек, которым он действительно нужен.

### Web/app

- Discord OAuth и одноразовый код входа из Discord;
- единый профиль пользователя;
- комнаты, сообщения и канонические личные DM;
- DM доступны только двум участникам; админские права не раскрывают чужую переписку;
- редактирование и удаление сообщений проверяют автора;
- API статуса версионированных ML-артефактов;
- CSRF/origin-проверки, CSP и безопасные cookie.

## Структура проекта

```text
core/                   канонические stores, сервисы, runtime policy, админка
fun_slesh/              Discord cogs и slash-команды
scheduled/              фоновые задачи
web_app/                отдельный web/app сервис
scripts/                deploy, backup, локальное обучение и sync артефактов
datebase/               runtime SQLite (не источник кода)
models/                 лёгкие артефакты и manifest.json
tests/                  unit/integration tests
deploy/systemd/          шаблоны systemd
```

Ключевые границы пародий:

- `core/parody_message_store.py` — корпус и checkpoint-данные;
- `core/parody_feedback_store.py` — оценки фраз;
- `core/parody_model_service.py` — обучение и inference Markov;
- `fun_slesh/parody_engine.py` — только Discord UI/orchestration;
- `core/ml_artifacts.py` — SHA256, версия и переносимость артефактов.

## ML/AI вне пародий

### Токсичность

`core/toxicity_model_service.py` реализует лёгкий Multinomial Naive Bayes по hashed character n-grams. Модель:

- не требует `torch`, `transformers` или внешнего API;
- загружается лениво из `models/toxicity_nb.json`;
- сравнивается с правилами, но `effective_level` остаётся уровнем правил;
- пишет shadow-предсказания в `toxicity_ml_shadow`;
- получает проверенную разметку из `toxicity_ml_feedback` через админ-панель.

Обучение:

```powershell
python scripts/train_toxicity_model.py --max-clean 2000
```

Пока проверенных меток мало, модель остаётся только наблюдателем. Переход к предупреждениям или автоматике допустим после отдельной оценки precision/recall на ручной разметке.

### Advisory-инсайты

`core/ml_insights.py` объединяет read-only сигналы, доступные администратору через `GET /api/ml/insights`:

1. Экономика: robust MAD-порог для необычных начислений и сверка wallet с ledger — без автоматического списания.
2. Игры: cosine similarity истории игровых сессий для поиска совместимых участников.
3. Активность: обнаруженные временные привычки с числом дней наблюдения.
4. Качество данных: orphan-проверки связей Steam/web и объём проверенной ML-разметки.

Все результаты имеют режим `advisory`: они объясняют, что стоит проверить, но сами ничего не блокируют и не исправляют.

Следующие шаги: рекомендации конкретных игр, ранжирование событий итогов и более точные модели после накопления обратной связи.

## Локальное Markov-обучение

Сначала синхронизировать свежий `messages.db`, затем обучить:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sync_messages_from_vps.ps1
powershell -ExecutionPolicy Bypass -File scripts/train_local.ps1 -All
```

Для одного пользователя:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/train_local.ps1 -UserId 123456789012345678
```

Синхронизация лёгких артефактов на VPS:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sync_training_to_vps.ps1
```

Скрипт сначала обновляет `models/manifest.json`, прекращает работу при пустом наборе артефактов и не отправляет каталоги тяжёлых моделей.

## Конфигурация

Скопируй `KGTD.env.example` в `KGTD.env` и заполни секреты. Минимально нужен Discord bot token. Основные группы:

- Discord bot/OAuth;
- web/app session и bot API;
- Steam/Riot API при использовании интеграций;
- server safety toggles;
- приватная web-admin панель;
- LiveKit, если включается self-hosted voice.

`KGTD.env` нельзя коммитить или включать в deploy bundle.

## Проверка

```powershell
python -m unittest tests.test_core_foundation
python -m py_compile main_file.py core/admin_panel.py web_app/server.py fun_slesh/parody_engine.py fun_slesh/toxicity.py
git diff --check
```

## Production

Рабочий каталог VPS: `/opt/dis-bot`.

Systemd units:

- `vipik-discord-bot.service`;
- `vipik-web-app.service`.

После deploy необходимо проверить:

```bash
systemctl is-active vipik-discord-bot vipik-web-app
journalctl -u vipik-discord-bot --no-pager -n 100
journalctl -u vipik-web-app --no-pager -n 100
```

Приватный web/app доступен через Tailscale. Не открывай админку и app в публичный интернет без отдельного reverse proxy, TLS и пересмотра модели доступа.
