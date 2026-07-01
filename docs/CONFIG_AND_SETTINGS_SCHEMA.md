# Config and Settings Schema

This file is the handoff map for the next Codex pass before building the site/app.

## Environment

Use `KGTD.env.example` as the source of truth for local/server variables. Real secrets stay only in `KGTD.env` on the target machine.

Required for the Discord bot:
- `tok` or `DISCORD_BOT_TOKEN`
- `DATABASE_DIR`
- `MODELS_DIR`

Required for Steam features:
- `STEAM_API_KEY`

Required for the future site/app:
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_REDIRECT_URI`
- `SESSION_SECRET`
- `JWT_SECRET`
- `BOT_API_TOKEN`

Optional heavy model bridge:
- `REMOTE_MODEL_API_URL`
- `REMOTE_MODEL_API_TOKEN`
- `REMOTE_MODEL_API_HOST`
- `REMOTE_MODEL_API_PORT`

Optional built-in admin panel:
- `WEB_ADMIN_ENABLED`
- `WEB_ADMIN_HOST`
- `WEB_ADMIN_PORT`
- `WEB_ADMIN_PUBLIC_URL`
- `WEB_ADMIN_TOKEN`
- `WEB_ADMIN_ALLOWED_IPS`
- `WEB_ADMIN_TITLE`

## Runtime Paths

New shared path helper:
- `core.paths.PROJECT_ROOT`
- `core.paths.DATABASE_DIR`
- `core.paths.MODELS_DIR`
- `core.paths.db_path(filename)`

The current codebase still has many local `DB_PATH = .../datebase/*.db` declarations. New code should use `core.paths`. Existing modules can be migrated gradually.

Current database files:
- `social.db`: economy, reputation, activity, WWM guild, Steam, channel settings.
- `messages.db`: collected Discord messages for parody training.
- `birthdays.db`: birthday dates and birthday output channel config.
- `reminders.db`: reminders and temporary role/reminder-style state.
- `persona.db`: generated persona/profile data.
- `parody_filters.db`: parody stop-lists.
- `parody_ratings.db`: parody phrase ratings.
- `wwm.db`: WWM knowledge base/search data.

## Unified Settings Store

New shared module:
- `core.settings_store`

Tables in `social.db`:

### `feature_settings`

One row per guild and feature.

Columns:
- `guild_id INTEGER`
- `feature TEXT`
- `enabled INTEGER`
- `payload TEXT`
- `updated_at TEXT`

Use it for JSON-like feature settings:

```python
from core.settings_store import get_feature_payload, set_feature_payload

settings = get_feature_payload(guild_id, "daily_summary", {"hour": 9})
set_feature_payload(guild_id, "daily_summary", {"hour": 10})
```

### `feature_channels`

One row per guild, feature, channel and channel mode.

Columns:
- `guild_id INTEGER`
- `feature TEXT`
- `channel_id INTEGER`
- `mode TEXT`
- `reason TEXT`
- `updated_at TEXT`

Supported `mode` values:
- `output`: where the feature posts.
- `allow`: only these channels are allowed.
- `exclude`: these channels are excluded.

Use it like:

```python
from core.settings_store import (
    set_feature_channel,
    clear_feature_channel,
    is_channel_allowed,
    get_feature_policy,
)

set_feature_channel(guild_id, "toxicity", channel_id, "exclude", "rules channel")
is_channel_allowed(guild_id, "toxicity", channel_id)
policy = get_feature_policy(guild_id, "toxicity")
```

## Suggested Feature Keys

Use stable snake_case names:
- `birthday`
- `wwm_guild`
- `wwm_kb`
- `steam`
- `daily_summary`
- `activity_rewards`
- `message_stats`
- `voice_roles`
- `toxicity`
- `social_chat`
- `parody_training`
- `heroes_troll`
- `sixty_seven`
- `reminders`
- `economy`
- `reputation`
- `web_chat`

## Web/App Contract

The future backend should not edit random module-specific tables directly unless there is no unified setting yet.

Preferred flow:
1. Discord OAuth identifies the user.
2. Backend checks admin permissions through Discord guild/member data or a cached bot API endpoint.
3. Web panel reads/writes `core.settings_store` through backend endpoints.
4. Bot modules gradually migrate from old local config tables to `settings_store`.

Minimum backend endpoints for the first web panel:
- `GET /api/guilds`
- `GET /api/guilds/:guildId/settings`
- `PATCH /api/guilds/:guildId/features/:feature`
- `PUT /api/guilds/:guildId/features/:feature/channels/:mode/:channelId`
- `DELETE /api/guilds/:guildId/features/:feature/channels/:mode/:channelId`
- `GET /api/me`
- `GET /api/chat`
- `POST /api/chat`

Protect bot-facing endpoints with `BOT_API_TOKEN`.

## Next Gen Migration Rule

This project is moving to a next-gen architecture. When a feature is actively rewritten, do not keep old logic just for compatibility.

Rule:
1. Move the feature to the new shared layer immediately.
2. Use `core.paths` for paths.
3. Use `core.settings_store` for feature/channel settings.
4. Use explicit schema/init functions for database tables.
5. Keep old tables only as one-time import sources if real production data must be migrated.
6. After migration, new code should not write to old feature-specific config tables.

The goal is not to preserve every old behavior. The goal is to make the bot + site/app consistent, maintainable and ready for the new platform.
