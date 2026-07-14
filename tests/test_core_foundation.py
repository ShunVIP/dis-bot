from __future__ import annotations

import sqlite3
import asyncio
import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web
from core import activity_rewards_service, activity_rewards_store, activity_service, activity_store, birthday_store, community_store, conversation_store, economy, economy_profile, game_profiles, game_service, game_store, heroes_service, heroes_store, ml_artifacts, ml_insights, parody_feedback_store, parody_message_store, parody_model_service, platform_store, profile_service, rep_roles_service, rep_roles_store, settings_migration, settings_store, summary_stats_store, summary_store, toxicity_model_service, voice_store, web_app_store
from core.db import connection as db_connection
from core.data_catalog import audit_all, ml_data_manifest, repair_wwm_orphan_features
from core.admin_panel import (
    FEATURES_BY_ID,
    _member_has_admin_access,
)
from core.summary_service import (
    DEFAULT_SUMMARY_TEXTS,
    block_enabled,
    bounded_int,
    merge_summary_settings,
    render_summary_template,
)
from web_app import server as web_server
from web_app.server import security_middleware
from scripts.build_ml_manifest import build_manifest
from scripts import audit_settings, finalize_settings_migration
from scripts.train_toxicity_model import train_model
from fun_slesh import social_chat


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
            patch.object(game_store, "SOCIAL_DB", self.db_path),
            patch.object(activity_store, "SOCIAL_DB", self.db_path),
            patch.object(activity_rewards_store, "SOCIAL_DB", self.db_path),
            patch.object(heroes_store, "SOCIAL_DB", self.db_path),
            patch.object(rep_roles_store, "SOCIAL_DB", self.db_path),
            patch.object(conversation_store, "SOCIAL_DB", self.db_path),
            patch.object(profile_service, "SOCIAL_DB", self.db_path),
            patch.object(web_app_store, "SOCIAL_DB", self.db_path),
            patch.object(platform_store, "SOCIAL_DB", self.db_path),
            patch.object(voice_store, "SOCIAL_DB", self.db_path),
            patch.object(summary_store, "SOCIAL_DB", self.db_path),
            patch.object(summary_stats_store, "SOCIAL_DB", self.db_path),
            patch.object(audit_settings, "SOCIAL_DB", self.db_path),
            patch.object(audit_settings, "BIRTHDAYS_DB", self.db_path),
            patch.object(finalize_settings_migration, "SOCIAL_DB", self.db_path),
            patch.object(finalize_settings_migration, "BIRTHDAYS_DB", self.db_path),
            patch.object(parody_message_store, "DB_PATH", self.db_path),
            patch.object(parody_feedback_store, "PARODY_RATINGS_DB", self.db_path),
        ]
        for item in self.patches:
            item.start()
        activity_rewards_store._INITIALIZED_DATABASES.discard(self.db_path)
        heroes_store._INITIALIZED_DATABASES.discard(self.db_path)
        rep_roles_store._INITIALIZED_DATABASES.discard(self.db_path)

    def tearDown(self):
        activity_rewards_store._INITIALIZED_DATABASES.discard(self.db_path)
        heroes_store._INITIALIZED_DATABASES.discard(self.db_path)
        rep_roles_store._INITIALIZED_DATABASES.discard(self.db_path)
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

    def test_feature_channel_entries_preserve_reason(self):
        settings_store.set_feature_channel(7, "message_stats", 55, "exclude", "off-topic")
        self.assertEqual(
            settings_store.list_feature_channels(7, "message_stats", "exclude")[0]["reason"],
            "off-topic",
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

    def test_verified_legacy_settings_are_archived_out_of_runtime_path(self):
        settings_store.set_feature_enabled(77, "daily_summary", True)
        settings_store.set_feature_channel(77, "daily_summary", 88, "output", "migration")
        with db_connection(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE daily_summary_config(guild_id INTEGER PRIMARY KEY, channel_id INTEGER, enabled INTEGER)"
            )
            conn.execute("INSERT INTO daily_summary_config VALUES(77, 88, 1)")

        preview = finalize_settings_migration.finalize(apply=False)
        self.assertTrue(preview["coverage"]["safe_to_finalize"])
        self.assertEqual(preview["actions"]["social"][0]["rows"], 1)
        result = finalize_settings_migration.finalize(apply=True)
        self.assertTrue(result["applied"])

        with db_connection(self.db_path) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("daily_summary_config", names)
            self.assertIn("daily_summary_config_legacy_backup", names)
            self.assertEqual(
                conn.execute("SELECT source_rows FROM settings_migration_archive WHERE table_name='daily_summary_config'").fetchone(),
                (1,),
            )

    def test_empty_legacy_table_is_finalized_and_recreated_duplicate_is_removed(self):
        with db_connection(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE daily_summary_config(guild_id INTEGER PRIMARY KEY, channel_id INTEGER, enabled INTEGER)"
            )
        preview = finalize_settings_migration.finalize(apply=False)
        self.assertTrue(preview["coverage"]["safe_to_finalize"])
        self.assertEqual(preview["actions"]["social"][0]["action"], "archive")
        finalize_settings_migration.finalize(apply=True)

        with db_connection(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE daily_summary_config(guild_id INTEGER PRIMARY KEY, channel_id INTEGER, enabled INTEGER)"
            )
        preview = finalize_settings_migration.finalize(apply=False)
        action = next(item for item in preview["actions"]["social"] if item["table"] == "daily_summary_config")
        self.assertEqual(action["action"], "drop_empty_recreated")
        finalize_settings_migration.finalize(apply=True)
        with db_connection(self.db_path) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("daily_summary_config", names)
        self.assertIn("daily_summary_config_legacy_backup", names)

    def test_activity_tracker_migrates_with_exact_coverage_and_archives(self):
        with db_connection(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE activity_tracker_config(
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    enabled INTEGER NOT NULL,
                    notify_starts INTEGER NOT NULL,
                    notify_ends INTEGER NOT NULL,
                    article_lookup INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO activity_tracker_config VALUES(77, 88, 0, 1, 0, 1)"
            )

        migrated = settings_migration.seed_admin_settings_from_legacy()
        self.assertEqual(migrated["activity_tracker"], 1)
        self.assertEqual(
            activity_service.is_activity_enabled(77),
            False,
        )
        report = audit_settings.build_report()
        self.assertEqual(report["coverage"]["issues"], [])
        self.assertTrue(report["coverage"]["safe_to_finalize"])

        finalized = finalize_settings_migration.finalize(apply=True)
        self.assertTrue(finalized["applied"])
        with db_connection(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertNotIn("activity_tracker_config", tables)
        self.assertIn("activity_tracker_config_legacy_backup", tables)


class ActivityRewardsLayerTests(IsolatedDatabaseTest):
    def test_legacy_reward_settings_and_exclusions_migrate_and_archive(self):
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE activity_rewards_config (
                    guild_id INTEGER PRIMARY KEY,
                    msg_enabled INTEGER NOT NULL DEFAULT 0,
                    msg_per_n INTEGER NOT NULL DEFAULT 10,
                    msg_coins INTEGER NOT NULL DEFAULT 2,
                    msg_rep_per_n INTEGER NOT NULL DEFAULT 50,
                    msg_rep INTEGER NOT NULL DEFAULT 1,
                    voice_enabled INTEGER NOT NULL DEFAULT 0,
                    voice_per_min INTEGER NOT NULL DEFAULT 5,
                    voice_coins INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE activity_excluded_channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(guild_id, channel_id)
                );
                INSERT INTO activity_rewards_config VALUES(77,1,3,4,9,2,1,6,5);
                INSERT INTO activity_excluded_channels VALUES(77,88,'off-topic');
                """
            )

        activity_rewards_store.ensure_activity_rewards_storage()
        config = activity_rewards_store.get_activity_reward_config(77)
        exclusions = activity_rewards_store.list_activity_channel_exclusions(77)
        self.assertEqual(config["msg_per_n"], 3)
        self.assertEqual(config["voice_coins"], 5)
        self.assertEqual(exclusions[0]["reason"], "off-topic")
        with db_connection(self.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            archived = conn.execute(
                "SELECT table_name, source_rows FROM settings_migration_archive "
                "WHERE table_name LIKE 'activity_%' ORDER BY table_name"
            ).fetchall()
        self.assertNotIn("activity_rewards_config", tables)
        self.assertNotIn("activity_excluded_channels", tables)
        self.assertIn("activity_rewards_config_legacy_backup", tables)
        self.assertIn("activity_excluded_channels_legacy_backup", tables)
        self.assertEqual(archived, [("activity_excluded_channels", 1), ("activity_rewards_config", 1)])

    def test_reward_service_uses_canonical_settings_and_shared_economy(self):
        economy_profile.set_economy_profile(42, economy_profile.GENDER_MALE, True)
        activity_rewards_store.update_activity_reward_config(7, {
            "msg_enabled": True,
            "msg_per_n": 2,
            "msg_coins": 3,
            "msg_rep_per_n": 2,
            "msg_rep": 1,
            "voice_enabled": True,
            "voice_per_min": 5,
            "voice_coins": 2,
        })
        self.assertEqual(activity_rewards_service.reward_message(42, 7)["coins"], 0)
        self.assertEqual(activity_rewards_service.reward_message(42, 7), {
            "count": 2, "coins": 3, "reputation": 1,
        })
        self.assertEqual(activity_rewards_service.reward_voice(42, 7, 4 * 60)["coins"], 0)
        self.assertEqual(activity_rewards_service.reward_voice(42, 7, 60)["coins"], 2)
        self.assertEqual(economy.get_balance(42), 5)
        with db_connection(self.db_path) as conn:
            reputation = conn.execute("SELECT SUM(delta) FROM reputation WHERE user_id=42").fetchone()[0]
        self.assertEqual(reputation, 1)

    def test_partial_reward_update_preserves_existing_values(self):
        activity_rewards_store.update_activity_reward_config(5, {"msg_enabled": True, "msg_per_n": 25})
        updated = activity_rewards_store.update_activity_reward_config(5, {"voice_enabled": True, "msg_per_n": None})
        self.assertTrue(updated["msg_enabled"])
        self.assertTrue(updated["voice_enabled"])
        self.assertEqual(updated["msg_per_n"], 25)
        settings_store.set_feature_enabled(5, "activity_rewards", False)
        disabled = activity_rewards_store.get_activity_reward_config(5)
        self.assertFalse(disabled["msg_enabled"])
        self.assertFalse(disabled["voice_enabled"])


class HeroesLayerTests(IsolatedDatabaseTest):
    def test_legacy_channel_moves_to_settings_without_touching_session_history(self):
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE heroes_troll_config(guild_id INTEGER PRIMARY KEY, channel_id INTEGER);
                CREATE TABLE heroes_sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, game_name TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT NOT NULL, seconds INTEGER NOT NULL
                );
                INSERT INTO heroes_troll_config VALUES(77, 88);
                INSERT INTO heroes_sessions(guild_id,user_id,game_name,started_at,ended_at,seconds)
                VALUES(77,42,'Heroes III','2026-07-01T10:00:00+00:00','2026-07-01T11:00:00+00:00',3600);
                """
            )
        heroes_store.ensure_heroes_storage()
        self.assertEqual(heroes_store.get_heroes_output_channel_id(77), 88)
        with db_connection(self.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            sessions = conn.execute("SELECT COUNT(*) FROM heroes_sessions").fetchone()[0]
            archived = conn.execute(
                "SELECT source_rows FROM settings_migration_archive WHERE table_name='heroes_troll_config'"
            ).fetchone()
        self.assertNotIn("heroes_troll_config", tables)
        self.assertIn("heroes_troll_config_legacy_backup", tables)
        self.assertEqual(sessions, 1)
        self.assertEqual(archived, (1,))

    def test_active_and_finished_session_store_round_trip(self):
        started = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
        ended = started + timedelta(minutes=95)
        heroes_store.remember_active_session(1, 2, "Heroes III", started)
        loaded = heroes_store.load_active_sessions()[0]
        self.assertEqual((loaded["guild_id"], loaded["user_id"], loaded["game_name"]), (1, 2, "Heroes III"))
        heroes_store.pop_active_session(1, 2)
        self.assertEqual(heroes_store.load_active_sessions(), [])
        self.assertEqual(heroes_store.save_finished_session(1, 2, "Heroes III", started, ended), 5700)
        with db_connection(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT seconds FROM heroes_sessions").fetchone()[0], 5700)

    def test_heroes_admin_switch_is_honored(self):
        self.assertTrue(heroes_store.heroes_troll_enabled(77))
        settings_store.set_feature_enabled(77, heroes_store.FEATURE_HEROES_TROLL, False)
        self.assertFalse(heroes_store.heroes_troll_enabled(77))

    def test_heroes_detection_and_messages_are_pure_service_logic(self):
        self.assertTrue(heroes_service.is_heroes_name("Heroes of Might & Magic III"))
        self.assertTrue(heroes_service.is_heroes_name("Olden Era"))
        self.assertFalse(heroes_service.is_heroes_name("League of Legends"))
        self.assertEqual(
            heroes_service.find_started_heroes({"Steam"}, {"Steam", "Heroes III"}),
            "Heroes III",
        )
        self.assertEqual(heroes_service.format_duration(5700), "1ч 35м")
        message = heroes_service.build_troll_message(2, "Heroes III", "Игрок")
        self.assertIn("Игрок", message)


class RepRolesLayerTests(IsolatedDatabaseTest):
    def test_legacy_enabled_switch_moves_to_settings_and_data_tables_remain(self):
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE rep_roles_config(guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE rep_role_thresholds(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                    min_rep INTEGER NOT NULL, label TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX idx_rrt_guild_rep ON rep_role_thresholds(guild_id,min_rep);
                INSERT INTO rep_roles_config VALUES(77,0);
                INSERT INTO rep_role_thresholds(guild_id,min_rep,label,created_at) VALUES(77,10,'ветеран','now');
                """
            )
        rep_roles_store.ensure_rep_roles_storage()
        self.assertFalse(rep_roles_store.rep_roles_enabled(77))
        self.assertEqual(rep_roles_store.list_thresholds(77)[0][1:], (10, "ветеран"))
        with db_connection(self.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            archived = conn.execute(
                "SELECT source_rows FROM settings_migration_archive WHERE table_name='rep_roles_config'"
            ).fetchone()
        self.assertNotIn("rep_roles_config", tables)
        self.assertIn("rep_roles_config_legacy_backup", tables)
        self.assertEqual(archived, (1,))

    def test_threshold_and_active_role_store_round_trip(self):
        rep_roles_store.upsert_threshold(1, 10, "новичок")
        rep_roles_store.upsert_threshold(1, 25, "ветеран")
        thresholds = rep_roles_store.list_thresholds(1)
        self.assertEqual([row[1] for row in thresholds], [10, 25])
        self.assertEqual(rep_roles_store.best_threshold(1, 24), (10, "новичок"))
        self.assertEqual(rep_roles_store.next_threshold(1, 10), (25, "ветеран"))

        expires = datetime(2026, 7, 8, tzinfo=timezone.utc)
        rep_roles_store.save_active_role(2, 1, 999, 10, expires)
        self.assertEqual(rep_roles_store.get_active_role(2, 1), (999, 10, False, expires.isoformat()))
        permanent = rep_roles_store.make_role_permanent(2, 1)
        self.assertEqual(permanent, (999, False))
        self.assertTrue(rep_roles_store.get_active_role(2, 1)[2])

        threshold_id = thresholds[0][0]
        self.assertTrue(rep_roles_store.update_threshold(1, threshold_id, 12, "участник"))
        self.assertEqual(rep_roles_store.delete_threshold(1, threshold_id), (12, "участник"))

    def test_role_name_service_is_bounded_and_keeps_level_label(self):
        name = rep_roles_service.generate_role_name(999999, 10, "ветеран")
        self.assertLessEqual(len(name), 100)
        self.assertTrue(name.endswith("· ветеран"))


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

    def test_oauth_method_is_fail_closed_without_complete_configuration(self):
        with patch.object(web_server, "DISCORD_CLIENT_ID", "client"), patch.object(
            web_server, "DISCORD_CLIENT_SECRET", ""
        ), patch.object(web_server, "DISCORD_REDIRECT_URI", "https://example.test/callback"), patch.object(
            web_server, "ALLOWED_GUILD_IDS", frozenset({123})
        ):
            self.assertFalse(web_server._auth_methods()["discord_oauth"])
        with patch.object(web_server, "DISCORD_CLIENT_ID", "client"), patch.object(
            web_server, "DISCORD_CLIENT_SECRET", "secret"
        ), patch.object(web_server, "DISCORD_REDIRECT_URI", "https://example.test/callback"), patch.object(
            web_server, "ALLOWED_GUILD_IDS", frozenset({123})
        ):
            self.assertTrue(web_server._auth_methods()["discord_oauth"])

    def test_security_middleware_blocks_cross_origin_writes_and_sets_headers(self):
        async def scenario():
            app = web.Application(middlewares=[security_middleware])
            async def write(_request):
                return web.json_response({"ok": True})
            async def parse_json(request):
                await request.json()
                return web.json_response({"ok": True})
            app.router.add_post("/write", write)
            app.router.add_post("/api/write", write)
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
                    self.assertNotIn("connect-src 'self' https: wss:", response.headers["Content-Security-Policy"])
                async with session.post(
                    base + "/api/write",
                    headers={"Origin": base, "X-Forwarded-Proto": "https"},
                ) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])
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
    def test_dm_unread_count_and_read_marker_are_member_scoped(self):
        for user_id in (1, 2, 3):
            web_app_store.upsert_web_user(user_id, f"user-{user_id}")
        thread = platform_store.get_or_create_dm(1, 2)
        platform_store.add_platform_message("dm", thread["id"], 1, "one", "first")

        self.assertEqual(platform_store.list_dm_threads(1)[0]["unread_count"], 0)
        self.assertEqual(platform_store.list_dm_threads(2)[0]["unread_count"], 1)
        self.assertFalse(platform_store.mark_dm_read(thread["id"], 3))
        self.assertTrue(platform_store.mark_dm_read(thread["id"], 2))
        self.assertEqual(platform_store.list_dm_threads(2)[0]["unread_count"], 0)

        platform_store.add_platform_message("dm", thread["id"], 1, "one", "second")
        platform_store.add_platform_message("dm", thread["id"], 2, "two", "reply")
        self.assertEqual(platform_store.list_dm_threads(2)[0]["unread_count"], 1)
        self.assertEqual(platform_store.list_dm_threads(1)[0]["unread_count"], 1)

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


class ChatStorageConsolidationTests(IsolatedDatabaseTest):
    def test_legacy_web_chat_is_migrated_once_and_archived(self):
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE web_chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER NOT NULL DEFAULT 0,
                    discord_user_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    attachment_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'web',
                    status TEXT NOT NULL DEFAULT 'stored',
                    edited_at TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE web_chat_reactions (
                    message_id INTEGER NOT NULL,
                    emoji TEXT NOT NULL,
                    discord_user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(message_id, emoji, discord_user_id)
                );
                CREATE TABLE web_bot_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    discord_user_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO web_chat_messages(
                    guild_id, channel_id, discord_user_id, author_name, content,
                    attachment_json, source, status, edited_at, deleted_at, created_at
                ) VALUES(10, 20, 30, 'Legacy', 'hello', '[]', 'discord', 'stored', '', '', 'now');
                INSERT INTO web_chat_reactions VALUES(1, '👍', 40, 'now');
                INSERT INTO web_bot_outbox(
                    guild_id, channel_id, discord_user_id, author_name, content, status, created_at
                ) VALUES(10, 20, 30, 'Legacy', 'queued', 'pending', 'now');
                """
            )

        platform_store.ensure_platform_tables()
        platform_store.ensure_platform_tables()
        messages = platform_store.list_general_chat_messages()
        outbox = platform_store.claim_pending_discord_outbox()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "hello")
        self.assertEqual(messages[0]["discord_user_id"], 30)
        self.assertEqual(messages[0]["source"], "discord")
        self.assertEqual(messages[0]["reactions"], [{"emoji": "👍", "count": 1}])
        self.assertEqual(len(outbox), 1)
        with db_connection(self.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            mapping_count = conn.execute("SELECT COUNT(*) FROM platform_web_chat_migration").fetchone()[0]
        self.assertNotIn("web_chat_messages", tables)
        self.assertNotIn("web_chat_reactions", tables)
        self.assertNotIn("web_bot_outbox", tables)
        self.assertIn("web_chat_messages_retired_backup", tables)
        self.assertIn("web_chat_reactions_retired_backup", tables)
        self.assertIn("web_bot_outbox_retired_backup", tables)
        self.assertEqual(mapping_count, 1)

    def test_general_chat_and_discord_outbox_share_platform_message(self):
        web_app_store.upsert_web_user(7, "seven")
        web_app_store.upsert_web_user(8, "eight")
        message_id = platform_store.add_general_chat_message(
            7, "Seven", "from app", guild_id=11, channel_id=22, source="web"
        )
        queued = platform_store.claim_pending_discord_outbox()
        self.assertEqual(queued[0]["message_id"], message_id)
        self.assertEqual(platform_store.list_general_chat_messages()[0]["status"], "pending")

        platform_store.mark_discord_outbox_sent(queued[0]["id"])
        self.assertEqual(platform_store.list_general_chat_messages()[0]["status"], "sent")

        dm = platform_store.get_or_create_dm(7, 8)
        dm_message = platform_store.add_platform_message("dm", dm["id"], 7, "Seven", "private")
        self.assertFalse(platform_store.edit_general_chat_message(dm_message, 7, "leak"))
        with self.assertRaises(ValueError):
            platform_store.toggle_general_chat_reaction(dm_message, 7, "👍")


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
    def test_owner_bootstrap_requires_explicit_allowlist_decision(self):
        self.assertFalse(community_store.ensure_first_owner(99))
        self.assertFalse(community_store.has_admin_access(99))
        self.assertTrue(community_store.ensure_first_owner(100, bootstrap_allowed=True))
        self.assertTrue(community_store.has_admin_access(100))
        self.assertFalse(community_store.has_admin_access(101))

    def test_web_admission_parses_ids_and_requires_allowed_membership(self):
        self.assertEqual(web_server._id_set("123, bad, 456, -7"), frozenset({123, 456}))
        with patch.object(web_server, "ALLOWED_GUILD_IDS", frozenset({123})):
            self.assertTrue(web_server._has_allowed_guild([{"id": "123"}, {"id": "999"}]))
            self.assertFalse(web_server._has_allowed_guild([{"id": "999"}]))
            self.assertFalse(web_server._has_allowed_guild({"id": "123"}))

    def test_discord_admin_panel_accepts_only_admin_or_manage_guild(self):
        regular = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False, manage_guild=False))
        manager = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False, manage_guild=True))
        admin = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True, manage_guild=False))
        self.assertFalse(_member_has_admin_access(None))
        self.assertFalse(_member_has_admin_access(regular))
        self.assertTrue(_member_has_admin_access(manager))
        self.assertTrue(_member_has_admin_access(admin))


