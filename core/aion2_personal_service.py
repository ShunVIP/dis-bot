"""Personal Aion helpers: reset period and compact, comparable character snapshots."""
import math
from datetime import datetime, timedelta, timezone

UTC=timezone.utc
DEFAULT_TASKS=('Подземелья недели','Еженедельные задания','Награды и покупки недели','PvP-активности','Крафт и сбор')


def week_window(now=None):
    now=(now or datetime.now(UTC)).astimezone(UTC)
    start=now.replace(hour=7,minute=0,second=0,microsecond=0)-timedelta(days=(now.weekday()-2)%7)
    if start>now:start-=timedelta(days=7)
    return start.isoformat(),start+timedelta(days=7)


def number(value):
    try:
        return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None
    except OverflowError:
        return None


def character_snapshot(bundle):
    profile=bundle['info']['profile']
    equipment=bundle.get('equipment')
    slots=(equipment or {}).get('equipment',{}).get('equipmentList')
    if not isinstance(slots,list):
        raise ValueError('NC не отдал экипировку. Неполный снимок не сохранён, чтобы не показать ложную потерю вещей.')
    if len(slots)>40:raise ValueError('Формат экипировки изменился. Снимок не сохранён.')
    stats=(bundle['info'].get('stat') or {}).get('statList') or []
    items={}
    for slot in slots[:40]:
        if not isinstance(slot,dict) or not isinstance(slot.get('slotPos'),int):continue
        items[str(slot['slotPos'])]={k:slot.get(k) for k in ('id','name','enchantLevel','exceedLevel','slotPosName')}
    return {'name':str(profile['characterName'])[:100],'level':number(profile.get('characterLevel')),
            'power':number(profile.get('combatPower')),
            'item_level':next((number(s.get('value')) for s in stats if s.get('type')=='ItemLevel'),None),
            'equipment':items}


def snapshot_changes(previous,current):
    metrics=[]
    for key,label in [('level','Уровень'),('item_level','Уровень предметов'),('power','Боевая мощь')]:
        before,after=previous.get(key),current.get(key)
        if number(before) is not None and number(after) is not None and before!=after:
            metrics.append((label,before,after,after-before))
    equipment=[]
    old,new=previous.get('equipment',{}),current.get('equipment',{})
    for slot in sorted(old.keys()|new.keys(),key=int):
        if old.get(slot)!=new.get(slot):
            equipment.append((slot,old.get(slot),new.get(slot)))
    return metrics,equipment
