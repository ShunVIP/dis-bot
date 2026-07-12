from __future__ import annotations

import sqlite3
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web
from core import birthday_store, community_store, economy, economy_profile, game_profiles, ml_artifacts, parody_feedback_store, parody_message_store, parody_model_service, platform_store, profile_service, settings_migration, settings_store, web_app_store
from core.db import connection as db_connection
from core.data_catalog import audit_all, ml_data_manifest, repair_wwm_orphan_features
from core.admin_panel import _member_has_admin_access
from core.summary_service import (
    DEFAULT_SUMMARY_TEXTS,
    block_enabled,
    bounded_int,
    merge_summary_settings,
    render_summary_template,
)
from web_app.server import security_middleware
from scripts.build_ml_manifest import build_manifest


class IsolatedDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "social.db")
        self.patches = [
            patch.object(settings_store, "SOCIAL_DB", self.db_path),
            patch.object(community_store, "SOCIAL_DB", self.db_path),
            patch.object(economy, "DB_PATH", self.db_path),
            patch.object(economy_profile, "DB_PATH", self.db_path),
            patch.object(settings_migration, "SOCIAL_DB", self.db_path),
            patch.object(settings_migration, "BIRTHDAYS_DB", self.db_path),
            patch.object(birthday_store, "BIRTHDAYS_DB", self.db_path),
            patch.object(game_profiles, "SOCIAL_DB", self.db_path),
            patch.object(profile_service, "SOCIAL_DB", self.db_path),
            patch.object(web_app_store, "SOCIAL_DB", self.db_path),
            patch.object(platform_store, "SOCIAL_DB", self.db_path),
            patch.object(parody_message_store, "DB_PATH", self.db_path),
            patch.object(parody_feedback_store, "PARODY_RATINGS_DB", self.db_path),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()


class SettingsStoreTests(IsolatedDatabaseTest):
    def test_feature_policy_uses_one_output_and_channel_precedence(self):
        settings_store.set_feature_channel(1, "daily_summary", 10, "output")
        settings_store.set_feature_channel(1, "daily_summary", 20, "output")
        settings_store.set_feature_channel(1, "daily_summary", 30, "allow")
        settings_store.set_feature_channel(1, "daily_summary", 31, "exclude")

        policy = settings_store.get_feature_policy(1, "daily_summary")

        self.assertEqual(policy.output_channel_id, 20)
        self.assertEqual(policy.allowed_channel_ids, (30,))
        self.assertEqual(policy.excluded_channel_ids, (31,))
        self.assertTrue(settings_store.is_channel_allowed(1, "daily_summary", 30))
        self.assertFalse(settings_store.is_channel_allowed(1, "daily_summary", 31))
        self.assertFalse(settings_store.is_channel_allowed(1, "daily_summary", 99))

    def test_payload_updates_merge_without_losing_existing_keys(self):
        settings_store.set_feature_payload(7, "daily_summary", {"theme": "neon", "limit": 3})
        settings_store.set_feature_payload(7, "daily_summary", {"limit": 5})
        self.assertEqual(
            settings_store.get_feature_payload(7, "daily_summary"),
            {"theme": "neon", "limit": 5},
        )

    def test_runtime_state_is_separate_from_user_configuration(self):
        settings_store.set_feature_payload(9, "economy", {"tax_rate_pct": 10})
        settings_store.set_feature_runtime_state(9, "economy", {"tax_last_run": "2026-07-12T10:00:00+00:00"})
        self.assertEqual(settings_store.get_feature_payload(9, "economy"), {"tax_rate_pct": 10})
        self.assertEqual(
            settings_store.get_feature_runtime_state(9, "economy"),
            {"tax_last_run": "2026-07-12T10:00:00+00:00"},
        )

    def test_legacy_tax_config_migrates_once_for_active_guild(self):
        with db_connection(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE tax_config(id INTEGER PRIMARY KEY, enabled INTEGER, rate_pct INTEGER, interval_h INTEGER, last_run TEXT)"
            )
            conn.execute(
                "INSERT INTO tax_config VALUES(1, 1, 15, 72, '2026-07-01T00:00:00+00:00')"
            )
        result = settings_migration.seed_admin_settings_from_legacy(guild_ids=[123])
        self.assertEqual(result["economy"], 1)
        self.assertEqual(
            settings_store.get_feature_payload(123, "economy"),
            {"tax_enabled": True, "tax_rate_pct": 15, "tax_interval_h": 72},
        )
        self.assertEqual(
            settings_store.get_feature_runtime_state(123, "economy")["tax_last_run"],
            "2026-07-01T00:00:00+00:00",
        )
        self.assertEqual(settings_migration.seed_admin_settings_from_legacy(guild_ids=[123])["economy"], 0)


class EconomyTests(IsolatedDatabaseTest):
    def test_positive_credit_requires_completed_profile_and_writes_ledger(self):
        self.assertEqual(economy.add_coins(42, 10, "daily"), 0)
        economy_profile.set_economy_profile(42, economy_profile.GENDER_MALE, True)
        self.assertEqual(economy.add_coins(42, 10, "daily", {"streak": 1}), 10)
        self.assertEqual(economy.add_coins(42, -3, "purchase"), 7)

        with db_connection(self.db_path) as conn:
            ledger = conn.execute(
                "SELECT delta, reason FROM coin_ledger WHERE user_id=? ORDER BY id",
                (42,),
            ).fetchall()
        self.assertEqual(ledger, [(10, "daily"), (-3, "purchase")])

    def test_profile_and_wallet_share_the_canonical_database(self):
        economy_profile.set_economy_profile(8, economy_profile.GENDER_FEMALE, True)
        economy.add_coins(8, 25, "seed")
        with db_connection(self.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("economy_profiles", tables)
        self.assertIn("coins_wallet", tables)
        self.assertIn("coin_ledger", tables)


class UnifiedProfileTests(IsolatedDatabaseTest):
    def test_profile_combines_user_owned_data_and_game_connections(self):
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE steam_profiles(user_id INTEGER PRIMARY KEY, steam_id TEXT NOT NULL, added_at TEXT NOT NULL);
                CREATE TABLE steam_owned_games_cache(
                    user_id INTEGER NOT NULL, appid INTEGER NOT NULL, name TEXT NOT NULL,
                    playtime_forever INTEGER NOT NULL, playtime_2weeks INTEGER NOT NULL DEFAULT 0,
                    last_played INTEGER NOT NULL DEFAULT 0, checked_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, appid)
                );
                CREATE TABLE wwm_profiles(
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, game_nick TEXT NOT NULL,
                    nick_synced INTEGER NOT NULL, character_card TEXT NOT NULL DEFAULT '',
                    character_updated_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(guild_id, user_id)
                );
                INSERT INTO steam_profiles VALUES(77, '76561198000000000', 'now');
                INSERT INTO steam_owned_games_cache VALUES(77, 1, 'Game', 120, 0, 0, 'now');
                INSERT INTO wwm_profiles VALUES(1, 77, 'WindFox', 1, '', '', 'now', 'now');
                """
            )
        profile = profile_service.update_unified_profile(
            77,
            {
                "community": {"display_name": "Fox", "status_text": "В игре", "accent_color": "#123456"},
                "birthday": "12.07",
                "economy": {"gender": "male", "age_confirmed": True},
            },
        )
        self.assertEqual(profile["community"]["display_name"], "Fox")
        self.assertEqual(profile["birthday"]["birthday"], "12.07")
        self.assertTrue(profile["economy"]["profile"]["age_confirmed"])
        self.assertEqual(profile["games"]["steam"]["cached_games"], 1)
        self.assertEqual(profile["games"]["wwm"]["game_nick"], "WindFox")


class WebSecurityTests(IsolatedDatabaseTest):
    def test_oauth_tokens_are_scrubbed_and_sessions_are_hashed(self):
        web_app_store.ensure_web_tables()
        web_app_store.upsert_web_user(5, "user", access_token="secret-a", refresh_token="secret-r")
        session = web_app_store.create_session(5)
        with db_connection(self.db_path) as conn:
            token_row = conn.execute("SELECT access_token, refresh_token FROM web_users WHERE discord_user_id=5").fetchone()
            stored_session = conn.execute("SELECT session_id FROM web_sessions WHERE discord_user_id=5").fetchone()[0]
        self.assertEqual(token_row, ("", ""))
        self.assertNotEqual(stored_session, session)
        self.assertEqual(len(stored_session), 64)
        self.assertEqual(web_app_store.get_session_user(session)["id"], 5)

    def test_discord_login_code_is_hashed_single_use_and_expires(self):
        web_app_store.upsert_web_user(6, "discord-user")
        code = web_app_store.issue_login_code(6)
        normalized = code.replace("-", "")
        with db_connection(self.db_path) as conn:
            stored = conn.execute("SELECT code_hash FROM web_login_codes WHERE discord_user_id=6").fetchone()[0]
        self.assertNotEqual(stored, normalized)
        self.assertEqual(len(stored), 64)
        self.assertEqual(web_app_store.consume_login_code(code)["id"], 6)
        self.assertIsNone(web_app_store.consume_login_code(code))

    def test_security_middleware_blocks_cross_origin_writes_and_sets_headers(self):
        async def scenario():
            app = web.Application(middlewares=[security_middleware])
            async def write(_request):
                return web.json_response({"ok": True})
            async def parse_json(request):
                await request.json()
                return web.json_response({"ok": True})
            app.router.add_post("/write", write)
            app.router.add_post("/parse-json", parse_json)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            async with ClientSession() as session:
                async with session.post(base + "/write") as response:
                    self.assertEqual(response.status, 403)
                async with session.post(base + "/write", headers={"Origin": base}) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                    self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
                async with session.post(
                    base + "/parse-json",
                    data="{bad\\json}",
                    headers={"Origin": base, "Content-Type": "application/json"},
                ) as response:
                    self.assertEqual(response.status, 400)
                    self.assertEqual((await response.json())["error"], "bad_json")
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            await runner.cleanup()

        asyncio.run(scenario())


class PlatformDmTests(IsolatedDatabaseTest):
    def test_dm_pair_uses_one_shared_thread_and_rejects_third_user(self):
        for user_id in (1, 2, 3):
            web_app_store.upsert_web_user(user_id, f"user-{user_id}")
        first = platform_store.get_or_create_dm(1, 2)
        second = platform_store.get_or_create_dm(2, 1)
        self.assertEqual(first["id"], second["id"])
        message_id = platform_store.add_platform_message("dm", first["id"], 1, "one", "secret")
        self.assertTrue(platform_store.can_access_platform_target("dm", first["id"], 1))
        self.assertTrue(platform_store.can_access_platform_target("dm", first["id"], 2))
        self.assertFalse(platform_store.can_access_platform_target("dm", first["id"], 3))
        self.assertEqual(platform_store.list_platform_messages("dm", first["id"])[0]["id"], message_id)
        self.assertEqual(platform_store.list_dm_threads(1)[0]["peer_id"], 2)
        self.assertEqual(platform_store.list_dm_threads(2)[0]["peer_id"], 1)
        self.assertFalse(platform_store.edit_platform_message(message_id, 2, "changed", can_admin=True))
        self.assertFalse(platform_store.delete_platform_message(message_id, 2, can_admin=True))
        self.assertEqual(platform_store.list_platform_messages("dm", first["id"])[0]["content"], "secret")

    def test_legacy_reciprocal_threads_are_merged_with_messages(self):
        platform_store.ensure_platform_tables()
        with db_connection(self.db_path) as conn:
            conn.execute("DROP INDEX idx_platform_dm_pair")
            conn.execute(
                "INSERT INTO platform_dm_threads(owner_id, peer_id, title, created_at, updated_at) VALUES(10,20,'','now','now')"
            )
            first_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO platform_dm_threads(owner_id, peer_id, title, created_at, updated_at) VALUES(20,10,'','now','now')"
            )
            second_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO platform_messages(scope,target_id,author_id,author_name,content,created_at) VALUES('dm',?,20,'two','old','now')",
                (second_id,),
            )
        platform_store.ensure_platform_tables()
        with db_connection(self.db_path) as conn:
            threads = conn.execute("SELECT id, member_low, member_high FROM platform_dm_threads").fetchall()
            message = conn.execute("SELECT id, target_id FROM platform_messages WHERE content='old'").fetchone()
            archived_threads = conn.execute(
                "SELECT id, owner_id, peer_id FROM platform_dm_threads_legacy_backup ORDER BY id"
            ).fetchall()
            archived_target = conn.execute(
                "SELECT message_id, original_target_id FROM platform_dm_message_target_backup"
            ).fetchone()
        self.assertEqual(threads, [(first_id, 10, 20)])
        self.assertEqual(message[1], first_id)
        self.assertEqual(archived_threads, [(first_id, 10, 20), (second_id, 20, 10)])
        self.assertEqual(archived_target, (message[0], second_id))


class MlArtifactTests(unittest.TestCase):
    def test_empty_manifest_is_materialized_for_observability(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "models"
            with patch.object(ml_artifacts, "MODELS_DIR", root), patch.object(
                ml_artifacts, "MANIFEST_PATH", root / "manifest.json"
            ):
                manifest = ml_artifacts.ensure_artifact_manifest()
            self.assertEqual(manifest["artifacts"], [])
            self.assertTrue((root / "manifest.json").is_file())

    def test_manifest_is_atomic_versioned_and_rejects_external_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "models"
            artifact = root / "42_mem.json"
            root.mkdir()
            artifact.write_text('{"chain": true}', encoding="utf-8")
            with patch.object(ml_artifacts, "MODELS_DIR", root), patch.object(
                ml_artifacts, "MANIFEST_PATH", root / "manifest.json"
            ):
                record = ml_artifacts.register_artifact(
                    pipeline="parody_markov",
                    user_id=42,
                    kind="mem",
                    path=artifact,
                    source_rows=120,
                    execution_location="local_pc",
                )
                manifest = ml_artifacts.load_artifact_manifest(verify_files=True)
                self.assertEqual(manifest["schema_version"], 1)
                self.assertEqual(manifest["artifacts"][0]["version"], record["sha256"][:16])
                self.assertTrue(manifest["artifacts"][0]["available"])
                self.assertTrue(manifest["artifacts"][0]["hash_matches"])
                with self.assertRaises(ValueError):
                    ml_artifacts.register_artifact(
                        pipeline="bad",
                        user_id=None,
                        kind="outside",
                        path=Path(temp_dir) / "outside.bin",
                        source_rows=0,
                        execution_location="local_pc",
                    )

    def test_bootstrap_discovers_existing_markov_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "models"
            root.mkdir()
            (root / "77_mem.json").write_text('{"chain": true}', encoding="utf-8")
            with patch("scripts.build_ml_manifest.MODELS_DIR", root), patch.object(
                ml_artifacts, "MODELS_DIR", root
            ), patch.object(ml_artifacts, "MANIFEST_PATH", root / "manifest.json"), patch(
                "scripts.build_ml_manifest._message_counts", return_value={77: 321}
            ):
                result = build_manifest()
                manifest = ml_artifacts.load_artifact_manifest()
            self.assertEqual(result, {"registered": 1, "artifacts": 1, "available": 1})
            self.assertEqual(manifest["artifacts"][0]["source_rows"], 321)


class ParodyLayerTests(IsolatedDatabaseTest):
    def test_message_store_owns_corpus_checkpoints_ranges_and_merge(self):
        rows = [
            (10, "first", 1, 2, 100, "old", "2024-01-01T00:00:00+00:00"),
            (10, "first", 1, 2, 101, "new", "2025-01-01T00:00:00+00:00"),
            (20, "second", 1, 2, 102, "merge", "2025-02-01T00:00:00+00:00"),
        ]
        self.assertEqual(parody_message_store.save_messages(rows), 3)
        self.assertEqual(parody_message_store.save_messages(rows), 0)
        parody_message_store.upsert_user(10, "first")
        parody_message_store.update_checkpoint(2, 101)
        self.assertEqual(parody_message_store.get_checkpoint(2), 101)
        self.assertEqual(parody_message_store.get_user_messages_between_years(10, 2024, 2025), ["old", "new"])
        self.assertEqual(parody_message_store.get_user_stats(10)["count"], 2)
        self.assertEqual(parody_message_store.merge_user_messages(10, 20), 1)
        self.assertEqual(parody_message_store.get_user_messages(10), ["old", "new", "merge"])
        self.assertEqual(parody_message_store.reset_checkpoints(), 1)

    def test_feedback_store_validates_and_reads_training_signals(self):
        parody_feedback_store.save_rating(10, "разум", "good", 1, 99)
        parody_feedback_store.save_rating(10, "разум", "bad", -1, 98)
        self.assertEqual(parody_feedback_store.get_good_phrases(10, "разум"), ["good"])
        self.assertEqual(parody_feedback_store.get_bad_phrases(10, "разум"), {"bad"})
        with self.assertRaises(ValueError):
            parody_feedback_store.save_rating(10, "разум", "invalid", 0, 99)

    def test_model_service_writes_atomically_and_registers_artifact(self):
        class FakeModel:
            @staticmethod
            def to_json():
                return '{"model": "ok"}'

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "models"
            with patch.object(parody_model_service, "MODELS_DIR", root), patch.object(
                ml_artifacts, "MODELS_DIR", root
            ), patch.object(ml_artifacts, "MANIFEST_PATH", root / "manifest.json"):
                parody_model_service.save_model(10, "мем", FakeModel(), source_rows=55)
                manifest = ml_artifacts.load_artifact_manifest(verify_files=True)
                self.assertEqual(manifest["artifacts"][0]["source_rows"], 55)
                self.assertTrue(manifest["artifacts"][0]["available"])
                self.assertEqual(list(root.glob("*.tmp.*")), [])
                self.assertEqual(parody_model_service.remove_user_models(10), 1)
                self.assertEqual(ml_artifacts.load_artifact_manifest()["artifacts"], [])


class PermissionTests(IsolatedDatabaseTest):
    def test_first_user_bootstraps_as_owner_and_admin(self):
        community_store.ensure_first_owner(100)
        self.assertTrue(community_store.has_admin_access(100))
        self.assertFalse(community_store.has_admin_access(101))

    def test_discord_admin_panel_accepts_only_admin_or_manage_guild(self):
        regular = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False, manage_guild=False))
        manager = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False, manage_guild=True))
        admin = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True, manage_guild=False))
        self.assertFalse(_member_has_admin_access(None))
        self.assertFalse(_member_has_admin_access(regular))
        self.assertTrue(_member_has_admin_access(manager))
        self.assertTrue(_member_has_admin_access(admin))


class SummaryServiceTests(unittest.TestCase):
    def test_template_keeps_unknown_placeholders_for_future_compatibility(self):
        rendered = render_summary_template("{guild}: {date} {future}", guild="ViPik", date="12.07")
        self.assertEqual(rendered, "ViPik: 12.07 {future}")

    def test_invalid_template_is_returned_without_crashing_scheduler(self):
        self.assertEqual(render_summary_template("bad {", guild="ViPik"), "bad {")

    def test_settings_merge_trims_text_and_preserves_boolean_values(self):
        merged = merge_summary_settings({"daily_title_template": "  Итог {date}  ", "summary_buttons_enabled": False})
        self.assertEqual(merged["daily_title_template"], "Итог {date}")
        self.assertFalse(merged["summary_buttons_enabled"])
        self.assertEqual(merged["weekly_title_template"], DEFAULT_SUMMARY_TEXTS["weekly_title_template"])

    def test_bounds_and_block_flags_are_stable_for_admin_payloads(self):
        self.assertEqual(bounded_int({"limit": "999"}, "limit", 3, maximum=10), 10)
        self.assertEqual(bounded_int({"limit": "bad"}, "limit", 3), 3)
        self.assertTrue(block_enabled({}, "daily_block_stats"))
        self.assertFalse(block_enabled({"daily_block_stats": "выкл"}, "daily_block_stats"))


class DataCatalogTests(IsolatedDatabaseTest):
    def test_read_only_audit_reports_integrity_schema_and_ml_manifest(self):
        settings_store.set_feature_payload(1, "daily_summary", {"theme": "neon"})
        audit = audit_all({"social": self.db_path})
        self.assertEqual(audit["summary"]["healthy"], 1)
        self.assertGreaterEqual(audit["summary"]["tables"], 2)
        self.assertEqual(audit["databases"][0]["integrity"], "ok")
        manifest = ml_data_manifest(audit)
        self.assertEqual(manifest["datasets"]["community_activity"]["training_location"], "local_pc")
        self.assertEqual(manifest["datasets"]["community_activity"]["inference_location"], "vps")
        self.assertIn("artifact_registry", manifest)

    def test_wwm_repair_archives_orphan_features_before_deleting(self):
        wwm_path = str(Path(self.temp_dir.name) / "wwm.db")
        with db_connection(wwm_path) as conn:
            conn.executescript(
                """
                CREATE TABLE entities(entity_id INTEGER PRIMARY KEY);
                CREATE TABLE entity_features(
                    entity_id INTEGER PRIMARY KEY,
                    predicted_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    snippet_en TEXT,
                    keywords_json TEXT,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO entities VALUES(1);
                INSERT INTO entity_features VALUES(1, 'npc', 0.9, '', '[]', 'now');
                INSERT INTO entity_features VALUES(2, 'quest', 0.8, '', '[]', 'now');
                """
            )
        result = repair_wwm_orphan_features(wwm_path)
        self.assertEqual(result, {"found": 1, "archived": 1, "deleted": 1, "remaining": 0})
        with db_connection(wwm_path) as conn:
            self.assertEqual(conn.execute("SELECT entity_id FROM entity_features").fetchall(), [(1,)])
            self.assertEqual(conn.execute("SELECT entity_id FROM orphan_entity_features_backup").fetchall(), [(2,)])


if __name__ == "__main__":
    unittest.main()