class VoiceRoomTests(IsolatedDatabaseTest):
    def test_private_room_is_hidden_until_invite_is_redeemed(self):
        room = voice_store.create_voice_room(7, "Тихая комната", created_by=100, is_private=True)

        self.assertEqual([item["id"] for item in voice_store.list_voice_rooms(7, user_id=100)][-1], room["id"])
        self.assertNotIn(room["id"], [item["id"] for item in voice_store.list_voice_rooms(7, user_id=200)])
        self.assertFalse(voice_store.can_access_voice_room(room["id"], 200))

        invite = voice_store.create_voice_invite(room["id"], 100, max_uses=1)
        self.assertTrue(voice_store.redeem_voice_invite(room["id"], 200, invite))
        self.assertTrue(voice_store.can_access_voice_room(room["id"], 200))
        self.assertFalse(voice_store.redeem_voice_invite(room["id"], 300, invite))
        self.assertIn(room["id"], [item["id"] for item in voice_store.list_voice_rooms(7, user_id=200)])

    def test_room_names_are_normalized_and_custom_rooms_are_bounded(self):
        room = voice_store.create_voice_room(1, "  Игровая   два  ", created_by=10)
        self.assertEqual(room["name"], "Игровая два")
        with self.assertRaises(ValueError):
            voice_store.create_voice_room(1, "   ", created_by=10)

    def test_livekit_token_is_signed_and_short_lived(self):
        with patch.object(web_server, "LIVEKIT_API_KEY", "test-key"), patch.object(
            web_server, "LIVEKIT_API_SECRET", "test-secret"
        ):
            token = web_server._livekit_token("42", "User", "room-1")
        header, payload, signature = token.split(".")
        expected = hmac.new(b"test-secret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        self.assertTrue(hmac.compare_digest(expected, actual))
        self.assertEqual(claims["iss"], "test-key")
        self.assertEqual(claims["video"]["room"], "room-1")
        self.assertTrue(claims["video"]["roomJoin"])
        self.assertLessEqual(claims["exp"] - claims["nbf"], 15 * 60 + 5)


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


class SummaryStoreTests(IsolatedDatabaseTest):
    def test_post_log_is_idempotent_and_uses_shared_connection_policy(self):
        self.assertFalse(summary_store.was_summary_posted(1, "weekly", "2026-W28"))
        self.assertTrue(summary_store.mark_summary_posted(1, "weekly", "2026-W28"))
        self.assertFalse(summary_store.mark_summary_posted(1, "weekly", "2026-W28"))
        self.assertTrue(summary_store.was_summary_posted(1, "weekly", "2026-W28"))


class SummaryStatsStoreTests(IsolatedDatabaseTest):
    def _create_tables(self):
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE msg_stats_daily(
                    guild_id INTEGER, user_id INTEGER, date TEXT, messages INTEGER,
                    words INTEGER DEFAULT 0, emojis INTEGER DEFAULT 0
                );
                CREATE TABLE voice_totals_daily(
                    guild_id INTEGER, user_id INTEGER, date TEXT, seconds INTEGER
                );
                CREATE TABLE voice_sessions(
                    guild_id INTEGER, channel_id INTEGER, started_at TEXT
                );
                """
            )

    def test_empty_daily_aggregates_have_stable_shape(self):
        self._create_tables()

        stats = summary_stats_store.get_today_stats(7, today_date=date(2026, 7, 13))

        self.assertEqual(stats["date"], "2026-07-13")
        self.assertEqual(stats["total_msgs"], 0)
        self.assertEqual(stats["total_voice_s"], 0)
        self.assertEqual(stats["total_game_s"], 0)
        self.assertEqual(stats["top_chatters"], [])
        self.assertEqual(stats["top_voice"], [])
        self.assertEqual(stats["voice_channels"], [])
        self.assertEqual(stats["toxic_count"], 0)
        self.assertEqual(stats["rep_events"], 0)

    def test_daily_aggregates_use_moscow_bounds_and_isolate_guilds(self):
        self._create_tables()
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE msg_word_freq_daily(guild_id INTEGER, date TEXT, word TEXT, count INTEGER);
                CREATE TABLE msg_emoji_freq_daily(guild_id INTEGER, date TEXT, emoji TEXT, count INTEGER);
                CREATE TABLE toxicity_log(
                    guild_id INTEGER, user_id INTEGER, level INTEGER,
                    msg_snippet TEXT, logged_at TEXT
                );
                CREATE TABLE reputation(user_id INTEGER, delta INTEGER, date TEXT);
                CREATE TABLE coins_wallet(user_id INTEGER, balance INTEGER);
                CREATE TABLE daily_rewards(user_id INTEGER, streak INTEGER);
                CREATE TABLE toxicity_weekly(guild_id INTEGER, user_id INTEGER, week TEXT, count INTEGER);
                CREATE TABLE heroes_sessions(
                    guild_id INTEGER, user_id INTEGER, seconds INTEGER, started_at TEXT
                );
                CREATE TABLE activity_sessions(
                    guild_id INTEGER, user_id INTEGER, activity_name TEXT,
                    activity_type TEXT, seconds INTEGER, started_at TEXT
                );
                INSERT INTO msg_stats_daily VALUES
                    (7, 101, '2026-07-13', 8, 80, 2),
                    (7, 102, '2026-07-13', 12, 50, 6),
                    (8, 999, '2026-07-13', 500, 900, 99);
                INSERT INTO voice_totals_daily VALUES
                    (7, 101, '2026-07-13', 90),
                    (7, 102, '2026-07-13', 150),
                    (8, 999, '2026-07-13', 9000);
                INSERT INTO voice_sessions VALUES
                    (7, 501, '2026-07-13T10:00:00+00:00'),
                    (8, 999, '2026-07-13T10:00:00+00:00');
                INSERT INTO msg_word_freq_daily VALUES
                    (7, '2026-07-13', 'бот', 4),
                    (7, '2026-07-13', 'игра', 7),
                    (8, '2026-07-13', 'чужое', 99);
                INSERT INTO msg_emoji_freq_daily VALUES
                    (7, '2026-07-13', ':)', 3),
                    (7, '2026-07-13', ':D', 5);
                INSERT INTO toxicity_log VALUES
                    (7, 101, 1, 'мягкая цитата', '2026-07-12T21:00:00+00:00'),
                    (7, 101, 3, 'сильная цитата', '2026-07-13T12:00:00+00:00'),
                    (7, 102, 2, 'за пределом', '2026-07-13T21:00:00+00:00'),
                    (8, 999, 3, 'чужой сервер', '2026-07-13T12:00:00+00:00');
                INSERT INTO reputation VALUES (101, 2, '2026-07-13');
                INSERT INTO coins_wallet VALUES (101, 25), (102, 80);
                INSERT INTO daily_rewards VALUES (101, 3), (102, 7);
                INSERT INTO toxicity_weekly VALUES
                    (7, 101, '2026-W28', 4),
                    (8, 999, '2026-W28', 100);
                INSERT INTO heroes_sessions VALUES
                    (7, 102, 75, '2026-07-13T12:00:00+00:00'),
                    (8, 999, 900, '2026-07-13T12:00:00+00:00');
                INSERT INTO activity_sessions VALUES
                    (7, 101, 'Game A', 'game', 120, '2026-07-12T21:00:00+00:00'),
                    (7, 102, 'Game A', 'game', 180, '2026-07-13T20:59:59+00:00'),
                    (7, 101, 'Game B', 'game', 60, '2026-07-13T21:00:00+00:00'),
                    (7, 101, 'Editor', 'app', 500, '2026-07-13T12:00:00+00:00'),
                    (8, 999, 'Other', 'game', 9999, '2026-07-13T12:00:00+00:00');
                """
            )

        stats = summary_stats_store.get_today_stats(7, today_date=date(2026, 7, 13))

        self.assertEqual(stats["total_msgs"], 20)
        self.assertEqual(stats["top_chatters"], [(102, 12), (101, 8)])
        self.assertEqual(stats["total_voice_s"], 240)
        self.assertEqual(stats["top_voice"], [(102, 150), (101, 90)])
        self.assertEqual(stats["top_words"], [("игра", 7), ("бот", 4)])
        self.assertEqual(stats["top_emojis"], [(":D", 5), (":)", 3)])
        self.assertEqual(stats["voice_channels"], [501])
        self.assertEqual(stats["toxic_count"], 2)
        self.assertEqual(stats["toxic_leader"], (101, 2))
        self.assertEqual(stats["toxic_quote"], "сильная цитата")
        self.assertEqual(stats["total_game_s"], 300)
        self.assertEqual(stats["top_games"], [("Game A", 300)])
        self.assertEqual(stats["top_game_users"], [(102, 180), (101, 120)])
        self.assertEqual(stats["rep_events"], 1)

        period = summary_stats_store.get_period_stats(
            7, date(2026, 7, 7), date(2026, 7, 14)
        )
        self.assertEqual(period["since"], "2026-07-07")
        self.assertEqual(period["until"], "2026-07-14")
        self.assertEqual(period["top_msgs"], [(102, 12), (101, 8)])
        self.assertEqual(period["top_words"], [(101, 80), (102, 50)])
        self.assertEqual(period["top_emojis"], [(102, 6), (101, 2)])
        self.assertEqual(period["top_balance"], [(102, 80), (101, 25)])
        self.assertEqual(period["top_streaks"], [(102, 7), (101, 3)])
        self.assertEqual(period["top_rep"], [(101, 2)])
        self.assertEqual(period["top_toxic"], [(101, 4)])
        self.assertEqual(period["top_heroes"], [(102, 75)])
        self.assertEqual(period["top_activities"], [("Editor", "app", 500), ("Game A", "game", 300)])
        self.assertEqual(period["top_other_activities"], [("Editor", "app", 500)])
        self.assertEqual(period["top_activity_users"], [(101, 620), (102, 180)])
        self.assertEqual(period["total_msgs"], 20)
        self.assertEqual(period["total_voice_s"], 240)
        self.assertEqual(period["total_game_s"], 300)

    def test_period_rejects_empty_or_reversed_range(self):
        with self.assertRaises(ValueError):
            summary_stats_store.get_period_stats(
                7, date(2026, 7, 13), date(2026, 7, 13)
            )


