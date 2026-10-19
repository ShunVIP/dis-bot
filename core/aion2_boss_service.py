"""Read-only community field-boss observations for Daminu EU, not NC telemetry.

Feed protocol: taengu/A2Tools-DPS-Meter, src-tauri/src/plugins/mod.rs.
Names/locations: A2Tools-Plugins event-timers game-data catalogue (attributed in UI).
No meter installation, account, packet capture, or report uploads are involved.
"""
import asyncio
import copy
import json
import time
from datetime import datetime, timezone

import aiohttp

SERVER_ID = 1314
FEED_URL = 'https://cdn.a2tools.app/feeds/boss-timers/1314.json'
CATALOG_URL = 'https://raw.githubusercontent.com/taengu/A2Tools-Plugins/main/plugins/event-timers/schedule.js'
SOURCE_URL = 'https://a2tools.app/'
CACHE_SECONDS = 120
STALE_SECONDS = 600
_cache = None
_catalog = None
_catalog_at = 0
_retry_at = 0
_lock = asyncio.Lock()


async def _read(url, limit=1_000_000):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12),
                                    headers={'User-Agent':'ViPik-Discord-Bot/1.0','Accept':'application/json, text/plain'}) as session:
        async with session.get(url, allow_redirects=False) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.content.iter_chunked(32768):
                raw.extend(chunk)
                if len(raw) > limit:
                    raise ValueError('Response too large')
            return raw.decode('utf-8')


def parse_catalog(source):
    """Read literal JSON data only; never execute upstream JavaScript."""
    def constant(name):
        marker = 'const '+name+' ='
        pos = source.find(marker)
        if pos < 0:
            raise ValueError('Catalogue format changed')
        value = json.JSONDecoder().raw_decode(source[pos+len(marker):].lstrip())[0]
        if not isinstance(value, dict):
            raise ValueError('Invalid catalogue')
        return value
    bosses, translations = constant('FIELD_BOSSES'), constant('EVENT_TEXT')
    def translate(key):
        row = translations.get(key) or {}
        return str(row.get('ru') or row.get('en') or '')[:150]
    return {str(key): {'name':translate(row.get('name')), 'area':translate(row.get('where')),
                      'side':row.get('side'), 'level':row.get('level')}
            for key, row in bosses.items() if str(key).isdigit() and isinstance(row, dict)}


def epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 4_102_444_800:
        return int(value)
    return None


def normalize(payload, catalog, now=None):
    now = int(now if now is not None else time.time())
    if not isinstance(payload, dict) or payload.get('format') != 1 or payload.get('server') != SERVER_ID:
        raise ValueError('Unexpected boss feed/server')
    updated, bosses = epoch(payload.get('updated')), payload.get('bosses')
    if updated is None or updated > now+120 or not isinstance(bosses, dict) or len(bosses)>200:
        raise ValueError('Invalid boss feed')
    rows = []
    for key, value in bosses.items():
        if not str(key).isdigit() or not isinstance(value, dict):
            continue
        meta = catalog.get(str(key), {})
        rows.append({'id':str(key), **meta, 'name':meta.get('name') or f'Босс #{key}',
                     'state':value.get('state') if value.get('state') in {'up','dead','fighting'} else 'unknown',
                     'next_spawn':epoch(value.get('nextSpawn')), 'reported_at':epoch(value.get('reportedAt')),
                     'confirmed':value.get('confirmed') is True})
    return {'server_id':SERVER_ID,'updated':updated,'fetched_at':now,'rows':rows,
            'stale':now-updated>STALE_SECONDS,'error':None,'catalog_available':bool(catalog)}


def status(row, snapshot, now=None):
    """Elapsed countdowns and old sightings never turn into a claimed live spawn."""
    now = int(now if now is not None else time.time())
    reported = row.get('reported_at')
    old = snapshot['stale'] or not reported or reported > now+120
    if row['state'] in {'up','fighting'}:
        if old or now-reported>300 or not row['confirmed']:
            return 'unknown', '⚪ Был замечен; нужно свежее подтверждение'
        return 'up', '🟢 Замечен в мире' if row['state']=='up' else '⚔️ По наблюдению — в бою'
    spawn = row.get('next_spawn')
    if old:
        return 'unknown', '⚠️ Устаревшие данные — статус неизвестен'
    if spawn is None:
        return 'unknown', '⚪ Время появления неизвестно'
    if spawn <= now:
        return 'unknown', '⚪ Расчётное время прошло — ждём наблюдения'
    prefix = '🕒 Ожидается' if row['confirmed'] else '❔ Предварительная оценка'
    return 'waiting', f'{prefix} <t:{spawn}:R> · <t:{spawn}:t>'


def ordered_rows(snapshot, side='elyos', now=None):
    now = int(now if now is not None else time.time())
    rows = [r for r in snapshot['rows'] if side=='all' or r.get('side') in {side,None}]
    def order(row):
        state, _ = status(row, snapshot, now)
        return (0 if state=='up' else 1 if state=='waiting' else 2,
                row.get('next_spawn') or 0, row['id'])
    return sorted(rows, key=order)


async def get_snapshot():
    global _cache, _catalog, _catalog_at, _retry_at
    async with _lock:
        now = time.time()
        if _cache and now-_cache['fetched_at'] < CACHE_SECONDS:
            result = copy.deepcopy(_cache)
            result['stale'] = bool(result['error']) or now-result['updated']>STALE_SECONDS
            return result
        if now < _retry_at:
            if _cache:
                return {**copy.deepcopy(_cache), 'stale':True, 'error':'Источник временно недоступен'}
            raise RuntimeError('Источник таймеров боссов временно недоступен. Попробуй через пару минут.')
        try:
            if now-_catalog_at > (86400 if _catalog else 600):
                try:
                    _catalog = parse_catalog(await _read(CATALOG_URL, 300_000))
                    _catalog_at = now
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError):
                    _catalog_at = now
            payload = json.loads(await _read(FEED_URL))
            result = normalize(payload, _catalog or {}, time.time())
            _cache = result
            return copy.deepcopy(result)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError):
            _retry_at = time.time()+CACHE_SECONDS
            if _cache:
                return {**copy.deepcopy(_cache), 'stale':True, 'error':'Источник временно недоступен'}
            raise RuntimeError('Источник таймеров боссов временно недоступен. Попробуй через пару минут.') from None
