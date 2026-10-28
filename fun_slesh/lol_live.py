"""Opt-in Discord UI and active-match delivery; never reads the local game client."""
import logging

import discord
from discord.ext import tasks

from core import lol_live_store as store, settings_store
from core.lol_live_service import LiveService

log = logging.getLogger(__name__)


def game_embed(game_key, rows):
    embed = discord.Embed(title='LoL — матч начался', description='Ранги соло/дуо обеих команд. Это ранг, не MMR.', color=0x3498DB)
    for team in sorted({r['team'] for r in rows}):
        lines = [f"**{discord.utils.escape_markdown(r['name'])}** — {r['rank']}" for r in rows if r['team'] == team]
        # Split long teams without exceeding Discord's field limit.
        chunk = ''
        for line in lines:
            if len(chunk) + len(line) > 1000:
                embed.add_field(name=f'Команда {team}', value=chunk, inline=False)
                chunk = ''
            chunk += line + '\n'
        if chunk:
            embed.add_field(name={100:'Синяя команда',200:'Красная команда'}.get(team,f'Команда {team}'), value=chunk, inline=False)
    embed.set_footer(text=f'LOL-LIVE {game_key}')
    return embed


class LiveRanks:
    def __init__(self, bot, key):
        self.bot = bot
        self.service = LiveService(key)

    @tasks.loop(seconds=30)
    async def loop(self):
        if not self.service.ready():
            return
        for guild in self.bot.guilds:
            try:
                await self.tick(guild)
            except Exception as exc:
                # No API response bodies, credentials or public error messages.
                log.warning('LoL live poll failed: %s', type(exc).__name__)

    @loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    async def tick(self, guild):
        policy = settings_store.get_feature_policy(guild.id, store.FEATURE)
        channel = guild.get_channel(policy.output_channel_id) if policy.output_channel_id else None
        if not policy.enabled or channel is None or not self.service.ready():
            return
        perms = channel.permissions_for(guild.me)
        if not all((perms.view_channel, perms.send_messages, perms.embed_links, perms.read_message_history)):
            return
        members = [m.id for m in guild.members if not m.bot and channel.permissions_for(m).view_channel]
        for account in store.watched_accounts(guild.id, members):
            if not self.service.ready():
                return
            game = await self.service.active_game(account)
            if not game or int(game.get('gameId',0)) <= 0:
                continue
            key = f"{account['platform'].upper()}:{int(game['gameId'])}"
            if store.delivered(guild.id, key):
                continue
            # Recover a delivery after a send-before-commit interruption.
            previous = None
            async for message in channel.history(limit=100):
                if message.author.id == self.bot.user.id and any(e.footer.text == f'LOL-LIVE {key}' for e in message.embeds):
                    previous = message
                    break
            if previous is None:
                rows = await self.service.participants(account['platform'], game)
                previous = await channel.send(embed=game_embed(key, rows), allowed_mentions=discord.AllowedMentions.none())
            store.remember_delivery(guild.id, key, channel.id, previous.id)

    async def hub(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message('Открой меню на сервере.', ephemeral=True)
            return
        policy = settings_store.get_feature_policy(interaction.guild.id, store.FEATURE)
        channel = interaction.guild.get_channel(policy.output_channel_id) if policy.output_channel_id else None
        status = 'включено' if store.enabled(interaction.guild.id, interaction.user.id) else 'выключено'
        api = self.service.last_error or ('ключ настроен' if self.service.key else 'ключ Riot API не настроен')
        await interaction.response.send_message(
            f"Ранги моей игры: **{status}**. Канал: {channel.mention if channel else 'не настроен'}.\n"
            f"{api}.\nПосле начала матча — одно сообщение обеих команд, без пингов. Не на этапе принятия игры.\n"
            'Включение действует только для твоего привязанного Riot ID.',
            view=LiveView(self, interaction.user.id), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class LiveView(discord.ui.View):
    def __init__(self, live, owner):
        super().__init__(timeout=180)
        self.live, self.owner = live, owner

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner and interaction.guild is not None:
            return True
        await interaction.response.send_message('Это личное меню другого участника.', ephemeral=True)
        return False

    @discord.ui.button(label='Включить для меня', style=discord.ButtonStyle.success)
    async def enable(self, interaction, button):
        policy = settings_store.get_feature_policy(interaction.guild.id, store.FEATURE)
        channel = interaction.guild.get_channel(policy.output_channel_id) if policy.output_channel_id else None
        if not policy.enabled or channel is None or not channel.permissions_for(interaction.user).view_channel:
            await interaction.response.send_message('Канал отправки недоступен или функция отключена админом.', ephemeral=True)
            return
        try:
            store.set_watch(interaction.guild.id, interaction.user.id, True)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message('Включено для твоего Riot ID.' + ('' if self.live.service.ready() else ' Отправка ждёт рабочего ключа Riot API.'), ephemeral=True)

    @discord.ui.button(label='Выключить', style=discord.ButtonStyle.secondary)
    async def disable(self, interaction, button):
        store.set_watch(interaction.guild.id, interaction.user.id, False)
        await interaction.response.send_message('Автоматические ранги твоих игр выключены.', ephemeral=True)
