from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.instagram_service import (
    InstagramConfig,
    InstagramDuplicate,
    InstagramError,
    InstagramMediaService,
    InstagramMetadata,
    InstagramTooLong,
    PreparedInstagramMedia,
    UnsupportedInstagramUrl,
    calculate_video_bitrate_kbps,
    find_instagram_urls,
    normalize_instagram_url,
)
from core import menu_catalog_service
from fun_slesh import instagram as instagram_cog


class InstagramUrlTests(unittest.TestCase):
    def test_slash_command_is_visible_and_classified_as_entertainment(self):
        self.assertEqual(instagram_cog.Instagram.instagram.name, "instagram")
        self.assertIn("instagram", menu_catalog_service.PUBLIC_SLASH_COMMANDS)
        self.assertEqual(
            menu_catalog_service.category_for_command("instagram", "fun_slesh.instagram"),
            "🎲 Развлечения",
        )

    def test_reel_url_is_recognized_and_query_is_removed(self):
        self.assertEqual(
            normalize_instagram_url("https://instagram.com/reel/AbC_123/?igsh=secret&utm_source=x"),
            "https://www.instagram.com/reel/AbC_123/",
        )

    def test_video_post_url_is_recognized(self):
        self.assertEqual(
            normalize_instagram_url("https://www.instagram.com/p/CODE-99/"),
            "https://www.instagram.com/p/CODE-99/",
        )

    def test_fake_domain_is_rejected(self):
        with self.assertRaises(UnsupportedInstagramUrl):
            normalize_instagram_url("https://instagram.com.example.org/reel/AbC123/")

    def test_only_supported_urls_are_extracted_in_original_order(self):
        urls = find_instagram_urls(
            "сначала https://example.org/x потом https://www.instagram.com/reel/First1/?x=1 "
            "и https://instagram.com/p/Second2/"
        )
        self.assertEqual(
            urls,
            [
                "https://www.instagram.com/reel/First1/",
                "https://www.instagram.com/p/Second2/",
            ],
        )

    def test_compression_bitrate_reserves_audio_and_container_space(self):
        bitrate = calculate_video_bitrate_kbps(60, 10 * 1024 * 1024, audio_kbps=80)
        self.assertGreater(bitrate, 1000)
        self.assertLess(bitrate, 1300)


