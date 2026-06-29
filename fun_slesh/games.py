# -*- coding: utf-8 -*-
# fun_slesh/games.py
"""
Игры сервера:
  /кнб              — КНБ с ботом
  /кнб_дуэль        — PvP КНБ
  /кнб_ход          — ход в дуэли
  /кнб_отмена       — отмена дуэли
  /угадай           — угадай число
  /виселица         — соло виселица против бота
  /виселица_старт   — мультиплеер: загадать слово
  /виселица_буква   — угадать букву в мульти-игре
  /бж               — блэкджек против бота (со ставкой)
  /бж_дуэль         — блэкджек PvP (оба против бота, кто ближе к 21)
"""

import os, sqlite3, random, asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord import app_commands

from core.economy import add_coins, get_balance
from core.economy_profile import currency_amount
from utils.events_bus import emit

UTC     = timezone.utc
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
CHOICES = ("камень", "ножницы", "бумага")

# ── Слова для виселицы ────────────────────────────────────────────────────────
HANGMAN_WORDS = [
    "программист","дискорд","сервер","сообщество","администратор",
    "разработчик","компьютер","клавиатура","мышеловка","операция",
    "алгоритм","переменная","функция","библиотека","интерфейс",
    "константа","оператор","компилятор","интерпретатор","процессор",
    "принтер","монитор","наушники","микрофон","веб-камера",
    "кофеварка","холодильник","телевизор","телефон","планшет",
    "автомобиль","мотоцикл","велосипед","самолёт","вертолёт",
    "шоколадка","мороженое","пельмени","борщ","пицца",
    "кинотеатр","библиотека","стадион","аквариум","зоопарк",
    "абракадабра","хулиганство","безобразие","вдохновение","загадочный",
]

# ── Колода карт ────────────────────────────────────────────────────────────────
SUITS  = ["♠", "♥", "♦", "♣"]
RANKS  = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
VALUES = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,
          "J":10,"Q":10,"K":10,"A":11}

