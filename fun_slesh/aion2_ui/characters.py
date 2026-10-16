import asyncio
import io
import json
import time
import zipfile

import discord

from core import aion2_character_service as api, aion2_character_store as store

_requests={}


def budget(user_id):
    now=time.monotonic()
    if now-_requests.get(user_id,-100)<5:
        raise ValueError("Подожди 5 секунд между запросами к NC.")
    if len(_requests)>1000:
        _requests.clear()
    _requests[user_id]=now


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


class OwnedView(discord.ui.View):
    def __init__(self,actor_id,target_id):
        super().__init__(timeout=600)
        self.actor_id,self.target_id=actor_id,target_id

    async def interaction_check(self,interaction):
        if interaction.user.id==self.actor_id and interaction.guild_id:
            return True
        await interaction.response.send_message("Открой своё окно Aion 2.",ephemeral=True)
        return False

    def can_manage(self,interaction):
        return interaction.user.id==self.target_id or interaction.permissions.administrator


def overview(bundle):
    profile=bundle['info']['profile']
    stats=bundle['info'].get('stat',{}).get('statList',[]) or []
    item_level=next((s.get('value') for s in stats if s.get('type')=='ItemLevel'),'—')
    embed=discord.Embed(title=f"{profile.get('characterName','Персонаж')} · {profile.get('serverName','EU')}",color=discord.Color.green())
    embed.description=(f"**{safe(profile.get('className','—'))}**, уровень **{profile.get('characterLevel','—')}**\n"
                       f"Уровень предметов: **{item_level}** · Боевая мощь: **{profile.get('combatPower','—')}**\n"
                       f"Раса: {safe(profile.get('raceName','—'))}\nЛегион: {safe(profile.get('regionName') or '—')}\n"
                       f"Титул: {safe(profile.get('titleName') or '—')}")
    embed.set_footer(text="Данные публичного сайта NC (кэш до 5 минут). Привязка не подтверждает владение аккаунтом. Odyle-энергия API не предоставлена.")
    return embed


