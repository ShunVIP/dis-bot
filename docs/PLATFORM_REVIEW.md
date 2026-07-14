# ViPik Platform Review

Дата первоначального ревью: 2026-06-30. Актуальное решение от 2026-07-13: пародии Markov-only, GPT/Persona/model bridge удалены; README является текущей картой проекта.

Этот документ заменяет старые handoff/audit-файлы и является текущей картой ревью проекта. Цель ревью: убрать сумбур, оставить Discord-бота понятным для пользователей, вынести админские и опасные действия в отдельную панель/app и постепенно развивать сайт/app как fallback-платформу для друзей.

## Текущая карта поверхностей

| Поверхность | Где | Назначение | Решение |
|---|---|---|---|
| Discord bot | `main_file.py`, `fun_slesh/` | Пользовательские действия, меню, фоновые listeners | Оставить, сделать компактным пользовательским UX |
| Меню бота | `fun_slesh/menu.py` | Главный вход `/команды` и `/админ` | Продолжать переводить команды в кнопки, select menus и modals |
| VPS admin panel | `core/admin_panel.py` | Статус, runtime policy, настройки и ML feedback | Расширять в сторону полноценной админки |
| User website/app | `web_app/`, `core/web_app_store.py`, `core/platform_store.py`, `core/voice_store.py` | Профили, чат, каналы, DM, voice rooms, fallback | Развивать как запасной Discord-like интерфейс |
| Local GUI | `scripts/bot_control_gui.py` | Локальное Markov/ML-обучение, sync, deploy helpers | Оставить как local-only инструмент владельца |
| Deploy/VPS | `scripts/bootstrap_vps.sh`, `scripts/deploy.sh`, `deploy/systemd/` | Продовый запуск и обновление | Оставить, менять отдельными deploy-шагами |

## Блоки ревью

| Блок | Тип | Текущий владелец | Целевой владелец | Решение |
|---|---|---|---|---|
| Экономика, баланс, магазин | user commands, DB | Discord menu | Discord + user app | Оставить, админские настройки в admin app |
| Игры и фан | user commands, views | Discord menu | Discord menu | Оставить только живые сценарии, редкие удалить после проверки |
| Размер, настроение, ачивки | user commands, counters | Discord menu | Discord + user profile | Оставить, вынести профиль в сайт/app |
| Статистика сообщений/войса | listeners, counters, DB | background + Discord | background + user app | Оставить, привести к shared stores |
| Итоги дня/недели/месяца | scheduler, autopost | Discord | background + admin settings | Оставить, настройки в admin app |
| Дни рождения | user commands, scheduler | Discord | Discord + user app | Оставить, админские правки в admin app |
| Напоминания/temp roles | user/admin commands, scheduler | Discord | user app + admin app | Напоминания оставить пользователям, temp roles в admin app |
| Пародия Markov | commands, models, maintenance | Discord + local GUI | user feature + local/admin tools | Только Markov; обучение/профилактику убрать из user UX |
| Сбор сообщений | listener/collector, DB | Discord/admin commands | background/admin app | Опасное, только admin/maintenance |
| Toxicity | listener, counters, admin settings | Discord | admin app + moderation views | Оставить после ревью пользы, настройки в admin app |
| Social chat | listener, settings | Discord | admin app + bot behavior | Оставить как включаемую фичу, настройки в admin app |
| Activity/Heroes/67 | listeners, local memes | Discord background | feature flags/admin app | Пересмотреть, оставить только если реально нужно серверу |
| Steam | external integration | Discord | Discord + user app | Оставить пользовательские сценарии, releases/admin в admin app |
| WWM KB/guild | search, DB, scheduler | Discord + web | Discord + user app | Оставить, KB refresh пересмотреть по ценности |
| LoL/Riot profiles | external integration, model profile | Discord + web | user app + Discord shortcut | Оставить, ключи только через `KGTD.env` |
| Web chat/DM/channels | web API/store | `web_app` | user website/app | Развивать как fallback communication |
| Voice rooms/screen share | web API/store, LiveKit config | `web_app` | user website/app | Развивать через LiveKit/WebRTC |
| Deploy scripts | tooling | local/VPS | owner-only tooling | Оставить, не смешивать с user/admin UI |

## Опасные действия

Эти действия нельзя держать в обычном пользовательском меню:

- Массовое Markov/ML-обучение.
- Полная профилактика.
- Сбор сообщений и сброс чекпоинтов.
- Индексация истории каналов.
- Массовое изменение ролей.
- Настройки каналов, фильтров, токсичности, болтовни, автопостов.
- Отправка моделей/баз на VPS.
- Любые операции с env, systemd и deploy.

## Stage 5 UX decisions 2026-06-30

Принятые продуктовые решения по Discord-боту:

- `/админ` больше не должен быть полноценным Discord UX. Все действия из `/админ`, включая `/др_ад`, `/д-р_ад`, `/др_канал` и прочие настройки, переносятся в отдельную админ-панель/app.
- `/админ` в Discord должен быть только коротким входом-кнопкой в админ-панель. Без описания разделов, адресов и статусов в сообщении.
- Целевой вход в web admin panel: Discord OAuth + проверка прав администратора/разрешенной роли на сервере. `WEB_ADMIN_TOKEN` считается временным fallback, а не финальным UX.
- После появления постоянного сообщения в админском канале `/админ` можно будет убрать из публичного slash sync и оставить только вечную кнопку.
- Обычный пользовательский вход остается один: `/команды`.
- Профиль должен стать единым пользовательским окном: личная карточка, 18+ экономика-профиль, день рождения, Steam, Riot/LoL, WWM-ник и будущие привязки аккаунтов.
- Магазин должен открываться как единый интерактивный магазин: просмотр ролей, покупка, действия с валютой внутри одного окна, а не отдельные разрозненные команды.
- Все топы должны вызываться через одно окно/раздел топов.
- Игры должны быть одним аккуратным игровым разделом, чтобы мини-игры, Steam, LoL, WWM и игровые интеграции не занимали много места в меню.
- Пародии оставляем строго Markov-only; ML развиваем в других подсистемах.
- Настройки токсичности полностью уходят в админку.
- Настройки болтовни полностью уходят в админку.
- Команды с одинаковым смыслом надо объединять в хабы и внутренние select/modals, чтобы они не мешали друг другу.

Первый слой реализации:

- `/админ` больше не строит Discord-каталог админ-команд, а показывает только кнопку входа в web admin panel.
- В `/команды` профиль расширен до хаба: личная инфа, ДР, настроение, ачивки и привязки Steam/Riot/WWM.
- Топы и итоги объединены в отдельный хаб вместо набора отдельных кнопок.
- Игровые функции объединены в игровой хаб: мини-игры, Steam, LoL и WWM.
- Магазин оставлен как хаб с просмотром, покупкой роли и переводом валюты.

## Cleanup 2026-06-30

Удалены устаревшие handoff/audit/миграционные документы, приватные placeholders, старые сборки, кэши, локальные логи/state-файлы, локальные SQLite-базы и локальные модели. `KGTD.env`, deploy templates, скрипты и исходный код сохранены.

После этого локальные данные нужно собирать заново уже после ревью решений по функциям.

## Admin panel foundation 2026-06-30

В `core/admin_panel.py` добавлен первый рабочий слой будущей админки:

- реестр переносимых зон: настройки сервера, экономика/роли, модели, maintenance, fallback platform;
- feature registry для `daily_summary`, `birthday`, `wwm_guild`, `steam`, `toxicity`, `social_chat`, `voice_roles`, `economy`, `parody_training`, `maintenance`, `fallback_platform`;
- формы включения/выключения фич;
- формы `output`, `allow`, `exclude` каналов там, где они применимы;
- JSON payload для дополнительной конфигурации;
- хранение через существующий `core.settings_store`, без новой параллельной таблицы.

Это ещё не перенос всей бизнес-логики из Discord-команд, но уже целевая точка, куда следующие этапы будут подключать реальные настройки.

## Module settings migration 2026-06-30

> Статус на 2026-07-13: миграция завершена. Описанные ниже fallback и dual-write были переходным состоянием. Production coverage-аудит подтвердил 4/4 legacy-строки без расхождений; исходные config-таблицы переименованы в `*_legacy_backup`, а runtime использует только `core.settings_store`.

Текущее состояние после production-аудита:

- `daily_summary`, `toxicity`, `social_chat`, `voice_roles`, `birthday`, `wwm_guild`, `steam`, `economy` и `activity_tracker` читают и изменяют только `core.settings_store`;
- их старые таблицы находятся только в `*_legacy_backup`, активных двойных путей нет;
- `activity_rewards` и исключения `message_stats` перенесены из `message_and_voice_stats.py` в `core/activity_rewards_store.py`;
- расчёт пассивных наград отделён в `core/activity_rewards_service.py`, Discord cog отвечает только за события и UI;
- `heroes_troll_config` и `rep_roles_config` также выведены из runtime после prod-аудита; история Heroes, пороги и активные роли сохранены в отдельных data-store таблицах.

## Следующий порядок работ

1. Добить пользовательское меню: убрать админские/maintenance пункты из `/команды`, переименовать спорные кнопки, проверить дубли.
2. Подключать реальные модули к admin panel settings: читать `core.settings_store` вместо локальных таблиц/ручных slash-настроек.
3. Развивать `web_app`: профили, чат, каналы, DM, voice rooms, screen share, presence.
4. Проходить модуль за модулем и принимать решение: оставить, переписать, перенести, отключить или удалить.