def _new_deck() -> list[str]:
    deck = [f"{r}{s}" for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck

def _card_value(card: str) -> int:
    rank = card[:-1]  # убираем масть
    return VALUES.get(rank, 0)

def _hand_total(hand: list[str]) -> int:
    total = sum(_card_value(c) for c in hand)
    aces  = sum(1 for c in hand if c[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces  -= 1
    return total

def _hand_str(hand: list[str]) -> str:
    return " ".join(hand)

# ── БД ────────────────────────────────────────────────────────────────────────
def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rps_duels (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                channel_id   INTEGER NOT NULL,
                initiator_id INTEGER NOT NULL,
                opponent_id  INTEGER NOT NULL,
                init_choice  TEXT,
                opp_choice   TEXT,
                status       TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rps_status  ON rps_duels(status);
            CREATE INDEX IF NOT EXISTS idx_rps_channel ON rps_duels(channel_id);

            -- Мультиплеер виселица
            CREATE TABLE IF NOT EXISTS hangman_games (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                host_id     INTEGER NOT NULL,
                word        TEXT    NOT NULL,
                guessed     TEXT    NOT NULL DEFAULT '',
                wrong       TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'active',
                created_at  TEXT    NOT NULL
            );
        """)

# ── КНБ helpers ───────────────────────────────────────────────────────────────
def _rps_result(user: str, bot_pick: str) -> int:
    if user == bot_pick: return 0
    return 1 if (user, bot_pick) in {("камень","ножницы"),
                                      ("ножницы","бумага"),
                                      ("бумага","камень")} else -1

def _money(user_id: int, amount: int) -> str:
    return currency_amount(user_id, amount)

def _balance_money(user_id: int) -> str:
    return currency_amount(user_id, get_balance(user_id))

# ── Виселица helpers ──────────────────────────────────────────────────────────
HANGMAN_STAGES = [
    "```\n  +---+\n  |   |\n      |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n  |   |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|   |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n /    |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n / \\  |\n      |\n=========```",
]
MAX_WRONG = len(HANGMAN_STAGES) - 1

def _mask_word(word: str, guessed: set[str]) -> str:
    return " ".join(c if c in guessed or c == "-" else r"\_" for c in word)

def _hangman_embed(word: str, guessed_str: str, wrong_str: str,
                   status: str = "active") -> discord.Embed:
    guessed = set(guessed_str)
    wrong   = list(wrong_str)
    stage   = HANGMAN_STAGES[min(len(wrong), MAX_WRONG)]
    mask    = _mask_word(word, guessed)

    if status == "win":
        color, title = discord.Color.green(), "🎉 Слово угадано!"
    elif status == "lose":
        color, title = discord.Color.red(), "💀 Игра окончена"
    else:
        color, title = discord.Color.blurple(), "🪢 Виселица"

    emb = discord.Embed(title=title, color=color)
    emb.add_field(name="Виселица",    value=stage,  inline=False)
    emb.add_field(name="Слово",       value=f"`{mask}`", inline=False)
    if wrong:
        emb.add_field(name=f"Ошибки ({len(wrong)}/{MAX_WRONG})",
                      value=" ".join(wrong), inline=False)
    if status == "lose":
        emb.add_field(name="Загаданное слово", value=f"**{word}**", inline=False)
    return emb


# ── Cog ───────────────────────────────────────────────────────────────────────
class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        _ensure_tables()
        # Активные соло-игры виселицы: user_id → {word, guessed, wrong}
        self._solo_hangman: dict[int, dict] = {}

    # ════════════════════════════════════════════════════════════
    #  КНБ
    # ════════════════════════════════════════════════════════════
    @app_commands.command(name="кнб", description="Камень-ножницы-бумага с ботом")
    @app_commands.describe(выбор="камень | ножницы | бумага")
    @app_commands.choices(выбор=[
        app_commands.Choice(name="камень",  value="камень"),
        app_commands.Choice(name="ножницы", value="ножницы"),
        app_commands.Choice(name="бумага",  value="бумага"),
    ])
    async def кнб(self, interaction: discord.Interaction, выбор: str):
        bot_pick = random.choice(CHOICES)
        res = _rps_result(выбор, bot_pick)
        if res > 0:
            new_bal = add_coins(interaction.user.id, 10, "game_win", {"game":"rps"})
            await emit("game_win", user_id=interaction.user.id, game="rps")
            txt   = f"✅ Победа! `{выбор}` vs `{bot_pick}`\n+{_money(interaction.user.id, 10)} → **{_money(interaction.user.id, new_bal)}**"
            color = discord.Color.green()
        elif res == 0:
            txt   = f"🤝 Ничья! Оба: `{выбор}`"
            color = discord.Color.blurple()
        else:
            txt   = f"❌ Проигрыш. `{выбор}` vs `{bot_pick}`"
            color = discord.Color.red()
        await interaction.response.send_message(
            embed=discord.Embed(title="✊✌️🖐 КНБ", description=txt, color=color))
        await emit("game_played", user_id=interaction.user.id,
                   guild_id=interaction.guild.id, game="rps")

    # ── КНБ дуэль ─────────────────────────────────────────────
    @app_commands.command(name="кнб_дуэль", description="PvP дуэль КНБ")
    @app_commands.describe(оппонент="Соперник", таймаут_мин="Минут до истечения (1-120)")
    async def кнб_дуэль(self, interaction: discord.Interaction,
                         оппонент: discord.Member,
                         таймаут_мин: app_commands.Range[int, 1, 120] = 15):
        if оппонент.bot or оппонент.id == interaction.user.id:
            await interaction.response.send_message("❌ Неверный соперник.", ephemeral=True)
            return
        emb = discord.Embed(
            title="⚔️ КНБ-дуэль",
            description=(
                f"{interaction.user.mention} вызывает {оппонент.mention}\n\n"
                f"{оппонент.mention}, нажми **Принять**, чтобы начать. "
                "После принятия оба игрока выбирают ход кнопками. "
                "Выбор каждого скрыт до финального результата."
            ),
            color=discord.Color.orange()
        )
        await interaction.response.send_message(
            content=оппонент.mention,
            embed=emb,
            view=RPSDuelInviteView(self, interaction.user, оппонент, таймаут_мин),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @app_commands.command(name="кнб_ход", description="Сделать скрытый ход в PvP дуэли")
    @app_commands.describe(дуэль="ID дуэли", выбор="Твой выбор")
    @app_commands.choices(выбор=[
        app_commands.Choice(name="камень",  value="камень"),
        app_commands.Choice(name="ножницы", value="ножницы"),
        app_commands.Choice(name="бумага",  value="бумага"),
    ])
    async def кнб_ход(self, interaction: discord.Interaction,
                       дуэль: int, выбор: str):
        now = datetime.now(UTC)
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id,guild_id,channel_id,initiator_id,opponent_id,"
                "init_choice,opp_choice,status,expires_at FROM rps_duels WHERE id=?",
                (дуэль,)
            ).fetchone()
        if not row:
            await interaction.response.send_message("❌ Дуэль не найдена.", ephemeral=True)
            return
        _id,guild_id,ch_id,init_id,opp_id,init_ch,opp_ch,status,expires = row
        if status != "open":
            await interaction.response.send_message("⛔ Дуэль уже завершена.", ephemeral=True)
            return
        if datetime.fromisoformat(expires) < now:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE rps_duels SET status='expired' WHERE id=?", (_id,))
            await interaction.response.send_message("⌛ Время истекло.", ephemeral=True)
            return
        if interaction.user.id not in (init_id, opp_id):
            await interaction.response.send_message("❌ Ты не участник.", ephemeral=True)
            return
        col   = "init_choice" if interaction.user.id == init_id else "opp_choice"
        cur_v = init_ch if col == "init_choice" else opp_ch
        if cur_v:
            await interaction.response.send_message("🔒 Ход уже сделан.", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(f"UPDATE rps_duels SET {col}=? WHERE id=?", (выбор, _id))
            row2 = conn.execute(
                "SELECT init_choice,opp_choice,status FROM rps_duels WHERE id=?",(_id,)
            ).fetchone()
        c_init, c_opp, c_st = row2
        await interaction.response.send_message(f"✅ Ход принят: **{выбор}**", ephemeral=True)
        if c_init and c_opp and c_st == "open":
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE rps_duels SET status='done' WHERE id=? AND status='open'",(_id,))
            res = _rps_result(c_init, c_opp)
            reward_txt = ""
            if res > 0:
                nb = add_coins(init_id, 20, "game_win", {"game":"rps_pvp"})
                await emit("game_win", user_id=init_id, game="rps")
                reward_txt = f"\n🏆 Победитель: <@{init_id}> +{_money(init_id, 20)} → **{_money(init_id, nb)}**"
            elif res < 0:
                nb = add_coins(opp_id, 20, "game_win", {"game":"rps_pvp"})
                await emit("game_win", user_id=opp_id, game="rps")
                reward_txt = f"\n🏆 Победитель: <@{opp_id}> +{_money(opp_id, 20)} → **{_money(opp_id, nb)}**"
            else:
                reward_txt = "\n🤝 Ничья!"
            emb = discord.Embed(
                title="⚔️ Итог КНБ дуэли",
                description=(f"<@{init_id}>: **{c_init}**\n"
                              f"<@{opp_id}>: **{c_opp}**{reward_txt}"),
                color=discord.Color.gold()
            )
            ch = self.bot.get_channel(ch_id)
            if ch:
                await ch.send(embed=emb)

    @app_commands.command(name="кнб_отмена", description="Отменить свою дуэль КНБ")
    @app_commands.describe(дуэль="ID дуэли")
    async def кнб_отмена(self, interaction: discord.Interaction, дуэль: int):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT initiator_id, status FROM rps_duels WHERE id=?", (дуэль,)
            ).fetchone()
        if not row:
            await interaction.response.send_message("❌ Не найдено.", ephemeral=True)
            return
        if row[1] != "open":
            await interaction.response.send_message("⛔ Уже завершена.", ephemeral=True)
            return
        if row[0] != interaction.user.id:
            await interaction.response.send_message("❌ Только создатель может отменить.", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE rps_duels SET status='cancelled' WHERE id=?", (дуэль,))
        await interaction.response.send_message(f"🛑 Дуэль #{дуэль} отменена.")

    # ════════════════════════════════════════════════════════════
    #  Угадай число
    # ════════════════════════════════════════════════════════════
    @app_commands.command(name="угадай", description="Угадай число и получи персональную валюту")
    @app_commands.describe(число="Твоя попытка", до="Максимальное число (по умолчанию 10)")
    async def угадай(self, interaction: discord.Interaction,
                      число: app_commands.Range[int, 1, 10000],
                      до: app_commands.Range[int, 2, 10000] = 10):
        if число > до:
            await interaction.response.send_message("❌ Число больше максимума.", ephemeral=True)
            return
        target = random.randint(1, до)
        if число == target:
            bonus = 40 if до >= 1000 else (20 if до >= 200 else (10 if до >= 50 else 0))
            delta = 10 + bonus
            nb    = add_coins(interaction.user.id, delta, "game_win", {"game":"guess"})
            await emit("game_win", user_id=interaction.user.id, game="guess")
            emb = discord.Embed(
                title="🎯 Угадал!",
                description=f"Число было **{target}** — совпало!\n+{_money(interaction.user.id, delta)} → **{_money(interaction.user.id, nb)}**",
                color=discord.Color.green())
        else:
            diff = abs(число - target)
            hint = "🔥 Горячо!" if diff <= 2 else ("♨️ Тепло" if diff <= 5 else "🧊 Холодно")
            emb = discord.Embed(
                title="🎯 Не угадал",
                description=f"Было **{target}**, ты назвал **{число}** — {hint}",
                color=discord.Color.red())
        await interaction.response.send_message(embed=emb)
        await emit("game_played", user_id=interaction.user.id,
                   guild_id=interaction.guild.id, game="guess")

    # ════════════════════════════════════════════════════════════
    #  ВИСЕЛИЦА — СОЛО
    # ════════════════════════════════════════════════════════════
    @app_commands.command(name="виселица", description="Соло виселица против бота")
    @app_commands.describe(слово="Оставь пустым — бот загадает сам")
    async def виселица(self, interaction: discord.Interaction,
                        слово: str = ""):
        uid = interaction.user.id
        if uid in self._solo_hangman:
            await interaction.response.send_message(
                "⚠️ У тебя уже есть активная игра! Заверши её или дождись конца.",
                ephemeral=True)
            return

        word = слово.strip().lower() if слово.strip() else random.choice(HANGMAN_WORDS)
        # Фильтруем — только кириллица и дефис
        import re
        if not re.match(r'^[а-яёa-z\-]+$', word):
            await interaction.response.send_message(
                "❌ Слово может содержать только буквы и дефис.", ephemeral=True)
            return

        self._solo_hangman[uid] = {"word": word, "guessed": set(), "wrong": []}

        emb = _hangman_embed(word, "", "")
        emb.set_footer(text="Нажимай кнопки чтобы угадывать буквы")

        view = HangmanSoloView(self, uid)
        await interaction.response.send_message(embed=emb, view=view)

    # ════════════════════════════════════════════════════════════
    #  ВИСЕЛИЦА — МУЛЬТИПЛЕЕР
    # ════════════════════════════════════════════════════════════
    @app_commands.command(name="виселица_старт",
                          description="Мультиплеер виселица — загадать слово для сервера")
    @app_commands.describe(слово="Слово которое угадывают другие")
    async def виселица_старт(self, interaction: discord.Interaction, слово: str):
        import re
        word = слово.strip().lower()
        if not re.match(r'^[а-яёa-z\-]{2,}$', word):
            await interaction.response.send_message(
                "❌ Слово: только буквы (2+ символа).", ephemeral=True)
            return

        # Закрываем старую игру в этом канале если есть
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE hangman_games SET status='cancelled'"
                " WHERE channel_id=? AND status='active'",
                (interaction.channel.id,)
            )
            cur = conn.execute(
                "INSERT INTO hangman_games(guild_id,channel_id,host_id,word,"
                "guessed,wrong,status,created_at) VALUES(?,?,?,?,'','','active',?)",
                (interaction.guild.id, interaction.channel.id,
                 interaction.user.id, word, datetime.now(UTC).isoformat())
            )
            game_id = cur.lastrowid

        await interaction.response.send_message(
            f"✅ Слово загадано ({len(word)} букв). Игра #{game_id}",
            ephemeral=True)

        emb = _hangman_embed(word, "", "")
        emb.title = f"🪢 Виселица — загадал {interaction.user.display_name}"
        emb.set_footer(text=f"Угадывайте буквы: /виселица_буква буква:А  (игра #{game_id})")
        await interaction.followup.send(embed=emb)

    @app_commands.command(name="виселица_буква",
                          description="Угадать букву в мультиплеер виселице")
    @app_commands.describe(буква="Одна буква")
    async def виселица_буква(self, interaction: discord.Interaction, буква: str):
        letter = буква.strip().lower()
        if len(letter) != 1:
            await interaction.response.send_message("❌ Введи одну букву.", ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id,host_id,word,guessed,wrong,status FROM hangman_games"
                " WHERE channel_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (interaction.channel.id,)
            ).fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ Нет активной игры в этом канале.", ephemeral=True)
            return

        game_id, host_id, word, guessed_str, wrong_str, status = row
        if interaction.user.id == host_id:
            await interaction.response.send_message(
                "❌ Загадавший не может угадывать.", ephemeral=True)
            return

        guessed = set(guessed_str)
        wrong   = list(wrong_str)

        if letter in guessed or letter in wrong:
            await interaction.response.send_message(
                f"⚠️ Буква `{letter.upper()}` уже была.", ephemeral=True)
            return

        if letter in word:
            guessed.add(letter)
            new_guessed = "".join(sorted(guessed))
            # Проверяем победу
            if all(c in guessed or c == "-" for c in word):
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        "UPDATE hangman_games SET guessed=?,status='win' WHERE id=?",
                        (new_guessed, game_id)
                    )
                reward = 30
                nb = add_coins(interaction.user.id, reward, "game_win", {"game":"hangman"})
                await emit("game_win", user_id=interaction.user.id, game="hangman")
                emb = _hangman_embed(word, new_guessed, wrong_str, "win")
                emb.add_field(
                    name="🏆 Победитель",
                    value=f"{interaction.user.mention} угадал последнюю букву!\n+{_money(interaction.user.id, reward)} → **{_money(interaction.user.id, nb)}**",
                    inline=False)
                await interaction.response.send_message(embed=emb)
                return
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE hangman_games SET guessed=? WHERE id=?",
                    (new_guessed, game_id)
                )
            emb = _hangman_embed(word, new_guessed, wrong_str)
            emb.add_field(name="✅", value=f"{interaction.user.mention} угадал `{letter.upper()}`!")
        else:
            wrong.append(letter.upper())
            new_wrong = "".join(wrong)
            if len(wrong) >= MAX_WRONG:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        "UPDATE hangman_games SET wrong=?,status='lose' WHERE id=?",
                        (new_wrong, game_id)
                    )
                emb = _hangman_embed(word, guessed_str, new_wrong, "lose")
                await interaction.response.send_message(embed=emb)
                return
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE hangman_games SET wrong=? WHERE id=?",
                    (new_wrong, game_id)
                )
            emb = _hangman_embed(word, guessed_str, new_wrong)
            emb.add_field(name="❌", value=f"{interaction.user.mention} ошибся: `{letter.upper()}`")

        await interaction.response.send_message(embed=emb)

    # ════════════════════════════════════════════════════════════
    #  БЛЭКДЖЕК — соло против бота
    # ════════════════════════════════════════════════════════════
    @app_commands.command(name="бж", description="Блэкджек против бота со ставкой")
    @app_commands.describe(ставка="Сколько персональной валюты поставить (минимум 5)")
    async def бж(self, interaction: discord.Interaction,
                  ставка: app_commands.Range[int, 5, 10_000]):
        bal = get_balance(interaction.user.id)
        if bal < ставка:
            await interaction.response.send_message(
                f"❌ Недостаточно валюты. Баланс: **{_money(interaction.user.id, bal)}**", ephemeral=True)
            return

        deck       = _new_deck()
        p_hand     = [deck.pop(), deck.pop()]
        d_hand     = [deck.pop(), deck.pop()]
        p_total    = _hand_total(p_hand)

        # Блэкджек сразу
        if p_total == 21:
            d_total = _hand_total(d_hand)
            if d_total == 21:
                result_txt = "🤝 Оба с блэкджеком — ничья! Ставка возвращена."
            else:
                win = int(ставка * 1.5)
                nb  = add_coins(interaction.user.id, win, "game_win", {"game":"bj"})
                result_txt = f"🃏 Блэкджек! +{_money(interaction.user.id, win)} → **{_money(interaction.user.id, nb)}**"
            emb = discord.Embed(title="🃏 Блэкджек", color=discord.Color.gold())
            emb.add_field(name=f"Твои карты ({p_total})", value=_hand_str(p_hand))
            emb.add_field(name=f"Карты бота ({d_total})", value=_hand_str(d_hand))
            emb.add_field(name="Результат", value=result_txt, inline=False)
            await interaction.response.send_message(embed=emb)
            return

        view = BlackjackView(
            cog=self, user_id=interaction.user.id,
            deck=deck, p_hand=p_hand, d_hand=d_hand, bet=ставка
        )
        emb = view.build_embed(show_dealer_hole=False)
        await interaction.response.send_message(embed=emb, view=view)

    # ════════════════════════════════════════════════════════════
    #  БЛЭКДЖЕК — дуэль PvP (оба против бота, кто ближе к 21)
    # ════════════════════════════════════════════════════════════
    @app_commands.command(name="бж_дуэль",
                          description="Блэкджек дуэль — оба против бота, ставка одинаковая")
    @app_commands.describe(
        соперник="Кого вызвать",
        ставка="Ставка каждого (минимум 5)",
    )
    async def бж_дуэль(self, interaction: discord.Interaction,
                        соперник: discord.Member,
                        ставка: app_commands.Range[int, 5, 10_000]):
        if соперник.bot or соперник.id == interaction.user.id:
            await interaction.response.send_message("❌ Неверный соперник.", ephemeral=True)
            return
        for uid, name in [(interaction.user.id, interaction.user.display_name),
                           (соперник.id, соперник.display_name)]:
            if get_balance(uid) < ставка:
                await interaction.response.send_message(
                    f"❌ У {name} недостаточно валюты (нужно {ставка}).", ephemeral=True)
                return

        view = BJDuelView(
            cog=self,
            p1=interaction.user, p2=соперник, bet=ставка,
            channel=interaction.channel
        )
        emb = discord.Embed(
            title="🃏 Блэкджек — дуэль",
            description=(f"{interaction.user.mention} vs {соперник.mention}\n"
                         f"Ставка: **{ставка}** персональной валюты каждый\n\n"
                         f"{соперник.mention}, подтверди участие!"),
            color=discord.Color.orange()
        )
        await interaction.response.send_message(
            content=соперник.mention, embed=emb, view=view)


# ════════════════════════════════════════════════════════════════════════════════
#  Views
# ════════════════════════════════════════════════════════════════════════════════


class RPSDuelInviteView(discord.ui.View):
    def __init__(self, cog: Games, initiator: discord.Member, opponent: discord.Member, timeout_min: int):
        super().__init__(timeout=timeout_min * 60)
        self.cog = cog
        self.initiator = initiator
        self.opponent = opponent
        self.accepted = False

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("❌ Этот вызов адресован не тебе.", ephemeral=True)
            return
        self.accepted = True
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="⚔️ КНБ-дуэль началась",
            description=(
                f"{self.initiator.mention} vs {self.opponent.mention}\n\n"
                "Оба игрока выбирают ход кнопками ниже. До финала выборы скрыты."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=RPSDuelChoiceView(self.cog, self.initiator, self.opponent))
        self.stop()

    @discord.ui.button(label="Отказаться", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id not in (self.initiator.id, self.opponent.id):
            await interaction.response.send_message("❌ Это не твоя дуэль.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="❌ КНБ-дуэль отменена",
            description=f"{interaction.user.mention} отменил дуэль.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        self.stop()


class RPSDuelChoiceView(discord.ui.View):
    def __init__(self, cog: Games, initiator: discord.Member, opponent: discord.Member):
        super().__init__(timeout=180)
        self.cog = cog
        self.initiator = initiator
        self.opponent = opponent
        self.choices: dict[int, str] = {}
        self.done = False

    async def _choose(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id not in (self.initiator.id, self.opponent.id):
            await interaction.response.send_message("❌ Это не твоя дуэль.", ephemeral=True)
            return
        if interaction.user.id in self.choices:
            await interaction.response.send_message("🔒 Ты уже сделал ход.", ephemeral=True)
            return
        self.choices[interaction.user.id] = choice
        await interaction.response.send_message(f"✅ Ход принят: **{choice}**", ephemeral=True)
        if len(self.choices) == 2 and not self.done:
            await self._finish(interaction)

    async def _finish(self, interaction: discord.Interaction):
        self.done = True
        init_choice = self.choices[self.initiator.id]
        opp_choice = self.choices[self.opponent.id]
        result = _rps_result(init_choice, opp_choice)
        reward_txt = ""
        color = discord.Color.gold()
        if result > 0:
            nb = add_coins(self.initiator.id, 20, "game_win", {"game": "rps_pvp"})
            await emit("game_win", user_id=self.initiator.id, game="rps")
            reward_txt = f"\n🏆 Победитель: {self.initiator.mention} +{_money(self.initiator.id, 20)} → **{_money(self.initiator.id, nb)}**"
            color = discord.Color.green()
        elif result < 0:
            nb = add_coins(self.opponent.id, 20, "game_win", {"game": "rps_pvp"})
            await emit("game_win", user_id=self.opponent.id, game="rps")
            reward_txt = f"\n🏆 Победитель: {self.opponent.mention} +{_money(self.opponent.id, 20)} → **{_money(self.opponent.id, nb)}**"
            color = discord.Color.green()
        else:
            reward_txt = "\n🤝 Ничья. Никто не стал больше, зато все посмотрели."

        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="⚔️ Итог КНБ-дуэли",
            description=(
                f"{self.initiator.mention}: **{init_choice}**\n"
                f"{self.opponent.mention}: **{opp_choice}**{reward_txt}"
            ),
            color=color,
        )
        await interaction.message.edit(embed=embed, view=self)
        await emit("game_played", user_id=self.initiator.id, guild_id=interaction.guild.id, game="rps_pvp")
        await emit("game_played", user_id=self.opponent.id, guild_id=interaction.guild.id, game="rps_pvp")
        self.stop()

    @discord.ui.button(label="Камень", style=discord.ButtonStyle.secondary, emoji="🪨")
    async def rock(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._choose(interaction, "камень")

    @discord.ui.button(label="Ножницы", style=discord.ButtonStyle.secondary, emoji="✂️")
    async def scissors(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._choose(interaction, "ножницы")

    @discord.ui.button(label="Бумага", style=discord.ButtonStyle.secondary, emoji="📄")
    async def paper(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._choose(interaction, "бумага")

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id not in (self.initiator.id, self.opponent.id):
            await interaction.response.send_message("❌ Это не твоя дуэль.", ephemeral=True)
            return
        self.done = True
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="🛑 КНБ-дуэль отменена",
            description=f"{interaction.user.mention} отменил игру.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        self.stop()


# ── Соло виселица кнопки ──────────────────────────────────────────────────────
RU_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"

class HangmanSoloView(discord.ui.View):
    def __init__(self, cog: Games, uid: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.uid = uid
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        state = self.cog._solo_hangman.get(self.uid)
        if not state:
            return
        used = state["guessed"] | set(l.lower() for l in state["wrong"])
        # Показываем только 25 кнопок (лимит Discord)
        shown = 0
        for ch in RU_ALPHABET:
            if shown >= 25:
                break
            if ch in used:
                continue
            btn = discord.ui.Button(
                label=ch.upper(),
                style=discord.ButtonStyle.secondary,
                custom_id=f"hm_{self.uid}_{ch}",
            )
            btn.callback = self._make_cb(ch)
            self.add_item(btn)
            shown += 1

    def _make_cb(self, letter: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.uid:
                await interaction.response.send_message(
                    "❌ Это не твоя игра.", ephemeral=True)
                return
            state = self.cog._solo_hangman.get(self.uid)
            if not state:
                await interaction.response.send_message(
                    "❌ Игра не найдена.", ephemeral=True)
                return
            word    = state["word"]
            guessed = state["guessed"]
            wrong   = state["wrong"]

            if letter in word:
                guessed.add(letter)
                if all(c in guessed or c == "-" for c in word):
                    del self.cog._solo_hangman[self.uid]
                    reward = max(10, 50 - len(wrong) * 8)
                    nb = add_coins(self.uid, reward, "game_win", {"game":"hangman"})
                    emb = _hangman_embed(word, "".join(sorted(guessed)), "".join(l.upper() for l in wrong), "win")
                    emb.add_field(name="🏆 Победа!", value=f"+{_money(self.uid, reward)} → **{_money(self.uid, nb)}**")
                    await interaction.response.edit_message(embed=emb, view=None)
                    return
            else:
                wrong.append(letter.upper())
                if len(wrong) >= MAX_WRONG:
                    del self.cog._solo_hangman[self.uid]
                    emb = _hangman_embed(word, "".join(sorted(guessed)), "".join(wrong), "lose")
                    await interaction.response.edit_message(embed=emb, view=None)
                    return

            self._build_buttons()
            emb = _hangman_embed(word, "".join(sorted(guessed)), "".join(wrong))
            await interaction.response.edit_message(embed=emb, view=self)
        return cb

    async def on_timeout(self):
        self.cog._solo_hangman.pop(self.uid, None)
        self.stop()


# ── Блэкджек соло view ────────────────────────────────────────────────────────
class BlackjackView(discord.ui.View):
    def __init__(self, cog: Games, user_id: int,
                 deck: list, p_hand: list, d_hand: list, bet: int):
        super().__init__(timeout=120)
        self.cog     = cog
        self.user_id = user_id
        self.deck    = deck
        self.p_hand  = p_hand
        self.d_hand  = d_hand
        self.bet     = bet
        self.done    = False

    def build_embed(self, show_dealer_hole: bool = False) -> discord.Embed:
        p_total = _hand_total(self.p_hand)
        if show_dealer_hole:
            d_total = _hand_total(self.d_hand)
            d_str   = _hand_str(self.d_hand)
        else:
            d_total = _card_value(self.d_hand[0])
            d_str   = f"{self.d_hand[0]} 🂠"

        emb = discord.Embed(title="🃏 Блэкджек", color=discord.Color.blurple())
        emb.add_field(name=f"Твои карты ({p_total})",
                      value=_hand_str(self.p_hand), inline=False)
        emb.add_field(name=f"Карты бота ({'?' if not show_dealer_hole else d_total})",
                      value=d_str, inline=False)
        emb.set_footer(text=f"Ставка: {_money(self.user_id, self.bet)}")
        return emb

    async def _finish(self, interaction: discord.Interaction, reason: str = ""):
        self.done = True
        p_total = _hand_total(self.p_hand)
        # Бот добирает до 17+
        while _hand_total(self.d_hand) < 17:
            self.d_hand.append(self.deck.pop())
        d_total = _hand_total(self.d_hand)

        if p_total > 21:
            result = "bust"
        elif d_total > 21 or p_total > d_total:
            result = "win"
        elif p_total == d_total:
            result = "push"
        else:
            result = "lose"

        if result == "win":
            nb  = add_coins(self.user_id, self.bet, "game_win", {"game":"bj"})
            txt = f"🏆 Победа! +{_money(self.user_id, self.bet)} → **{_money(self.user_id, nb)}**"
            color = discord.Color.green()
        elif result == "push":
            txt   = "🤝 Ничья — ставка возвращена."
            color = discord.Color.blurple()
        else:
            nb  = add_coins(self.user_id, -self.bet, "game_lose", {"game":"bj"})
            txt = f"💸 Поражение. -{_money(self.user_id, self.bet)} → **{_balance_money(self.user_id)}**"
            color = discord.Color.red()
            if result == "bust":
                txt = "💥 Перебор! " + txt

        emb = self.build_embed(show_dealer_hole=True)
        emb.color = color
        emb.add_field(name="Результат", value=txt, inline=False)
        if reason:
            emb.add_field(name="", value=reason, inline=False)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=emb, view=self)
        await emit("game_played", user_id=self.user_id,
                   guild_id=interaction.guild.id, game="blackjack")
        self.stop()

    @discord.ui.button(label="Ещё", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Не твоя игра.", ephemeral=True)
            return
        self.p_hand.append(self.deck.pop())
        if _hand_total(self.p_hand) > 21:
            await self._finish(interaction)
            return
        await interaction.response.edit_message(
            embed=self.build_embed(show_dealer_hole=False), view=self)

    @discord.ui.button(label="Хватит", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Не твоя игра.", ephemeral=True)
            return
        await self._finish(interaction)

    @discord.ui.button(label="Удвоить", style=discord.ButtonStyle.danger, emoji="💰")
    async def double_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Не твоя игра.", ephemeral=True)
            return
        if len(self.p_hand) != 2:
            await interaction.response.send_message(
                "❌ Удвоить можно только на первых двух картах.", ephemeral=True)
            return
        if get_balance(self.user_id) < self.bet:
            await interaction.response.send_message("❌ Недостаточно валюты.", ephemeral=True)
            return
        self.bet *= 2
        self.p_hand.append(self.deck.pop())
        await self._finish(interaction, "💰 Ставка удвоена!")

    async def on_timeout(self):
        if not self.done:
            add_coins(self.user_id, -self.bet, "game_lose", {"game":"bj","reason":"timeout"})
        self.stop()


# ── Блэкджек дуэль view ───────────────────────────────────────────────────────
class BJDuelView(discord.ui.View):
    def __init__(self, cog: Games,
                 p1: discord.Member, p2: discord.Member,
                 bet: int, channel):
        super().__init__(timeout=60)
        self.cog     = cog
        self.p1      = p1
        self.p2      = p2
        self.bet     = bet
        self.channel = channel
        self.accepted = False

    @discord.ui.button(label="Принять вызов", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.p2.id:
            await interaction.response.send_message(
                "❌ Вызов адресован не тебе.", ephemeral=True)
            return
        self.accepted = True
        self.stop()
        for child in self.children:
            child.disabled = True
        play_view = BJDuelPlayView(self.cog, self.p1, self.p2, self.bet)
        await interaction.response.edit_message(
            content=None,
            embed=play_view.build_embed(),
            view=play_view,
        )

    @discord.ui.button(label="Отказаться", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1.id, self.p2.id):
            return
        self.stop()
        await interaction.response.edit_message(
            content="❌ Дуэль отменена.", embed=None, view=None)

    async def _run_duel(self, interaction: discord.Interaction):
        deck   = _new_deck()
        hands  = {
            self.p1.id: [deck.pop(), deck.pop()],
            self.p2.id: [deck.pop(), deck.pop()],
        }
        # Каждый добирает до 17 (автоматически)
        for uid in (self.p1.id, self.p2.id):
            while _hand_total(hands[uid]) < 17:
                hands[uid].append(deck.pop())

        t1 = _hand_total(hands[self.p1.id])
        t2 = _hand_total(hands[self.p2.id])

        def score(t):
            return t if t <= 21 else 0  # перебор = 0

        s1, s2 = score(t1), score(t2)
        emb = discord.Embed(title="🃏 Итог блэкджек дуэли", color=discord.Color.gold())
        emb.add_field(name=f"{self.p1.display_name} ({t1})",
                      value=_hand_str(hands[self.p1.id]), inline=False)
        emb.add_field(name=f"{self.p2.display_name} ({t2})",
                      value=_hand_str(hands[self.p2.id]), inline=False)

        if s1 > s2:
            nb = add_coins(self.p1.id,  self.bet, "game_win",  {"game":"bj_duel"})
            add_coins(self.p2.id, -self.bet, "game_lose", {"game":"bj_duel"})
            emb.add_field(name="🏆 Победитель",
                          value=f"{self.p1.mention} +{_money(self.p1.id, self.bet)} → **{_money(self.p1.id, nb)}**")
        elif s2 > s1:
            nb = add_coins(self.p2.id,  self.bet, "game_win",  {"game":"bj_duel"})
            add_coins(self.p1.id, -self.bet, "game_lose", {"game":"bj_duel"})
            emb.add_field(name="🏆 Победитель",
                          value=f"{self.p2.mention} +{_money(self.p2.id, self.bet)} → **{_money(self.p2.id, nb)}**")
        else:
            emb.add_field(name="🤝 Ничья", value="Ставки возвращены.")

        await self.channel.send(embed=emb)

    async def on_timeout(self):
        if not self.accepted:
            try:
                await self.channel.send(
                    f"⌛ {self.p2.mention} не ответил — дуэль отменена.")
            except Exception:
                pass
        self.stop()


class BJDuelPlayView(discord.ui.View):
    def __init__(self, cog: Games, p1: discord.Member, p2: discord.Member, bet: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.p1 = p1
        self.p2 = p2
        self.bet = bet
        self.deck = _new_deck()
        self.hands = {
            p1.id: [self.deck.pop(), self.deck.pop()],
            p2.id: [self.deck.pop(), self.deck.pop()],
        }
        self.stood: set[int] = set()
        self.done = False

    def _participants(self) -> tuple[int, int]:
        return self.p1.id, self.p2.id

    def _line_for(self, member: discord.Member) -> str:
        total = _hand_total(self.hands[member.id])
        state = "готов" if member.id in self.stood else "ходит"
        if total > 21:
            state = "перебор"
        return f"{member.mention} ({total}) — {_hand_str(self.hands[member.id])}\n`{state}`"

    def build_embed(self, result: str | None = None) -> discord.Embed:
        embed = discord.Embed(
            title="🃏 Блэкджек-дуэль",
            description="Игроки жмут **Ещё** или **Хватит**. Итог виден всем.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name=self.p1.display_name, value=self._line_for(self.p1), inline=False)
        embed.add_field(name=self.p2.display_name, value=self._line_for(self.p2), inline=False)
        embed.set_footer(text=f"Ставка: {self.bet} персональной валюты каждый")
        if result:
            embed.add_field(name="Итог", value=result, inline=False)
        return embed

    async def _ensure_player(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self._participants():
            await interaction.response.send_message("❌ Это не твоя дуэль.", ephemeral=True)
            return False
        if self.done:
            await interaction.response.send_message("⛔ Дуэль уже завершена.", ephemeral=True)
            return False
        if interaction.user.id in self.stood:
            await interaction.response.send_message("🔒 Ты уже остановился.", ephemeral=True)
            return False
        return True

    def _score(self, uid: int) -> int:
        total = _hand_total(self.hands[uid])
        return total if total <= 21 else 0

    def _should_finish(self) -> bool:
        ids = self._participants()
        return all(uid in self.stood or _hand_total(self.hands[uid]) > 21 for uid in ids)

    async def _finish(self, interaction: discord.Interaction):
        self.done = True
        p1_score = self._score(self.p1.id)
        p2_score = self._score(self.p2.id)
        if p1_score > p2_score:
            nb = add_coins(self.p1.id, self.bet, "game_win", {"game": "bj_duel"})
            add_coins(self.p2.id, -self.bet, "game_lose", {"game": "bj_duel"})
            result = f"🏆 Победитель: {self.p1.mention} +{_money(self.p1.id, self.bet)} → **{_money(self.p1.id, nb)}**"
        elif p2_score > p1_score:
            nb = add_coins(self.p2.id, self.bet, "game_win", {"game": "bj_duel"})
            add_coins(self.p1.id, -self.bet, "game_lose", {"game": "bj_duel"})
            result = f"🏆 Победитель: {self.p2.mention} +{_money(self.p2.id, self.bet)} → **{_money(self.p2.id, nb)}**"
        else:
            result = "🤝 Ничья. Ставки остаются на месте."

        for child in self.children:
            child.disabled = True
        embed = self.build_embed(result)
        embed.color = discord.Color.gold()
        await interaction.response.edit_message(embed=embed, view=self)
        await emit("game_played", user_id=self.p1.id, guild_id=interaction.guild.id, game="bj_duel")
        await emit("game_played", user_id=self.p2.id, guild_id=interaction.guild.id, game="bj_duel")
        self.stop()

    @discord.ui.button(label="Ещё", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._ensure_player(interaction):
            return
        self.hands[interaction.user.id].append(self.deck.pop())
        if _hand_total(self.hands[interaction.user.id]) > 21:
            self.stood.add(interaction.user.id)
        if self._should_finish():
            await self._finish(interaction)
            return
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Хватит", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._ensure_player(interaction):
            return
        self.stood.add(interaction.user.id)
        if self._should_finish():
            await self._finish(interaction)
            return
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id not in self._participants():
            await interaction.response.send_message("❌ Это не твоя дуэль.", ephemeral=True)
            return
        self.done = True
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="❌ Блэкджек-дуэль отменена",
            description=f"{interaction.user.mention} отменил игру. Валюта не списана.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        self.stop()


async def setup(bot):
    await bot.add_cog(Games(bot))