class SearchModal(discord.ui.Modal,title="Найти персонажа Aion 2 EU"):
    nickname=discord.ui.TextInput(label="Ник персонажа",max_length=40)
    server=discord.ui.TextInput(label="Сервер: название / ID (можно пусто)",default="Дамину",required=False,max_length=40)

    def __init__(self,actor_id,target_id):
        super().__init__()
        self.actor_id,self.target_id=actor_id,target_id

    async def on_submit(self,interaction):
        if interaction.user.id!=self.actor_id or not interaction.guild_id:
            await interaction.response.send_message("Открой своё окно Aion 2.",ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            budget(interaction.user.id)
            items,total=await api.search_characters(str(self.nickname),str(self.server))
            if not items:
                await interaction.followup.send("Персонажи не найдены. Проверь ник и сервер.",ephemeral=True)
                return
            view=OwnedView(self.actor_id,self.target_id)
            view.add_item(CharacterPicker(items[:25],self.actor_id,self.target_id))
            await interaction.followup.send(f"Найдено: {total}. Показаны первые {min(len(items),25)}. Выбери точный ник и сервер; при необходимости уточни поиск.",view=view,ephemeral=True)
        except (ValueError,RuntimeError) as exc:
            await interaction.followup.send(str(exc),ephemeral=True)


class CharacterPicker(discord.ui.Select):
    def __init__(self,items,actor_id,target_id,linked=False):
        self.items=items
        self.actor_id,self.target_id=actor_id,target_id
        options=[discord.SelectOption(label=(('★ ' if row.get('primary') else '')+row['name'])[:100],description=f"{row.get('server',row['server_id'])} · ур. {row.get('level','—')}",value=str(i)) for i,row in enumerate(items)]
        super().__init__(placeholder="Выбери персонажа",options=options)

    async def callback(self,interaction):
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            budget(interaction.user.id)
            row=self.items[int(self.values[0])]
            bundle=await api.load_character(row)
            view=CharacterDataView(self.actor_id,self.target_id,bundle,row.get('id'),self.actor_id==self.target_id or interaction.permissions.administrator)
            await interaction.followup.send(embed=overview(bundle),view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        except (ValueError,RuntimeError) as exc:
            await interaction.followup.send(str(exc),ephemeral=True)


class SectionPicker(discord.ui.Select):
    def __init__(self):
        labels={'overview':'Карточка','profile':'Профиль: все поля','stat':'Характеристики: все поля','title':'Титулы','ranking':'Рейтинги','daevanion':'Daevanion','equipment':'Экипировка и облики','petwing':'Питомец и крылья','skill':'Навыки','all':'Исходные ответы API'}
        super().__init__(placeholder="Какой раздел посмотреть?",options=[discord.SelectOption(label=label,value=key) for key,label in labels.items()],row=0)

    async def callback(self,interaction):
        view=self.view
        key=self.values[0]
        if key=='overview':
            view.pages=[]
            await interaction.response.edit_message(embed=overview(view.bundle),view=view)
            return
        if key=='all':
            data=view.bundle
        elif key in view.bundle['info']:
            data=view.bundle['info'][key]
        else:
            data=(view.bundle.get('equipment') or {}).get(key)
        view.set_pages(key,data)
        await interaction.response.edit_message(embed=view.page_embed(),view=view)


class DetailPicker(discord.ui.Select):
    def __init__(self,rows,kind):
        self.rows,self.kind=rows,kind
        super().__init__(placeholder="Подробности предмета" if kind=='item' else "Узлы и эффекты Daevanion",options=[discord.SelectOption(label=str(r.get('name',r['id']))[:100],value=str(i)) for i,r in enumerate(rows)],row=1 if kind=='item' else 2)

    async def callback(self,interaction):
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            budget(interaction.user.id)
            row=self.rows[int(self.values[0])]
            extra={'id':row['id'],'enchantLevel':row.get('enchantLevel',0),'slotPos':row['slotPos']} if self.kind=='item' else {'boardId':row['id']}
            data=await api.section(self.view.bundle['ref'],self.kind,**extra)
            view=CharacterDataView(self.view.actor_id,self.view.target_id,self.view.bundle,self.view.account_id,self.view.can_manage(interaction))
            view.set_pages(str(row.get('name',self.kind)),data)
            await interaction.followup.send(embed=view.page_embed(),view=view,ephemeral=True)
        except (ValueError,RuntimeError) as exc:
            await interaction.followup.send(str(exc),ephemeral=True)


class CharacterDataView(OwnedView):
    def __init__(self,actor_id,target_id,bundle,account_id=None,can_manage=True):
        super().__init__(actor_id,target_id)
        self.bundle,self.account_id=bundle,account_id
        self.pages=[]; self.page=0; self.section=''
        self.add_item(SectionPicker())
        slots=(bundle.get('equipment') or {}).get('equipment',{}).get('equipmentList',[]) or []
        boards=bundle['info'].get('daevanion',{}).get('boardList',[]) or []
        if slots: self.add_item(DetailPicker(slots[:25],'item'))
        if boards: self.add_item(DetailPicker(boards[:25],'board'))
        self.link_button.disabled=not can_manage
        self.unlink_button.disabled=not can_manage or not account_id

    def set_pages(self,title,data):
        self.section=title; self.page=0
        if data is None:
            text='Этот раздел пуст или не отдан NC. См. ошибки в исходном JSON.'
        else:
            text=json.dumps(data,ensure_ascii=False,indent=2)
        text=discord.utils.escape_mentions(text).replace('```','`\u200b``')
        self.pages=[text[i:i+3400] for i in range(0,len(text),3400)] or ['Нет данных.']

    def page_embed(self):
        return discord.Embed(title=self.section[:200],description=f"```json\n{self.pages[self.page]}\n```").set_footer(text=f"Страница {self.page+1}/{len(self.pages)} · все поля ответа NC; для скачивания — Полный JSON")

    @discord.ui.button(label="Привязать / сделать основным",row=3,style=discord.ButtonStyle.success)
    async def link_button(self,interaction,button):
        ref=self.bundle['ref']
        try:
            await asyncio.to_thread(store.link,interaction.user.id,self.target_id,ref['server_id'],ref['character_id'],self.bundle['info']['profile']['characterName'],is_admin=interaction.permissions.administrator)
            rows=await asyncio.to_thread(store.list_links,self.target_id)
            self.account_id=next(r['id'] for r in rows if r['server_id']==ref['server_id'] and r['character_id']==ref['character_id'])
            self.unlink_button.disabled=False
            await interaction.response.edit_message(content=f"✅ Персонаж привязан к Discord ID {self.target_id} и выбран основным. Владение игровым аккаунтом не проверено.",view=self)
        except (ValueError,PermissionError) as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)

    @discord.ui.button(label="Отвязать",row=3)
    async def unlink_button(self,interaction,button):
        try:
            await asyncio.to_thread(store.change_link,interaction.user.id,self.target_id,self.account_id,remove=True,is_admin=interaction.permissions.administrator)
            self.account_id=None; self.unlink_button.disabled=True
            await interaction.response.edit_message(content="✅ Привязка удалена.",view=self)
        except (ValueError,PermissionError) as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)

    @discord.ui.button(label="Полный JSON",row=3)
    async def export(self,interaction,button):
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            budget(interaction.user.id)
            data=await asyncio.wait_for(api.full_export(self.bundle),timeout=60)
            raw=json.dumps(data,ensure_ascii=False,indent=2).encode()
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('character.json',raw)
            buffer.seek(0)
            if buffer.getbuffer().nbytes>7_000_000:
                raise ValueError("Ответ слишком большой для вложения Discord.")
            await interaction.followup.send("Все полученные поля API, подробности предметов и Daevanion. Недоступные запросы перечислены в errors. Архив создан в памяти, на диске не сохраняется.",file=discord.File(buffer,filename='aion2-character.zip'),ephemeral=True)
        except (ValueError,RuntimeError,asyncio.TimeoutError) as exc:
            await interaction.followup.send(str(exc) or "NC не успел отдать полную выгрузку. Попробуй позже.",ephemeral=True)

    @discord.ui.button(label="◀",row=4)
    async def prev(self,interaction,button):
        if self.pages: self.page=(self.page-1)%len(self.pages)
        await interaction.response.edit_message(embed=self.page_embed() if self.pages else overview(self.bundle),view=self)

    @discord.ui.button(label="▶",row=4)
    async def next(self,interaction,button):
        if self.pages: self.page=(self.page+1)%len(self.pages)
        await interaction.response.edit_message(embed=self.page_embed() if self.pages else overview(self.bundle),view=self)


