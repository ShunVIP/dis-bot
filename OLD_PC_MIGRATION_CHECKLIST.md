# OLD_PC_MIGRATION_CHECKLIST

Чеклист для возврата на основной ПК. Цель - собрать все, чего нет в GitHub, и передать следующему Codex понятный контекст.

## 1. Git и код

- [ ] Проверить, где лежит рабочий репозиторий.
- [ ] Выполнить `git status --short --branch`.
- [ ] Выполнить `git remote -v`.
- [ ] Выполнить `git log --oneline -5`.
- [ ] Проверить, есть ли незакоммиченные изменения.
- [ ] Если есть незакоммиченные изменения, не терять их и не делать reset.

## 2. Секреты

Найти, но не публиковать в чат:

- [ ] `KGTD.env`
- [ ] Discord Bot Token (`tok`)
- [ ] `STEAM_API_KEY`
- [ ] `WEB_ADMIN_TOKEN`
- [ ] `REMOTE_MODEL_API_TOKEN`
- [ ] GitHub secrets для deploy, если доступны
- [ ] SSH private key `%USERPROFILE%\.ssh\disbot_vps_ed25519`

Важно: если `REMOTE_MODEL_API_TOKEN` или bridge token был `secretkeyvipik`, заменить на новый случайный токен.

## 3. Локальные runtime-файлы

Проверить наличие:

- [ ] `datebase/`
- [ ] `datebase/messages.db`
- [ ] `datebase/social.db`
- [ ] `datebase/birthdays.db`
- [ ] `datebase/reminders.db`
- [ ] `datebase/persona.db`
- [ ] `datebase/parody_filters.db`
- [ ] `datebase/parody_ratings.db`
- [ ] `datebase/wwm.db`
- [ ] `models/`
- [ ] `models/gpt/`
- [ ] `.control_center.local.json`
- [ ] `.model_bridge.runtime.json`
- [ ] `dist/ViPikBotControl.exe`

## 4. VPS

Проверить SSH:

```powershell
ssh -i "$env:USERPROFILE\.ssh\disbot_vps_ed25519" root@206.245.134.221 "hostname && systemctl is-active vipik-discord-bot"
```

Проверить на VPS:

- [ ] `/opt/dis-bot/KGTD.env`
- [ ] `/opt/dis-bot/datebase/messages.db`
- [ ] `/opt/dis-bot/datebase/`
- [ ] `/opt/dis-bot/models/`
- [ ] `systemctl status vipik-discord-bot`
- [ ] `journalctl -u vipik-discord-bot --no-pager -n 100`

Не выводить реальные токены в чат.

## 5. Tailscale и bridge

- [ ] Проверить, установлен ли Tailscale.
- [ ] Проверить Tailscale IP основного ПК.
- [ ] Проверить, запускался ли bridge на `0.0.0.0:8787`.
- [ ] Проверить, какой token использовался для bridge.
- [ ] Если token публичный/дефолтный, заменить.
- [ ] Проверить доступность `http://127.0.0.1:8787/health` при запущенном bridge.

## 6. Запуск локально

Только после проверки, что это не конфликтует с продовым ботом:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main_file.py
```

## 7. Перед продолжением разработки

- [ ] Синхронизировать `messages.db` с VPS.
- [ ] Проверить тестовый импорт модулей.
- [ ] Проверить, что `.gitignore` защищает секреты.
- [ ] Сделать коммит только кода/документации, без runtime data.
- [ ] Снова сделать GitHub repo приватным, если он временно публичный.

