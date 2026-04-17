# -*- coding: utf-8 -*-
# fun_slesh/test_hello.py

import discord
from discord.ext import commands
from discord import app_commands
import random
import aiohttp
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

# =========================
# ВНУТРЕННЕЕ СОСТОЯНИЕ ОПРОСОВ (на время работы процесса)
# =========================
class PollState:
    def __init__(
        self,
        question: str,
        options: list[str],
        ends_at_utc: Optional[datetime],
        anonymous: bool,
        role_id: Optional[int],
        eligible_role_member_ids: set[int],
        creator_id: int,
        message_id: Optional[int] = None,
    ):
        self.question = question
        self.options = options
        self.ends_at_utc = ends_at_utc.replace(tzinfo=timezone.utc) if ends_at_utc else None
        self.anonymous = anonymous
        self.role_id = role_id
        self.eligible_role_member_ids = eligible_role_member_ids
        self.creator_id = creator_id
        self.message_id = message_id
        self.votes: dict[int, int] = {}      # user_id -> option_index
        self.counts: list[int] = [0] * len(options)
        self.lock = asyncio.Lock()
        self.closed = False

    def total(self) -> int:
        return sum(self.counts)

    def everybody_in_role_voted(self) -> bool:
        if not self.role_id or not self.eligible_role_member_ids:
            return False
        return self.eligible_role_member_ids.issubset(self.votes.keys())


# message_id -> PollState
_POLLS: dict[int, PollState] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные штуки
# ─────────────────────────────────────────────────────────────────────────────
def progress_bar(pct: float, width: int = 12) -> str:
    filled = int(round(pct * width))
    return "▰" * filled + "▱" * (width - filled)


def build_poll_embed(st: PollState, guild: Optional[discord.Guild], live: bool = False) -> discord.Embed:
    total_for_pct = max(1, st.total())
    sections: list[str] = []

    # Получаем читаемое имя
    def resolve_name(uid: int) -> str:
        if guild:
            m = guild.get_member(uid)
            if m:
                return m.display_name
        return f"<@{uid}>"

    # Варианты + (опционально) список голосовавших
    for i, opt in enumerate(st.options):
        cnt = st.counts[i]
        pct = cnt / total_for_pct
        bar = progress_bar(pct)
        line = f"**{opt}** — {cnt} ({int(pct*100)}%)\n{bar}"

        if not st.anonymous and st.votes:
            voters = [uid for uid, choice in st.votes.items() if choice == i]
            if voters:
                show = [resolve_name(u) for u in voters[:20]]
                more = len(voters) - len(show)
                voters_txt = ", ".join(show) + (f" +{more}" if more > 0 else "")
                line += f"\n👥 {voters_txt}"

        sections.append(line)

    # Красивый относительный таймстамп переносим в description
    ends_txt = "без срока"
    if st.ends_at_utc:
        ends_txt = f"<t:{int(st.ends_at_utc.timestamp())}:R>"

    title = "📊 Опрос (идёт)" if live else "📊 Опрос — итоги"
    desc = (
        f"**Вопрос:** {st.question}\n\n"
        + "\n\n".join(sections)
        + f"\n\n⏱ Завершится {ends_txt}"
    )
    emb = discord.Embed(
        title=title,
        description=desc,
        color=discord.Color.blurple() if live else discord.Color.green(),
    )
    emb.set_footer(text=f"Всего голосов: {st.total()}")
    return emb


