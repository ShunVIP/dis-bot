"""Private friend-server tools, all explicitly requested through the existing menu."""
import asyncio
from datetime import datetime

import discord

from core import aion2_personal_store as store, aion2_personal_service as service
from core import aion2_character_store as links, aion2_character_service as api, settings_store
from core.aion2_presence import is_playing_aion2
from .characters import budget
from .presentation import text, label, fields_pages, picture


class PrivateView(discord.ui.View):
    def __init__(self,user_id,guild_id):
        super().__init__(timeout=600)
        self.user_id,self.guild_id=user_id,guild_id

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id and interaction.guild_id==self.guild_id:return True
        await interaction.response.send_message('Открой своё окно Aion 2.',ephemeral=True)
        return False


class Pages(PrivateView):
    def __init__(self,user_id,guild_id,pages):
        super().__init__(user_id,guild_id)
        self.pages,self.page=pages,0
        self.prev.disabled=self.next.disabled=len(pages)<2

    def embed(self):
        self.page=min(self.page,len(self.pages)-1)
        return self.pages[self.page].copy().set_footer(text=f'Страница {self.page+1}/{len(self.pages)} · личное окно, без рассылок')

    @discord.ui.button(label='◀',row=2)
    async def prev(self,interaction,button):
        self.page=(self.page-1)%len(self.pages)
        await interaction.response.edit_message(embed=self.embed(),view=self)

    @discord.ui.button(label='▶',row=2)
    async def next(self,interaction,button):
        self.page=(self.page+1)%len(self.pages)
        await interaction.response.edit_message(embed=self.embed(),view=self)


def checklist_embed(data):
    done=sum(row['done'] for row in data['tasks'])
    lines=[('✅ ' if row['done'] else '⬜ ')+text(row['label'],100) for row in data['tasks']]
    return discord.Embed(title=f'Мой чеклист Aion 2 · {done}/{len(lines)}',description='\n'.join(lines),color=discord.Color.green()).add_field(
        name='Следующий сброс отметок',value=f"<t:{int(data['reset'].timestamp())}:F> · <t:{int(data['reset'].timestamp())}:R>",inline=False).set_footer(
        text='Ручные личные отметки, не данные игрового аккаунта. Неделя: среда 07:00 UTC.')


class TaskSelect(discord.ui.Select):
    def __init__(self,data,remove=False):
        self.week,self.remove=data['week'],remove
        super().__init__(row=0,placeholder='Удалить задачу' if remove else 'Отметить / снять отметку',options=[discord.SelectOption(
            label=(('✅ ' if row['done'] else '⬜ ')+row['label'])[:100],value=str(row['id'])) for row in data['tasks']])

    async def callback(self,interaction):
        await interaction.response.defer()
        try:
            if self.remove:await asyncio.to_thread(store.delete_task,interaction.guild_id,interaction.user.id,int(self.values[0]))
            else:await asyncio.to_thread(store.toggle_task,interaction.guild_id,interaction.user.id,int(self.values[0]),self.week)
            data=await asyncio.to_thread(store.checklist,interaction.guild_id,interaction.user.id)
            await interaction.edit_original_response(embed=checklist_embed(data),view=ChecklistView(interaction.user.id,interaction.guild_id,data),content=None)
        except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)


class TaskModal(discord.ui.Modal,title='Своя задача Aion 2'):
    name=discord.ui.TextInput(label='Название',max_length=80)

    def __init__(self,user_id,guild_id):
        super().__init__()
        self.user_id,self.guild_id=user_id,guild_id

    async def on_submit(self,interaction):
        if interaction.user.id!=self.user_id or interaction.guild_id!=self.guild_id:return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            await asyncio.to_thread(store.add_task,self.guild_id,self.user_id,str(self.name))
            data=await asyncio.to_thread(store.checklist,self.guild_id,self.user_id)
            await interaction.followup.send(embed=checklist_embed(data),view=ChecklistView(self.user_id,self.guild_id,data),ephemeral=True)
        except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)


