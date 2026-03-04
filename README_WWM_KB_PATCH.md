# WWM KB Patch (dis-bot 2.0 -> towards 3.0)

This patch adds a daily ETL pipeline to collect **Where Winds Meet** knowledge base data from multiple sources.
Current implementation includes **Fandom MediaWiki API** collector (safe/standard).

## What it adds

- `wwm_kb/` module:
  - `collectors/` source adapters
  - `pipeline.py` (collect -> store RAW -> postprocess -> emit event)
  - `db.py` (SQLite schema for RAW + source_state)
- `scheduled/daily_kb_task.py`:
  - APScheduler job: every day at **00:00 Europe/Berlin**

## How to wire it in

In your bot startup (e.g., `main_file.py` inside `setup_hook()`):

```py
from scheduled.daily_kb_task import setup_daily_kb_refresh

setup_daily_kb_refresh()
```

## Event emitted

After refresh completes:

- `wwm_kb_refresh_completed` with payload:
  - `run_id`
  - `inserted`
  - `errors`
  - `postprocess_status`

Subscribers can log, notify Discord, or start ML.

## Notes

- DB path defaults to `./wwm.db`. Override with env `WWM_DB_PATH`.
- Requires dependencies: `aiohttp` (add to requirements).
