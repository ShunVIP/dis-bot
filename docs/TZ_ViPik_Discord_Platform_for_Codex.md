# ТЕХНИЧЕСКОЕ ЗАДАНИЕ

# ViPik Discord Platform

Документ для поэтапной реализации через Codex.

Этот документ связан с исходным ТЗ `TZ_Discord_Platform_for_Codex_FULL.docx`, но адаптирован под существующий проект `ShunVIP/dis-bot` / `ViPik Discord Bot`. Его нужно рассматривать как рабочее ТЗ для разработчика: не как идею "когда-нибудь", а как план реализации сайта/приложения вокруг текущего Discord-бота.

## 1. Краткое резюме

Нужно расширить существующего Discord-бота до полноценной платформы управления Discord-сообществом.

Платформа должна включать:

- отдельный сайт сообщества;
- веб-приложение/личный кабинет;
- вход через Discord OAuth2;
- админ-панель;
- интеграцию с текущим Python Discord bot;
- fallback-портал на случай, когда Discord недоступен;
- возможность общения/обращений вне Discord, если Discord отвалился;
- базу пользователей, заявок, тикетов, объявлений, настроек и аудита;
- безопасную работу с VPS, env-секретами, базами и bridge для GPT-моделей.

Текущий бот не переписывать с нуля. Его нужно постепенно интегрировать с backend через внутренний API.

## 2. Цель продукта

Сделать независимый от Discord внешний контур сообщества:

1. Пользователь заходит на сайт через Discord.
2. Система запоминает пользователя, его Discord ID, профиль, роли и статус.
3. Пользователь видит кабинет, заявки, тикеты, объявления, инструкции и статус сервисов.
4. Модератор/администратор управляет заявками, тикетами, объявлениями, ролями и настройками.
5. Бот получает задачи от backend и отправляет события в backend.
6. Если Discord лежит, уже авторизованные пользователи могут открыть fallback-портал и продолжить связь через сайт.

Важное ограничение: если Discord полностью недоступен, новый вход через Discord OAuth2 может не работать. Поэтому fallback должен опираться на уже существующие сессии, refresh-сессии и, после MVP, резервные коды доступа.

## 3. Что уже есть

Существующий репозиторий `dis-bot` содержит:

- Python Discord bot на `discord.py`;
- точку входа `main_file.py`;
- загрузку `KGTD.env` через `config.py`;
- slash-модули в `fun_slesh/`;
- SQLite базы в `datebase/`;
- Markov/GPT пародии и обучение;
- локальные модели в `models/`;
- VPS deploy scripts;
- systemd templates;
- веб-админку в `core/admin_panel.py`;
- bridge для тяжелых GPT-моделей через Tailscale;
- GitHub Actions deploy на VPS.

Это нужно сохранить и использовать.

## 4. Границы MVP

MVP должен быть небольшим, но рабочим. Его нужно запускать локально одной командой через Docker Compose.

В MVP входит:

- backend API;
- PostgreSQL;
- Redis или простая очередь задач;
- frontend сайт/приложение;
- Discord OAuth2 login;
- пользовательский кабинет;
- базовая админ-панель;
- заявки;
- тикеты;
- объявления;
- fallback-портал;
- fallback-чат или fallback-обращения;
- интеграция существующего бота через Bot API;
- audit logs;
- `.env.example`;
- README;
- тесты.

В MVP не входит:

- мобильное приложение в App Store / Google Play;
- Kubernetes;
- микросервисы;
- платежи;
- selfbot;
- собственный Discord client;
- обход Discord API;
- scraping Discord вне официальных API;
- замена Discord voice/chat;
- массовые рассылки;
- перенос тяжелой GPT-модели на VPS.

## 5. Предлагаемый стек

Backend:

- Python 3.12+;
- FastAPI;
- SQLAlchemy 2.x;
- Alembic;
- PostgreSQL;
- Redis для очередей/кэша, если нужно;
- pytest.

Frontend:

