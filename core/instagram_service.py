"""Safe, asynchronous Instagram video preparation for Discord uploads."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable
from urllib.parse import urlsplit

from utils.logger import log as _base_log


log = _base_log.bind(src="instagram")
_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_SUPPORTED_HOSTS = frozenset(
    {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}
)
_SUPPORTED_KINDS = frozenset({"reel", "reels", "p", "tv"})
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


class InstagramError(Exception):
    user_message = "Не удалось получить видео. Возможно, публикация закрыта или удалена."


class UnsupportedInstagramUrl(InstagramError):
    user_message = "Эта ссылка Instagram не поддерживается."


class InstagramUnavailable(InstagramError):
    user_message = "Загрузка Instagram сейчас недоступна: на сервере отсутствует yt-dlp или FFmpeg."


class InstagramBusy(InstagramError):
    user_message = "Сервис загрузки временно занят. Попробуйте немного позже."


class InstagramDuplicate(InstagramError):
    user_message = "Этот ролик уже обрабатывается или был отправлен совсем недавно."


class InstagramTooLong(InstagramError):
    def __init__(self, maximum: int):
        super().__init__(maximum)
        self.user_message = f"Видео слишком длинное. Максимальная длительность — {maximum} секунд."


class InstagramTooLarge(InstagramError):
    user_message = "Видео слишком большое для отправки в Discord."


class InstagramTimeout(InstagramError):
    user_message = "Обработка видео заняла слишком много времени."


@dataclass(frozen=True)
class InstagramConfig:
    auto_download: bool = True
    max_duration_seconds: int = 300
    max_concurrent_downloads: int = 2
    cookies_file: Path | None = None
    fallback_upload_limit_mb: int = 10
    timeout_seconds: int = 120
    cache_ttl_seconds: int = 600

    @classmethod
    def from_env(cls) -> "InstagramConfig":
        cookies_value = os.getenv("INSTAGRAM_COOKIES_FILE", "").strip()
        cookies_path = Path(cookies_value).expanduser() if cookies_value else None
        if cookies_path is not None and not cookies_path.is_file():
            log.warning(
                "INSTAGRAM_COOKIES_FILE is configured but unavailable; continuing without cookies"
            )
            cookies_path = None
        return cls(
            auto_download=_env_bool("INSTAGRAM_AUTO_DOWNLOAD", True),
            max_duration_seconds=_env_int("INSTAGRAM_MAX_DURATION_SECONDS", 300, 1, 3600),
            max_concurrent_downloads=_env_int("INSTAGRAM_MAX_CONCURRENT_DOWNLOADS", 2, 1, 8),
            cookies_file=cookies_path,
            fallback_upload_limit_mb=_env_int("DISCORD_UPLOAD_LIMIT_MB", 10, 1, 500),
            timeout_seconds=_env_int("INSTAGRAM_TIMEOUT_SECONDS", 120, 15, 600),
            cache_ttl_seconds=_env_int("INSTAGRAM_CACHE_TTL_SECONDS", 600, 30, 86400),
        )


@dataclass(frozen=True)
class InstagramMetadata:
    source_url: str
    shortcode: str
    duration: float
    uploader: str
    caption: str


@dataclass(frozen=True)
class PreparedInstagramMedia:
    path: Path
    filename: str
    metadata: InstagramMetadata


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def normalize_instagram_url(value: str) -> str:
    """Validate and canonicalize one supported Instagram post URL."""
    candidate = str(value or "").strip().rstrip(".,!?:;)]}\"'")
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise UnsupportedInstagramUrl() from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or host not in _SUPPORTED_HOSTS:
        raise UnsupportedInstagramUrl()
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsupportedInstagramUrl() from exc
    if parsed.username or parsed.password or port is not None:
        raise UnsupportedInstagramUrl()
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() not in _SUPPORTED_KINDS:
        raise UnsupportedInstagramUrl()
    shortcode = parts[1]
    if not _SHORTCODE_RE.fullmatch(shortcode):
        raise UnsupportedInstagramUrl()
    kind = "reel" if parts[0].lower() in {"reel", "reels"} else parts[0].lower()
    return f"https://www.instagram.com/{kind}/{shortcode}/"


def find_instagram_urls(text: str) -> list[str]:
    found: list[str] = []
    for raw_url in _URL_RE.findall(str(text or "")):
        try:
            normalized = normalize_instagram_url(raw_url)
        except UnsupportedInstagramUrl:
            continue
        if normalized not in found:
            found.append(normalized)
    return found


def calculate_video_bitrate_kbps(
    duration_seconds: float,
    upload_limit_bytes: int,
    *,
    audio_kbps: int = 80,
    container_reserve: float = 0.08,
) -> int:
    if duration_seconds <= 0 or upload_limit_bytes <= 0:
        raise ValueError("duration and upload limit must be positive")
    usable_bits = upload_limit_bytes * 8 * (1.0 - container_reserve)
    total_kbps = usable_bits / duration_seconds / 1000
    return max(64, int(total_kbps - audio_kbps))


def _locate_executable(name: str) -> tuple[str, ...] | None:
    executable = shutil.which(name)
    if executable:
        return (executable,)
    beside_python = Path(sys.executable).with_name(name + (".exe" if os.name == "nt" else ""))
    if beside_python.is_file():
        return (str(beside_python),)
    if name == "yt-dlp":
        try:
            __import__("yt_dlp")
        except ImportError:
            return None
        return (sys.executable, "-m", "yt_dlp")
    return None


class InstagramMediaService:
    def __init__(
        self,
        config: InstagramConfig | None = None,
        *,
        yt_dlp_command: tuple[str, ...] | None = None,
        ffmpeg_command: tuple[str, ...] | None = None,
        temp_root: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config or InstagramConfig.from_env()
        self.yt_dlp_command = yt_dlp_command or _locate_executable("yt-dlp")
        self.ffmpeg_command = ffmpeg_command or _locate_executable("ffmpeg")
        self.temp_root = temp_root
        self._clock = clock
        self._state_lock = asyncio.Lock()
        self._active_urls: set[str] = set()
        self._active_users: set[int] = set()
        self._recent_urls: OrderedDict[str, float] = OrderedDict()

    @property
    def available(self) -> bool:
        return bool(self.yt_dlp_command and self.ffmpeg_command)

    def fallback_upload_limit_bytes(self) -> int:
        return self.config.fallback_upload_limit_mb * 1024 * 1024

    def _purge_recent(self) -> None:
        now = self._clock()
        while self._recent_urls:
            url, expires_at = next(iter(self._recent_urls.items()))
            if expires_at > now:
                break
            self._recent_urls.pop(url, None)

    async def _claim(self, url: str, user_id: int) -> None:
        async with self._state_lock:
            self._purge_recent()
            if url in self._active_urls or url in self._recent_urls:
                raise InstagramDuplicate()
            if user_id in self._active_users or len(self._active_urls) >= self.config.max_concurrent_downloads:
                raise InstagramBusy()
            self._active_urls.add(url)
            self._active_users.add(user_id)

    async def _release(self, url: str, user_id: int, *, completed: bool) -> None:
        async with self._state_lock:
            self._active_urls.discard(url)
            self._active_users.discard(user_id)
            if completed:
                self._recent_urls[url] = self._clock() + self.config.cache_ttl_seconds
                self._recent_urls.move_to_end(url)
                while len(self._recent_urls) > 256:
                    self._recent_urls.popitem(last=False)

    @asynccontextmanager
    async def prepare(
        self,
        raw_url: str,
        *,
        user_id: int,
        upload_limit_bytes: int | None = None,
    ) -> AsyncIterator[PreparedInstagramMedia]:
        url = normalize_instagram_url(raw_url)
        if not self.available:
            raise InstagramUnavailable()
        await self._claim(url, int(user_id))
        completed = False
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            temp_dir = tempfile.TemporaryDirectory(prefix="vipik-instagram-", dir=self.temp_root)
            limit = int(upload_limit_bytes or self.fallback_upload_limit_bytes())
            try:
                media = await asyncio.wait_for(
                    self._build_media(url, Path(temp_dir.name), limit),
                    timeout=self.config.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise InstagramTimeout() from exc
            yield media
            completed = True
        finally:
            try:
                if temp_dir is not None:
                    temp_dir.cleanup()
            finally:
                await self._release(url, int(user_id), completed=completed)

    async def _build_media(
        self,
        url: str,
        temp_dir: Path,
        upload_limit_bytes: int,
    ) -> PreparedInstagramMedia:
        metadata = await self._fetch_metadata(url)
        if metadata.duration > self.config.max_duration_seconds:
            raise InstagramTooLong(self.config.max_duration_seconds)
        source = await self._download(url, temp_dir)
        normalized = temp_dir / "discord.mp4"
        await self._transcode(source, normalized)
        output = normalized
        if output.stat().st_size > upload_limit_bytes:
            compressed = temp_dir / "discord-compressed.mp4"
            await self._compress(normalized, compressed, metadata.duration, upload_limit_bytes)
            output = compressed
        if not output.is_file() or output.stat().st_size > upload_limit_bytes:
            raise InstagramTooLarge()
        return PreparedInstagramMedia(
            path=output,
            filename=f"instagram_{metadata.shortcode}.mp4",
            metadata=metadata,
        )

    def _cookie_args(self) -> list[str]:
        if self.config.cookies_file and self.config.cookies_file.is_file():
            return ["--cookies", str(self.config.cookies_file)]
        return []

    async def _fetch_metadata(self, url: str) -> InstagramMetadata:
        stdout = await self._run_process([
            *self.yt_dlp_command,
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
            "--no-cache-dir",
            *self._cookie_args(),
            url,
        ], stage="metadata")
        try:
            data = json.loads(stdout)
            duration = float(data.get("duration") or 0)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InstagramError() from exc
        if duration <= 0:
            raise InstagramError()
        availability = str(data.get("availability") or "").strip().lower()
        if availability and availability not in {"public", "unlisted"}:
            raise InstagramError()
        shortcode = urlsplit(url).path.strip("/").split("/")[1]
        return InstagramMetadata(
            source_url=url,
            shortcode=shortcode,
            duration=duration,
            uploader=str(data.get("uploader") or data.get("channel") or "неизвестен")[:100],
            caption=str(data.get("description") or data.get("title") or "").strip()[:700],
        )

    async def _download(self, url: str, temp_dir: Path) -> Path:
        template = temp_dir / "source.%(ext)s"
        await self._run_process([
            *self.yt_dlp_command,
            "--no-playlist",
            "--no-warnings",
            "--no-cache-dir",
            "--restrict-filenames",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            str(template),
            *self._cookie_args(),
            url,
        ], stage="download")
        candidates = [path for path in temp_dir.glob("source.*") if path.is_file()]
        if not candidates:
            raise InstagramError()
        source = max(candidates, key=lambda path: path.stat().st_size)
        if temp_dir.resolve() not in source.resolve().parents:
            raise InstagramError()
        return source

    async def _transcode(self, source: Path, target: Path) -> None:
        await self._run_process([
            *self.ffmpeg_command,
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            str(target),
        ], stage="transcode")

    async def _compress(self, source: Path, target: Path, duration: float, limit: int) -> None:
        video_kbps = calculate_video_bitrate_kbps(duration, limit, audio_kbps=80)
        await self._run_process([
            *self.ffmpeg_command,
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf",
            "scale=min(1280\\,iw):min(720\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{int(video_kbps * 1.2)}k",
            "-bufsize", f"{video_kbps * 2}k",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "80k",
            "-movflags", "+faststart",
            str(target),
        ], stage="compress")

    async def _run_process(self, args: list[str], *, stage: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await process.communicate()
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                raise
        except OSError as exc:
            log.error("Instagram {} process could not start: {}", stage, type(exc).__name__)
            raise InstagramUnavailable() from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-1200:]
            if self.config.cookies_file:
                detail = detail.replace(str(self.config.cookies_file), "<cookies-file>")
            log.warning("Instagram {} failed code={}: {}", stage, process.returncode, detail)
            raise InstagramError()
        return stdout.decode("utf-8", errors="replace").strip()