class ChecklistView(PrivateView):
    def __init__(self,user_id,guild_id,data):
        super().__init__(user_id,guild_id)
        self.data=data
        self.add_item(TaskSelect(data))

    @discord.ui.button(label='Добавить задачу',row=1)
    async def add(self,interaction,button):
        await interaction.response.send_modal(TaskModal(self.user_id,self.guild_id))

    @discord.ui.button(label='Удалить задачу',row=1)
    async def remove(self,interaction,button):
        data=await asyncio.to_thread(store.checklist,self.guild_id,self.user_id)
        view=PrivateView(self.user_id,self.guild_id)
        view.add_item(TaskSelect(data,remove=True))
        await interaction.response.send_message('Выбери задачу для удаления из своего списка.',view=view,ephemeral=True)

    @discord.ui.button(label='Обновить',row=1)
    async def refresh(self,interaction,button):
        await interaction.response.defer()
        data=await asyncio.to_thread(store.checklist,self.guild_id,self.user_id)
        await interaction.edit_original_response(embed=checklist_embed(data),view=ChecklistView(self.user_id,self.guild_id,data))


async def open_checklist(interaction):
    await interaction.response.defer(ephemeral=True,thinking=True)
    data=await asyncio.to_thread(store.checklist,interaction.guild_id,interaction.user.id)
    await interaction.followup.send(embed=checklist_embed(data),view=ChecklistView(interaction.user.id,interaction.guild_id,data),ephemeral=True)


async def players_pages(interaction):
    policy=await asyncio.to_thread(settings_store.get_feature_policy,interaction.guild_id,'aion2_timers')
    channel=interaction.guild.get_channel(policy.output_channel_id) if policy.output_channel_id else interaction.channel
    if not isinstance(channel,discord.TextChannel) or not channel.permissions_for(interaction.user).view_channel:
        raise ValueError('Нет доступа к каналу Aion 2. Состав закрытого канала не показываю.')
    members=sorted((m for m in channel.members if is_playing_aion2(m)),key=lambda m:m.display_name.casefold())
    accounts=await asyncio.to_thread(links.roster,[m.id for m in members])
    by_user={}
    for account in accounts:by_user.setdefault(account['user_id'],[]).append(account)
    fields=[]
    for member in members:
        own=by_user.get(member.id,[])
        chars='\n'.join(f"{'★ ' if i==0 else ''}{text(a['name'])} · сервер {a['server_id']}" for i,a in enumerate(own))
        fields.append((member.display_name,chars or 'Персонаж ещё не привязан.'))
    if not fields:fields.append(('Сейчас никого не видно','Никто с доступом к каналу не показывает запущенный Aion 2 в Discord.'))
    return fields_pages('Кто сейчас в Aion 2',fields,intro='По игровой активности Discord в канале Aion 2. Скрытая активность не видна.\nНики ниже — заявленные привязки, а не определение активного персонажа.')


class PlayersView(Pages):
    @discord.ui.button(label='Обновить список',row=1)
    async def refresh(self,interaction,button):
        await interaction.response.defer()
        try:
            self.pages=await players_pages(interaction);self.page=0
            self.prev.disabled=self.next.disabled=len(self.pages)<2
            await interaction.edit_original_response(embed=self.embed(),view=self)
        except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)


async def open_players(interaction):
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        pages=await players_pages(interaction)
        view=PlayersView(interaction.user.id,interaction.guild_id,pages)
        await interaction.followup.send(embed=view.embed(),view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)


