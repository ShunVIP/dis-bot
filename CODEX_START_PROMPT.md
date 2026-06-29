
Additional current work note, 2026-06-20:

- The Discord UX is being collapsed to two public slash commands only: `/команды` and `/админ`.
- Read `fun_slesh/menu.py` and `main_file.py` before changing command registration.
- Preserve `collapse_slash_commands_to_menu()` behavior unless the user explicitly asks to restore all slash commands.
- Continue converting hidden commands into section buttons/selects/modals inside `/команды` and `/админ`.
- Do not duplicate command business logic if an existing cog callback can be reused safely.

# CODEX_START_PROMPT

Скопируй этот промпт в Codex на основном ПК после переноса архива/репозитория.

```text
Ты работаешь с проектом ShunVIP/dis-bot / ViPik Discord Bot. Сначала ничего не меняй.

Прочитай в корне проекта:
- MAIN_PC_CODEX_FULL_HANDOFF.md
- CODEX_HANDOFF.md
- docs/TZ_ViPik_Discord_Platform_for_Codex.md
- OLD_PC_MIGRATION_CHECKLIST.md
- README.md
- AGENT_CONTEXT.md
- .gitignore
- KGTD.env.example
- config.py
- main_file.py
- core/runtime_policy.py
- .github/workflows/deploy.yml
- scripts/sync_messages_from_vps.ps1
- scripts/enable_remote_models.ps1
- scripts/disable_remote_models.ps1
- scripts/install_bot_on_vps.ps1
- scripts/deploy.sh

Задача:
1. Аккуратно понять текущее состояние git: branch, remote, dirty files, последние коммиты.
2. Понять, какие секреты/runtime-файлы есть локально, но не выводить их значения.
3. Проверить наличие KGTD.env, datebase/, models/, SSH-ключа к VPS, Tailscale/bridge state.
4. Прочитать ТЗ платформы и связать его с текущим dis-bot: сайт/приложение, Discord OAuth2, кабинет, админка, fallback-портал, fallback-чат, bot API.
5. Составить отчет: что есть, чего не хватает, что опасно трогать.
6. Предложить безопасный план восстановления/продолжения разработки по этапам из ТЗ.

Ограничения:
- Не печатай токены и пароли.
- Не коммить KGTD.env, datebase/, models/, SSH keys, .control_center.local.json, .model_bridge.runtime.json.
- Не запускай второй экземпляр Discord-бота с тем же токеном без моего подтверждения.
- Не меняй VPS, systemd, KGTD.env на VPS и GitHub secrets без отдельного подтверждения.
- Не делай git reset --hard и не откатывай чужие изменения.
- Помни, что главный messages.db находится на VPS.
- Тяжелое GPT-обучение должно быть локально на ПК, не на VPS.

Начни с аудита и дай короткий понятный вывод.
```

## Config/settings foundation note

- `KGTD.env.example` is now the bot + future web/app env template.
- New code should use `core.paths` for database/model paths.
- New feature configuration should use `core.settings_store`.
- Read `docs/CONFIG_AND_SETTINGS_SCHEMA.md` before starting the web panel.
- First migrated example: `fun_slesh/parody_channel_settings.py` uses `core.settings_store`.
- Read `docs/GAME_PROFILE_ML_ROADMAP.md` before implementing Riot/LoL profile linking and player-type ML.
- Web/app MVP exists in `web_app/`; read `docs/WEB_APP_IMPLEMENTATION.md` before changing it.
- This is now a next-gen migration, not old-logic preservation. Read `docs/NEXT_GEN_ARCHITECTURE.md` before rewriting any feature.
- Main PC upgrade checklist exists in `MAIN_PC_BIG_UPDATE_GUIDE.md`.
- Full next-gen handoff for the main PC exists in `MAIN_PC_CODEX_FULL_HANDOFF.md`.
