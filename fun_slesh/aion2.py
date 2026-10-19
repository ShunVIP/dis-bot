"""Aion 2 on-demand hub. No unsolicited posts or guessed game timers."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import aion2_service as service, aion2_store as store
from core import aion2_timers
from core.aion2_presence import has_players
from core import settings_store
from core import aion2_character_store
from fun_slesh.aion2_ui.characters import open_character_hub

log = logging.getLogger(__name__)
TIMERS_FEATURE = "aion2_timers"
DASHBOARD_MARKER = "AION2-EU timers"


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def timer_embed(now=None, *, show_shugo=True):
    data = aion2_timers.snapshot(now)
    def display(when):
        return f"**{when.astimezone(service.MSK):%d.%m %H:%M} МСК** · <t:{int(when.timestamp())}:R>"
    embed = discord.Embed(title="Aion 2 — таймеры Global / Европа", color=discord.Color.blue())
    if show_shugo:
        embed.add_field(name="🎪 Фестиваль шуго", value=display(data["shugo"])+"\nКаждый час, в :00. Вход короткий — будь готов заранее.",inline=False)
    embed.add_field(name="🌀 Ближайший рифт", value=display(data["rifts"][0])+"\nДалее по МСК: "+", ".join(f"{when.astimezone(service.MSK):%d.%m %H:%M}" for when in data["rifts"][1:])+"\nКаждые 3 часа: 00, 03, 06, 09, 12, 15, 18, 21 UTC.",inline=False)
    embed.add_field(name="🌅 Ежедневный сброс",value=display(data["daily"])+"\nКаждый день в 10:00 МСК (07:00 UTC).",inline=False)
    embed.add_field(name="📅 Еженедельный сброс",value=display(data["weekly"])+"\nСреда, 10:00 МСК (07:00 UTC).",inline=False)
    note = "⚠️ После перевода часов 25.10 расписание требует повторной сверки с игрой.\n" if data["recheck_due"] else "После перевода часов 25.10 сверяй расписание с игрой.\n"
    embed.description = note + f"Расчёт по [наблюдениям игроков EU]({aion2_timers.SOURCE_URL}), проверено {aion2_timers.VERIFIED_ON}. Техработы могут изменить открытие событий."
    embed.set_footer(text="Отсчёт Discord обновляется сам; кнопка «Обновить» пересчитывает ближайшие события. Это не live-проверка игровых серверов.")
    embed.timestamp = data["now"]
    return embed


class TimerView(discord.ui.View):
    def __init__(self,user_id):
        super().__init__(timeout=900)
        self.user_id=user_id

    @discord.ui.button(label="Обновить таймеры",emoji="🔄")
    async def refresh(self,interaction,button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Открой таймеры в своём окне Aion 2.",ephemeral=True)
            return
        await interaction.response.edit_message(embed=timer_embed(),view=self)


class MemberModal(discord.ui.Modal, title="Мой персонаж Aion 2"):
    nickname = discord.ui.TextInput(label="Ник персонажа", max_length=40)
    region = discord.ui.TextInput(label="Регион: global / kr / tw", default="global", max_length=6)
    server = discord.ui.TextInput(label="Сервер", max_length=40)
    class_name = discord.ui.TextInput(label="Класс / роль в группе", max_length=40)

    async def on_submit(self, interaction):
        try:
            values = service.validate_member(str(self.nickname),str(self.region),str(self.server),str(self.class_name))
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await asyncio.to_thread(store.save_member, interaction.guild_id,interaction.user.id,*values)
        await interaction.followup.send("✅ Карточка сохранена. Она видна участникам этого сервера в разделе «Состав». Данные введены тобой, владение персонажем не проверялось.",ephemeral=True)


class EventModal(discord.ui.Modal, title="Событие группы Aion 2"):
    event_title = discord.ui.TextInput(label="Название события", min_length=2, max_length=100)
    date_text = discord.ui.TextInput(label="Дата МСК: ДД.ММ.ГГГГ ЧЧ:ММ", max_length=16)

    async def on_submit(self, interaction):
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("Нужно право «Управление сервером».",ephemeral=True)
            return
        try:
            title,starts = service.parse_event(str(self.event_title),str(self.date_text))
            event_id = await asyncio.to_thread(store.add_event,interaction.guild_id,interaction.user.id,title,starts)
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Событие #{event_id} добавлено. Оно доступно в расписании группы.",ephemeral=True)


class DeleteEventModal(discord.ui.Modal, title="Удалить событие Aion 2"):
    event_id = discord.ui.TextInput(label="Номер события из расписания", max_length=12)

    async def on_submit(self, interaction):
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("Нужно право «Управление сервером».",ephemeral=True)
            return
        if not str(self.event_id).isdigit():
            await interaction.response.send_message("Номер должен быть числом.",ephemeral=True)
            return
        removed = await asyncio.to_thread(store.delete_event,interaction.guild_id,int(str(self.event_id)))
        await interaction.response.send_message("✅ Удалено." if removed else "Событие на этом сервере не найдено.",ephemeral=True)


class Aion2View(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id
        for label,url in (("Официальный сайт",service.OFFICIAL_SITE),("Поиск персонажа",service.CHARACTER_SEARCH),("Steam","https://store.steampowered.com/app/3393110/")):
            self.add_item(discord.ui.Button(label=label,url=url,row=3))

    async def interaction_check(self, interaction):
        if interaction.user.id == self.user_id and interaction.guild_id:
            return True
        await interaction.response.send_message("Открой своё окно через «Команды → Игры → Игровой хаб → Aion 2».",ephemeral=True)
        return False

    async def news(self, interaction, kind=None):
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            items,stale = await service.get_news()
        except RuntimeError as exc:
            await interaction.followup.send(str(exc),ephemeral=True)
            return
        selected = [item for item in items if kind is None or item["kind"] == kind][:5]
        embed = discord.Embed(title={None:"Aion 2 — официальные новости Global","maintenance":"Aion 2 — объявления о техработах","event":"Aion 2 — объявления о событиях"}[kind],color=discord.Color.blue())
        for item in selected:
            embed.add_field(name=item["title"][:200],value=f"<t:{item['timestamp']}:f>\n{safe(item['text'][:700])}\n[Полное объявление]({item['url']})",inline=False)
        if not selected:
            embed.description = "В последних 30 официальных публикациях подходящих объявлений нет."
        embed.set_footer(text=("Кэш: обновить источник сейчас не удалось. " if stale else "")+"Публикации Global. Это объявления, а не подтверждение текущего состояния серверов. Время отображается Discord локально.")
        await interaction.followup.send(embed=embed,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Новости Global",emoji="📰",row=0)
    async def news_button(self, interaction, button):
        await self.news(interaction)

    @discord.ui.button(label="Таймеры EU",emoji="⏱️",row=4)
    async def timers(self,interaction,button):
        await interaction.response.send_message(embed=timer_embed(),view=TimerView(interaction.user.id),ephemeral=True)

    @discord.ui.button(label="Техработы Global",emoji="🔧",row=0)
    async def maintenance(self, interaction, button):
        await self.news(interaction,"maintenance")

    @discord.ui.button(label="События Global",emoji="🎁",row=0)
    async def events_news(self, interaction, button):
        await self.news(interaction,"event")

    @discord.ui.button(label="Персонажи / API",emoji="👤",row=1)
    async def profile(self, interaction, button):
        await open_character_hub(interaction)

    @discord.ui.button(label="Ручная карточка",row=4)
    async def manual_profile(self,interaction,button):
        await interaction.response.send_modal(MemberModal())

    @discord.ui.button(label="Состав",emoji="👥",row=1)
    async def roster(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        members = await asyncio.to_thread(store.list_members,interaction.guild_id)
        linked = await asyncio.to_thread(aion2_character_store.roster,[m.id for m in interaction.guild.members if not m.bot])
        linked_users = {m['user_id'] for m in linked}
        lines = [f"<@{m['user_id']}> · **{safe(m['name'])}** · EU / сервер {m['server_id']} · привязка API" for m in linked]
        lines += [f"<@{m['user_id']}> · **{safe(m['nickname'])}** · {safe(m['class_name'])} · {service.REGIONS.get(m['region'],m['region'])} / {safe(m['server'])}" for m in members if m['user_id'] not in linked_users]
        await interaction.followup.send(embed=discord.Embed(title="Aion 2 — состав сервера",description=("\n".join(lines)[:3900] or "Пока нет карточек. Добавь свою кнопкой «Мой персонаж».")),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Удалить ручную карточку",row=1)
    async def forget(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await asyncio.to_thread(store.delete_member,interaction.guild_id,interaction.user.id)
        await interaction.followup.send("✅ Твоя карточка на этом сервере удалена.",ephemeral=True)

    @discord.ui.button(label="Расписание группы",emoji="📅",row=2)
    async def schedule(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        events = await asyncio.to_thread(store.list_events,interaction.guild_id,datetime.now(timezone.utc).isoformat())
        lines = []
        for event_id,title,starts in events:
            when = datetime.fromisoformat(starts)
            lines.append(f"**#{event_id} {safe(title)}** — {when.astimezone(service.MSK):%d.%m %H:%M} МСК · <t:{int(when.timestamp())}:R>")
        await interaction.followup.send(embed=discord.Embed(title="Aion 2 — расписание вашей группы",description="\n".join(lines)[:3900] or "Будущих событий нет. Управляющий сервером может добавить рейд или сбор группы.").set_footer(text="События добавляются администрацией сервера. Это не официальный таймер игры."),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Добавить событие",row=2)
    async def add(self, interaction, button):
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("Нужно право «Управление сервером».",ephemeral=True)
            return
        await interaction.response.send_modal(EventModal())

    @discord.ui.button(label="Удалить событие",row=2)
    async def remove(self, interaction, button):
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("Нужно право «Управление сервером».",ephemeral=True)
            return
        await interaction.response.send_modal(DeleteEventModal())


class Aion2(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self._notice_recovered = set()

    async def cog_load(self):
        self.timer_posts.start()

    def cog_unload(self):
        self.timer_posts.cancel()

    async def _recent_post(self,channel,marker):
        async for message in channel.history(limit=30):
            if message.author.id == self.bot.user.id and any(
                (embed.footer.text or "").endswith(marker) for embed in message.embeds
            ):
                return message
        return None

    async def _cleanup_notices(self,guild_id,channel,state,now):
        pending = {int(item["id"]):item for item in state.get("pending_notices",[])}
        # Recover old notifications and a send-before-state-save crash after restart.
        if channel.id not in self._notice_recovered:
            async for message in channel.history(limit=None):
                if message.author.id != self.bot.user.id:
                    continue
                for embed in message.embeds:
                    marker = (embed.footer.text or "").split("\n")[-1]
                    if not marker.startswith("AION2-EU notice "):
                        continue
                    try:
                        starts = datetime.fromisoformat(marker.removeprefix("AION2-EU notice "))
                        expires = aion2_timers.notice_expires_at(starts)
                    except ValueError:
                        continue
                    pending[message.id] = {"id":message.id,"expires_at":expires.isoformat()}
            self._notice_recovered.add(channel.id)
        for message_id,item in list(pending.items()):
            if datetime.fromisoformat(item["expires_at"]) > now:
                continue
            try:
                message = await channel.fetch_message(message_id)
                if message.author.id == self.bot.user.id:
                    await message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.warning("Aion2 notice deletion failed channel=%s message=%s",channel.id,message_id)
                continue
            del pending[message_id]
        updated = list(pending.values())
        if updated != state.get("pending_notices",[]):
            state["pending_notices"] = updated
            await asyncio.to_thread(settings_store.set_feature_runtime_state,guild_id,TIMERS_FEATURE,state)

    async def _publish_timers(self,guild,now):
        policy = await asyncio.to_thread(settings_store.get_feature_policy,guild.id,TIMERS_FEATURE)
        if not policy.output_channel_id:
            return
        channel = guild.get_channel(policy.output_channel_id)
        if not isinstance(channel,discord.TextChannel) or guild.me is None:
            return
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.read_message_history):
            return
        state = await asyncio.to_thread(settings_store.get_feature_runtime_state,guild.id,TIMERS_FEATURE)
        if state.get("channel_id") != channel.id:
            state = {"channel_id":channel.id,"message_id":None,"dashboard_key":None,"last_notice_key":None,"pending_notices":[]}
        await self._cleanup_notices(guild.id,channel,state,now)
        if not policy.enabled or not (perms.send_messages and perms.embed_links):
            return
        data = aion2_timers.snapshot(now)
        show_shugo = has_players(channel)
        dashboard_key = data["shugo"].isoformat()+f":shugo={int(show_shugo)}"
        if state.get("dashboard_key") != dashboard_key:
            message = None
            if state.get("message_id"):
                try:
                    message = await channel.fetch_message(int(state["message_id"]))
                    if message.author.id != self.bot.user.id:
                        message = None
                except discord.NotFound:
                    pass
            if message is None:
                message = await self._recent_post(channel,DASHBOARD_MARKER)
            embed = timer_embed(now,show_shugo=show_shugo)
            embed.set_footer(text=embed.footer.text+"\n"+DASHBOARD_MARKER)
            if message:
                await message.edit(embed=embed)
            else:
                message = await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
            if not message.pinned and perms.manage_messages:
                try:
                    await message.pin(reason="Окно таймеров Aion 2 по просьбе владельца сервера")
                except discord.HTTPException:
                    log.warning("Aion2 dashboard pin failed guild=%s channel=%s",guild.id,channel.id)
            state.update(message_id=message.id,dashboard_key=dashboard_key)
            await asyncio.to_thread(settings_store.set_feature_runtime_state,guild.id,TIMERS_FEATURE,state)
        notice = aion2_timers.due_notice(now)
        if not notice:
            return
        names = [name for name in notice["names"] if show_shugo or name != "🎪 Фестиваль шуго"]
        if state.get("last_notice_key") == notice["key"] and state.get("last_notice_names") == names:
            return
        # Do not query channel history every 15 seconds for a suppressed Shugo-only event.
        if not names and state.get("last_notice_key") != notice["key"]:
            return
        marker = "AION2-EU notice " + notice["key"]
        previous = await self._recent_post(channel,marker)
        pending = {int(item["id"]):item for item in state.get("pending_notices",[])}
        if not names:
            if previous is not None:
                await previous.delete()
                pending.pop(previous.id,None)
        else:
            starts = notice["starts_at"]
            embed = discord.Embed(title="Aion 2 — скоро начало",description="\n".join(names)+f"\n\n**{starts.astimezone(service.MSK):%H:%M} МСК** · <t:{int(starts.timestamp())}:R>",color=discord.Color.blue())
            embed.set_footer(text="Расчётное расписание EU. Техработы могут изменить время.\n"+marker)
            if previous is None:
                previous = await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
            else:
                await previous.edit(embed=embed,allowed_mentions=discord.AllowedMentions.none())
            pending[previous.id] = {"id":previous.id,"expires_at":aion2_timers.notice_expires_at(notice["starts_at"]).isoformat()}
        state["pending_notices"] = list(pending.values())
        state["last_notice_key"] = notice["key"]
        state["last_notice_names"] = names
        await asyncio.to_thread(settings_store.set_feature_runtime_state,guild.id,TIMERS_FEATURE,state)

    @tasks.loop(seconds=15)
    async def timer_posts(self):
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            try:
                await self._publish_timers(guild,now)
            except Exception:
                log.exception("Aion2 timer delivery failed guild=%s",guild.id)

    @timer_posts.before_loop
    async def before_timer_posts(self):
        await self.bot.wait_until_ready()

    async def open_hub(self,interaction):
        if not interaction.guild_id:
            await interaction.response.send_message("Раздел Aion 2 доступен на сервере.",ephemeral=True)
            return
        await interaction.response.send_message(
            "**Aion 2**\nОфициальные новости Global, техработы и события; карточки игроков Global/KR/TW; расписание вашей группы по МСК.\nПоиск и характеристики персонажей доступны на официальном сайте. Таймеры публикуются только в специально настроенный администрацией канал.",
            view=Aion2View(interaction.user.id),ephemeral=True)


async def setup(bot):
    await bot.add_cog(Aion2(bot))
