# Next Gen Architecture Direction

The project is no longer trying to preserve every old implementation detail.

When we rewrite a feature, we move it to the new architecture directly.

## Core Rule

If a module is touched for a real rewrite, do not drag the old logic forward.

Use the old code only to understand behavior and migrate valuable data. The new implementation should stand on the new rails.

## New Rails

- `core.paths` for all runtime paths.
- `core.settings_store` for feature settings and channel policies.
- `core.web_app_store` for website users, sessions, chat and web/bot outbox.
- `core.game_profiles` for game accounts and model profiles.
- `web_app/` as the first website/app surface.
- `/команды` and `/админ` as the Discord entry points.
- Buttons, selects and modals instead of exposed command sprawl.

## What Not To Preserve

Do not preserve old logic just because it exists:

- module-local config tables when the feature can use `settings_store`;
- duplicated channel allow/exclude systems;
- hard-coded database paths;
- slash-command-first UX;
- scattered env names when `KGTD.env.example` has a new canonical key;
- old “Сиськи only” economy text where personalized currency is now required;
- old LoL/game-profile placeholders once the new `game_profiles` layer exists.

## Migration Style

Preferred flow for each feature:

1. Identify the user-facing behavior worth keeping.
2. Design the new table/settings shape.
3. Add a one-time importer only if old production data matters.
4. Replace the feature logic.
5. Delete or ignore old writes.
6. Expose it through Discord menu and web/app.
7. Verify with compile and, when possible, a local runtime smoke test.

## Product Direction

The target product is:

- Discord bot as one interface;
- website/app as another interface;
- shared database and settings layer;
- Discord OAuth identity;
- fallback chat outside Discord;
- game profiles and ML player-type analysis;
- admin control panel for channels, schedules, features and integrations.

This is a platform now, not just a pile of Discord slash commands.