async def finish_poll(message: discord.Message, view: "PollView", st: PollState, ping_role: Optional[discord.Role], reason: str):
    async with st.lock:
        if st.closed:
            return
        st.closed = True

    final = build_poll_embed(st, message.guild, live=False)

    # выключим кнопки
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            child.disabled = True
    try:
        await message.edit(embed=final, view=view)
    except Exception:
        pass

    # победитель (первый максимум)
    winner_idx = None
    if st.counts:
        max_votes = max(st.counts)
        if max_votes > 0:
            winner_idx = st.counts.index(max_votes)

    # Итоговый пост
    mention_text = ""
    allowed = discord.AllowedMentions.none()
    if ping_role is not None:
        mention_text = ping_role.mention
        allowed = discord.AllowedMentions(roles=[ping_role])

    winner_line = ""
    if winner_idx is not None:
        winner_line = f"**Победил вариант:** {st.options[winner_idx]} — {st.counts[winner_idx]} голосов"

    # 🆕 Разбор по вариантам с никами (если опрос не анонимный)
    breakdown = ""
    if not st.anonymous and st.votes:
        def resolve_name(uid: int) -> str:
            if message.guild:
                m = message.guild.get_member(uid)
                if m:
                    return m.display_name
            return f"<@{uid}>"

        chunks: list[str] = []
        for i, opt in enumerate(st.options):
            voters = [uid for uid, choice in st.votes.items() if choice == i]
            if voters:
                names = [resolve_name(u) for u in voters[:20]]
                more = len(voters) - len(names)
                extra = f" +{more}" if more > 0 else ""
                chunks.append(f"• **{opt}** — {len(voters)}: " + ", ".join(names) + extra)
        if chunks:
            breakdown = "\n\n**Кто за что голосовал:**\n" + "\n".join(chunks)

    try:
        await message.reply(
            content=mention_text,
            allowed_mentions=allowed,
            mention_author=False,
            embed=discord.Embed(
                title="📝 Итоги опроса",
                description=(winner_line or "Голосов нет.")
                            + (f"\n\nПричина закрытия: {reason}" if reason else "")
                            + breakdown,
                color=discord.Color.gold(),
            ),
        )
    except Exception:
        pass

    _POLLS.pop(message.id, None)


# ─────────────────────────────────────────────────────────────────────────────
# UI Кнопки
# ─────────────────────────────────────────────────────────────────────────────
class PollButton(discord.ui.Button):
    def __init__(self, option_idx: int, label: str):
        super().__init__(style=discord.ButtonStyle.primary, label=label)
        self.option_idx = option_idx

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        st = _POLLS.get(message.id)
        if not st:
            await interaction.response.send_message("❌ Опрос уже недоступен.", ephemeral=True)
            return

        if st.closed or (st.ends_at_utc and datetime.now(timezone.utc) >= st.ends_at_utc):
            await interaction.response.send_message("⌛ Опрос уже завершён.", ephemeral=True)
            return

        async with st.lock:
            prev = st.votes.get(interaction.user.id)
            if prev == self.option_idx:
                await interaction.response.send_message("✅ Ваш голос уже учтён за этот вариант.", ephemeral=True)
                return
            if prev is not None and 0 <= prev < len(st.counts):
                st.counts[prev] = max(0, st.counts[prev] - 1)
            st.votes[interaction.user.id] = self.option_idx
            st.counts[self.option_idx] += 1

        # обновим embed «вживую»
        await interaction.response.defer(ephemeral=True, thinking=False)
        try:
            new_embed = build_poll_embed(st, message.guild, live=True)
            await message.edit(embed=new_embed, view=self.view)
        except Exception:
            pass
        await interaction.followup.send("🗳️ Голос принят!", ephemeral=True)

        # досрочное завершение: все из роли проголосовали
        view: PollView = self.view  # type: ignore
        if st.everybody_in_role_voted() and view:
            await finish_poll(message, view, st, ping_role=view.ping_role, reason="Все участники роли проголосовали")


class ClosePollButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="Завершить")

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        st = _POLLS.get(message.id)
        if not st:
            await interaction.response.send_message("❌ Опрос уже недоступен.", ephemeral=True)
            return

        # Только создатель опроса может закрыть
        if interaction.user.id != st.creator_id:
            await interaction.response.send_message("⛔ Завершить опрос может только его создатель.", ephemeral=True)
            return

        view: PollView = self.view  # type: ignore
        await finish_poll(
            message,
            view,
            st,
            ping_role=view.ping_role,
            reason="Закрыт создателем",
        )
        await interaction.response.send_message("✅ Опрос закрыт.", ephemeral=True)


