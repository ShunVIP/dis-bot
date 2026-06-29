# RESTORE_EMPTY_ASSETS

This archive now includes empty placeholders for missing private/runtime assets.

## Included as empty template/placeholders

- `KGTD.env` - blank env file with all known keys.
- `datebase/` - empty folder with README and `.gitkeep`.
- `models/` - empty folder with README and `.gitkeep`.
- `_private_placeholders/` - notes for SSH key and local runtime files.

## Not included because they are private or unavailable on this PC

- real Discord bot token
- real Steam API key
- real web admin token
- real remote model token
- real SQLite databases
- real trained models
- real SSH private key
- `.git` history

## What to do on the main PC

1. Use the real git clone if it exists.
2. Copy useful handoff files into that clone:
   - `CODEX_HANDOFF.md`
   - `OLD_PC_MIGRATION_CHECKLIST.md`
   - `CODEX_START_PROMPT.md`
   - `LOCAL_AUDIT_SUMMARY.md`
   - `RESTORE_EMPTY_ASSETS.md`
3. Fill `KGTD.env` from the old PC/VPS.
4. Restore `datebase/` from VPS or old PC.
5. Restore `models/` from old PC.
6. Restore SSH key to `%USERPROFILE%\.ssh\disbot_vps_ed25519`.
7. Do not commit private/runtime files.