- Next.js;
- TypeScript;
- React;
- Tailwind или другой аккуратный UI kit;
- SSR/SPA по необходимости.

Bot integration:

- текущий `discord.py` bot;
- отдельный bot API client;
- `BOT_API_TOKEN`;
- polling `/bot/tasks` или Redis queue;
- отправка событий в `/bot/events`.

Deploy:

- Docker Compose для MVP;
- Nginx/Caddy/Traefik и HTTPS для production;
- VPS: текущий `/opt/dis-bot` сохранить до отдельного плана миграции.

## 6. Целевая архитектура

```text
User Browser / Web App
        |
        v
Frontend Next.js
        |
        v
Backend FastAPI <----> PostgreSQL
        |                 |
        |                 +--> users / sessions / roles
        |                 +--> applications / tickets / messages
        |                 +--> announcements / audit_logs
        |                 +--> bot_tasks / bot_events
        |
        +----> Redis queue/cache
        |
        +----> Existing Discord Bot
        |          |
        |          +--> Discord Bot API
        |          +--> current slash commands
        |          +--> current SQLite/runtime data during transition
        |
        +----> Fallback Portal / Fallback Chat
        |
        +----> Admin Panel
```

Переход должен быть постепенным: сначала backend дополняет бота, потом часть настроек и данных переезжает в PostgreSQL.

## 7. Роли

Guest:

- не авторизован;
- видит публичную страницу, статус и инструкции.

User:

- авторизован через Discord;
- видит профиль, заявки, тикеты, объявления, fallback-чат.

Moderator:

- обрабатывает заявки;
- отвечает в тикетах;
- видит ограниченные данные пользователей;
- получает уведомления.

Administrator:

- управляет пользователями, ролями, объявлениями, настройками бота;
- видит audit logs;
- управляет fallback mode.

Super Administrator:

- полный доступ;
- системные настройки;
- управление интеграциями, bot tokens, service settings через env/secret manager, но не через frontend.

## 8. Discord OAuth2

Нужно реализовать Authorization Code Flow:

- `/auth/discord/login`;
- `/auth/discord/callback`;
- проверка `state`;
- защита от CSRF;
- получение Discord profile;
- сохранение `discord_id`, `username`, `avatar_url`, `email`, `last_login_at`;
- создание httpOnly secure session cookie или JWT refresh-token pattern;
- `/auth/me`;
- `/auth/logout`;
- инвалидация сессии.

Нельзя:

- хранить Discord user token как основной механизм;
- использовать selfbot;
- отдавать client secret на frontend;
- хранить bot token в базе или frontend.

## 9. Личный кабинет

Пользователь должен видеть:

- Discord профиль;
- текущий статус в системе;
- роли/группы, если доступно;
- свои заявки;
- свои тикеты;
- fallback-чат/обращения;
- объявления;
- инструкции;
- статус Discord/VPS/бота, если разрешено.

## 10. Заявки

Заявка нужна для управляемого доступа/ролей/участия в сообществе.

Статусы:

```text
new | review | approved | rejected | cancelled
```

Пользователь:

- создает заявку;
- видит статус;
- может отменить до обработки;
- видит комментарий модератора.

Модератор:

- берет заявку в работу;
- одобряет/отклоняет;
- оставляет комментарий;
- при одобрении создает bot task на выдачу роли.

Бот:

- получает задачу;
- выдает роль в Discord;
- возвращает результат в backend.

## 11. Тикеты

Тикеты нужны для поддержки и общения вне Discord.

Статусы:

```text
open | pending | closed
```

Функции:

- пользователь создает тикет;
- пользователь и staff пишут сообщения;
- закрытый тикет доступен пользователю только на чтение;
- модераторы видят очередь тикетов;
- важные действия пишутся в audit logs.

## 12. Fallback-портал

Fallback-портал нужен, когда Discord недоступен, заблокирован, лагает или сервер/каналы временно недоступны.

MVP fallback-портала:

- страница статуса;
- объявления;
- инструкции;
- тикеты;
- fallback-чат или fallback-обращения;
- доступ для уже авторизованных пользователей;
- админский переключатель `fallback_mode`;
- отображение причины/статуса аварийного режима.

Важное поведение:

- если Discord OAuth2 не работает, новые пользователи могут не войти;
- пользователи с действующей сессией должны иметь доступ;
- refresh sessions должны жить достаточно долго;
- после MVP можно добавить backup login codes.

## 13. Fallback-чат

Нужно реализовать минимальный чат внутри сайта, чтобы сообщество могло общаться при проблемах с Discord.

MVP вариант:

- общий emergency room;
- сообщения только для авторизованных пользователей;
- moderator/admin могут закреплять сообщение;
- rate limit;
- basic moderation;
- audit/admin log;
- хранение сообщений в PostgreSQL.

После MVP:

- несколько комнат;
- private staff replies;
- attachments;
- push/email notifications;
- slow mode;
- export истории.

## 14. Админ-панель

Админ-панель должна включать:

- список пользователей;
- поиск по Discord ID, username, email;
- карточку пользователя;
- заявки с фильтрами;
- тикеты с фильтрами;
- fallback chat moderation;
- объявления;
- audit logs;
- настройки бота;
- role mapping;
- feature flags;
- статус bot integration;
- статус VPS/bridge только в безопасном виде.

Все admin endpoints защищать RBAC.

## 15. Интеграция существующего бота

Текущий бот не переписывать с нуля.

Отдельное UX-решение для Discord-команд:

- публично через `/` должны быть видны только `/команды` и `/админ`;
- все пользовательские действия выбираются через меню, кнопки, select menus и modals;
- админские и maintenance-действия должны постепенно переехать в web/admin panel;
- старые slash-команды можно сохранять как внутренние обработчики, но не синхронизировать их как публичные Discord slash-команды.

Нужно добавить:

- bot API client module;
- отправку событий в backend;
- получение настроек из backend там, где это безопасно;
- получение задач от backend;
- отправку результата выполнения задач;
- отдельный `BOT_API_TOKEN`.

Bot endpoints:

```text
POST /bot/events
GET  /bot/settings
GET  /bot/tasks
POST /bot/tasks/{task_id}/result
```

Типы задач:

```text
grant_role
revoke_role
send_notification
sync_member
update_setting
```

## 16. База данных MVP

Минимальные таблицы:

```text
users
sessions
roles
user_roles
applications
tickets
ticket_messages
fallback_rooms
fallback_messages
announcements
bot_settings
bot_tasks
bot_events
audit_logs
feature_flags
```

Обязательные поля:

users:

- id;
- discord_id unique;
- username;
- avatar_url;
- email;
- is_active;
- is_moderator;
- is_admin;
- created_at;
- updated_at;
- last_login_at.

sessions:

- id;
- user_id;
- refresh_token_hash/session_id;
- user_agent;
- ip_address;
- expires_at;
- revoked_at;
- created_at.

applications:

- id;
- user_id;
- status;
- text;
- moderator_comment;
- reviewed_by;
- created_at;
- updated_at.

tickets:

- id;
- user_id;
- status;
- title;
- priority;
- created_at;
- updated_at;
- closed_at.

fallback_messages:

- id;
- room_id;
- user_id;
- message;
- is_deleted;
- created_at;
- deleted_at.

bot_tasks:

- id;
- type;
- status;
- payload_json;
- result_json;
- attempts;
- created_at;
- updated_at;
- completed_at.

audit_logs:

- id;
- actor_user_id;
- event_type;
- target_type;
- target_id;
- metadata_json;
- ip_address;
- created_at.

## 17. REST API MVP

Public:

```text
GET /health
GET /status
GET /announcements/public
GET /auth/discord/login
GET /auth/discord/callback
```

User:

```text
GET  /auth/me
POST /auth/logout
GET  /users/me
PATCH /users/me

GET  /applications/me
POST /applications
POST /applications/{id}/cancel

GET  /tickets/me
POST /tickets
GET  /tickets/{id}
POST /tickets/{id}/messages

GET  /fallback/status
GET  /fallback/rooms
GET  /fallback/rooms/{id}/messages
POST /fallback/rooms/{id}/messages
```

