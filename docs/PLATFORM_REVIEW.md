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

Первый подключенный модуль: `fun_slesh/daily_summary.py`.

- Автопостинг итогов дня/недели/месяца теперь читает `daily_summary` policy из `core.settings_store`.
- Старый `daily_summary_config` оставлен как fallback, чтобы не ломать существующие серверные настройки.
- Discord-admin команды `/итоги канал` и `/итоги вкл` теперь пишут и в новый `settings_store`, и в старую таблицу совместимости.
- `core/admin_panel.py` пишет feature settings в реальный guild id первого сервера бота, а не в абстрактный `0`.

Второй подключенный модуль: `fun_slesh/toxicity.py`.

- Детектор токсичности теперь читает `toxicity` policy из `core.settings_store`.
- Поддержаны `enabled`, JSON payload `threshold`, `allow` каналы и `exclude` каналы.
- Старые `toxicity_config` и `toxicity_excluded_channels` оставлены как fallback.
- Discord-admin команды токсичности пишут и в новый `settings_store`, и в старые таблицы совместимости.

Третий подключенный модуль: `fun_slesh/social_chat.py`.

- Болтовня бота теперь читает `social_chat` policy из `core.settings_store`.
- Поддержаны `enabled`, JSON payload `chance_percent` и `mention_only`, `allow` каналы и `exclude` каналы.
- Старые `social_chat_config` и `social_chat_excluded_channels` оставлены как fallback.
- Discord-admin команды `/болтовня ...` пишут и в новый `settings_store`, и в старые таблицы совместимости.

Четвертый подключенный модуль: `fun_slesh/voice_roles.py`.

- Авто-роли голосовых каналов теперь читают `voice_roles` policy из `core.settings_store`.
- Поддержаны `enabled` и `exclude` каналы.
- Старые `voice_roles_config` и `voice_roles_excluded_channels` оставлены как fallback.
- Discord-admin команды `/войс_роли ...` пишут и в новый `settings_store`, и в старые таблицы совместимости.

Пятый подключенный блок: дни рождения.

- `fun_slesh/birthday.py` и `scheduled/hourly_task.py` теперь читают `birthday.output` из `core.settings_store`.
- Старый `birthday_config` оставлен как fallback.
- Команда `/др_канал` пишет и в новый `settings_store`, и в старую таблицу совместимости.

Шестой подключенный блок: `fun_slesh/wwm_guild.py`.

- WWM welcome channel теперь читает `wwm_guild.output`.
- Reception channel, auto nickname и nickname template читаются из JSON payload.
- Старый `wwm_config` оставлен как fallback.
- Команды `/wwm канал`, `/wwm приемная`, `/wwm ники` синхронизируют новый `settings_store`.

Седьмой подключенный блок: `fun_slesh/steam.py`.

- Steam release/discount notifications теперь читают `steam.output`.
- Минимальная скидка читается из JSON payload `discount_min_pct`.
- Старый `steam_config` оставлен как fallback.
- Команда `/релизы канал` пишет и в новый `settings_store`, и в старую таблицу совместимости.

Восьмой подключенный блок: налог экономики в `fun_slesh/daily.py`.

- Scheduler налога читает JSON payload `tax_enabled`, `tax_rate_pct`, `tax_interval_h` из `economy`.
- Старый `tax_config` оставлен как fallback и хранит `last_run`.
- Команда `/налог_настроить` пишет и в новый `settings_store`, и в старую таблицу совместимости.

Следующие хорошие кандидаты на перенос: role shop settings, birthday text/style, Steam auto prompts, WWM KB search/indexing, reminders.

## Следующий порядок работ

1. Добить пользовательское меню: убрать админские/maintenance пункты из `/команды`, переименовать спорные кнопки, проверить дубли.
2. Подключать реальные модули к admin panel settings: читать `core.settings_store` вместо локальных таблиц/ручных slash-настроек.
3. Развивать `web_app`: профили, чат, каналы, DM, voice rooms, screen share, presence.
4. Проходить модуль за модулем и принимать решение: оставить, переписать, перенести, отключить или удалить.