class TargetPicker(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Выбери участника Discord")

    async def callback(self,interaction):
        member=interaction.guild.get_member(self.values[0].id)
        if member is None or member.bot:
            await interaction.response.send_message("Выбери участника этого сервера, не бота.",ephemeral=True)
            return
        await open_character_hub(interaction,member.id)


class CharacterHubView(OwnedView):
    @discord.ui.button(label="Найти / привязать персонажа",row=0,style=discord.ButtonStyle.primary)
    async def search(self,interaction,button):
        await interaction.response.send_modal(SearchModal(self.actor_id,self.target_id))

    @discord.ui.button(label="Привязанные персонажи",row=0)
    async def links(self,interaction,button):
        await interaction.response.defer(ephemeral=True,thinking=True)
        rows=await asyncio.to_thread(store.list_links,self.target_id)
        if not rows:
            await interaction.followup.send("Привязок пока нет. Нажми «Найти / привязать персонажа».",ephemeral=True)
            return
        try:
            names={int(row['serverId']):row['serverName'] for row in await api.servers()}
        except RuntimeError:
            names={}
        for row in rows:
            row['server']=names.get(row['server_id'],str(row['server_id']))
        view=OwnedView(self.actor_id,self.target_id)
        view.add_item(CharacterPicker(rows,self.actor_id,self.target_id,linked=True))
        await interaction.followup.send("★ — основной персонаж. Выбери персонажа для просмотра API, смены основного или отвязки.",view=view,ephemeral=True)

    @discord.ui.button(label="Другой участник Discord",row=1)
    async def target(self,interaction,button):
        view=OwnedView(self.actor_id,self.target_id)
        view.add_item(TargetPicker())
        await interaction.response.send_message("Администратор может менять чужие привязки; остальные — только смотреть публичные данные.",view=view,ephemeral=True)


async def open_character_hub(interaction,target_id=None):
    if not interaction.guild_id:
        await interaction.response.send_message("Открой раздел на Discord-сервере.",ephemeral=True)
        return
    target_id=target_id or interaction.user.id
    member=interaction.guild.get_member(target_id)
    name=safe(member.display_name) if member else str(target_id)
    await interaction.response.send_message(f"**Персонажи Aion 2 Global / EU · {name}**\nПоиск, данные публичного API, до 10 привязок и основной персонаж. Привязки заявленные — владение аккаунтом NC не проверяется.",view=CharacterHubView(interaction.user.id,target_id),ephemeral=True)