Moderator/Admin:

```text
GET  /admin/users
GET  /admin/users/{id}
GET  /admin/applications
POST /admin/applications/{id}/review
POST /admin/applications/{id}/approve
POST /admin/applications/{id}/reject

GET  /admin/tickets
POST /admin/tickets/{id}/messages
POST /admin/tickets/{id}/close

GET  /admin/fallback/messages
POST /admin/fallback/messages/{id}/delete
POST /admin/fallback/pinned

GET  /admin/announcements
POST /admin/announcements
PATCH /admin/announcements/{id}
DELETE /admin/announcements/{id}

GET  /admin/audit-logs
GET  /admin/bot/settings
PATCH /admin/bot/settings
```

Bot:

```text
POST /bot/events
GET  /bot/settings
GET  /bot/tasks
POST /bot/tasks/{id}/result
```

## 18. Безопасность

Все секреты только через `.env`/secret manager:

```text
DISCORD_CLIENT_ID
DISCORD_CLIENT_SECRET
DISCORD_BOT_TOKEN
DATABASE_URL
REDIS_URL
JWT_SECRET
SESSION_SECRET
BOT_API_TOKEN
WEB_ADMIN_TOKEN
```

Требования:

- не хранить Discord bot token в базе;
- не отдавать секреты на frontend;
- Bot API защищать отдельным ключом;
- httpOnly cookies;
- secure cookies в production;
- SameSite Lax/Strict;
- OAuth state validation;
- RBAC middleware;
- rate limits;
- audit logs;
- sanitization HTML/Markdown;
- защита fallback chat от спама;
- не использовать selfbot/user token.

## 19. Структура репозитория целевой платформы

Вариант A: монорепозиторий рядом с текущим ботом.

```text
vipik-platform/
  README.md
  .env.example
  docker-compose.yml
  docs/
    TZ_ViPik_Discord_Platform_for_Codex.md
    architecture.md
    api.md
    codex-prompts.md
  backend/
    app/
      main.py
      core/
      db/
      models/
      schemas/
      api/
      services/
      tests/
  frontend/
    app/
    components/
    lib/
    pages/
    tests/
  bot_adapter/
    client.py
    events.py
    tasks.py
  existing_bot/
    README.md
```

Вариант B: развивать прямо внутри `dis-bot`, добавив `backend/` и `frontend/`.

Решение принять на Этапе 0 после аудита старого ПК и git.

## 20. Этапы реализации для Codex

Не просить Codex сделать все сразу. Работать этапами.

### Этап 0 - аудит и проектный каркас

Цель:

- понять текущий `dis-bot`;
- выбрать monorepo layout;
- добавить `docs/`, `.env.example`, Docker Compose skeleton;
- не ломать текущего бота.

Промпт:

```text
Ты реализуешь ViPik Discord Platform на базе существующего ShunVIP/dis-bot. Сначала проведи аудит структуры, прочитай CODEX_HANDOFF.md, AGENT_CONTEXT.md, README.md и docs/TZ_ViPik_Discord_Platform_for_Codex.md. Не переписывай бота. Создай минимальный каркас backend FastAPI, frontend Next.js, docker-compose и README так, чтобы текущий бот остался нетронутым.
```

### Этап 1 - backend foundation

Цель:

- FastAPI;
- PostgreSQL;
- SQLAlchemy;
- Alembic;
- health endpoint;
- settings;
- tests.

Промпт:

```text
Implement backend foundation only: FastAPI app, typed settings, PostgreSQL connection, SQLAlchemy base, Alembic migrations, /health, pytest setup, Dockerfile. Add base models users, sessions, audit_logs. Do not implement OAuth or frontend yet.
```

### Этап 2 - Discord OAuth2

Цель:

- login/callback/logout/me;
- sessions;
- mocked tests.

