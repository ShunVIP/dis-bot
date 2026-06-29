# Main PC Big Update Guide

Инструкция для основного ПК: как накатить большую обнову бота + сайта/app из git-версии и не потерять секреты, базы и модели.

Если нужно передать весь контекст Codex на основном ПК одним файлом, сначала открывай `MAIN_PC_CODEX_FULL_HANDOFF.md`.

## 0. Что важно не потерять

Перед любыми действиями сохрани отдельно:

- `KGTD.env`
- `datebase/`
- `models/`
- SSH-ключи к VPS
- `.control_center.local.json`, если есть
- `.model_bridge.runtime.json`, если есть
- любые локальные `.env`, которых нет в git

Эти файлы не должны попадать в git.

## 1. Сделать резервную копию старой папки

На основном ПК:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы
Copy-Item -Recurse -Force .\bot .\bot_BACKUP_before_big_update
```

Если проект лежит в другом месте, поменяй путь.

## 2. Проверить git

В старой папке:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot\dis-bot-main
git status
git remote -v
git branch
```

Если это не git-репозиторий, а ZIP, лучше рядом заново клонировать:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot
git clone https://github.com/ShunVIP/dis-bot.git dis-bot-main-new
```

Потом переносить секреты и базы уже в `dis-bot-main-new`.

## 3. Забрать свежую версию из git

Если папка уже git:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot\dis-bot-main
git fetch --all
git pull
```

Если есть локальные изменения, сначала:

```powershell
git status
```

Не делай `git reset --hard`, пока не понял, что именно будет потеряно.

## 4. Перенести приватные файлы

Из backup или старой рабочей папки перенеси в новую git-версию:

```powershell
Copy-Item -Force ..\bot_BACKUP_before_big_update\dis-bot-main\KGTD.env .\KGTD.env
Copy-Item -Recurse -Force ..\bot_BACKUP_before_big_update\dis-bot-main\datebase .\datebase
Copy-Item -Recurse -Force ..\bot_BACKUP_before_big_update\dis-bot-main\models .\models
```

Если путь другой, поправь вручную.

## 5. Обновить `KGTD.env`

Сравни свой `KGTD.env` с новым:

```text
KGTD.env.example
```

Минимально проверь, что есть:

```text
tok=
DISCORD_BOT_TOKEN=
STEAM_API_KEY=
RIOT_API_KEY=
DATABASE_DIR=./datebase
MODELS_DIR=./models
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=http://127.0.0.1:3000/auth/discord/callback
DISCORD_OAUTH_SCOPES=identify email guilds guilds.members.read connections
SESSION_SECRET=
JWT_SECRET=
BOT_API_TOKEN=
APP_API_HOST=127.0.0.1
APP_API_PORT=3000
WEB_ADMIN_TOKEN=
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

`tok` и `DISCORD_BOT_TOKEN` могут быть одинаковыми. Старый бот использует `tok`, новая структура также понимает `DISCORD_BOT_TOKEN`.

## 6. Discord Developer Portal

В Discord Developer Portal для приложения добавь redirect URI:

```text
http://127.0.0.1:3000/auth/discord/callback
```

Для сайта нужны scopes:

```text
identify guilds guilds.members.read connections
```

`connections` нужен, чтобы видеть привязанные Riot/LoL аккаунты пользователя после входа через Discord.

## 7. Установить зависимости

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot\dis-bot-main
python -m pip install -r requirements.txt
```

Если Python не найден:

```powershell
py -m pip install -r requirements.txt
```

## 8. Проверить компиляцию

```powershell
python -m py_compile config.py main_file.py run_web_app.py run_control_app.py
python -m py_compile core\paths.py core\settings_store.py core\web_app_store.py core\game_profiles.py core\riot_client.py core\lol_player_model.py
python -m py_compile fun_slesh\lol_profile.py fun_slesh\web_bridge.py fun_slesh\menu.py
```

Если всё молчит, синтаксис нормальный.

## 9. Запустить сайт/app

