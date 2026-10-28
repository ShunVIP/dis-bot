"""Opt-in public A2Tools reports and explicitly manual meter summaries; no capture."""
import asyncio
import copy
import json
import re
import time
from collections import defaultdict
from urllib.parse import urlsplit,parse_qs

import aiohttp

from core.aion2_personal_service import number

_cache={}
_lock=asyncio.Lock()


def report_id(url):
    try:
        parsed=urlsplit(str(url).strip())
        match=re.fullmatch(r'/logs/([A-Za-z0-9_-]{16,64})/?',parsed.path)
        if (parsed.scheme!='https' or parsed.hostname!='a2tools.app' or parsed.username or parsed.password
                or parsed.port not in (None,443) or not match or set(parse_qs(parsed.query)) - {'lang'}):
            raise ValueError()
        return match[1]
    except ValueError:
        raise ValueError('Нужна публичная ссылка https://a2tools.app/logs/… без ключей доступа.') from None


def nonnegative(value):
    value=number(value)
    if value is None or value<0 or value>10**14:raise ValueError('Некорректное число в отчёте.')
    return value


def normalize_report(payload):
    if not isinstance(payload,dict):raise ValueError('Формат отчёта изменился.')
    record=payload.get('record')
    if not isinstance(record,dict):raise ValueError('Формат отчёта изменился.')
    duration=nonnegative(record.get('durationMs'))/1000
    if not 0<duration<=86400:raise ValueError('Некорректная длительность боя.')
    total=nonnegative(record.get('totalDamage'))
    skills=(record.get('details') or {}).get('skills')
    actors=record.get('actors')
    if not isinstance(skills,list) or not isinstance(actors,list) or len(skills)>5000 or len(actors)>100:
        raise ValueError('Отчёт слишком большой или неполный.')
    grouped=defaultdict(list)
    for row in skills:
        grouped[str(row.get('actorId'))].append({'name':str(row.get('name') or 'Навык')[:100],
            'damage':nonnegative(row.get('dmg',0))})
    result=[]
    seen=set()
    for actor in actors:
        key=str(actor.get('actorId'))
        if key in seen:continue
        seen.add(key)
        own=grouped.pop(key,[])
        damage=sum(s['damage'] for s in own)
        result.append({'name':str(actor.get('nickname') or f'Участник {len(result)+1}')[:80],
            'damage':damage,'dps':damage/duration,'skills':sorted(own,key=lambda s:s['damage'],reverse=True)[:8]})
    for own in grouped.values():
        damage=sum(s['damage'] for s in own)
        result.append({'name':'Участник без имени','damage':damage,'dps':damage/duration,
                       'skills':sorted(own,key=lambda s:s['damage'],reverse=True)[:8]})
    return {'boss':str(record.get('bossName') or 'Бой')[:150],'duration':duration,'damage':total,
            'dps':total/duration,'actors':sorted(result,key=lambda a:a['damage'],reverse=True),
            'partial':abs(sum(a['damage'] for a in result)-total)>max(1,total*.01)}


async def public_report(url):
    key=report_id(url)
    async with _lock:
        cached=_cache.get(key)
        if cached and time.monotonic()-cached[0]<300:return copy.deepcopy(cached[1])
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                    headers={'User-Agent':'ViPik-Discord-Bot/1.0','Accept':'application/json'}) as session:
                async with session.get(f'https://a2tools.app/logs/{key}/fight.json',allow_redirects=False) as response:
                    if response.status!=200:
                        raise RuntimeError('Отчёт недоступен или не публичный. Бот не входит в чужие аккаунты.')
                    raw=bytearray()
                    async for chunk in response.content.iter_chunked(32768):
                        raw.extend(chunk)
                        if len(raw)>3_000_000:raise RuntimeError('Отчёт превышает лимит 3 МБ.')
                    result=normalize_report(json.loads(raw))
        except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,TypeError,KeyError,AttributeError,RecursionError):
            raise RuntimeError('Не удалось прочитать публичный отчёт A2Tools. Проверь ссылку или попробуй позже.') from None
        result['url']=f'https://a2tools.app/logs/{key}'
        if len(_cache)>=16:del _cache[next(iter(_cache))]
        _cache[key]=(time.monotonic(),result)
        return copy.deepcopy(result)


def manual_report(damage,duration,boss='Бой'):
    raw=str(damage).strip().lower().replace(' ','').replace('\u00a0','').replace(',','.')
    match=re.fullmatch(r'(\d+(?:\.\d+)?)([kкmм]?)',raw)
    if not match:raise ValueError('Урон: число, например 1234567, 1234k или 1.2m.')
    amount=float(match[1])*({'k':1000,'к':1000,'m':1000000,'м':1000000}.get(match[2],1))
    parts=str(duration).strip().replace(',','.').split(':')
    try:
        values=[float(v) for v in parts]
        if not 1<=len(values)<=3 or any(v<0 for v in values) or any(v>=60 for v in values[1:]):raise ValueError()
        seconds=sum(value*60**index for index,value in enumerate(reversed(values)))
        if number(seconds) is None or not 0<seconds<=86400:raise ValueError()
    except (ValueError,OverflowError):raise ValueError('Время: секунды или ММ:СС, например 120 или 2:00.') from None
    nonnegative(amount)
    return {'boss':str(boss or 'Бой')[:150],'duration':seconds,'damage':amount,'dps':amount/seconds}