class GameLayerTests(IsolatedDatabaseTest):
    def test_pure_game_rules_cover_rps_blackjack_and_word_validation(self):
        self.assertEqual(game_service.rps_result("камень", "ножницы"), 1)
        self.assertEqual(game_service.rps_result("бумага", "ножницы"), -1)
        self.assertEqual(game_service.rps_result("камень", "камень"), 0)
        self.assertEqual(game_service.hand_total(["A♠", "A♥", "9♦"]), 21)
        self.assertEqual(game_service.hand_total(["K♠", "Q♥", "2♦"]), 22)
        self.assertEqual(len(set(game_service.new_deck())), 52)
        self.assertEqual(game_service.normalize_hangman_word("  Тест-слово "), "тест-слово")
        with self.assertRaises(ValueError):
            game_service.normalize_hangman_word("x")

    def test_hangman_store_replaces_channel_game_and_applies_atomic_turns(self):
        first = game_store.start_hangman_game(
            7, 50, 100, "старое", created_at="2026-07-13T00:00:00+00:00"
        )
        second = game_store.start_hangman_game(
            7, 50, 101, "аб", created_at="2026-07-13T00:01:00+00:00"
        )
        other = game_store.start_hangman_game(
            8, 60, 200, "вг", created_at="2026-07-13T00:02:00+00:00"
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(game_store.get_active_hangman_game(50)["id"], second["id"])
        self.assertEqual(game_store.get_active_hangman_game(60)["id"], other["id"])
        self.assertEqual(
            game_store.guess_hangman_letter(50, 101, "а", max_wrong=2)["outcome"],
            "host_forbidden",
        )
        hit = game_store.guess_hangman_letter(50, 300, "а", max_wrong=2)
        self.assertEqual(hit["outcome"], "hit")
        self.assertEqual(hit["game"]["guessed"], "а")
        self.assertEqual(
            game_store.guess_hangman_letter(50, 300, "А", max_wrong=2)["outcome"],
            "repeated",
        )
        won = game_store.guess_hangman_letter(50, 300, "б", max_wrong=2)
        self.assertEqual(won["outcome"], "win")
        self.assertEqual(won["game"]["status"], "win")
        self.assertIsNone(game_store.get_active_hangman_game(50))

        self.assertEqual(
            game_store.guess_hangman_letter(60, 300, "д", max_wrong=2)["outcome"],
            "miss",
        )
        lost = game_store.guess_hangman_letter(60, 300, "е", max_wrong=2)
        self.assertEqual(lost["outcome"], "lose")
        self.assertEqual(lost["game"]["wrong"], "ДЕ")

        with db_connection(self.db_path) as conn:
            old_status = conn.execute(
                "SELECT status FROM hangman_games WHERE id=?", (first["id"],)
            ).fetchone()[0]
        self.assertEqual(old_status, "cancelled")


class ActivityLayerTests(IsolatedDatabaseTest):
    def test_legacy_habits_are_retired_without_deleting_audit_data(self):
        with db_connection(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE activity_game_habits(guild_id INTEGER, user_id INTEGER)"
            )
            conn.execute("INSERT INTO activity_game_habits VALUES(7, 101)")
        activity_store.ensure_activity_tables()
        with db_connection(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertNotIn("activity_game_habits", tables)
            self.assertIn("activity_game_habits_retired_backup", tables)
            self.assertEqual(
                conn.execute(
                    "SELECT guild_id,user_id FROM activity_game_habits_retired_backup"
                ).fetchall(),
                [(7, 101)],
            )

    def test_admin_panel_exposes_activity_policy_without_restart(self):
        feature = FEATURES_BY_ID["activity_tracker"]
        self.assertEqual(feature["channel_modes"], ())
        self.assertFalse(feature.get("restart_on_change", False))

    def test_settings_service_has_one_canonical_policy(self):
        self.assertTrue(activity_service.is_activity_enabled(7))
        activity_service.set_activity_enabled(7, False)
        self.assertFalse(activity_service.is_activity_enabled(7))

    def test_store_finishes_sessions_and_builds_guild_top(self):
        start = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
        activity_store.remember_activity_start(
            7, 101, "Game A", "game", started_at=start
        )
        loaded = activity_store.load_active_sessions()
        self.assertEqual(loaded, [(7, 101, "Game A", "game", start.isoformat())])
        seconds = activity_store.finish_activity_session(
            7,
            101,
            "Game A",
            "game",
            ended_at=start + timedelta(seconds=125),
        )
        self.assertEqual(seconds, 125)
        self.assertEqual(activity_store.load_active_sessions(), [])

        activity_store.remember_activity_start(
            7, 102, "Editor", "app", started_at=start
        )
        activity_store.finish_activity_session(
            7,
            102,
            "Editor",
            "app",
            ended_at=start + timedelta(seconds=300),
        )
        activity_store.remember_activity_start(
            8, 999, "Other", "game", started_at=start
        )
        activity_store.finish_activity_session(
            8,
            999,
            "Other",
            "game",
            ended_at=start + timedelta(seconds=9999),
        )

        top = activity_store.get_activity_top(7, "2026-07-13T00:00:00+00:00")
        self.assertEqual(top["top_games"], [("Game A", 125)])
        self.assertEqual(top["top_game_users"], [(101, 125)])
        self.assertEqual(top["other_activities"], [("Editor", "app", 300)])
        self.assertEqual(top["top_all_users"], [(102, 300), (101, 125)])


class ConversationLayerTests(IsolatedDatabaseTest):
    def test_legacy_random_chat_setting_is_safely_mention_only(self):
        settings_store.set_feature_payload(
            7,
            "social_chat",
            {"chance_percent": 12, "mention_only": False},
        )
        enabled, chance, mention_only, ambient_opt_in, allowed, excluded = social_chat._get_config(7)
        self.assertTrue(enabled)
        self.assertEqual(chance, 0)
        self.assertTrue(mention_only)
        self.assertFalse(ambient_opt_in)
        self.assertEqual(allowed, set())
        self.assertEqual(excluded, set())

    def test_explicit_turn_context_and_reaction_feedback_share_one_store(self):
        conversation_store.record_turn(
            bot_message_id=900,
            source_message_id=800,
            guild_id=7,
            channel_id=70,
            user_id=101,
            user_text="Випик, как дела?",
            bot_text="Нормально, пока VPS не дымится.",
            provider="ollama",
            model="qwen3:4b",
            latency_ms=321,
        )
        self.assertEqual(
            conversation_store.recent_context(7, 70, 101),
            [
                {"role": "user", "content": "Випик, как дела?"},
                {"role": "assistant", "content": "Нормально, пока VPS не дымится."},
            ],
        )
        self.assertEqual(conversation_store.recent_context(7, 70, 999), [])
        self.assertTrue(conversation_store.record_feedback(900, 101, 1))
        self.assertFalse(conversation_store.record_feedback(901, 101, -1))
        with db_connection(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT score FROM conversation_feedback WHERE bot_message_id=900"
                ).fetchone(),
                (1,),
            )


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


class ToxicityMlTests(unittest.TestCase):
    def test_shadow_model_predicts_without_overriding_rules(self):
        examples = [(f"спокойное обсуждение игры номер {index}", 0) for index in range(40)]
        examples += [(f"ты идиот и дебил номер {index}", 1) for index in range(30)]
        model = train_model(examples, buckets=256)

        level, confidence, version = toxicity_model_service.predict_ml_level("ты идиот и дебил", model)
        self.assertEqual(level, 1)
        self.assertGreater(confidence, 0.5)
        self.assertTrue(version.startswith("tox-nb-"))
        prediction = toxicity_model_service.detect_toxicity("обычная спокойная беседа")
        self.assertEqual(prediction["effective_level"], prediction["rule_level"])


class MlInsightsTests(IsolatedDatabaseTest):
    def test_advisory_insights_find_anomalies_pairs_and_quality_issues(self):
        with db_connection(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE coin_ledger(id INTEGER PRIMARY KEY, user_id INTEGER, delta INTEGER, reason TEXT, meta TEXT, created_at TEXT);
                CREATE TABLE coins_wallet(user_id INTEGER PRIMARY KEY, balance INTEGER, updated_at TEXT);
                INSERT INTO coin_ledger VALUES(1,1,10,'daily','{}','now');
                INSERT INTO coin_ledger VALUES(2,1,12,'daily','{}','now');
                INSERT INTO coin_ledger VALUES(3,1,500,'daily','{}','now');
                INSERT INTO coins_wallet VALUES(1,999,'now');
                CREATE TABLE activity_sessions(id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, activity_name TEXT, activity_type TEXT, started_at TEXT, ended_at TEXT, seconds INTEGER);
                INSERT INTO activity_sessions VALUES(1,7,1,'Game A','game','a','b',3600);
                INSERT INTO activity_sessions VALUES(2,7,2,'Game A','game','a','b',1800);
                CREATE TABLE steam_profiles(user_id INTEGER PRIMARY KEY);
                CREATE TABLE steam_owned_games_cache(user_id INTEGER, appid INTEGER);
                INSERT INTO steam_owned_games_cache VALUES(99,1);
                """
            )
        result = ml_insights.build_ml_insights(database=self.db_path, guild_id=7)
        self.assertEqual(result["mode"], "advisory")
        self.assertEqual(result["economy"]["anomalies"][0]["delta"], 500)
        self.assertEqual(result["economy"]["wallet_mismatches"][0]["user_id"], 1)
        self.assertEqual(result["activity"]["compatible_players"][0]["shared_games"], ["Game A"])
        self.assertEqual(result["data_quality"]["checks"]["orphan_steam_games"], 1)
        self.assertFalse(result["data_quality"]["healthy"])


if __name__ == "__main__":
    unittest.main()