Промпт:

```text
Implement Discord OAuth2 login flow with state validation, secure session cookie or refresh-token session pattern, user persistence/update, /auth/me and /auth/logout. Add tests with mocked Discord API. Never expose secrets to frontend.
```

### Этап 3 - личный кабинет

Цель:

- Next.js frontend;
- login button;
- dashboard;
- profile;
- applications/tickets preview.

Промпт:

```text
Implement the user web app: Discord login entry, authenticated dashboard, profile card, applications list placeholder, tickets list placeholder, announcements and status panel. Use backend auth state. Keep UI practical and not marketing-only.
```

### Этап 4 - bot integration

Цель:

- Bot API;
- `BOT_API_TOKEN`;
- bot_tasks;
- bot_events;
- адаптер для текущего бота.

Промпт:

```text
Integrate existing dis-bot with backend via Bot API. Add POST /bot/events, GET /bot/settings, GET /bot/tasks, POST /bot/tasks/{id}/result, API key auth, bot_tasks model, bot_events model, and a small Python client module for the existing bot. Do not rewrite slash commands.
```

### Этап 5 - заявки

Цель:

- заявки пользователя;
- moderation queue;
- approve -> bot task.

Промпт:

```text
Implement applications module: models, schemas, user endpoints, admin review endpoints, audit logs, and enqueue bot task on approval. Add frontend user application form and admin review queue.
```

### Этап 6 - тикеты

Цель:

- пользовательские тикеты;
- сообщения;
- staff moderation;
- audit.

Промпт:

```text
Implement ticket system with tickets and ticket_messages models, user and admin endpoints, permission checks, frontend user ticket pages, admin ticket queue, and tests for access control.
```

### Этап 7 - fallback portal

Цель:

- status;
- announcements;
- instructions;
- tickets;
- fallback mode flag.

Промпт:

```text
Implement fallback portal pages and backend support: status, announcements, instructions, tickets access for already authenticated users, fallback_mode feature flag, and admin controls. It must avoid calling Discord for already authenticated users.
```

### Этап 8 - fallback chat

Цель:

- emergency room;
- messages;
- moderation;
- rate limit.

Промпт:

```text
Implement MVP fallback chat: authenticated emergency room, message persistence, rate limiting, moderation delete, pinned admin notice, audit logs, and frontend chat UI. This is for communication when Discord is unavailable.
```

### Этап 9 - admin panel

Цель:

- users;
- applications;
- tickets;
- announcements;
- audit logs;
- settings.

Промпт:

```text
Implement admin panel with user search, user detail, application moderation, ticket moderation, announcements management, fallback chat moderation, audit logs viewer, and bot settings editor. Enforce RBAC in backend and frontend route guards.
```

## 21. Критерии готовности MVP

MVP считается готовым, если:

- проект запускается через `docker compose up --build`;
- backend открывает Swagger/OpenAPI;
- frontend открывается локально;
- Discord OAuth2 работает в dev/mock и production mode;
- пользователь может войти и увидеть кабинет;
- пользователь может создать заявку;
- модератор может одобрить заявку;
- при одобрении создается bot task;
- бот может получить тестовую задачу;
- пользователь может создать тикет;
- staff может ответить и закрыть тикет;
- fallback portal доступен авторизованному пользователю;
- fallback chat работает без обращения к Discord для уже авторизованного пользователя;
- admin endpoints защищены RBAC;
- bot endpoints защищены `BOT_API_TOKEN`;
- audit logs пишутся;
- секреты не попадают в git;
- README содержит запуск, env, troubleshooting.

## 22. План тестирования

Auth:

- OAuth state validation;
- mocked Discord callback;
- logout;
- expired session;
- no Discord availability with existing session.

RBAC:

- user cannot access admin endpoints;
- moderator limited;
- admin full access.

Applications:

- create;
- list own;
- approve;
- reject;
- bot task creation.

Tickets:

- create;
- read own;
- staff read all;
- append message;
- close.

Fallback:

