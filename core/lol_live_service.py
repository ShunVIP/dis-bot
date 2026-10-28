"""Read-only Spectator-V5 ranks. No lobby/client access; respects API backoff."""
import asyncio
import time

from core.riot_client import RiotClient,RiotRouting,RiotApiError,PLATFORM_HOSTS

TIERS={'IRON':'Железо','BRONZE':'Бронза','SILVER':'Серебро','GOLD':'Золото','PLATINUM':'Платина',
       'EMERALD':'Изумруд','DIAMOND':'Алмаз','MASTER':'Мастер','GRANDMASTER':'Грандмастер','CHALLENGER':'Претендент'}


def solo_rank(entries):
    entry=next((r for r in entries if r.get('queueType')=='RANKED_SOLO_5x5'),None)
    if entry is None:return 'Без ранга соло/дуо'
    tier=entry.get('tier','')
    division='' if tier in {'MASTER','GRANDMASTER','CHALLENGER'} else str(entry.get('rank',''))
    return f"{TIERS.get(tier,tier or 'Ранг неизвестен')} {division} · {entry.get('leaguePoints',0)} LP".strip()


class LiveService:
    def __init__(self,key):
        self.key=key
        self.blocked_until=0
        self.last_error=''
        self._next_request=0
        self._lock=asyncio.Lock()
        self._ranks={}

    def ready(self):return bool(self.key) and time.monotonic()>=self.blocked_until

    async def request(self,client,method,*args):
        async with self._lock:
            if not self.ready():raise RiotApiError('Live ranks are waiting for API access')
            delay=self._next_request-time.monotonic()
            if delay>0:await asyncio.sleep(delay)
            self._next_request=time.monotonic()+1.7  # Under development-key 100 requests / 2 minutes.
            try:
                return await getattr(client,method)(*args)
            except RiotApiError as exc:
                if exc.status in {401,403}:
                    self.last_error=f'Riot API отклонил ключ ({exc.status}). Нужен рабочий ключ и перезапуск бота.'
                    self.blocked_until=time.monotonic()+3600
                elif exc.status==429:
                    self.last_error='Riot API ограничил запросы; бот ждёт Retry-After.'
                    self.blocked_until=time.monotonic()+max(float(exc.retry_after or 120),120)
                raise

    def client(self,platform):
        if platform not in PLATFORM_HOSTS:raise ValueError('Перепривяжи аккаунт с корректным регионом Riot.')
        regional=('americas' if platform in {'na1','br1','la1','la2'} else
                  'asia' if platform in {'kr','jp1'} else
                  'sea' if platform in {'oc1','sg2','tw2','vn2'} else 'europe')
        return RiotClient(self.key,RiotRouting(platform,regional),retry_rate_limit=False)

    async def active_game(self,account):
        return await self.request(self.client(account['platform']),'active_game',account['puuid'])

    async def participants(self,platform,game):
        rows=game.get('participants')
        if not isinstance(rows,list) or not 1<=len(rows)<=20:raise ValueError('Неизвестный формат состава Riot.')
        client=self.client(platform);result=[]
        for index,row in enumerate(rows):
            name=str(row.get('riotId') or '')
            puuid=row.get('puuid')
            if not name and puuid:
                try:
                    account=await self.request(client,'account_by_puuid',puuid)
                    name=f"{account.get('gameName','Игрок')}#{account.get('tagLine','')}"
                except RiotApiError as exc:
                    if exc.status in {401,403,429} or not self.ready():raise
            name=name or f'Игрок {index+1}'
            rank='Данные ранга недоступны'
            if row.get('bot'):rank='Бот'
            elif puuid:
                cache_key=(platform,puuid)
                cached=self._ranks.get(cache_key)
                if cached and time.monotonic()-cached[0]<900:rank=cached[1]
                else:
                    try:
                        rank=solo_rank(await self.request(client,'ranked_entries_by_puuid',puuid))
                        if len(self._ranks)>=1000:self._ranks.clear()
                        self._ranks[cache_key]=(time.monotonic(),rank)
                    except RiotApiError as exc:
                        if exc.status in {401,403,429} or not self.ready():raise
            result.append({'name':name[:120],'rank':rank,'team':int(row.get('teamId',0))})
        return result
