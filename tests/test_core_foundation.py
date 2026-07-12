from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import community_store, economy, economy_profile, settings_store
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


class IsolatedDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "social.db")
        self.patches = [
            patch.object(settings_store, "SOCIAL_DB", self.db_path),
            patch.object(community_store, "SOCIAL_DB", self.db_path),
            patch.object(economy, "DB_PATH", self.db_path),
            patch.object(economy_profile, "DB_PATH", self.db_path),
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
