# -*- coding: utf-8 -*-
# fun_slesh/toxicity.py
"""
Детектор токсичности + троллинг:
  - on_message: анализирует каждое сообщение через быстрый эвристический фильтр
  - При обнаружении: публично позорит, пародирует через Markov/GPT, ведёт счётчик
  - Счётчик сбрасывается раз в неделю

Команды:
  /токсики          — топ токсичных участников за период
  /токсичность_вкл  — (Админ) включить/выключить систему
  /токсичность_порог — (Админ) настроить чувствительность (1-10)
  /токсичность_канал — (Админ) ограничить работу определёнными каналами
"""

import sqlite3, random, re, asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from core.paths import SOCIAL_DB
from core.settings_store import (
    clear_feature_channel,
    clear_feature_channels,
    get_feature_payload,
    get_feature_policy,
    has_feature_setting,
    set_feature_channel,
    set_feature_enabled,
    set_feature_payload,
)

DB_PATH  = SOCIAL_DB
UTC      = timezone.utc
MSK      = ZoneInfo("Europe/Moscow")
FEATURE_TOXICITY = "toxicity"

# ── Токсичные паттерны (эвристика, без ML) ────────────────────────────────────
# Три уровня: мягкий (1), средний (2), жёсткий (3)
TOXIC_PATTERNS = {
    1: [  # мягкий — грубость, наезды
        r'\bтупой\b', r'\bидиот\b', r'\bдебил\b', r'\bкретин\b',
        r'\bлох\b', r'\bнуб\b', r'\bноуб\b', r'\bзалупа\b',
        r'\bурод\b', r'\bуродли\w+', r'\bмудак\b', r'\bпридурок\b',
        r'\bшлюх\w*\b', r'\bпошёл\s+нах\w*', r'\bиди\s+нах\w*',
        r'\bнуб\w*\b', r'\bказёл\b', r'\bкозёл\b',
        r'\bбля\w*\b', r'\bговно\b', r'\bжопа\b', r'\bхуй\b',
        r'\bбнс\b', r'\bbns\b', r'\bкодзима\b', r'\bгений\b',
    ],
    2: [  # средний — оскорбления
        r'\bеба\w+\b', r'\bёба\w+\b', r'\bеблан\b', r'\bёблан\b',
        r'\bпиздёж\b', r'\bпиздёт\b', r'\bпиздун\b',
        r'\bзаткнись\b', r'\bзаткни\s+пасть',
        r'\bты\s+отстой\b', r'\bты\s+дно\b',
        r'\bпроиграл\s+в\s+жизни', r'\bнеудачник\b',
        r'\bсосёшь\b', r'\bсоси\b',
        r'\bебну\w*\b', r'\bёбну\w*\b',
    ],
    3: [  # жёсткий — прямые оскорбления/угрозы
        r'\bпошёл\s+нахуй\b', r'\bиди\s+нахуй\b',
        r'\bпиздец\s+тебе\b', r'\bубью\b', r'\bубить\s+тебя',
        r'\bсдохни\b', r'\bсдохнешь\b',
    ],
}

# Компилируем
_COMPILED: dict[int, list] = {
    lvl: [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]
    for lvl, patterns in TOXIC_PATTERNS.items()
}

