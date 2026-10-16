"""Read-only public NC EU character endpoints; bounded cache, no account cookies."""
import asyncio
import copy
import html
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import aiohttp

BASE="https://aion2.plaync.com"
SEARCH="https://api-search.plaync.com/aion2global/search/v2/character"
_cache={}
_limit=asyncio.Semaphore(3)


def character_ref(server_id,character_id):
    server_id=int(server_id)
    character_id=unquote(str(character_id))
    if server_id<=0 or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}={0,2}",character_id):
        raise ValueError("Некорректный идентификатор персонажа.")
    return {"server_id":server_id,"character_id":character_id,"region":"eu"}


async def _get(url,params):
    key=(url,tuple(sorted(params.items())))
    cached=_cache.get(key)
    if cached and time.monotonic()-cached[0]<300:
        return copy.deepcopy(cached[1])
    try:
        async with _limit:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12),headers={"User-Agent":"ViPik/1.0","Referer":BASE+"/en-us/characters/index"}) as session:
                async with session.get(url,params=params) as response:
                    if response.status==429:
                        raise RuntimeError("NC ограничил частоту запросов. Попробуй через минуту.")
                    response.raise_for_status()
                    raw=bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw)>1_000_000:
                            raise ValueError("API response too large")
                    result=json.loads(raw)
                    if not isinstance(result,dict):
                        raise ValueError("Unexpected API response")
    except (aiohttp.ClientError,asyncio.TimeoutError,ValueError):
        raise RuntimeError("NC сейчас не отдал этот раздел. Попробуй позже.") from None
    if len(_cache)>=128:
        del _cache[next(iter(_cache))]
    _cache[key]=(time.monotonic(),result)
    return copy.deepcopy(result)


async def servers():
    return (await _get(BASE+"/en-us/api/gameinfo/servers",{"region":"eu","lang":"en-US"})).get("serverList",[])


async def search_characters(nickname,server=""):
    nickname=nickname.strip()
    if not 1<=len(nickname)<=40:
        raise ValueError("Ник должен быть длиной от 1 до 40 символов.")
    params={"keyword":nickname,"region":"eu","localeInfo":"en-US","size":40,"page":1}
    if server.strip():
        wanted=server.strip().casefold()
        wanted={"дамину":"daminu"}.get(wanted,wanted)
        found=next((s for s in await servers() if wanted in {str(s["serverId"]),str(s["serverName"]).casefold()}),None)
        if not found:
            raise ValueError("Сервер EU не найден. Введи английское название или ID; Дамину тоже поддерживается.")
        params["serverId"]=int(found["serverId"])
    data=await _get(SEARCH,params)
    items=[]
    for row in data.get("list",[]):
        ref=character_ref(row["serverId"],row["characterId"])
        items.append(ref|{"name":html.unescape(re.sub(r"<[^>]+>","",row["name"])),"server":row.get("serverName",""),"level":row.get("level",0)})
    return items,int(data.get("pagination",{}).get("total",len(items)))


async def section(ref,kind,**extra):
    paths={"info":"info","equipment":"equipment","item":"equipment/item","board":"daevanion/detail"}
    if kind not in paths:
        raise ValueError("Неизвестный раздел API.")
    ref=character_ref(ref["server_id"],ref["character_id"])
    params={"serverId":ref["server_id"],"characterId":ref["character_id"],"region":"eu","lang":"en-US"}
    allowed={"item":{"id","enchantLevel","slotPos"},"board":{"boardId"}}.get(kind,set())
    if set(extra)!=allowed:
        raise ValueError("Некорректные параметры раздела.")
    params.update({k:int(v) for k,v in extra.items()})
    return await _get(BASE+"/api/character/"+paths[kind],params)


async def load_character(ref):
    info,equipment=await asyncio.gather(section(ref,"info"),section(ref,"equipment"),return_exceptions=True)
    if isinstance(info,Exception):
        raise info
    profile=info.get("profile")
    if not isinstance(profile,dict) or not profile.get("characterName"):
        raise RuntimeError("Персонаж не найден в ответе NC.")
    if int(profile.get("serverId",ref["server_id"]))!=int(ref["server_id"]):
        raise RuntimeError("NC вернул персонажа с другого сервера. Повтори поиск.")
    return {"ref":character_ref(ref["server_id"],ref["character_id"]),"fetched_at":datetime.now(timezone.utc).isoformat(),
            "info":info,"equipment":None if isinstance(equipment,Exception) else equipment,
            "errors":{"equipment":str(equipment)} if isinstance(equipment,Exception) else {}}


async def full_export(bundle):
    """Include every returned field, plus lazy equipment and Daevanion details."""
    result=copy.deepcopy(bundle)
    result["item_details"]={}
    result["board_details"]={}
    slots=(bundle.get("equipment") or {}).get("equipment",{}).get("equipmentList",[]) or []
    boards=bundle["info"].get("daevanion",{}).get("boardList",[]) or []
    async def item(slot):
        key=str(slot["slotPos"])
        try:
            result["item_details"][key]=await section(bundle["ref"],"item",id=slot["id"],enchantLevel=slot.get("enchantLevel",0),slotPos=slot["slotPos"])
        except (ValueError,RuntimeError) as exc:
            result["errors"]["item_"+key]=str(exc)
    async def board(row):
        key=str(row["id"])
        try:
            result["board_details"][key]=await section(bundle["ref"],"board",boardId=row["id"])
        except (ValueError,RuntimeError) as exc:
            result["errors"]["board_"+key]=str(exc)
    await asyncio.gather(*(item(s) for s in slots[:30]),*(board(b) for b in boards[:30]))
    if len(slots)>30 or len(boards)>30:
        result["errors"]["limit"]="Полная выгрузка ограничена 30 слотами и 30 досками; исходные списки сохранены целиком."
    return result
