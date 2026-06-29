# Private placeholders

This folder exists only in the handoff archive to show which private files are missing.

Do not commit real private files into git.

## Expected private files outside repo

- `%USERPROFILE%\.ssh\disbot_vps_ed25519`
- `%USERPROFILE%\.ssh\known_hosts`

## Expected private/runtime files inside local repo

- `KGTD.env`
- `datebase/*.db`
- `models/`
- `.control_center.local.json`
- `.model_bridge.runtime.json`

## Git history

This handoff archive was made from a GitHub ZIP, not from `git clone`, because `git` was not available on the temporary PC. The archive does not include `.git` history.

On the main PC, prefer using the real git repository/clone and copy these handoff files into it if needed.

