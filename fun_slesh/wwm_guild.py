# -*- coding: utf-8 -*-
"""WWM guild onboarding and member identification."""

import re
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.paths import SOCIAL_DB
from core.settings_store import get_feature_policy, has_feature_setting, set_feature_channel, set_feature_payload

DB_PATH = SOCIAL_DB
FEATURE_WWM_GUILD = "wwm_guild"
UTC = timezone.utc
NICK_RE = re.compile(r"^[\wА-Яа-яЁё .'\-\[\]]{2,32}$", re.UNICODE)


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wwm_profiles (
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                game_nick     TEXT    NOT NULL,
                nick_synced   INTEGER NOT NULL DEFAULT 0,
                character_card TEXT    NOT NULL DEFAULT '',
                character_updated_at TEXT NOT NULL DEFAULT '',
                created_at    TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS wwm_config (
                guild_id           INTEGER PRIMARY KEY,
                welcome_channel_id INTEGER,
                reception_channel_id INTEGER,
                auto_nickname      INTEGER NOT NULL DEFAULT 1,
                nickname_template  TEXT    NOT NULL DEFAULT '{game_nick}'
            );
            """
        )
        for statement in (
            "ALTER TABLE wwm_profiles ADD COLUMN character_card TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE wwm_profiles ADD COLUMN character_updated_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE wwm_config ADD COLUMN reception_channel_id INTEGER",
        ):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        conn.commit()


def _legacy_config(guild_id: int) -> tuple[int | None, int | None, bool, str]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO wwm_config(guild_id) VALUES(?)", (guild_id,))
        row = conn.execute(
            "SELECT welcome_channel_id, reception_channel_id, auto_nickname, nickname_template FROM wwm_config WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
        conn.commit()
    return (row[0], row[1], bool(row[2]), row[3] or "{game_nick}")


def _config(guild_id: int) -> tuple[int | None, int | None, bool, str]:
    welcome_channel_id, reception_channel_id, auto_nickname, nickname_template = _legacy_config(guild_id)
    policy = get_feature_policy(guild_id, FEATURE_WWM_GUILD)
    payload = policy.extra or {}
    configured = has_feature_setting(guild_id, FEATURE_WWM_GUILD) or policy.output_channel_id is not None or bool(payload)

    if configured:
        welcome_channel_id = None
        reception_channel_id = None
    if policy.output_channel_id:
        welcome_channel_id = int(policy.output_channel_id)
    try:
        reception_channel_id = int(payload.get("reception_channel_id") or reception_channel_id or 0) or None
    except (TypeError, ValueError):
        pass
    if "auto_nickname" in payload:
        auto_nickname = bool(payload.get("auto_nickname"))
    if payload.get("nickname_template"):
        nickname_template = str(payload["nickname_template"])[:80]
    return welcome_channel_id, reception_channel_id, auto_nickname, nickname_template or "{game_nick}"


def _steam_id(user_id: int) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT steam_id FROM steam_profiles WHERE user_id=?", (user_id,)).fetchone()
    return str(row[0]) if row else None


def _profile(guild_id: int, user_id: int) -> tuple[str, int] | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT game_nick, nick_synced FROM wwm_profiles WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
    return (str(row[0]), int(row[1])) if row else None


def _save_profile(guild_id: int, user_id: int, game_nick: str, nick_synced: bool):
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO wwm_profiles(guild_id, user_id, game_nick, nick_synced, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                game_nick=excluded.game_nick,
                nick_synced=excluded.nick_synced,
                updated_at=excluded.updated_at
            """,
            (guild_id, user_id, game_nick, int(nick_synced), now, now),
        )
        conn.commit()


def _format_nickname(template: str, game_nick: str, member: discord.Member) -> str:
    value = (template or "{game_nick}").format(
        game_nick=game_nick,
        discord_name=member.name,
        display_name=member.display_name,
    )
    return value.strip()[:32] or game_nick[:32]