- already logged-in user can access fallback;
- new OAuth login fails gracefully when Discord unavailable;
- chat message create/read;
- rate limit;
- moderator delete/pin.

Bot API:

- reject missing API key;
- accept valid API key;
- persist event;
- return queued task;
- accept task result.

Audit:

- admin actions create audit log;
- moderation actions create audit log.

## 23. Production требования

- HTTPS через Nginx/Caddy/Traefik.
- PostgreSQL backup policy.
- Logs rotation.
- Health checks для backend/frontend/bot/database.
- Secret manager или `.env` на сервере.
- Sentry/аналог после MVP.
- Не ломать текущий VPS bot до отдельного migration plan.
- Не переносить тяжелые GPT-модели на VPS.
- Tailscale использовать для приватных admin/bridge контуров.

## 24. Риски

Discord downtime:

- OAuth может быть недоступен;
- mitigation: long-lived refresh sessions, fallback portal, backup login codes after MVP.

Rate limits:

- Discord API ограничивает действия;
- mitigation: queue, retry/backoff, task status.

Token leakage:

- bot token опасен;
- mitigation: env only, rotation, minimal permissions.

Scope creep:

- Codex может начать делать слишком много;
- mitigation: этапы и acceptance criteria.

Breaking existing bot:

- нельзя ломать текущие команды;
- mitigation: bot adapter first, no rewrite.

Bad migration:

- SQLite -> PostgreSQL требует осторожности;
- mitigation: сначала read-only sync/adapters, потом миграции.

## 25. Чеклист перед началом разработки

- [ ] Вернуть GitHub repo в private.
- [ ] На основном ПК проверить реальный git clone.
- [ ] Прочитать `CODEX_HANDOFF.md`.
- [ ] Проверить `KGTD.env`, но не печатать секреты.
- [ ] Проверить `datebase/`, `models/`, SSH key.
- [ ] Проверить VPS `/opt/dis-bot`.
- [ ] Не запускать второй экземпляр бота без подтверждения.
- [ ] Создать ветку для platform work.
- [ ] Начать с Этапа 0.

## 26. Главная инструкция Codex-разработчику

Ты разработчик этого проекта. Твоя задача - не просто описать идею, а реализовывать ее по этапам.

Правила:

- работай маленькими рабочими шагами;
- сохраняй текущего бота работоспособным;
- не трогай секреты без подтверждения;
- не коммить runtime data;
- добавляй тесты;
- после каждого этапа обновляй README;
- не делай второй Discord client/selfbot;
- fallback должен быть реальной частью продукта, а не декоративной страницей.

## 27. Discord menu UX implementation note, 2026-06-20

User decision: the bot should not expose the full command set directly through Discord `/` autocomplete. The public command surface should be collapsed to two root commands:

- `/команды` for regular user actions;
- `/админ` for administrator actions.

All other actions should be reachable through sections, buttons, select menus, and modals. This is a UX requirement, not just a cosmetic change. It reduces visible command noise and makes the bot feel like an application with sections.

Already started in code:

- `main_file.py` collapses the synced app command tree to `/команды` and `/админ` after loading cogs.
- `fun_slesh/menu.py` builds a live catalog from hidden commands.
- `fun_slesh/menu.py` has working section actions for economy, reputation, stats, birthdays, games, random/fun, parody, Steam, search/WWM, and reminders.

Acceptance criteria for this part:

- After bot restart and Discord command sync, normal users should see only `/команды` and `/админ` in slash autocomplete.
- `/команды` should show public sections and allow common actions through buttons/forms.
- `/админ` should show only admin/maintenance catalog entries to members with administrator permission.
- Old command business logic should remain reusable internally. Prefer calling existing cog methods from menu actions.
- Admin and maintenance commands that are too dangerous or too complex for Discord UI should later move into the web/admin panel from this TZ.

Known gap:

- The first pass does not guarantee every hidden command has a finished button/modal flow. Continue converting the remaining catalog entries section by section and test each action with a real Discord bot token.
