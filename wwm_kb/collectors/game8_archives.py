import asyncio
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .base import Collector, RawRecord

BASE_URL = "https://game8.co"
SEED_URL = "https://game8.co/games/Where-Winds-Meet/"

ARCHIVE_RE = re.compile(r"^/games/Where-Winds-Meet/archives/\d+$")
SCOPE_RE = re.compile(r"^/games/Where-Winds-Meet/")


def _is_same_scope(href: str) -> bool:
    return bool(SCOPE_RE.match(href))


def _is_archive(href: str) -> bool:
    return bool(ARCHIVE_RE.match(href))


def _clean_title(t: str | None) -> str | None:
    if not t:
        return None
    t = t.strip()
    if not t:
        return None
    # часто бывает "Something | Game8"
    t = t.replace("| Game8", "").strip()
    return t or None


def _extract_title(soup: BeautifulSoup) -> str | None:
    # 1) h1
    h1 = soup.find("h1")
    if h1:
        t = _clean_title(h1.get_text(strip=True))
        if t:
            return t

    # 2) og:title
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        t = _clean_title(og.get("content"))
        if t:
            return t

    # 3) twitter:title
    tw = soup.find("meta", attrs={"name": "twitter:title"})
    if tw and tw.get("content"):
        t = _clean_title(tw.get("content"))
        if t:
            return t

    # 4) <title>
    ttag = soup.find("title")
    if ttag:
        t = _clean_title(ttag.get_text(strip=True))
        if t:
            return t

    return None


def _is_noise_block(txt: str) -> bool:
    low = (txt or "").lower()
    noise_markers = (
        "what can you do as a free member",
        "create your free account",
        "premium features",
        "watchlist",
        "favorite games",
        "site interface",
        "want more information",
        "learn more",
    )
    return any(m in low for m in noise_markers)


def _extract_article_text(soup: BeautifulSoup) -> str:
    """
    Пытаемся достать именно тело статьи (а не навигацию/CTA).
    1) Пробуем наиболее вероятные контейнеры.
    2) Если не нашли — берём самый длинный осмысленный блок.
    """
    selectors = [
        "article",
        "main article",
        "main",
        "div#content",
        "div.content",
        "div.article-body",
        "section",
    ]

    # 1) Явные контейнеры
    for sel in selectors:
        el = soup.select_one(sel)
        if not el:
            continue
        txt = el.get_text("\n", strip=True)
        if len(txt) >= 1500 and not _is_noise_block(txt):
            return txt

    # 2) Fallback: самый длинный блок
    best = ""
    for el in soup.find_all(["article", "section", "div"]):
        txt = el.get_text("\n", strip=True)
        if len(txt) > len(best) and len(txt) >= 1500 and not _is_noise_block(txt):
            best = txt

    return best


class Game8ArchivesCollector(Collector):
    source = "game8"
    method = "html_archives"

    def __init__(self, max_pages: int = 99999, delay_sec: float = 0.9):
        self.max_pages = max_pages
        self.delay_sec = delay_sec

    async def _fetch_text(self, session: aiohttp.ClientSession, url: str) -> str:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            return await r.text()

    def _extract_archives(self, html: str) -> set[str]:
        soup = BeautifulSoup(html, "lxml")
        found: set[str] = set()
        for a in soup.select("a[href]"):
            href = a.get("href")
            if not href:
                continue

            parsed = urlparse(href)
            path = parsed.path if parsed.scheme else href.split("?")[0].split("#")[0]

            if _is_archive(path):
                found.add(urljoin(BASE_URL, path))
        return found

    def _page_text_min(self, html: str) -> tuple[str | None, str]:
        soup = BeautifulSoup(html, "lxml")

        title = _extract_title(soup)

        # берём именно тело статьи
        article_text = _extract_article_text(soup)
        if article_text:
            return title, article_text

        # fallback: если совсем ничего — как было (шумно, но хоть что-то)
        body = soup.find("body") or soup
        return title, body.get_text("\n", strip=True)

    async def collect(self):
        headers = {"User-Agent": "dis-bot/2.0 (WWM KB; Game8 daily sync)"}
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1) Seed → найти все /archives/<id>
            seed_html = await self._fetch_text(session, SEED_URL)
            queue = list(self._extract_archives(seed_html))
            seen: set[str] = set()

            pages = 0
            while queue and pages < self.max_pages:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)

                try:
                    html = await self._fetch_text(session, url)
                except Exception:
                    # пропускаем, но не валим весь прогон
                    await asyncio.sleep(self.delay_sec)
                    continue

                # расширяем граф ссылок: статья может ссылаться на другие archives
                more = self._extract_archives(html)
                for u in more:
                    if u not in seen:
                        queue.append(u)

                title, text = self._page_text_min(html)

                payload = {
                    "url": url,
                    "title": title,
                    "text": text,
                    "html_sha256": hashlib.sha256(
                        html.encode("utf-8", errors="ignore")
                    ).hexdigest(),
                }
                payload_json = json.dumps(payload, ensure_ascii=False)
                content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

                yield RawRecord(
                    source=self.source,
                    method=self.method,
                    entity_type="game8_archive_article",
                    external_id=url,  # уникальный ключ = URL
                    title=title,
                    url=url,
                    payload_json=payload_json,
                    content_hash=content_hash,
                )

                pages += 1
                await asyncio.sleep(self.delay_sec)