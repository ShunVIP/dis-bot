# MAIN PC CODEX FULL HANDOFF

Дата handoff: 2026-06-22.

Этот файл нужен, чтобы на основном ПК открыть проект, дать Codex один понятный документ и продолжить большую обнову без пересбора контекста с нуля.

## 1. Короткая цель проекта

ViPik становится не просто Discord-ботом, а приватной платформой для малого круга:

- Discord-бот остается входом через `/команды` и `/админ`.
- Сайт/app становится вторым интерфейсом: логин через Discord, резервный вход по email/паролю, чат, ЛС, комнаты, участники, роли, косметика, игры и настройки.
- Discord используется как первичная личность и совместимость, но платформа должна продолжать жить, если Discord недоступен.
- Голос должен быть близок по удобству к привычному серверному голосу: комнаты, mute/unmute, deafen, leave, invite link. Для реального многопользовательского голоса нужен LiveKit или другой WebRTC/SFU-сервер.
- Игровой слой начинается с League of Legends: Riot ID, профиль, статистика, первая модель типа игрока. Потом расширять на другие игры.

Важно: это оригинальная приватная реализация. Не копировать исходный код, бренд, лого, ассеты или точный proprietary UI Discord. Можно сохранять удобные паттерны: серверы, комнаты, ЛС, роли, карточки, голосовые контролы.

## 2. Что уже реализовано в этом архиве

Бот:

- Основной Discord UX сведен к `/команды` и `/админ`.
- Скрытые команды переезжают в кнопки, select-меню и modal-формы.
- Добавлен раздел игр, сейчас основной рабочий модуль: League of Legends.
- Экономика учитывает 18+ и пол: для мужчин валюта `Пенис`, для девушек `Сиськи`; репутация называется `Размер`.
- Настройки каналов функций должны идти через `core.settings_store`.

Сайт/app:

- `web_app/` на aiohttp.
- Discord OAuth: `/auth/discord`, `/auth/discord/callback`, `/auth/logout`.
- Резервный вход после первого Discord-входа: `/auth/local`, `/api/me/login-profile`.
- Серверный экран: текстовые каналы, ЛС, сообщения, активность, участники.
- Live-обновления сообщений через SSE для fallback-чата и выбранного канала/ЛС.
- Сообщения можно редактировать, удалять, отмечать реакциями; ссылки кликабельны, прямые ссылки на картинки показывают превью.
- Есть локальная загрузка вложений в `datebase/uploads`: картинки, видео, аудио и обычные файлы прикрепляются к сообщениям.
- Есть поиск по fallback-чату и выбранному каналу/ЛС.
- Экран участников: локальные профили, роли, статусы, бейджи, косметика.
- Экран голоса: комнаты, invite link, mute/unmute, deafen, leave. Без LiveKit работает как локальная оболочка/демо с доступом к микрофону.
- Экран игр: Riot ID link, LoL refresh, unlink, просмотр сохраненного профиля и модели.
- Настройки платформы: профиль сервера, баннер, иконка/инициалы, описание, каталог ролей, настройки каналов функций.
- Первый вошедший пользователь автоматически становится `owner`; настройки платформы, роли, создание каналов и feature-channel настройки требуют `owner` или `admin`.
- Добавлена PWA-упаковка: `manifest.json`, `service-worker.js`, `icon.svg`; сайт можно ставить как приложение из браузера.
- Добавлена PWA install-кнопка, когда браузер разрешает установку.

Локальная программа:

- `run_control_app.py` запускает GUI-панель.
- GUI умеет запускать сайт/app, открывать сайт, запускать бота, ставить зависимости и выполнять старые служебные действия.

## 3. Файлы, которые надо прочитать первыми

На основном ПК Codex должен начать с чтения:

```text
MAIN_PC_CODEX_FULL_HANDOFF.md
MAIN_PC_BIG_UPDATE_GUIDE.md
CODEX_START_PROMPT.md
docs/NEXT_GEN_ARCHITECTURE.md
docs/WEB_APP_IMPLEMENTATION.md
docs/CONFIG_AND_SETTINGS_SCHEMA.md
docs/GAME_PROFILE_ML_ROADMAP.md
README.md
KGTD.env.example
config.py
main_file.py
fun_slesh/menu.py
web_app/server.py
web_app/static/index.html
web_app/static/app.js
web_app/static/styles.css
core/settings_store.py
core/web_app_store.py
core/platform_store.py
core/community_store.py
core/voice_store.py
core/game_profiles.py
core/riot_client.py
core/lol_player_model.py
```

## 4. Приватные файлы, которых может не быть в архиве

Их нельзя коммитить и нельзя печатать значения в чат:

```text
KGTD.env
datebase/
models/
SSH-ключи к VPS
.control_center.local.json
.model_bridge.runtime.json
локальные .env файлы
GitHub secrets
VPS systemd/env файлы
```

Если этих файлов нет в архиве, это нормально. На основном ПК Codex должен найти их в старой рабочей папке или backup и перенести аккуратно.

## 5. Обязательные env-ключи

Сравнить реальный `KGTD.env` с `KGTD.env.example`.

Минимум для бота:

```text
tok=
DISCORD_BOT_TOKEN=
DATABASE_DIR=./datebase
MODELS_DIR=./models
```

Для сайта/app:

```text
APP_BASE_URL=http://127.0.0.1:3000
APP_API_HOST=127.0.0.1
APP_API_PORT=3000
SESSION_SECRET=
JWT_SECRET=
BOT_API_TOKEN=
UPLOADS_DIR=./datebase/uploads
UPLOAD_MAX_MB=25
```

Для Discord OAuth:

```text
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=http://127.0.0.1:3000/auth/discord/callback
DISCORD_OAUTH_SCOPES=identify email guilds guilds.members.read connections
```