def progress_pages(history,created):
    current=history[0]['data']
    shown=lambda key:'—' if current.get(key) is None else current[key]
    fields=[('Текущий снимок',f"Уровень: {shown('level')}\nУровень предметов: {shown('item_level')}\nБоевая мощь: {shown('power')}")]
    if len(history)<2:
        fields.append(('Начальная точка','Первый снимок сохранён. Следующий изменившийся снимок покажет разницу. Прошлую историю из API восстановить нельзя.'))
    else:
        metrics,equipment=service.snapshot_changes(history[1]['data'],current)
        for name,before,after,delta in metrics:fields.append((name,f'{before} → {after} ({delta:+g})'))
        for slot,before,after in equipment:
            def item(row):return f"{text(row.get('name') or 'Предмет')} +{row.get('enchantLevel') or 0}" if row else 'Нет предмета'
            fields.append((label((after or before).get('slotPosName') or 'Слот '+slot),item(before)+' → '+item(after)))
    dates=[]
    for row in history:
        when=int(datetime.fromisoformat(row['captured_at']).timestamp())
        dates.append(f"<t:{when}:f> · мощь {row['data'].get('power','—')} · предметы {row['data'].get('item_level','—')}")
    fields.append(('Сохранённая история','\n'.join(dates)))
    return fields_pages('Прогресс · '+current['name'],fields,intro=('Сохранён новый снимок.' if created else 'Данные не изменились; повторный снимок не создавался.')+'\nПо запросу, кэш NC до 5 минут. До 30 снимков персонажа / 300 на пользователя.')


class ClearHistoryView(PrivateView):
    @discord.ui.button(label='Да, удалить всю мою историю Aion 2',style=discord.ButtonStyle.danger)
    async def confirm(self,interaction,button):
        await interaction.response.defer()
        await asyncio.to_thread(store.clear_history,self.user_id)
        await interaction.edit_original_response(content='Твоя история снимков удалена. Привязки персонажей сохранены.',view=None)


class ProgressView(Pages):
    def __init__(self,user_id,guild_id,pages,ref):
        super().__init__(user_id,guild_id,pages)
        self.ref=ref

    @discord.ui.button(label='Обновить снимок',row=1)
    async def refresh(self,interaction,button):
        await capture_progress(interaction,self.ref,edit=True)


async def capture_progress(interaction,ref,edit=False):
    await interaction.response.defer(ephemeral=True,thinking=not edit)
    try:
        budget(interaction.user.id)
        bundle=await api.load_character(ref)
        data=service.character_snapshot(bundle)
        created=await asyncio.to_thread(store.save_snapshot,interaction.user.id,ref,data)
        history=await asyncio.to_thread(store.snapshot_history,interaction.user.id,ref)
        if not history:raise ValueError('История была очищена в другом окне. Обнови снимок ещё раз.')
        pages=progress_pages(history,created)
        portrait=picture(bundle['info']['profile'].get('profileImage'))
        if portrait:
            for page in pages:page.set_thumbnail(url=portrait)
        view=ProgressView(interaction.user.id,interaction.guild_id,pages,ref)
        if edit:await interaction.edit_original_response(embed=view.embed(),view=view)
        else:await interaction.followup.send(embed=view.embed(),view=view,ephemeral=True)
    except (ValueError,RuntimeError,PermissionError) as exc:await interaction.followup.send(str(exc),ephemeral=True)


class ProgressPicker(discord.ui.Select):
    def __init__(self,rows):
        self.rows=rows
        super().__init__(placeholder='Свой персонаж для снимка',options=[discord.SelectOption(label=(('★ ' if r['primary'] else '')+r['name'])[:100],value=str(i)) for i,r in enumerate(rows)])

    async def callback(self,interaction):
        await capture_progress(interaction,self.rows[int(self.values[0])])


class ProgressEntry(PrivateView):
    @discord.ui.button(label='Удалить мою историю снимков',row=1,style=discord.ButtonStyle.danger)
    async def clear(self,interaction,button):
        await interaction.response.send_message('Удалить все твои сохранённые снимки Aion 2? Это не удалит привязки.',view=ClearHistoryView(self.user_id,self.guild_id),ephemeral=True)


async def open_progress(interaction):
    await interaction.response.defer(ephemeral=True,thinking=True)
    rows=await asyncio.to_thread(links.list_links,interaction.user.id)
    view=ProgressEntry(interaction.user.id,interaction.guild_id)
    if rows:view.add_item(ProgressPicker(rows))
    await interaction.followup.send('Выбери своего персонажа: бот сохранит компактный снимок и сравнит с предыдущим. Это личная история, не публичный рейтинг.' if rows else 'Сначала привяжи персонажа в «Персонажи / API». Старую историю при необходимости можно удалить ниже.',view=view,ephemeral=True)
