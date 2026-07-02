import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from datetime import datetime

from core.paths import BIRTHDAYS_DB
from core.settings_store import get_feature_policy, has_feature_setting, set_feature_channel

DB_PATH = BIRTHDAYS_DB
FEATURE_BIRTHDAY = "birthday"

def _ensure_table():
    """Гарантируем наличие таблицы дней рождения (на случай чистой установки)."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                birthday TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS birthday_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER
            )
        """)
        conn.commit()


def _birthday_channel_id(guild_id: int) -> int | None:
    policy = get_feature_policy(guild_id, FEATURE_BIRTHDAY)
    if policy.output_channel_id:
        return int(policy.output_channel_id)
    if has_feature_setting(guild_id, FEATURE_BIRTHDAY):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT channel_id FROM birthday_config WHERE guild_id=?", (guild_id,)).fetchone()
    return int(row[0]) if row and row[0] else None

class Birthday(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        _ensure_table()  # <-- добавлено


    def set_birthday(self, user_id: int, date_str: str):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("REPLACE INTO birthdays (user_id, birthday) VALUES (?, ?)", (user_id, date_str))
            conn.commit()

    def remove_birthday(self, user_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM birthdays WHERE user_id = ?", (user_id,))
            conn.commit()

    @app_commands.command(name="др", description="Установить свой день рождения")
    @app_commands.describe(дата="Введите дату в формате ДД.ММ (например, 20.04)")
    async def др(self, interaction: discord.Interaction, дата: str):
        try:
            datetime.strptime(f"{дата}.2020", "%d.%m.%Y")
            self.set_birthday(interaction.user.id, дата)
            await interaction.response.send_message(f"✅ День рождения установлен: {дата}")
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат даты. Используй: ДД.ММ")

    @app_commands.command(name="д-р", description="Удалить свой день рождения")
    async def д_р(self, interaction: discord.Interaction):
        self.remove_birthday(interaction.user.id)
        await interaction.response.send_message("✅ День рождения удалён.")

    @app_commands.command(name="др_ад", description="(Админ) Установить день рождения другому пользователю")
    @app_commands.describe(
        пользователь="Пользователь, которому установить день рождения",
        дата="Введите дату в формате ИМЯ ПОЛЬЗОВАТЕЛЯ ДД.ММ"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def др_ад(self, interaction: discord.Interaction, пользователь: discord.Member, дата: str):
        try:
            datetime.strptime(f"{дата}.2020", "%d.%m.%Y")
            self.set_birthday(пользователь.id, дата)
            await interaction.response.send_message(f"✅ Установлен день рождения {пользователь.mention}: {дата}")
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат даты. Используй: ДД.ММ")

    @app_commands.command(name="др_канал", description="(Админ) Настроить канал поздравлений с днем рождения")
    @app_commands.describe(канал="Канал, куда бот будет отправлять ежедневные поздравления")
    @app_commands.checks.has_permissions(administrator=True)
    async def др_канал(self, interaction: discord.Interaction, канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO birthday_config(guild_id, channel_id)
                VALUES(?,?)
                ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id
                """,
                (interaction.guild.id, канал.id),
            )
            conn.commit()
        set_feature_channel(interaction.guild.id, FEATURE_BIRTHDAY, канал.id, "output", "Discord command")
        await interaction.response.send_message(f"✅ Поздравления с ДР будут отправляться в {канал.mention}.", ephemeral=True)

    @app_commands.command(name="д-р_ад", description="(Админ) Удалить день рождения выбранного пользователя")
    @app_commands.describe(пользователь="Пользователь, у которого удалить день рождения")
    @app_commands.checks.has_permissions(administrator=True)
    async def д_р_ад(self, interaction: discord.Interaction, пользователь: discord.Member):
        self.remove_birthday(пользователь.id)
        await interaction.response.send_message(f"✅ День рождения {пользователь.mention} удалён.")

    @app_commands.command(name="все_др", description="Показать все установленные дни рождения")
    async def все_др(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, birthday FROM birthdays")
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message("❌ Ни один пользователь не установил дату рождения.")
            return

        lines = []
        for user_id, birthday in rows:
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member else f"<@{user_id}> (не на сервере)"
            lines.append(f"**{name}** — `{birthday}`")

        message = "\n".join(lines)
        await interaction.response.send_message(f"📅 Все дни рождения:\n{message}")

    @app_commands.command(name="когда_др", description="Показать дату дня рождения пользователя (или вашу, если не указано)")
    @app_commands.describe(пользователь="Пользователь, чью дату рождения хотите узнать")
    async def когда_др(self, interaction: discord.Interaction, пользователь: discord.Member = None):
        target = пользователь or interaction.user

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT birthday FROM birthdays WHERE user_id = ?", (target.id,))
            row = cur.fetchone()

        if row:
            if пользователь:
                await interaction.response.send_message(f"📅 День рождения {target.mention}: `{row[0]}`")
            else:
                await interaction.response.send_message(f"📅 Ваш день рождения: `{row[0]}`")
        else:
            if пользователь:
                await interaction.response.send_message(f"❌ Пользователь {target.mention} не установил дату рождения.")
            else:
                await interaction.response.send_message("❌ Вы ещё не установили день рождения.")

async def setup(bot):
    await bot.add_cog(Birthday(bot))