class PollView(discord.ui.View):
    def __init__(
        self,
        message_id: int,
        options: list[str],
        ping_role: Optional[discord.Role],
        creator_id: int,
        timeout_seconds: Optional[float],  # None = без таймаута
    ):
        super().__init__(timeout=timeout_seconds)
        self.message_id = message_id
        self.ping_role = ping_role
        self.creator_id = creator_id
        for idx, title in enumerate(options):
            self.add_item(PollButton(option_idx=idx, label=title))
        # Кнопка досрочного завершения (только автор может нажать)
        self.add_item(ClosePollButton())

    async def on_timeout(self):
        # если стоял таймер и мы сюда дошли — просто не трогаем; закрытие по времени делает фоновая задача
        pass


# =========================
# БАЗОВЫЕ/ФАН КОМАНДЫ
# =========================
class FunAndInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ✅ /пинг
    @app_commands.command(name="пинг", description="Проверить задержку бота")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Пингани гиганта: задержка в развитии `{latency}мс`")

    # 👤 /кто
    @app_commands.command(name="кто", description="Показать информацию о пользователе")
    @app_commands.describe(пользователь="Укажите пользователя (по умолчанию — вы)")
    async def кто(self, interaction: discord.Interaction, пользователь: Optional[discord.Member] = None):
        member = пользователь or interaction.user
        joined_at = member.joined_at.strftime("%d.%m.%Y %H:%M") if member.joined_at else "Неизвестно"
        roles = [role.mention for role in member.roles if role.name != "@everyone"]
        roles_str = ", ".join(roles) if roles else "Без ролей"

        embed = discord.Embed(title=f"Информация о {member.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Имя", value=member.mention, inline=True)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="В кружке чая с", value=joined_at, inline=False)
        embed.add_field(name="Роли", value=roles_str, inline=False)

        await interaction.response.send_message(embed=embed)

    # 🌐 /сервер
    @app_commands.command(name="сервер", description="Показать информацию о сервере")
    async def сервер(self, interaction: discord.Interaction):
        guild = interaction.guild
        owner = await guild.fetch_member(guild.owner_id)

        embed = discord.Embed(title=f"Сервер: {guild.name}", color=discord.Color.green())
        embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
        embed.add_field(name="ID", value=guild.id, inline=True)
        embed.add_field(name="Владелец", value=owner.mention, inline=True)
        embed.add_field(name="Создан", value=guild.created_at.strftime("%d.%m.%Y %H:%M"), inline=False)
        embed.add_field(name="Участников", value=guild.member_count, inline=True)
        embed.add_field(name="Каналов", value=len(guild.channels), inline=True)

        await interaction.response.send_message(embed=embed)

    # 🎲 /монетка
    @app_commands.command(name="монетка", description="Подбросить монетку")
    async def монетка(self, interaction: discord.Interaction):
        result = random.choice(["Орёл 🦅", "Решка 💰"])
        await interaction.response.send_message(f"🪙 {result}")

    # 🔮 /шар
    @app_commands.command(name="шар", description="Магический шар 8 даст тебе ответ")
    @app_commands.describe(вопрос="Задай вопрос шару")
    async def шар(self, interaction: discord.Interaction, вопрос: str):
        ответы = [
            # ✅ Позитивные
            "Однозначно да, братуха",
            "Спрашиваешь? Конечно да",
            "Вселенная говорит ДА",
            "Знаки указывают — всё будет",
            "100% мой бро",
            "Даже не сомневайся",
            "Судьба уже решила — да",
            # 🤔 Нейтральные
            "Спроси ещё раз после кофе",
            "Туман войны не рассеялся",
            "Мб да, мб нет, жизнь покажет",
            "Шар завис, перезагрузи реальность",
            "Ответ скрыт за пределами видимого",
            "Хороший вопрос, плохой тайминг",
            "Звёзды молчат, и я тоже",
            # ❌ Негативные
            "Нет, и даже не надейся",
            "Шар смеётся над этим вопросом",
            "Это ловушка — ни в коем случае",
            "Категорически нет, братан",
            "Вселенная говорит — даже не думай",
            "Лучше не надо, серьёзно",
            # 😂 Мемные
            "Ответ есть, но я его не скажу",
            "404: ответ не найден",
            "Это выше моего понимания",
            "Наташ, мы всё уронили",
            "По данным шара — скорее всего нет, но кто знает",
            "Подожди 3-5 рабочих лет",
            "Шар видит страдание в твоих глазах",
        ]
        embed = discord.Embed(title="🎱 Магический шар 8", color=discord.Color.purple())
        embed.add_field(name="Вопрос", value=вопрос, inline=False)
        embed.add_field(name="Ответ", value=random.choice(ответы), inline=False)
        await interaction.response.send_message(embed=embed)

    # 🎲 /кубик
    @app_commands.command(name="кубик", description="Бросить кубик (по умолчанию d6)")
    @app_commands.describe(граней="Сколько граней у кубика (например, 6 или 20)")
    async def кубик(self, interaction: discord.Interaction, граней: int = 6):
        if граней < 2 or граней > 1000:
            await interaction.response.send_message("❌ Кубик должен иметь от 2 до 1000 граней.")
            return
        result = random.randint(1, граней)
        await interaction.response.send_message(f"🎲 Выпало: **{result}** из {граней}")

    # 🧠 /анекдот
    @app_commands.command(name="анекдот", description="Рандомный анекдот с просторов интернета")
    async def анекдот(self, interaction: discord.Interaction):
        await interaction.response.defer()
        joke = None

        # Источник 1: umnij.ru JSON API
        try:
            url = "https://www.anekdot.ru/rss/randomu.html"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        html = await r.text()
                        import re as _re
                        # Анекдоты в тегах <div class="text">...</div>
                        matches = _re.findall(r'<div class="text">(.*?)</div>', html, _re.DOTALL)
                        if matches:
                            raw = random.choice(matches)
                            joke = _re.sub(r'<[^>]+>', '', raw).strip().replace("&nbsp;", " ").replace("&amp;", "&")
        except Exception:
            pass

        # Источник 2: jokesrv.ru
        if not joke:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get("https://jokesrv.rubedo.cloud/", timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            data = await r.json(content_type=None)
                            joke = data.get("content", "").strip()
            except Exception:
                pass

        # Фолбэк — локальная подборка
        if not joke:
            FALLBACK = [
                "— Мам, а почему у нас нет денег?\n— Потому что папа программист.\n— Но программисты много зарабатывают!\n— Он пишет на Python.",
                "Оптимист: стакан наполовину полон.\nПессимист: стакан наполовину пуст.\nПрограммист: стакан в два раза больше, чем надо.",
                "— Как дела?\n— Как у процессора: много задач, мало памяти, всё зависает.",
                "В понедельник лучше не начинать ничего нового. В пятницу — ничего старого. В среду вообще непонятно что происходит.",
                "Подходит мужик к другому:\n— Слушай, ты вчера что-то обещал?\n— Не помню.\n— Вот и хорошо, я тоже.",
            ]
            joke = random.choice(FALLBACK)

        embed = discord.Embed(
            title="🃏 Анекдот",
            description=joke[:2000],
            color=discord.Color.yellow()
        )
        await interaction.followup.send(embed=embed)

    # 🐱 /котик
    @app_commands.command(name="котик", description="Случайная картинка котика")
    async def котик(self, interaction: discord.Interaction):
        url = "https://api.thecatapi.com/v1/images/search"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    image_url = data[0].get("url")
                else:
                    image_url = None

        if image_url:
            await interaction.response.send_message(f"🐱 Вот котик для тебя:\n{image_url}")
        else:
            await interaction.response.send_message("❌ Не удалось найти котика. Попробуй позже.")

    # =========================
    # ⏳ /опрос — кнопки, дедлайн(опц.), итог; роль (опц.); досрочное закрытие; вывод никнеймов
    # =========================
    @app_commands.command(name="опрос", description="Создать опрос с кнопками. Можно без срока, с ручным закрытием.")
    @app_commands.describe(
        вопрос="Текст вопроса",
        варианты="Варианты через ; (например: Да;Нет;Мне всё равно)",
        минут="Через сколько минут завершить (оставь пусто или 0 — без срока)",
        анонимно="Если True — имена голосовавших не показываются",
        роль="Роль, которую упомянуть (оставь пусто — никого не тегать)"
    )
    async def опрос(
        self,
        interaction: discord.Interaction,
        вопрос: str,
        варианты: str,
        минут: Optional[int] = None,
        анонимно: bool = False,
        роль: Optional[discord.Role] = None
    ):
        opts = [x.strip() for x in варианты.split(";") if x.strip()]
        if len(opts) < 2 or len(opts) > 10:
            await interaction.response.send_message("❌ Нужно от 2 до 10 вариантов, разделённых `;`.", ephemeral=True)
            return

        # нормализуем минуты
        if минут is not None and минут < 0:
            await interaction.response.send_message("❌ Минуты не могут быть отрицательными.", ephemeral=True)
            return
        auto_close = bool(минут and минут > 0)

        # 🆕 Вариант B: набор «ожидаемых голосующих» по роли через API, с fallback
        required_voters: set[int] = set()
        if роль is not None:
            # Пытаемся получить полный список участников гильдии и отфильтровать по роли
            try:
                guild = interaction.guild
                if guild is not None:
                    fetched_ids: set[int] = {
                        m.id async for m in guild.fetch_members(limit=None)
                        if (роль in m.roles) and (not m.bot)
                    }
                    required_voters = fetched_ids
            except Exception:
                # Fallback на кеш роли (может быть неполным без интентов, но лучше так, чем ничего)
                try:
                    required_voters = {m.id for m in роль.members if not m.bot}
                except Exception:
                    required_voters = set()

        ends_at_utc = None
        if auto_close:
            ends_at_utc = datetime.now(timezone.utc) + timedelta(minutes=int(минут))

        state = PollState(
            question=вопрос,
            options=opts,
            ends_at_utc=ends_at_utc,
            anonymous=анонимно,
            role_id=(роль.id if роль else None),
            eligible_role_member_ids=required_voters,
            creator_id=interaction.user.id,
        )

        draft = build_poll_embed(state, interaction.guild, live=True)

        # безопасное упоминание роли
        await interaction.response.defer()
        mention_text = роль.mention if роль else ""
        allowed = discord.AllowedMentions(roles=[роль]) if роль else discord.AllowedMentions.none()
        msg = await interaction.followup.send(content=mention_text, embed=draft, allowed_mentions=allowed, wait=True)

        # сохранить message_id
        state.message_id = msg.id
        _POLLS[msg.id] = state

        # таймаут View: None если нет автозакрытия
        view_timeout = float(минут * 60) if auto_close else None
        view = PollView(
            message_id=msg.id,
            options=opts,
            ping_role=роль,
            creator_id=interaction.user.id,
            timeout_seconds=view_timeout,
        )
        await msg.edit(embed=draft, view=view)

        # фоновая задача — обычное завершение по времени (если задано)
        if auto_close:
            async def close_later():
                try:
                    await asyncio.sleep(max(1, (ends_at_utc - datetime.now(timezone.utc)).total_seconds()))
                except Exception:
                    return
                st = _POLLS.get(msg.id)
                if not st or st.closed:
                    return
                await finish_poll(msg, view, st, ping_role=None, reason="Истёк таймер")
            asyncio.create_task(close_later())

    # 😂 /мем — RU приоритет, EN падение
    @app_commands.command(name="мем", description="Показать случайный мем")
    async def мем(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Русскоязычные subreddits через meme-api
        RU_SUBS = ["Pikabu", "ru_memes", "PurpleOrangeMemes"]
        random.shuffle(RU_SUBS)

        meme_url, meme_title = None, None

        for sub in RU_SUBS:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://meme-api.com/gimme/{sub}",
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as r:
                        if r.status == 200:
                            data = await r.json()
                            if not data.get("nsfw", True):
                                meme_url   = data.get("url")
                                meme_title = data.get("title", "")
                                if meme_url:
                                    break
            except Exception:
                continue

        # Фолбэк — любой subreddit
        if not meme_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://meme-api.com/gimme",
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as r:
                        if r.status == 200:
                            data = await r.json()
                            meme_url   = data.get("url")
                            meme_title = data.get("title", "")
            except Exception:
                pass

        if meme_url:
            embed = discord.Embed(
                title=meme_title or "😂 Мем",
                color=discord.Color.orange()
            )
            embed.set_image(url=meme_url)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Не удалось достать мем. Попробуй позже.")


async def setup(bot):
    await bot.add_cog(FunAndInfo(bot))
