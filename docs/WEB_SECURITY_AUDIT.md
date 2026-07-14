# Web application security audit

Date: 2026-07-14

Scope: `web_app/server.py`, `core/web_app_store.py`, profile/chat/DM/upload APIs and the planned VPS service.

## Deployment decision

The user web application must remain private and Tailscale-only. It is not approved for public Internet exposure yet. The lightweight admin panel on port 8080 and the user application on port 3000 are separate services and must stay separate.

## Fixed in code

- OAuth access and refresh tokens are no longer retained after the Discord identity snapshot is stored. Existing plaintext token values are scrubbed during schema initialization.
- Session cookies contain a random secret, while SQLite stores only its SHA-256 digest.
- Cookies are `HttpOnly`, `SameSite=Lax`, path-scoped, and automatically `Secure` behind HTTPS.
- Cross-origin state-changing requests are rejected. The bot ingest endpoint uses its independent bearer token instead of cookie authentication.
- Local login is limited to 10 attempts per IP per five minutes.
- New passwords require at least 10 characters and continue to use PBKDF2-HMAC-SHA256 with a per-user salt.
- Responses include CSP, frame denial, MIME sniffing protection, referrer policy and permissions policy.
- Uploads are limited by size and count; executable web formats such as HTML, SVG and JavaScript are rejected.
- Uploaded files are no longer public static routes: a valid ViPik session is required to read them, and new message attachments must reference a ViPik-owned upload path.
- The PWA service worker explicitly excludes `/uploads/`, `/api/` and `/auth/` from browser Cache Storage.
- Message posting, reactions, DM creation and upload requests use persistent per-user rate limits with `429` and `Retry-After` responses.
- The compatibility chat cannot submit arbitrary Discord guild/channel IDs. Discord outbox routing comes only from the administrator-owned `web_chat.output_channel` setting.
- DM read/post/edit/reaction access is checked at the HTTP boundary; admin status never grants access to another pair's DM.
- Editing or deleting another user's channel message as an admin creates a `platform_audit_log` event exposed through an admin-only endpoint.
- Profile and authorization tests cover user/admin separation, hashed sessions, token scrubbing and same-origin writes.

## Required before broader or public exposure

1. Use `/приложение` as the default passwordless Discord-verified login. Codes are hashed, single-use and expire after 10 minutes.
2. OAuth is optional. If enabled, configure a dedicated callback and set `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `DISCORD_REDIRECT_URI`.
3. Set a strong `BOT_API_TOKEN` before enabling direct HTTP ingestion from the bot. Shared-database chat/outbox operation does not require it.
4. Bind the service only to the VPS Tailscale address, never `0.0.0.0` or the public interface.
5. Verify login, logout, profile update, chat and DM using a non-admin account and an admin account. Automated HTTP tests cover the access boundary; a live two-account UI check remains required before public exposure.
6. Keep LiveKit/voice on its current private Tailscale boundary until its separate TLS, media-port and room-authorization audit is complete.

## Residual risks

- HTTP inside Tailscale is private but does not provide browser TLS guarantees. Tailscale HTTPS or a private TLS reverse proxy is preferred before long-term use.
- SQLite is appropriate for the current small private community, but long-running writes must continue using the shared connection helper and short transactions.
- Uploaded files require authentication and use unguessable names, but access is not yet bound to a specific channel/DM membership. Per-message file authorization is required for public deployment.
- Dependency and container/host patch management remain operational responsibilities of the VPS.