class InstagramServiceTests(unittest.TestCase):
    @staticmethod
    def _service(temp_root: Path, **config_overrides) -> InstagramMediaService:
        config = InstagramConfig(**config_overrides)
        return InstagramMediaService(
            config,
            yt_dlp_command=("yt-dlp",),
            ffmpeg_command=("ffmpeg",),
            ffprobe_command=("ffprobe",),
            temp_root=temp_root,
        )

    def test_duration_limit_stops_before_download(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                service = self._service(Path(directory), max_duration_seconds=300)
                metadata = InstagramMetadata(
                    source_url="https://www.instagram.com/reel/Long01/",
                    shortcode="Long01",
                    duration=301,
                    uploader="tester",
                    caption="",
                )
                with patch.object(service, "_fetch_metadata", AsyncMock(return_value=metadata)), patch.object(
                    service,
                    "_download",
                    AsyncMock(),
                ) as download:
                    with self.assertRaises(InstagramTooLong):
                        async with service.prepare(metadata.source_url, user_id=1):
                            pass
                    download.assert_not_awaited()

        asyncio.run(scenario())

    def test_missing_metadata_duration_is_probed_after_download(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                service = self._service(Path(directory), max_duration_seconds=300)
                metadata = InstagramMetadata(
                    source_url="https://www.instagram.com/reel/Probe01/",
                    shortcode="Probe01",
                    duration=0,
                    uploader="tester",
                    caption="",
                )
                source = Path(directory) / "source.mp4"
                source.write_bytes(b"video")
                normalized = Path(directory) / "discord.mp4"

                async def fake_transcode(_source: Path, target: Path):
                    target.write_bytes(b"video")

                with patch.object(service, "_fetch_metadata", AsyncMock(return_value=metadata)), patch.object(
                    service,
                    "_download",
                    AsyncMock(return_value=source),
                ), patch.object(service, "_probe_duration", AsyncMock(return_value=42)) as probe, patch.object(
                    service,
                    "_transcode",
                    side_effect=fake_transcode,
                ):
                    media = await service._build_media(metadata.source_url, Path(directory), 1024)
                probe.assert_awaited_once_with(source)
                self.assertEqual(media.metadata.duration, 42)

        asyncio.run(scenario())

    def test_ffmpeg_contract_uses_discord_codecs_faststart_and_720p_bound(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                service = self._service(Path(directory))
                with patch.object(service, "_run_process", AsyncMock(return_value="")) as run:
                    await service._transcode(Path("source.webm"), Path("discord.mp4"))
                    transcode_args = run.await_args.args[0]
                    self.assertIn("libx264", transcode_args)
                    self.assertIn("aac", transcode_args)
                    self.assertIn("+faststart", transcode_args)

                    await service._compress(
                        Path("discord.mp4"),
                        Path("compressed.mp4"),
                        60,
                        10 * 1024 * 1024,
                    )
                    compress_args = run.await_args.args[0]
                    scale = compress_args[compress_args.index("-vf") + 1]
                    self.assertIn("1280", scale)
                    self.assertIn("720", scale)
                    self.assertIn("libx264", compress_args)
                    self.assertIn("aac", compress_args)

        asyncio.run(scenario())

    def test_ytdlp_calls_disable_persistent_cache(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                service = self._service(Path(directory))
                metadata_json = (
                    '{"duration": 10, "uploader": "tester", '
                    '"description": "caption", "availability": "public"}'
                )
                with patch.object(
                    service,
                    "_run_process",
                    AsyncMock(return_value=metadata_json),
                ) as run:
                    await service._fetch_metadata("https://www.instagram.com/reel/Cache01/")
                    self.assertIn("--no-cache-dir", run.await_args.args[0])

        asyncio.run(scenario())

    def test_temporary_directory_is_removed_after_processing_error(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service = self._service(root)
                with patch.object(service, "_build_media", AsyncMock(side_effect=InstagramError())):
                    with self.assertRaises(InstagramError):
                        async with service.prepare("https://instagram.com/reel/Clean01/", user_id=1):
                            pass
                self.assertEqual(list(root.iterdir()), [])

        asyncio.run(scenario())

    def test_failed_temporary_directory_creation_releases_user_and_url(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                service = self._service(Path(directory))
                with patch(
                    "core.instagram_service.tempfile.TemporaryDirectory",
                    side_effect=OSError("temp unavailable"),
                ):
                    with self.assertRaises(OSError):
                        async with service.prepare(
                            "https://instagram.com/reel/Temp01/",
                            user_id=1,
                        ):
                            pass
                self.assertEqual(service._active_urls, set())
                self.assertEqual(service._active_users, set())

        asyncio.run(scenario())

    def test_same_url_cannot_be_processed_concurrently(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                service = self._service(Path(directory), cache_ttl_seconds=600)
                started = asyncio.Event()
                finish = asyncio.Event()

                async def fake_build(url: str, temp_dir: Path, limit: int):
                    started.set()
                    await finish.wait()
                    path = temp_dir / "discord.mp4"
                    path.write_bytes(b"video")
                    return PreparedInstagramMedia(
                        path=path,
                        filename="instagram_Same01.mp4",
                        metadata=InstagramMetadata(url, "Same01", 5, "tester", "caption"),
                    )

                async def first_request():
                    async with service.prepare("https://instagram.com/reel/Same01/", user_id=1) as media:
                        self.assertTrue(media.path.exists())

                with patch.object(service, "_build_media", side_effect=fake_build):
                    task = asyncio.create_task(first_request())
                    await started.wait()
                    with self.assertRaises(InstagramDuplicate):
                        async with service.prepare("https://www.instagram.com/reel/Same01/?x=2", user_id=2):
                            pass
                    finish.set()
                    await task

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
