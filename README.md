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
- тихий сбор игровых сессий и статистика активностей без проактивных напоминаний;
- ежедневные и периодические итоги сервера;
- экономика, репутация, роли, дни рождения, напоминания;
- Steam, Riot/LoL и Wuthering Waves-интеграции;
- токсичность: рабочие правила + отдельный ML-классификатор в `shadow`-режиме;
- пародии и имитация стиля используют только Markov;
- разговорные ответы по явному обращению могут использовать бесплатную локальную Qwen3 через Ollama.

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
- комнаты, сообщения и канонические личные DM с выбором участника и счётчиками непрочитанного;
- DM доступны только двум участникам; админские права не раскрывают чужую переписку;
- общий web-чат, каналы, DM, реакции и Discord-outbox используют единое хранилище `platform_store`; старые `web_chat_*` таблицы мигрируются один раз и архивируются после сверки;
- редактирование и удаление сообщений проверяют автора;
- API статуса версионированных ML-артефактов;
- приватные голосовые комнаты через self-hosted LiveKit, выбор устройств и показ экрана;
- закрытые voice-комнаты с membership и ограниченными приглашениями;
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

Границы статистики и пассивных наград:

- `core/activity_rewards_store.py` — канонические настройки `activity_rewards`, исключения `message_stats`, счётчики и безопасная legacy-миграция;
- `core/activity_rewards_service.py` — расчёт наград и интеграция с общей экономикой;
- `fun_slesh/message_and_voice_stats.py` — Discord-команды, события и представление без собственного config-store.

Границы игровых и репутационных ролей:

- `core/heroes_store.py` сохраняет Heroes-сессии и канал `heroes_troll`, а `core/heroes_service.py` распознаёт игры и формирует шутливый текст;
- `core/reputation_store.py` — единственный записывающий владелец `reputation` и `mood`; ручные оценки, игровые события, activity rewards и Размер-роли используют его API;
- `core/reputation_service.py` содержит cooldown игровых наград и представление настроения без Discord/SQLite;
- `core/rep_roles_store.py` хранит пороги и выданные роли, флаг `rep_roles` находится в `settings_store`;
- `core/rep_roles_service.py` генерирует название роли из Markov-корпуса без переноса GPT в пародии;
- Discord cogs больше не создают и не читают собственные config-таблицы этих функций.

Проверка production/backup для этого слоя: `python -m scripts.report_reputation_storage --database datebase/social.db`.

Границы игровых и репутационных ролей:

- `core/heroes_store.py` сохраняет Heroes-сессии и канал `heroes_troll`, а `core/heroes_service.py` распознаёт игры и формирует шутливый текст;
- `core/reputation_store.py` — единственный записывающий владелец `reputation` и `mood`; ручные оценки, игровые события, activity rewards и Размер-роли используют его API;
- `core/reputation_service.py` содержит cooldown игровых наград и представление настроения без Discord/SQLite;
- `core/rep_roles_store.py` хранит пороги и выданные роли, флаг `rep_roles` находится в `settings_store`;
- `core/rep_roles_service.py` генерирует название роли из Markov-корпуса без переноса GPT в пародии;
- Discord cogs больше не создают и не читают собственные config-таблицы этих функций.

Проверка production/backup для этого слоя: `python -m scripts.report_reputation_storage --database datebase/social.db`.

## ML/AI вне пародий

### Токсичность

`core/toxicity_model_service.py` реализует лёгкий Multinomial Naive Bayes по hashed character n-grams. Модель:

- не требует `torch`, `transformers` или внешнего API;
- загружается лениво из `models/toxicity_nb.json`;
- сравнивается с правилами, но `effective_level` остаётся уровнем правил;
- пишет shadow-предсказания в `toxicity_ml_shadow`;
- получает проверенную разметку из `toxicity_ml_feedback` через админ-панель.

Границы runtime:

- `core/toxicity_store.py` — единственный владелец журналов, weekly-счётчиков, shadow-примеров, feedback и настроек функции;
- `core/toxicity_service.py` — cooldown и формирование шутливого ответа; пародийная вставка остаётся Markov-only;
- `fun_slesh/toxicity.py` — Discord-события и slash UI без прямого SQLite;
- журнал события и weekly-счётчик записываются одной транзакцией, а `scripts/report_toxicity_storage.py` сравнивает production и backup без изменения БД.

Границы runtime:

- `core/toxicity_store.py` — единственный владелец журналов, weekly-счётчиков, shadow-примеров, feedback и настроек функции;
- `core/toxicity_service.py` — cooldown и формирование шутливого ответа; пародийная вставка остаётся Markov-only;
- `fun_slesh/toxicity.py` — Discord-события и slash UI без прямого SQLite;
- журнал события и weekly-счётчик записываются одной транзакцией, а `scripts/report_toxicity_storage.py` сравнивает production и backup без изменения БД.

Обучение:

```powershell
python scripts/train_toxicity_model.py --max-clean 2000
```

Пока проверенных меток мало, модель остаётся только наблюдателем. Переход к предупреждениям или автоматике допустим после отдельной оценки precision/recall на ручной разметке.

### Разговорный бот без платного API

`core/conversation_service.py` обращается к Ollama на приватном ПК, а VPS хранит
короткий контекст явных диалогов в `conversation_turns`. По умолчанию бот отвечает
только на упоминание, имя `ViPik` или ответ на его сообщение. Случайные ответы
возможны лишь после двух действий администратора: включить добровольный режим и
задать разрешённые каналы. Отложенный троллинг и напоминания о привычном времени
игры удалены.

Рекомендуемая модель для ПК с 16 ГБ VRAM — `qwen3:8b`. Настройки:

```env
LOCAL_CHAT_API_URL=http://100.x.y.z:11434
LOCAL_CHAT_MODEL=qwen3:8b
LOCAL_CHAT_TIMEOUT_SECONDS=45
```

Если ПК или Ollama недоступны, бот автоматически использует Markov-пародию либо
короткие встроенные ответы. Реакции 👍/👎 на его ответы сохраняются в
`conversation_feedback` и формируют проверенный датасет для будущей локальной
LoRA/ранжирующей модели; обучение на всём чате без согласия не выполняется.

### Advisory-инсайты

`core/ml_insights.py` объединяет read-only сигналы, доступные администратору через `GET /api/ml/insights`:

1. Экономика: robust MAD-порог для необычных начислений и сверка wallet с ledger — без автоматического списания.
2. Игры: cosine similarity истории игровых сессий для поиска совместимых участников.
3. Активность: сходство истории игр для добровольных совместных рекомендаций, без напоминаний.
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
Для web OAuth обязательно задаются `APP_ALLOWED_GUILD_IDS`; bootstrap-владелец может быть только в `APP_OWNER_USER_IDS`.

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
- `vipik-web-app.service`;
- `vipik-livekit.service`.

После deploy необходимо проверить:

```bash
systemctl is-active vipik-discord-bot vipik-web-app vipik-livekit
journalctl -u vipik-discord-bot --no-pager -n 100
journalctl -u vipik-web-app --no-pager -n 100
scripts/smoke_livekit.sh
```

Приватный web/app и signaling LiveKit публикуются через Tailscale Serve после однократного включения Serve владельцем tailnet. Media-порты разрешены только через `tailscale0`; публичное размещение без отдельного security review запрещено.

Production-миграция основного набора feature-настроек завершена: проверенные старые config-таблицы выведены из runtime-пути в `*_legacy_backup`. Пассивные награды и исключения статистики также мигрируют в `core.settings_store`; оставшиеся модульные конфигурации переносятся поэтапно после отдельного prod-аудита.
