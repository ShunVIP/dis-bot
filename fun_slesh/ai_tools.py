# fun_slesh/ai_tools.py
import asyncio
import aiohttp
import discord
import urllib.parse
from discord.ext import commands
from discord import app_commands

WIKI_HEADERS = {"User-Agent": "ViPikBot/1.0 (Discord bot; private server)"}

RATE_LIMIT_MSG = "⚠️ Лимит бесплатных запросов исчерпан. Попробуй позже."
SERVICE_DOWN_MSG = "⚠️ Сервис сейчас недоступен. Попробуй позже."

class AITools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ───────────────────────────  /вики  ───────────────────────────
    @app_commands.command(name="вики", description="Краткая справка из Википедии (ru → en)")
    @app_commands.describe(
        запрос="Что найти? (например: «Данте»)",
        язык="Предпочитаемый язык: ru/en (по умолчанию ru)"
    )
    async def вики(self, interaction: discord.Interaction, запрос: str, язык: str = "ru"):
        await interaction.response.defer()

        async def search_and_summary(lang: str, q: str, limit: int = 5):
            base = f"https://{lang}.wikipedia.org/w/rest.php/v1"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12), headers=WIKI_HEADERS) as session:
                # Поиск нескольких кандидатов
                async with session.get(f"{base}/search/page", params={"q": q, "limit": limit}) as r:
                    if r.status == 429: return None, RATE_LIMIT_MSG
                    if r.status >= 500: return None, SERVICE_DOWN_MSG
                    if r.status != 200: return None, f"❌ Ошибка поиска Википедии: HTTP {r.status}"
                    s = await r.json()

                hits = s.get("pages", [])
                if not hits:
                    return [], None

                # Выбор лучшего совпадения
                def norm(x: str) -> str:
                    return (x or "").strip().lower().replace("ё", "е")
                nq = norm(q)
                best = next((h for h in hits if norm(h.get("title","")) == nq), None) \
                    or next((h for h in hits if norm(h.get("title","")).startswith(nq)), None) \
                    or next((h for h in hits if nq in norm(h.get("title",""))), None) \
                    or hits[0]

                key = best.get("key") or (best.get("title") or "").replace(" ", "_")
                if not key:
                    return hits, None

                # Summary по лучшему ключу
                safe_key = urllib.parse.quote(key, safe="")
                async with session.get(f"{base}/page/{safe_key}/summary") as r:
                    if r.status == 404:  # у редких страниц может не быть summary
                        return hits, None
                    if r.status == 429: return None, RATE_LIMIT_MSG
                    if r.status >= 500: return None, SERVICE_DOWN_MSG
                    if r.status != 200: return None, f"❌ Не удалось получить описание: HTTP {r.status}"
                    summary = await r.json()

            return {"summary": summary, "key": key, "lang": lang, "hits": hits}, None

        langs = [язык.lower(), "en"] if язык.lower() in ("ru","en") else ["ru","en"]
        result, err = await search_and_summary(langs[0], запрос, limit=5)
        if err: await interaction.followup.send(err); return
        if not result:
            result, err = await search_and_summary(langs[1], запрос, limit=5)
            if err: await interaction.followup.send(err); return
            if not result: await interaction.followup.send("🔎 Ничего не нашёл ни в ru, ни в en."); return

        # Если пришёл список кандидатов без summary — покажем варианты
        if isinstance(result, list):
            lang = langs[0]
            lines = []
            for h in result[:5]:
                t = h.get("title") or h.get("key") or "Без названия"
                k = (h.get("key") or t).replace(" ", "_")
                url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(k, safe='')}"
                lines.append(f"• [{t}]({url})")
            await interaction.followup.send(
                f"Искали: **{запрос}**\nНашёл несколько вариантов, уточни:\n" + "\n".join(lines)
            )
            return

        # Нормальный summary — рендерим embed
        summary, lang, key = result["summary"], result["lang"], result["key"]
        extract = summary.get("extract") or "Описание отсутствует."
        page_url = f"https://{lang}.wikipedia.org/wiki/{key}"
        thumbnail = (summary.get("thumbnail") or {}).get("url")

        embed = discord.Embed(
            title=f"📚 {summary.get('title', key)}",
            description=extract[:3900],
            url=page_url,
            color=discord.Color.green()
        )
        embed.set_author(name=f"Искали: {запрос} | Язык: {lang}")
        if thumbnail: embed.set_thumbnail(url=thumbnail)

        hits = result.get("hits") or []
        if len(hits) > 1:
            alt = [h.get("title") for h in hits[1:4] if h.get("title")]
            if alt: embed.set_footer(text="Ещё варианты: " + " • ".join(alt))

        await interaction.followup.send(embed=embed)

    # ──────────────────────────  /пабмед  ──────────────────────────
    @app_commands.command(name="пабмед", description="Поиск статей на PubMed")
    @app_commands.describe(
        запрос="Поисковая фраза (например: vitamin D sleep)",
        сколько="Сколько результатов показать на странице (1–5, по умолчанию 3)",
        новые_сначала="Если true — самые свежие публикации первыми (по умолчанию true)",
        за_дней="Фильтр по давности публикации (например, 30 — за последний месяц)"
    )
    async def пабмед(
        self,
        interaction: discord.Interaction,
        запрос: str,
        сколько: app_commands.Range[int,1,5] = 3,
        новые_сначала: bool = True,
        за_дней: app_commands.Range[int,1,3650] | None = None
    ):
        await interaction.response.defer()

        async def esearch(term: str, retmax: int, newest: bool, reldays: int | None, retstart: int = 0):
            url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            params = {
                "db": "pubmed",
                "term": term,
                "retmax": retmax,
                "retstart": retstart,
                "retmode": "json",
                "sort": "pub+date" if newest else "relevance",
                "datetype": "pdat"
            }
            if reldays:
                # reldate = последние N дней от сегодня
                params["reldate"] = reldays
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                async with session.get(url, params=params) as r:
                    if r.status == 429: return None, 0, RATE_LIMIT_MSG
                    if r.status >= 500: return None, 0, SERVICE_DOWN_MSG
                    if r.status != 200: return None, 0, f"❌ PubMed esearch HTTP {r.status}"
                    data = await r.json()
            ids = (data.get("esearchresult", {}).get("idlist") or [])
            total = int(data.get("esearchresult", {}).get("count", "0"))
            return ids, total, None

        async def esummary(pmids: list[str]):
            if not pmids:
                return {}, None
            url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            params = {"db":"pubmed","id":",".join(pmids),"retmode":"json"}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                async with session.get(url, params=params) as r:
                    if r.status == 429: return None, RATE_LIMIT_MSG
                    if r.status >= 500: return None, SERVICE_DOWN_MSG
                    if r.status != 200: return None, f"❌ PubMed esummary HTTP {r.status}"
                    return await r.json(), None

        # Первая страница
        idlist, total, err = await esearch(запрос, сколько, новые_сначала, за_дней, retstart=0)
        if err: await interaction.followup.send(err); return
        if not idlist:
            await interaction.followup.send("🔎 Ничего не нашёл в PubMed по этому запросу.")
            return

        summ, err = await esummary(idlist)
        if err: await interaction.followup.send(err); return

        def build_results(pmids: list[str], summaries: dict) -> list[dict]:
            results = []
            resmap = (summaries or {}).get("result", {})
            for pmid in pmids:
                item = resmap.get(pmid) or {}
                title = item.get("title") or "(без названия)"
                journal = item.get("fulljournalname") or item.get("source") or ""
                pubdate = item.get("pubdate") or item.get("epubdate") or ""
                authors = [a.get("name") for a in item.get("authors", []) if a.get("name")]
                doi = None
                for aid in item.get("articleids", []):
                    if aid.get("idtype") == "doi":
                        doi = aid.get("value"); break
                results.append({
                    "pmid": pmid,
                    "title": title,
                    "journal": journal,
                    "year": (pubdate.split(" ")[0] if pubdate else ""),
                    "authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
                    "doi": doi
                })
            return results

        page_results = build_results(idlist, summ)
        sort_label = "новые сначала" if новые_сначала else "релевантность"
        filter_label = f" • За {за_дней} дн." if за_дней else ""

        # Рендер страницы
        def make_embed(results: list[dict], page_start: int) -> discord.Embed:
            if len(results) == 1:
                r = results[0]
                url = f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/"
                embed = discord.Embed(
                    title=f"🧪 {r['title']}",
                    url=url,
                    description=(f"**Journal:** {r['journal']} ({r['year']})\n"
                                 f"**Authors:** {r['authors']}\n"
                                 f"**PMID:** {r['pmid']}\n"
                                 + (f"**DOI:** {r['doi']}\n" if r['doi'] else "")),
                    color=discord.Color.dark_teal()
                )
            else:
                lines = []
                for r in results:
                    url = f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/"
                    doi_txt = f" • DOI: {r['doi']}" if r['doi'] else ""
                    lines.append(f"• [{r['title']}]({url}) — {r['journal']} ({r['year']}); {r['authors']}{doi_txt}")
                embed = discord.Embed(
                    title=f"🧪 PubMed: {запрос}",
                    description="\n\n".join(lines),
                    color=discord.Color.dark_teal()
                )
            cur_range_end = page_start + len(results)
            embed.set_footer(text=f"Сортировка: {sort_label}{filter_label} • Показано: {cur_range_end}/{total}")
            embed.set_author(name=f"Искали: {запрос}")
            return embed

        # Кнопочная пагинация (подгрузка следующей страницы через retstart)
        class PubMedPager(discord.ui.View):
            def __init__(self, cog: "AITools", term: str, page_size: int, newest: bool, reldays: int | None, total_count: int):
                super().__init__(timeout=60)
                self.cog = cog
                self.term = term
                self.page_size = page_size
                self.newest = newest
                self.reldays = reldays
                self.total = total_count
                self.retstart = page_size  # следующая позиция
                # отключим кнопку сразу, если нечего листать
                if self.retstart >= self.total:
                    for child in self.children:
                        child.disabled = True

            @discord.ui.button(label="Показать ещё", style=discord.ButtonStyle.primary)
            async def next_page(self, interaction_button: discord.Interaction, button: discord.ui.Button):
                await interaction_button.response.defer()
                ids, total, err2 = await esearch(self.term, self.page_size, self.newest, self.reldays, retstart=self.retstart)
                if err2:
                    await interaction_button.followup.send(err2, ephemeral=True)
                    return
                if not ids:
                    button.disabled = True
                    await interaction_button.edit_original_response(view=self)
                    return
                summ2, err3 = await esummary(ids)
                if err3:
                    await interaction_button.followup.send(err3, ephemeral=True)
                    return

                results2 = build_results(ids, summ2)
                emb = make_embed(results2, page_start=self.retstart)
                self.retstart += len(ids)
                # Если дошли до конца — отключим кнопку
                if self.retstart >= self.total:
                    button.disabled = True
                await interaction_button.edit_original_response(embed=emb, view=self)

        view = PubMedPager(self, запрос, сколько, новые_сначала, за_дней, total)
        embed = make_embed(page_results, page_start=0)
        await interaction.followup.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(AITools(bot))
