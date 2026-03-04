# fun_slesh/achievements_engine.py
import os, sqlite3
import discord
from discord.ext import commands
from discord import app_commands
from typing import Callable, Awaitable
from utils.events_bus import subscribe
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))

def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS achievements (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                code       TEXT NOT NULL,
                title      TEXT NOT NULL,
                awarded_at TEXT NOT NULL,
                UNIQUE(user_id, code)
            )
        """)
        conn.commit()

def _award_if_absent(user_id: int, code: str, title: str) -> bool:
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO achievements (user_id, code, title, awarded_at) VALUES (?, ?, ?, ?)",
                (user_id, code, title, now)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

class AchievementsEngine(commands.Cog):
    """Подписывается на события и выдаёт ачивки, + даёт /ачивки."""
    def __init__(self, bot):
        self.bot = bot
        _ensure_tables()

        # Подписки на события
        subscribe("daily_claimed", self.on_daily_claimed)
        subscribe("game_win", self.on_game_win)
        subscribe("rep_given", self.on_rep_given)

    # ── Правила ────────────────────────────────────────────────────────────────
    async def on_daily_claimed(self, user_id: int, streak: int, amount: int):
        if _award_if_absent(user_id, "first_daily", "Первый дейлик"):
            print(f"[ach] + first_daily for {user_id}")
        for n, code in ((3, "streak_3"), (7, "streak_7"), (14, "streak_14"), (30, "streak_30")):
            if streak == n:
                title = f"Серия {n}"
                if _award_if_absent(user_id, code, title):
                    print(f"[ach] + {code} for {user_id}")

    async def on_game_win(self, user_id: int, game: str):
        if game == "rps":
            _award_if_absent(user_id, "first_win_rps", "Первая победа в КНБ")
        elif game == "guess":
            _award_if_absent(user_id, "first_win_guess", "Первая победа в угадайке")

    async def on_rep_given(self, user_id: int, given_by: int, date: str):
        # Ачивка за первую выданную репу — даём её дающему
        _award_if_absent(given_by, "first_rep_given", "Первая выданная репутация")

    # ── /ачивки ────────────────────────────────────────────────────────────────
    @app_commands.command(name="ачивки", description="Показать ваши ачивки")
    async def ачивки(self, interaction: discord.Interaction, пользователь: discord.Member | None = None):
        target = пользователь or interaction.user
        _ensure_tables()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT code, title, awarded_at FROM achievements WHERE user_id = ? ORDER BY awarded_at ASC", (target.id,))
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(f"😶 У {target.mention} пока нет ачивок.")
            return

        lines = []
        for code, title, ts in rows[:25]:
            when = ts.split("T")[0]
            lines.append(f"• **{title}** `({code})` — {when}")

        more = f"\n… и ещё {len(rows) - 25}" if len(rows) > 25 else ""
        emb = discord.Embed(
            title=f"🏆 Ачивки {target.display_name}",
            description="\n".join(lines) + more,
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=emb)

async def setup(bot):
    await bot.add_cog(AchievementsEngine(bot))
