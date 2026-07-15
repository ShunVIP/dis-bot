# Web/App Implementation

The local web/app MVP lives in `web_app/` and is tied to the bot through the same SQLite databases.

## What is implemented

- Discord OAuth login:
  - `/auth/discord`
  - `/auth/discord/callback`
  - `/auth/logout`
- Local fallback login after first Discord verification:
  - `/auth/local`
  - `/api/me/login-profile`
- Web API:
  - `/api/me`
  - `/api/settings`
  - `/api/community/me`
  - `/api/community/members`
  - `/api/community/roles`
  - `/api/platform/bootstrap`
  - `/api/platform/server`
  - `/api/platform/messages`
  - `/api/platform/messages/stream`
  - `/api/platform/channels`
  - `/api/platform/dms`
  - `/api/platform/audit` (admin only)
  - `/api/guilds/{guild_id}/features/{feature}`
  - `/api/chat`
  - `/api/chat/stream`
  - `/api/uploads`
  - `/api/lol/profile`
  - `/api/lol/link`
  - `/api/lol/refresh`
  - `/api/lol/unlink`
  - `/api/voice/rooms`
  - `/api/voice/token`
  - `/api/bot/chat`
- UI:
  - overview with backup email/password setup
  - server screen with text channels, DMs, activity sidebar and member list
  - members screen with local roles, badges, statuses and cosmetics
  - compatibility chat view backed by the same `general` platform channel
  - League of Legends game section with Riot ID linking, refresh, unlink and stored profile view
  - Native Discord-like voice shell with rooms, join by link, mute, deafen and disconnect controls
  - settings editor for platform profile, banner preset, role catalog and feature channel policies
  - friendly `social_chat` consent/chance editor; an empty guild field resolves to the configured allowed guild instead of writing settings for guild `0`
  - SSE live updates for the shared `general` channel and the selected platform channel/DM
  - message reactions, edit/delete, clickable links and image/link previews
  - local file uploads and attachment rendering for images, video, audio and generic files
  - authenticated upload downloads, owned-upload URL validation and persistent per-user anti-spam limits
  - chat search for the shared `general` channel and selected platform channel/DM
  - PWA install button when the browser exposes the install prompt
  - PWA shell with manifest, service worker and installable app metadata
- Bot bridge:
  - `fun_slesh/web_bridge.py` polls the canonical `platform_discord_outbox`
  - web clients cannot choose `guild_id` or `channel_id`; delivery uses only the enabled admin-owned `web_chat.output_channel` policy
  - Discord messages from configured `web_chat` channels are stored in the platform `general` channel

## Files

- `run_web_app.py`: local app entry point.
- `web_app/server.py`: aiohttp backend.
- `web_app/static/index.html`: frontend shell.
- `web_app/static/styles.css`: app styles.
- `web_app/static/app.js`: frontend behavior.
- `web_app/static/manifest.json`: PWA metadata.
- `web_app/static/service-worker.js`: app-shell cache for installed/PWA mode.
- `web_app/static/icon.svg`: local app icon.
- `core.paths.UPLOADS_DIR`: local upload storage, defaults to `datebase/uploads`.
- `core/web_app_store.py`: sessions, web users, chat, outbox.
- `core/community_store.py`: local roles, member cards, badges, statuses and profile cosmetics.
- `core/platform_store.py`: local servers, text channels, DM threads, platform messages, rate events, moderation audit and game activity.
- `core/conversation_service.py`: local Ollama routing, persisted runtime health and exponential circuit breaker.
- `core/moderation_service.py`: toxicity review projection and audited human feedback.
- `fun_slesh/web_bridge.py`: Discord bot bridge for web chat outbox.

## Conversational model status

- `GET /api/ml/conversation-status` is admin-only and reports whether the private Ollama endpoint is configured, online, or in cooldown.
- Bot and web app share `conversation_runtime_status`, so failures remain visible across process restarts.
- A failed request opens a 15–300 second circuit breaker; Discord uses its immediate fallback while the endpoint is cooling down.
- Only self-approved, training-opted-in turns whose provider is exactly `ollama` enter QLoRA. Markov, meme and template responses are excluded by both dataset and readiness SQL.

## Required env

In `KGTD.env`:

```text
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=http://127.0.0.1:3000/auth/discord/callback
DISCORD_OAUTH_SCOPES=identify email guilds guilds.members.read connections
SESSION_SECRET=
BOT_API_TOKEN=
APP_API_HOST=127.0.0.1
APP_API_PORT=3000
UPLOADS_DIR=./datebase/uploads
UPLOAD_MAX_MB=25
```

For Riot/LoL:

```text
RIOT_API_KEY=
RIOT_PLATFORM_REGION=ru
RIOT_REGIONAL_ROUTING=europe
```

For native fallback voice:

```text
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

Empty LiveKit values keep the voice page in local demo mode. After deploying LiveKit, the backend issues room-scoped tokens and the frontend can connect the same Discord-like UI to the real voice server.

## Fallback Login Model

The first identity proof is Discord OAuth. During OAuth the app stores Discord ID, username, avatar, connections, and email when the `email` scope is granted.

After that, the user can save a local email, backup nickname and password in the `Резервный вход` block. If Discord is down or blocked, `/auth/local` creates the same local session from email + password.

This is intentionally not a separate public registration flow yet. Users should be known by Discord first, then receive fallback access.

## Admin Model

Discord OAuth is admitted only when the user belongs to a guild listed in `APP_ALLOWED_GUILD_IDS`. A fresh database grants `owner` only to a user explicitly listed in `APP_OWNER_USER_IDS`; an arbitrary first login never becomes owner. Platform/server settings, role catalog changes, channel creation and feature-channel settings require `owner` or `admin` in `community_user_roles`.

The frontend hides admin-only settings for non-admins, but backend checks remain the source of truth.

## Community Layer

The platform now keeps local member cards and roles in addition to Discord identity:

- `community_profiles`: display name, status, bio, accent color, banner preset, avatar decoration and badges.
- `community_roles`: local role catalog.
- `community_user_roles`: member role assignments.

The settings screen can now update the local role catalog through `/api/community/roles`. Discord role import should later write into the same catalog instead of creating a second role system.

This allows the private group to keep a Discord-like social layer inside the app even if Discord is unavailable. Discord roles can be imported into this layer later through the bot bridge.

## Platform Screen

The app has a private server-style screen:

- text channel list;
- DM thread list;
- central message timeline;
- message composer;
- reactions, edit/delete and link previews;
- local attachments;
- right activity list;
- right member list.

This is an original implementation for private use. Do not copy Discord branding, assets, client source, or exact proprietary UI code.

The platform server profile is stored in `platform_servers` and can be edited from the settings screen:

- server name;
- description;
- icon/initials;
- banner preset.

## Run locally

```powershell
python run_web_app.py
```

If `python` is not in PATH on the temporary PC, use the Codex bundled Python path or configure Python normally on the main PC.

Open:

```text
http://127.0.0.1:3000
```

## Discord Developer Portal setup

Add redirect URI:

```text
http://127.0.0.1:3000/auth/discord/callback
```

Use scopes:

```text
identify guilds guilds.members.read connections
```

The `connections` scope is needed to see linked Riot/League accounts after the user logs in.

## Current limitations

- The site can read Discord connections only after user OAuth consent.
- The bot cannot silently read Riot/LoL connections for every server member.
- Admin permission checks are now local owner/admin checks; before public deployment, add strict guild/admin checks tied to the real Discord guild.
- The LoL update action exists in both Discord and the web UI. The website can now link Riot ID, refresh Riot API data, unlink the account and display the stored model profile.
- The chat works locally even if Discord is unavailable; Discord delivery happens only when the bot is online and a valid channel is provided.
- To mirror a Discord channel into web chat, set feature `web_chat` channel mode `output` or `allow` through the settings UI/API.
- PWA install mode is present. Full native desktop packaging with Tauri/Electron is still a separate pass.
- File upload storage is local and basic. `UPLOAD_MAX_MB` controls per-file request size. Before public/VPS use, add moderation rules, per-user quotas and cleanup policy.

## Next hardening pass

- Add real guild/admin authorization in backend.
- Add web form for Riot ID linking and refresh through backend.
- Extend SSE/live updates to member presence, typing indicators and room activity.
- Add bot-to-web ingest for selected Discord channels.
- Move more feature channel settings to `core.settings_store`.
- Add quotas, moderation and cleanup for uploaded attachments.
- Package desktop shell if PWA is not enough.
