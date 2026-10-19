"""Read-only boss board and personal paginated view; no spawn pings."""
import time
from datetime import datetime, timezone, timedelta

import discord

from core import aion2_boss_service as service
from .presentation import text

MSK = timezone(timedelta(hours=3))
SIDES = {'elyos':'Элийцы + общие', 'asmodian':'Асмодиане + общие', 'all':'Обе фракции'}


def boss_embed(snapshot, side='elyos', page=0, *, compact=False, now=None):
    now = int(now if now is not None else time.time())
    rows = service.ordered_rows(snapshot,side,now)
    size = 8 if compact else 6
    count = max(1,(len(rows)+size-1)//size)
    page = min(max(page,0),count-1)
    description = (f"**{SIDES[side]}** · наблюдения игроков A2Tools, не официальный API NC.\n"
                   f"Последние данные источника: <t:{snapshot['updated']}:R>.\n"
                   'Прошедший отсчёт сам по себе не означает, что босс появился.')
    if snapshot['stale'] or snapshot.get('error'):
        description = '⚠️ **Нет свежих данных. Статусы требуют проверки в игре.**\n'+description
    if not snapshot.get('catalog_available',True):
        description += '\n⚠️ Справочник названий временно недоступен.'
    embed = discord.Embed(title='Мировые боссы · Дамину / EU',description=description,
                          color=discord.Color.orange() if snapshot['stale'] else discord.Color.blue())
    for row in rows[page*size:(page+1)*size]:
        _, status = service.status(row,snapshot,now)
        value = status
        if row.get('next_spawn') and row['next_spawn']>now:
            value += f"\nОриентир: **{datetime.fromtimestamp(row['next_spawn'],MSK):%d.%m %H:%M} МСК**"
        if row.get('reported_at'):
            value += f"\nНаблюдение: <t:{row['reported_at']}:R>"
        name = row['name']
        if row.get('area'):
            name += ' · '+row['area']
        embed.add_field(name=text(name,256),value=value,inline=False)
    if not rows:
        embed.add_field(name='Наблюдений пока нет',value='Для выбранной фракции источник пока не отдал боссов.',inline=False)
    suffix = f'Ближайшие {min(size,len(rows))} из {len(rows)} · полный список — кнопкой' if compact else f'Страница {page+1}/{count} · ◀ ▶ листать'
    embed.set_footer(text=suffix+' · данные A2Tools; названия из игровых таблиц A2Tools-Plugins')
    return embed, count


async def open_bosses(interaction):
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        snapshot = await service.get_snapshot()
    except RuntimeError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
        return
    view = BossesView(interaction.user.id,snapshot)
    await interaction.followup.send(embed=view.embed(),view=view,ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())


class SideSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='Фракция боссов',row=0,options=[discord.SelectOption(label=label,value=key,default=key=='elyos') for key,label in SIDES.items()])

    async def callback(self,interaction):
        view = self.view
        view.side,self.view.page = self.values[0],0
        for option in self.options:
            option.default = option.value==view.side
        await interaction.response.edit_message(embed=view.embed(),view=view)


class BossesView(discord.ui.View):
    def __init__(self,user_id,snapshot):
        super().__init__(timeout=600)
        self.user_id,self.snapshot,self.side,self.page = user_id,snapshot,'elyos',0
        self.add_item(SideSelect())
        self.add_item(discord.ui.Button(label='Источник: A2Tools',url=service.SOURCE_URL,row=2))
        self.embed()

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id and interaction.guild_id:
            return True
        await interaction.response.send_message('Открой свой список боссов через Aion 2.',ephemeral=True)
        return False

    def embed(self):
        embed,count = boss_embed(self.snapshot,self.side,self.page)
        self.prev.disabled=self.next.disabled=count<2
        return embed

    @discord.ui.button(label='◀',row=1)
    async def prev(self,interaction,button):
        _,count = boss_embed(self.snapshot,self.side)
        self.page=(self.page-1)%count
        await interaction.response.edit_message(embed=self.embed(),view=self)

    @discord.ui.button(label='▶',row=1)
    async def next(self,interaction,button):
        _,count = boss_embed(self.snapshot,self.side)
        self.page=(self.page+1)%count
        await interaction.response.edit_message(embed=self.embed(),view=self)

    @discord.ui.button(label='Обновить',row=1)
    async def refresh(self,interaction,button):
        await interaction.response.defer()
        try:
            self.snapshot=await service.get_snapshot()
            await interaction.edit_original_response(embed=self.embed(),view=self)
        except RuntimeError as exc:
            await interaction.followup.send(str(exc),ephemeral=True)


class BossBoardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label='Источник: A2Tools',url=service.SOURCE_URL))

    @discord.ui.button(label='Все боссы / обновить',custom_id='aion2:bosses:daminu:open',style=discord.ButtonStyle.primary)
    async def open(self,interaction,button):
        await open_bosses(interaction)