def _valid_game_nick(value: str) -> bool:
    return bool(NICK_RE.match((value or "").strip()))


async def _sync_member_nick(member: discord.Member, game_nick: str, template: str) -> tuple[bool, str | None]:
    wanted = _format_nickname(template, game_nick, member)
    if member.display_name == wanted or member.nick == wanted:
        return True, None
    try:
        await member.edit(nick=wanted, reason="WWM guild game nickname registration")
        return True, None
    except discord.Forbidden:
        return False, "У бота нет прав изменить серверный ник этого участника."
    except discord.HTTPException:
        return False, "Discord не принял изменение ника. Ник сохранен в карточке."


class WWMNickModal(discord.ui.Modal, title="Игровой ник WWM"):
    game_nick = discord.ui.TextInput(
        label="Ник в Where Winds Meet",
        placeholder="Например: ShunVIP",
        min_length=2,
        max_length=32,
    )

    def __init__(self, cog: "WWMGuild"):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.register_nick(interaction, str(self.game_nick))


class WWMSteamModal(discord.ui.Modal, title="Привязать Steam"):
    profile = discord.ui.TextInput(
        label="Steam ссылка, vanity или SteamID64",
        placeholder="https://steamcommunity.com/id/...",
        min_length=2,
        max_length=120,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        steam = self.bot.get_cog("Steam")
        if not steam or not hasattr(steam, "steam_привязать"):
            await interaction.response.send_message("❌ Steam-модуль сейчас недоступен.", ephemeral=True)
            return
        await steam.steam_привязать(interaction, str(self.profile))


class WWMWelcomeView(discord.ui.View):
    def __init__(self, cog: "WWMGuild"):
        super().__init__(timeout=7 * 24 * 3600)
        self.cog = cog

    @discord.ui.button(label="Я игрок WWM", emoji="🌿", style=discord.ButtonStyle.primary)
    async def wwm_player(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(
            "Отлично. Сначала укажи имя персонажа WWM, потом можешь привязать Steam.",
            view=WWMPlayerSetupView(self.cog),
            ephemeral=True,
        )

    @discord.ui.button(label="Я не из WWM", emoji="🚪", style=discord.ButtonStyle.secondary)
    async def not_wwm_player(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.cog.open_reception(interaction)


class WWMPlayerSetupView(discord.ui.View):
    def __init__(self, cog: "WWMGuild"):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="Ввести имя персонажа", emoji="📝", style=discord.ButtonStyle.primary)
    async def set_nick(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(WWMNickModal(self.cog))

    @discord.ui.button(label="Привязать Steam", emoji="🔗", style=discord.ButtonStyle.secondary)
    async def link_steam(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(WWMSteamModal(self.cog.bot))

    @discord.ui.button(label="Моя карточка", emoji="🎴", style=discord.ButtonStyle.secondary)
    async def my_card(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.cog.send_card(interaction, interaction.user)


class WWMGuild(commands.Cog):
    wwm_group = app_commands.Group(
        name="wwm",
        description="WWM-гильдия: игровой ник, Steam и карточки участников",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()

    def _welcome_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id, _, _, _ = _config(guild.id)
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel
        if isinstance(guild.system_channel, discord.TextChannel):
            return guild.system_channel
        return discord.utils.find(
            lambda ch: isinstance(ch, discord.TextChannel)
            and ch.permissions_for(guild.me).send_messages,
            guild.text_channels,
        )

    def _welcome_embed(self, member: discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title="Ламповый чай",
            description="Салам, ты попал на сервер гильдии Ламповый чай, если ты пришел как игрок Where Winds Meet нажми соответствующую кнопку",
            color=discord.Color.from_rgb(82, 180, 132),
        )
        embed.set_footer(text="Если ты не из WWM, бот позовет администрацию в настроенном канале.")
        return embed

    def _reception_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        _, channel_id, _, _ = _config(guild.id)
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel
        return self._welcome_channel(guild)

    async def open_reception(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Это работает только на сервере.", ephemeral=True)
            return

        channel = self._reception_channel(interaction.guild)
        if not channel:
            await interaction.response.send_message("❌ Канал для обращения к администрации не настроен и не найден.", ephemeral=True)
            return

        admin_mentions = " ".join(role.mention for role in interaction.guild.roles if role.permissions.administrator)
        await channel.send(
            f"{admin_mentions}\n{interaction.user.mention} нажал **Я не из WWM**.\n"
            "Нужно вручную разобраться, кто это и какой доступ ему выдать.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await interaction.response.send_message(
            f"✅ Администрация получит уведомление в {channel.mention}. Отдельный канал не создавался.",
            ephemeral=True,
        )

    async def register_nick(self, interaction: discord.Interaction, game_nick: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Это работает только на сервере.", ephemeral=True)
            return
        game_nick = game_nick.strip()
        if not _valid_game_nick(game_nick):
            await interaction.response.send_message(
                "❌ Ник должен быть 2-32 символа. Можно буквы, цифры, пробел, дефис, точку, апостроф и квадратные скобки.",
                ephemeral=True,
            )
            return

        _, _, auto_nickname, template = _config(interaction.guild.id)
        synced = False
        note = None
        if auto_nickname:
            synced, note = await _sync_member_nick(interaction.user, game_nick, template)
        _save_profile(interaction.guild.id, interaction.user.id, game_nick, synced)

        steam_linked = "да" if _steam_id(interaction.user.id) else "нет"
        text = f"✅ Игровой ник WWM сохранен: **{game_nick}**\nSteam привязан: **{steam_linked}**"
        if note:
            text += f"\n\n⚠️ {note}"
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def send_card(self, interaction: discord.Interaction, target: discord.Member | discord.User | None = None):
        if not interaction.guild:
            await interaction.response.send_message("❌ Это работает только на сервере.", ephemeral=True)
            return
        target = target or interaction.user
        profile = _profile(interaction.guild.id, target.id)
        steam_id = _steam_id(target.id)

        embed = discord.Embed(
            title=f"WWM-карточка: {getattr(target, 'display_name', target.name)}",
            color=discord.Color.from_rgb(82, 180, 132),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Discord", value=target.mention, inline=True)
        embed.add_field(name="Игровой ник WWM", value=f"`{profile[0]}`" if profile else "не указан", inline=True)
        embed.add_field(name="Steam", value=f"[профиль](https://steamcommunity.com/profiles/{steam_id})" if steam_id else "не привязан", inline=True)
        if isinstance(target, discord.Member):
            embed.add_field(name="Серверный ник", value=target.display_name, inline=True)
        if profile and not profile[1]:
            embed.set_footer(text="Ник сохранен в базе, но серверный ник Discord не был изменен.")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        channel = self._welcome_channel(member.guild)
        if not channel:
            return
        try:
            await channel.send(
                content=member.mention,
                embed=self._welcome_embed(member),
                view=WWMWelcomeView(self),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            pass

    @wwm_group.command(name="ник", description="Указать обязательный игровой ник WWM")
    @app_commands.describe(игровой_ник="Твой ник в Where Winds Meet")
    async def wwm_ник(self, interaction: discord.Interaction, игровой_ник: str):
        await self.register_nick(interaction, игровой_ник)

    @wwm_group.command(name="карточка", description="Показать WWM-карточку участника")
    @app_commands.describe(пользователь="Чью карточку показать")
    async def wwm_карточка(self, interaction: discord.Interaction, пользователь: discord.Member | None = None):
        await self.send_card(interaction, пользователь or interaction.user)

    @wwm_group.command(name="состав", description="Список участников WWM с указанным игровым ником")
    async def wwm_состав(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Это работает только на сервере.", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT user_id, game_nick FROM wwm_profiles
                WHERE guild_id=?
                ORDER BY lower(game_nick)
                LIMIT 50
                """,
                (interaction.guild.id,),
            ).fetchall()
        if not rows:
            await interaction.response.send_message("Пока никто не указал WWM-ник.", ephemeral=True)
            return
        lines = []
        for user_id, game_nick in rows:
            member = interaction.guild.get_member(int(user_id))
            mention = member.mention if member else f"`{user_id}`"
            steam_mark = " · Steam" if _steam_id(int(user_id)) else ""
            lines.append(f"**{game_nick}** — {mention}{steam_mark}")
        embed = discord.Embed(
            title="Состав WWM-гильдии",
            description="\n".join(lines),
            color=discord.Color.from_rgb(82, 180, 132),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @wwm_group.command(name="без_ника", description="(Админ) Кто еще не указал WWM-ник")
    @app_commands.checks.has_permissions(administrator=True)
    async def wwm_без_ника(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Это работает только на сервере.", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            registered = {
                int(row[0])
                for row in conn.execute("SELECT user_id FROM wwm_profiles WHERE guild_id=?", (interaction.guild.id,))
            }
        missing = [m for m in interaction.guild.members if not m.bot and m.id not in registered]
        if not missing:
            await interaction.response.send_message("✅ У всех участников есть WWM-ник.", ephemeral=True)
            return
        lines = [m.mention for m in missing[:50]]
        suffix = f"\n...и еще {len(missing) - 50}" if len(missing) > 50 else ""
        await interaction.response.send_message(
            "Участники без WWM-ника:\n" + "\n".join(lines) + suffix,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @wwm_group.command(name="канал", description="(Админ) Настроить канал WWM-приветствия")
    @app_commands.checks.has_permissions(administrator=True)
    async def wwm_канал(self, interaction: discord.Interaction, канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO wwm_config(guild_id, welcome_channel_id)
                VALUES(?,?)
                ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id=excluded.welcome_channel_id
                """,
                (interaction.guild.id, канал.id),
            )
            conn.commit()
        set_feature_channel(interaction.guild.id, FEATURE_WWM_GUILD, канал.id, "output", "Discord command")
        await interaction.response.send_message(f"✅ WWM-приветствие будет отправляться в {канал.mention}.", ephemeral=True)

    @wwm_group.command(name="приемная", description="(Админ) Настроить канал обращений не-WWM участников")
    @app_commands.checks.has_permissions(administrator=True)
    async def wwm_приемная(self, interaction: discord.Interaction, канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO wwm_config(guild_id, reception_channel_id)
                VALUES(?,?)
                ON CONFLICT(guild_id) DO UPDATE SET reception_channel_id=excluded.reception_channel_id
                """,
                (interaction.guild.id, канал.id),
            )
            conn.commit()
        set_feature_payload(interaction.guild.id, FEATURE_WWM_GUILD, {"reception_channel_id": канал.id})
        await interaction.response.send_message(f"✅ Обращения не-WWM участников будут уходить в {канал.mention}.", ephemeral=True)

    @wwm_group.command(name="ники", description="(Админ) Настроить авто-изменение серверного ника")
    @app_commands.describe(
        включить="Менять серверный ник на игровой ник WWM",
        шаблон="Например: {game_nick} или WWM | {game_nick}",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def wwm_ники(self, interaction: discord.Interaction, включить: bool, шаблон: str | None = None):
        template = (шаблон or "{game_nick}").strip()[:80]
        if "{game_nick}" not in template:
            await interaction.response.send_message("❌ В шаблоне должен быть `{game_nick}`.", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO wwm_config(guild_id, auto_nickname, nickname_template)
                VALUES(?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    auto_nickname=excluded.auto_nickname,
                    nickname_template=excluded.nickname_template
                """,
                (interaction.guild.id, int(включить), template),
            )
            conn.commit()
        set_feature_payload(
            interaction.guild.id,
            FEATURE_WWM_GUILD,
            {"auto_nickname": bool(включить), "nickname_template": template},
        )
        await interaction.response.send_message(
            f"✅ Авто-ники: {'вкл' if включить else 'выкл'} · шаблон `{template}`",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WWMGuild(bot))