# Шаблоны ответов по уровням (публичный позор)
SHAME_TEMPLATES = {
    1: [
        "👀 {mention} опять начинает, это уже **{count}**-й раз на этой неделе",
        "📊 {mention} набирает статистику: **{count}** токсичных сообщений за неделю",
        "🤔 {mention} не может без этого, счётчик: **{count}**",
    ],
    2: [
        "🚨 {mention} жарит — уже **{count}** раз за неделю, серьёзно?",
        "📈 {mention} бьёт рекорды: **{count}** раз за неделю",
        "🏆 {mention} лидирует в номинации «токсик недели»: **{count}** очков",
    ],
    3: [
        "🔥 {mention} совсем поехал — **{count}** раз за неделю, ты в порядке?",
        "💀 {mention} финалит: **{count}** токсичных за неделю, может хватит?",
        "🎖️ {mention} получает медаль «Главный токсик»: **{count}** раз за неделю",
    ],
}


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS toxicity_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                level      INTEGER NOT NULL,
                msg_snippet TEXT   NOT NULL DEFAULT '',
                logged_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS toxicity_weekly (
                user_id   INTEGER NOT NULL,
                guild_id  INTEGER NOT NULL,
                week      TEXT    NOT NULL,
                count     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id, week)
            );

        """)


def _get_config(guild_id: int) -> tuple[bool, int, set[int], set[int]]:
    """Возвращает (enabled, threshold_lvl, allowed_channel_ids, excluded_channel_ids)."""
    policy = get_feature_policy(guild_id, FEATURE_TOXICITY)
    payload = get_feature_payload(guild_id, FEATURE_TOXICITY)
    enabled = policy.enabled
    try:
        threshold = int(payload.get("threshold") or 1)
    except (TypeError, ValueError):
        threshold = 1
    allowed = set(policy.allowed_channel_ids)
    excluded = set(policy.excluded_channel_ids)
    return enabled, max(1, min(threshold, 3)), allowed, excluded


def _toxicity_excluded_channel_ids(guild_id: int) -> set[int]:
    return _get_config(guild_id)[3]


def _is_toxicity_channel_excluded(guild_id: int, channel_id: int) -> bool:
    return int(channel_id) in _toxicity_excluded_channel_ids(guild_id)


def _save_toxicity_enabled(guild_id: int, enabled: bool):
    set_feature_enabled(guild_id, FEATURE_TOXICITY, enabled)


def _save_toxicity_threshold(guild_id: int, threshold: int):
    threshold = max(1, min(int(threshold), 3))
    payload = get_feature_payload(guild_id, FEATURE_TOXICITY)
    payload["threshold"] = threshold
    set_feature_payload(guild_id, FEATURE_TOXICITY, payload)


def _save_toxicity_allow_channels(guild_id: int, channel_ids: set[int]):
    clear_feature_channels(guild_id, FEATURE_TOXICITY, "allow")
    for channel_id in sorted(channel_ids):
        set_feature_channel(guild_id, FEATURE_TOXICITY, channel_id, "allow", "Discord admin command")


def _save_toxicity_excluded_channel(guild_id: int, channel_id: int, reason: str = ""):
    set_feature_channel(guild_id, FEATURE_TOXICITY, channel_id, "exclude", reason or "Discord admin command")


def _clear_toxicity_excluded_channel(guild_id: int, channel_id: int) -> int:
    return clear_feature_channel(guild_id, FEATURE_TOXICITY, channel_id, "exclude")


def _detect_level(text: str) -> int:
    """Возвращает уровень токсичности (0 = нет)."""
    for lvl in (3, 2, 1):
        for pat in _COMPILED[lvl]:
            if pat.search(text):
                return lvl
    return 0


def _current_week() -> str:
    return datetime.now(MSK).strftime("%Y-W%W")


def _inc_counter(guild_id: int, user_id: int) -> int:
    week = _current_week()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO toxicity_weekly(user_id, guild_id, week, count)"
            " VALUES(?,?,?,1)"
            " ON CONFLICT(user_id, guild_id, week)"
            " DO UPDATE SET count = count + 1",
            (user_id, guild_id, week)
        )
        row = conn.execute(
            "SELECT count FROM toxicity_weekly"
            " WHERE user_id=? AND guild_id=? AND week=?",
            (user_id, guild_id, week)
        ).fetchone()
    return row[0] if row else 1


# ── Генерация троллинг-ответа ─────────────────────────────────────────────────
def _markov_troll(user_id: int) -> str | None:
    """Генерирует фразу через актуальные модели пародии пользователя."""
    try:
        from fun_slesh.parody_engine import generate_phrase, model_exists

        # Для токсичности лучше сначала брать более внятную модель,
        # а уже потом абсурдную.
        if model_exists(user_id, "разум"):
            sentence = generate_phrase(user_id, "разум")
            if sentence:
                return sentence

        if model_exists(user_id, "мем"):
            sentence = generate_phrase(user_id, "мем")
            if sentence:
                return sentence
    except Exception:
        pass
    return None


async def _gpt_troll(user_id: int, toxic_msg: str) -> str | None:
    """Генерирует ответ через GPT в стиле пользователя."""
    try:
        from fun_slesh.parody_persona import load_persona
        from fun_slesh.parody_gpt import generate_gpt_phrase
        profile = load_persona(user_id)
        if not profile:
            return None
        phrase = await asyncio.get_event_loop().run_in_executor(
            None, generate_gpt_phrase, profile, toxic_msg
        )
        return phrase
    except Exception:
        pass
    return None


def _build_troll_response(mention: str, count: int, level: int,
                           parody: str | None = None) -> str:
    shame = random.choice(SHAME_TEMPLATES.get(level, SHAME_TEMPLATES[1]))
    base  = shame.format(mention=mention, count=count)

    if parody:
        # Пародия на их же стиль
        connectors = [
            f"\n\nА вот как это звучит на твоём языке: *«{parody}»*",
            f"\n\nПереводим на твой: *«{parody}»*",
            f"\n\nТвоя же модель говорит: *«{parody}»*",
        ]
        base += random.choice(connectors)

    return base


# ── Cog ───────────────────────────────────────────────────────────────────────
class Toxicity(commands.Cog):
    toxicity_group = app_commands.Group(
        name="токсичность",
        description="Детектор токсичности и его настройки"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()
        # Небольшой кулдаун на ответы чтобы не спамить
        self._cooldowns: dict[tuple[int, int], datetime] = {}

    def _check_cooldown(self, guild_id: int, user_id: int) -> bool:
        """True = можно отвечать."""
        key = (guild_id, user_id)
        last = self._cooldowns.get(key)
        now  = datetime.now(UTC)
        if last and (now - last).total_seconds() < 24 * 3600:
            return False
        self._cooldowns[key] = now
        return True

    async def _send_troll_reply(self, message: discord.Message, response: str):
        try:
            await message.reply(response, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not message.content:
            return

        guild_id = message.guild.id
        user_id  = message.author.id

        enabled, threshold, ch_filter, excluded_channels = _get_config(guild_id)
        if not enabled:
            return
        if message.channel.id in excluded_channels:
            return

        # Фильтр по каналам
        if ch_filter and message.channel.id not in ch_filter:
            return

        # Детектируем
        level = _detect_level(message.content)
        if level < threshold:
            return

        # Кулдаун
        if not self._check_cooldown(guild_id, user_id):
            return

        # Логируем
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO toxicity_log(guild_id,user_id,channel_id,level,msg_snippet,logged_at)"
                " VALUES(?,?,?,?,?,?)",
                (guild_id, user_id, message.channel.id, level,
                 message.content[:100], datetime.now(UTC).isoformat())
            )

        count  = _inc_counter(guild_id, user_id)
        parody = None

        # Пытаемся сгенерировать пародию (не блокируем основной поток)
        markov = _markov_troll(user_id)
        if markov:
            parody = markov
        elif level >= 2:
            # GPT только для среднего+ уровня
            gpt = await _gpt_troll(user_id, message.content)
            if gpt:
                parody = gpt

        response = _build_troll_response(
            mention=message.author.mention,
            count=count,
            level=level,
            parody=parody
        )

        await self._send_troll_reply(message, response)

    # ── /токсики ──────────────────────────────────────────────────────────────
    @toxicity_group.command(name="топ",
                            description="Топ токсичных участников за неделю")
    @app_commands.describe(
        период="Неделя (по умолчанию текущая) или 'всё время'"
    )
    @app_commands.choices(период=[
        app_commands.Choice(name="Текущая неделя", value="week"),
        app_commands.Choice(name="Всё время",       value="all"),
    ])
    async def токсики(self, interaction: discord.Interaction,
                       период: str = "week"):
        guild_id = interaction.guild.id

        with sqlite3.connect(DB_PATH) as conn:
            if период == "week":
                week = _current_week()
                rows = conn.execute(
                    "SELECT user_id, count FROM toxicity_weekly"
                    " WHERE guild_id=? AND week=?"
                    " ORDER BY count DESC LIMIT 10",
                    (guild_id, week)
                ).fetchall()
                title = "☢️ Топ токсиков этой недели"
            else:
                rows = conn.execute(
                    "SELECT user_id, SUM(count) as total FROM toxicity_weekly"
                    " WHERE guild_id=? GROUP BY user_id ORDER BY total DESC LIMIT 10",
                    (guild_id,)
                ).fetchall()
                title = "☢️ Топ токсиков за всё время"

        if not rows:
            await interaction.response.send_message(
                "✅ Токсиков не обнаружено. Сервер в порядке!", ephemeral=True)
            return

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (uid, cnt) in enumerate(rows):
            medal = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{medal} <@{uid}> — **{cnt}** раз")

        emb = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=emb)

    # ── /токсичность_вкл ──────────────────────────────────────────────────────
    @toxicity_group.command(name="вкл",
                            description="(Админ) Включить/выключить детектор токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def токсичность_вкл(self, interaction: discord.Interaction,
                                включить: bool):
        _save_toxicity_enabled(interaction.guild.id, включить)
        status = "✅ Включён" if включить else "⛔ Выключен"
        await interaction.response.send_message(
            f"{status} детектор токсичности. Настройка сохранена в admin feature settings.", ephemeral=True)

    # ── /токсичность_порог ────────────────────────────────────────────────────
    @toxicity_group.command(name="порог",
                            description="(Админ) Уровень чувствительности (1=мягко, 3=только жёсткое)")
    @app_commands.describe(уровень="1 — любая грубость, 2 — оскорбления, 3 — только жёсткое")
    @app_commands.choices(уровень=[
        app_commands.Choice(name="1 — любая грубость",         value=1),
        app_commands.Choice(name="2 — оскорбления",            value=2),
        app_commands.Choice(name="3 — только жёсткое",         value=3),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def токсичность_порог(self, interaction: discord.Interaction,
                                 уровень: int):
        _save_toxicity_threshold(interaction.guild.id, уровень)
        labels = {1: "любая грубость", 2: "оскорбления", 3: "только жёсткое"}
        await interaction.response.send_message(
            f"✅ Порог установлен: **уровень {уровень}** ({labels[уровень]}). Настройка сохранена в admin feature settings.",
            ephemeral=True)

    # ── /токсичность_канал ────────────────────────────────────────────────────
    @toxicity_group.command(name="канал",
                            description="(Админ) Ограничить мониторинг каналами (пусто = все каналы)")
    @app_commands.describe(
        канал="Добавить/убрать канал из мониторинга",
        действие="Добавить или убрать"
    )
    @app_commands.choices(действие=[
        app_commands.Choice(name="Добавить", value="add"),
        app_commands.Choice(name="Убрать",   value="remove"),
        app_commands.Choice(name="Сбросить (все каналы)", value="reset"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def токсичность_канал(self, interaction: discord.Interaction,
                                 действие: str,
                                 канал: discord.TextChannel | None = None):
        guild_id = interaction.guild.id
        current = set(_get_config(guild_id)[2])

        if действие == "reset":
            current = set()
        elif канал:
            if действие == "add":
                current.add(канал.id)
            else:
                current.discard(канал.id)

        _save_toxicity_allow_channels(guild_id, current)

        if not current:
            msg = "✅ Мониторинг ведётся во **всех каналах**."
        else:
            mentions = [f"<#{cid}>" for cid in current]
            msg = "✅ Мониторинг каналов: " + ", ".join(mentions)
        await interaction.response.send_message(msg, ephemeral=True)

    @toxicity_group.command(name="исключить", description="(Админ) Исключить канал из детектора токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def toxicity_exclude_channel(
        self,
        interaction: discord.Interaction,
        канал: discord.TextChannel,
        причина: str = "",
    ):
        _save_toxicity_excluded_channel(interaction.guild.id, канал.id, причина)
        await interaction.response.send_message(
            f"✅ {канал.mention} исключён из детектора токсичности. Настройка сохранена в admin feature settings.", ephemeral=True
        )

    @toxicity_group.command(name="вернуть", description="(Админ) Вернуть канал в детектор токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def toxicity_include_channel(self, interaction: discord.Interaction, канал: discord.TextChannel):
        deleted = _clear_toxicity_excluded_channel(interaction.guild.id, канал.id)
        text = f"✅ {канал.mention} снова участвует в детекторе токсичности." if deleted else "ℹ️ Этого канала не было в исключениях."
        await interaction.response.send_message(text, ephemeral=True)

    @toxicity_group.command(name="исключения", description="(Админ) Показать каналы, исключённые из токсичности")
    @app_commands.checks.has_permissions(administrator=True)
    async def toxicity_excluded_channels(self, interaction: discord.Interaction):
        ids = _toxicity_excluded_channel_ids(interaction.guild.id)
        text = ", ".join(f"<#{cid}>" for cid in sorted(ids)) if ids else "Исключений нет."
        await interaction.response.send_message(f"Каналы вне детектора токсичности: {text}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Toxicity(bot))