Для голоса:

```text
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

Если LiveKit пустой, сайт не даст настоящий групповой голос, только UI/локальный микрофон. Для настоящего голоса поднять LiveKit на ПК/VPS/отдельном сервере и вписать ключи.

Для игр:

```text
STEAM_API_KEY=
RIOT_API_KEY=
RIOT_PLATFORM_REGION=ru
RIOT_REGIONAL_ROUTING=europe
```

Для VPS/моста:

```text
VPS_HOST=
VPS_USER=
VPS_APP_DIR=/opt/dis-bot
REMOTE_MODEL_API_TOKEN=
WEB_ADMIN_TOKEN=
```

## 6. Как накатывать на основном ПК

Сначала backup:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы
Copy-Item -Recurse -Force .\bot .\bot_BACKUP_before_nextgen_update
```

Если текущая папка не git-репозиторий, лучше рядом заново клонировать:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot
git clone https://github.com/ShunVIP/dis-bot.git dis-bot-main-new
```

Потом перенести приватное:

```powershell
Copy-Item -Force ..\bot_BACKUP_before_nextgen_update\dis-bot-main\KGTD.env .\KGTD.env
Copy-Item -Recurse -Force ..\bot_BACKUP_before_nextgen_update\dis-bot-main\datebase .\datebase
Copy-Item -Recurse -Force ..\bot_BACKUP_before_nextgen_update\dis-bot-main\models .\models
```

Установить зависимости:

```powershell
python -m pip install -r requirements.txt
```

Если `python` не найден:

```powershell
py -m pip install -r requirements.txt
```

## 7. Проверки после переноса

Python syntax:

```powershell
python -m py_compile config.py main_file.py run_web_app.py run_control_app.py
python -m py_compile core\paths.py core\settings_store.py core\web_app_store.py core\community_store.py core\platform_store.py core\voice_store.py
python -m py_compile core\game_profiles.py core\riot_client.py core\lol_player_model.py
python -m py_compile fun_slesh\menu.py fun_slesh\lol_profile.py fun_slesh\web_bridge.py fun_slesh\games.py
```

JS syntax, если есть Node:

```powershell
node --check web_app\static\app.js
```

Запуск сайта:

```powershell
python run_web_app.py
```

Открыть:

```text
http://127.0.0.1:3000
```

Health:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:3000/health -UseBasicParsing
```

Запуск локальной программы:

```powershell
python run_control_app.py
```

Запуск бота:

```powershell
python main_file.py
```

Не запускать второй экземпляр бота с тем же токеном, если уже работает прод на VPS.

## 8. Что проверить руками

Discord:

- `/команды` открывает меню.
- `/админ` открывает админское меню.
- Лишние старые slash-команды не торчат пользователям.
- Раздел игр содержит League of Legends.
- LoL-профиль работает через Riot ID при наличии `RIOT_API_KEY`.

Сайт/app:

- Вход через Discord.
- После входа можно указать email/пароль для резервного входа.
- Резервный вход работает через `/auth/local`.
- Экран `Сервер`: каналы, ЛС, сообщения.
- Новые сообщения в fallback-чате и выбранном канале/ЛС появляются без ручного обновления страницы.
- Проверить реакции, редактирование, удаление и link/image previews в fallback-чате и серверном чате.
- Проверить загрузку файлов в fallback-чате и серверном чате.
- Экран `Участники`: косметика, статусы, роли.
- Экран `Голос`: создание комнаты, вход, mute/unmute, deafen, leave, копирование ссылки.
- Экран `Игры`: Riot ID link, refresh, unlink.
- Экран `Настройки платформы`: профиль сервера, баннер, иконка, описание, роли, каналы функций.

## 9. Что еще не закончено

Голос:

- Сейчас backend выдает LiveKit token, но frontend еще не подключает LiveKit JS SDK как полноценный WebRTC-клиент.
- Нужно добавить зависимость/скрипт LiveKit client, подключение к комнате, список участников, mute/deafen на реальные треки.

Права:

- Backend уже имеет локальную owner/admin-защиту для опасных действий.
- Перед VPS/public-доступом нужен строгий guild/admin check через реальный Discord guild и роли.

Discord bridge:

- Нужно глубже импортировать Discord-роли, активности и каналы в локальную платформу.
- Нужно решить, какие каналы зеркалить в web/app, а какие исключать.

Медиа:

- Есть link/image previews для вставленных URL.
- Есть локальная загрузка файлов и хранение вложений в `UPLOADS_DIR`, по умолчанию `datebase/uploads`.
- `UPLOAD_MAX_MB` задает лимит размера upload-запроса.
- Перед VPS/public-доступом нужны квоты, модерация и политика очистки старых вложений.

Desktop app:

- Есть Python GUI-панель и PWA-упаковка сайта/app.
- Нет полноценного Electron/Tauri-десктопа.
- Следующий шаг: если PWA будет мало, завернуть сайт/app в Tauri/Electron desktop-shell.

VPS:

- Для продового сайта нужны systemd/nginx/reverse proxy/TLS.
- Для голоса нужен LiveKit или другой SFU-сервер.

## 10. Следующий правильный проход

1. На основном ПК восстановить секреты, базы, модели.
2. Прогнать проверки из раздела 7.
3. Поднять сайт локально и проверить OAuth.
4. Поднять бота без конфликта с VPS.
5. Добавить admin-check в web API.
6. Доделать реальный LiveKit frontend.
7. Сделать импорт ролей/активностей Discord в `community_store` и `platform_store`.
8. Если PWA недостаточно, начать Tauri/Electron desktop-shell.

Главное правило: если фича переделывается, переносить ее сразу на next-gen слой, а не тянуть старую логику ради сохранения формы.