Отдельное окно PowerShell:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot\dis-bot-main
python run_web_app.py
```

Открыть:

```text
http://127.0.0.1:3000
```

Проверка:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:3000/health -UseBasicParsing
```

## 10. Запустить локальную программу-панель

Отдельное окно PowerShell:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot\dis-bot-main
python run_control_app.py
```

В программе есть кнопки:

- `Запустить сайт/app`
- `Открыть сайт/app`
- `Запустить бота локально`
- `Установить зависимости`
- старые действия для VPS/GPT/синхронизации

## 11. Запустить бота

Во втором окне PowerShell:

```powershell
cd C:\Users\<YOU>\OneDrive\Документы\bot\dis-bot-main
python main_file.py
```

Проверить в Discord:

- `/команды`
- `/админ`
- раздел `Игры -> League of Legends`
- `lol привязать`
- `lol обновить`
- `lol профиль`

## 12. Проверить web-chat связку

В сайте:

1. Войти через Discord.
2. Открыть `Чат`.
3. Написать сообщение без `Guild ID/Channel ID`: оно сохранится только в web-chat.
4. Написать сообщение с `Guild ID` и `Channel ID`: бот отправит его в Discord, если бот запущен.

Чтобы Discord-канал зеркалился обратно в web-chat:

1. Открыть на сайте `Настройки бота`.
2. `feature`: `web_chat`
3. `mode`: `output` или `allow`
4. `Channel ID`: нужный Discord-канал.

## 13. Проверить LoL/Riot

В `KGTD.env` нужен:

```text
RIOT_API_KEY=
RIOT_PLATFORM_REGION=ru
RIOT_REGIONAL_ROUTING=europe
```

В Discord:

```text
lol привязать RiotName#TAG
lol обновить
lol профиль
```

На сайте раздел `Игры` позволяет привязать Riot ID, обновить LoL статистику, отвязать профиль и посмотреть сохранённую модель игрока.

## 14. Если переносишь на VPS

На VPS обязательно сохранить:

- `/opt/dis-bot/KGTD.env`
- `/opt/dis-bot/datebase/`
- `/opt/dis-bot/models/`

После обновления:

```bash
cd /opt/dis-bot
python -m pip install -r requirements.txt
python -m py_compile config.py main_file.py run_web_app.py run_control_app.py
```

Потом перезапускать systemd только если уверен, что env и базы на месте.

## 15. Что появилось в этой большой обнове

- Меню `/команды` и `/админ` как главные входы.
- Разделы и кнопки вместо россыпи slash-команд.
- WWM приветствие, ник, Steam, карточка.
- Настраиваемые каналы/исключения для важных функций.
- Персональная валюта 18+: мужчины `Пенис`, девушки `Сиськи`.
- Репутация как `Размер`.
- LoL/Riot профиль и первая модель типа игрока.
- Единый слой настроек `core.settings_store`.
- Сайт/app `web_app/`.
- Локальная программа-панель `run_control_app.py`.
- Discord OAuth.
- Резервный вход в сайт/app по email и паролю после первого Discord-входа.
- Локальные роли, карточки участников, статусы, бейджи и косметика профиля.
- Экран `Сервер`: текстовые комнаты, ЛС, сообщения, активность и список участников.
- Web fallback-chat.
- Нативный fallback-голос: раздел `Голос`, комнаты, mute/unmute, deafen, disconnect, invite-ссылки. Для реального голоса нужен LiveKit server.
- Мост web-chat ↔ Discord bot.

## 16. Документы, которые читать следующему Codex

- `CODEX_START_PROMPT.md`
- `MAIN_PC_CODEX_FULL_HANDOFF.md`
- `docs/CONFIG_AND_SETTINGS_SCHEMA.md`
- `docs/NEXT_GEN_ARCHITECTURE.md`
- `docs/GAME_PROFILE_ML_ROADMAP.md`
- `docs/WEB_APP_IMPLEMENTATION.md`
- `MAIN_PC_BIG_UPDATE_GUIDE.md`
