from __future__ import annotations

import json
import sqlite3
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from provider_switch.controller import ApplicationController
from provider_switch.models import AppSettings, ConfigSnapshot, HealthResult, ProviderProfile, ProviderTelemetry, QuotaWindow
from provider_switch.ui import format_tokens
from provider_switch.qt_ui import _format_quota_windows
from provider_switch.monitoring import (
    _glm_quota_request,
    _parse_glm_quota,
    _parse_generic_quota,
    _relay_quota_url,
    scan_token_usage,
    scan_token_usage_seconds,
    load_switch_timeline,
)
from provider_switch.monitor_history import MonitorHistory
from provider_switch.settings import SettingsStore
from provider_switch.thread_state import sync_thread_models


class MonitoringTests(unittest.TestCase):
    def test_request_health_reads_codex_outcomes_without_probe(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            session_dir = home / "sessions" / "2026" / "09" / "19"
            session_dir.mkdir(parents=True)
            events = [
                {"timestamp": "2026-09-19T01:00:00+00:00", "type": "session_meta", "payload": {"model_provider": "ZAI"}},
                {"timestamp": "2026-09-19T01:00:01+00:00", "type": "event_msg", "payload": {"type": "task_complete"}},
                {"timestamp": "2026-09-19T01:00:02+00:00", "type": "response_item", "payload": {"status": "failed"}},
            ]
            (session_dir / "rollout-health.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8"
            )
            records = MonitorHistory.load_codex_request_records(home, 365 * 86400)
            self.assertEqual(["healthy", "offline"], [item.state for item in records])
            self.assertEqual(["ZAI", "ZAI"], [item.profile_id for item in records])
    def test_glm_quota_classifies_five_hour_and_weekly_windows(self) -> None:
        payload = {
            "data": {
                "level": "PRO",
                "limits": [
                    {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 40, "nextResetTime": 1900000000000},
                    {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 15, "nextResetTime": 1800000000000},
                ],
            }
        }
        windows, message = _parse_glm_quota(payload)
        self.assertEqual(["5 小时", "周额度"], [item.label for item in windows])
        self.assertEqual(85, windows[0].remaining)
        self.assertEqual("GLM PRO", message)

    def test_glm_team_quota_request_adds_type_and_headers(self) -> None:
        profile = ProviderProfile("glm", "GLM", "glm", "ZAI")
        profile.quota_url = "https://open.bigmodel.cn/api/monitor/usage/quota/limit?foo=bar"
        profile.quota_organization_id = " org-test "
        profile.quota_project_id = " proj-test "
        url, headers = _glm_quota_request(profile)
        self.assertEqual("https://open.bigmodel.cn/api/monitor/usage/quota/limit?foo=bar&type=2", url)
        self.assertEqual({"bigmodel-organization": "org-test", "bigmodel-project": "proj-test"}, headers)

    def test_relay_quota_defaults_to_sub2api_usage_endpoint(self) -> None:
        profile = ProviderProfile(
            "relay2", "API2", "relay", "relay_2", "https://relay.example/v1/responses"
        )
        self.assertEqual("https://relay.example/v1/usage?days=30", _relay_quota_url(profile))

    def test_sub2api_quota_parser_uses_remaining(self) -> None:
        profile = ProviderProfile("relay2", "API2", "relay", "relay_2")
        windows, message = _parse_generic_quota(profile, {"isValid": True, "remaining": 12.5, "unit": "USD"})
        self.assertEqual(1, len(windows))
        self.assertEqual(12.5, windows[0].remaining)
        self.assertEqual("USD", windows[0].unit)

    def test_local_token_usage_is_displayed_in_millions(self) -> None:
        self.assertEqual("0.0M", format_tokens(0))
        self.assertEqual("12.3M", format_tokens(12_345_678))
        self.assertEqual("8,810.7M", format_tokens(8_810_684_590))

    def test_wallet_quota_without_percentage_has_no_display_percentage(self) -> None:
        window = QuotaWindow("钱包余额", remaining=100995323.2886, unit="USD")
        self.assertIsNone(window.used_percent)

    def test_glm_card_shows_five_hour_and_weekly_windows(self) -> None:
        windows = [
            QuotaWindow("5 小时", used_percent=19.0, remaining=81.0, total=100.0, unit="%"),
            QuotaWindow("周额度", used_percent=7.0, remaining=93.0, total=100.0, unit="%"),
        ]
        self.assertEqual(
            ["5 小时剩余 81.0%", "周额度剩余 93.0%"],
            _format_quota_windows(windows).splitlines(),
        )

    def test_session_scanner_uses_final_cumulative_token_count_once(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            session_dir = home / "sessions" / "2026" / "09" / "17"
            session_dir.mkdir(parents=True)
            events = [
                {"type": "session_meta", "payload": {"model_provider": "cch_gz"}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 2, "reasoning_output_tokens": 1, "total_tokens": 12}},
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 25, "cached_input_tokens": 8, "output_tokens": 5, "reasoning_output_tokens": 2, "total_tokens": 30}},
                    },
                },
            ]
            (session_dir / "rollout-test.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8"
            )
            usage = scan_token_usage(home, 30)["cch_gz"]
            self.assertEqual(30, usage.total_tokens)
            self.assertEqual(25, usage.input_tokens)
            self.assertEqual(1, usage.sessions)

    def test_token_usage_attributes_sessions_by_switch_timeline(self) -> None:
        import os
        import time as time_module
        from datetime import datetime, timezone, timedelta

        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            data_root = home / "switch-data"
            data_root.mkdir(parents=True)
            now = time_module.time()
            # Session written "now", meta still carries the stable provider label.
            session_dir = home / "sessions" / "2026" / "09" / "18"
            session_dir.mkdir(parents=True)
            events = [
                {"type": "session_meta", "payload": {"model_provider": "cch_gz"}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 4, "reasoning_output_tokens": 0, "total_tokens": 24}},
                    },
                },
            ]
            session_file = session_dir / "rollout-timeline.jsonl"
            session_file.write_text(
                "\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8"
            )
            os.utime(session_file, (now, now))
            # Switch log: glm was switched to 1 hour ago (before the session mtime).
            switched_at = datetime.fromtimestamp(now - 3600, tz=timezone.utc).isoformat()
            switch_log = data_root / "switch-history.jsonl"
            switch_log.write_text(
                json.dumps({"timestamp": switched_at, "profile_id": "glm", "provider_key": "cch_gz"}) + "\n",
                encoding="utf-8",
            )
            timeline = load_switch_timeline(switch_log)
            self.assertEqual(1, len(timeline))
            self.assertEqual("glm", timeline[0][1])
            usage = scan_token_usage_seconds(home, 30 * 86400, switch_log)
            self.assertIn("glm", usage)
            self.assertEqual(24, usage["glm"].total_tokens)
            self.assertEqual(1, usage["glm"].sessions)
            self.assertEqual("timeline", usage["glm"].attribution)

    def test_session_scanner_uses_request_deltas_and_event_switch_time(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            session_dir = home / "sessions" / "2026" / "09" / "18"
            session_dir.mkdir(parents=True)
            now = datetime.now(timezone.utc)
            events = [
                {"type": "session_meta", "payload": {"model_provider": "stable"}},
                {"timestamp": (now - timedelta(minutes=3)).isoformat(), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10, "reasoning_output_tokens": 4, "total_tokens": 110}}}},
                {"timestamp": (now - timedelta(minutes=1)).isoformat(), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 180, "cached_input_tokens": 60, "output_tokens": 25, "reasoning_output_tokens": 9, "total_tokens": 205}}}},
            ]
            session_file = session_dir / "rollout-delta.jsonl"
            session_file.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
            switch_log = home / "switch-history.jsonl"
            switch_log.write_text(
                json.dumps({"timestamp": (now - timedelta(minutes=2)).isoformat(), "profile_id": "glm"}) + "\n",
                encoding="utf-8",
            )
            usage = scan_token_usage_seconds(home, 3600, switch_log)
            self.assertEqual(110, usage["unknown"].total_tokens)
            self.assertEqual(95, usage["glm"].total_tokens)
            self.assertEqual(1, usage["unknown"].sessions)
            self.assertEqual(1, usage["glm"].sessions)

    def test_thread_model_sync_updates_open_threads_and_preserves_archived(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            database = home / "state_1.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, model TEXT, archived INTEGER)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?)",
                (
                    ("open", "cch_gz", "gpt-5.6-sol", 0),
                    ("same", "cch_gz", "glm-5.3-flash", 0),
                    ("old", "cch_gz", "gpt-5.6-sol", 1),
                    ("other", "relay_2", "gpt-5.6-sol", 0),
                ),
            )
            connection.commit()
            connection.close()
            results = sync_thread_models(home, "cch_gz", "glm-5.3-flash")
            connection = sqlite3.connect(database)
            values = dict(connection.execute("SELECT id, model FROM threads"))
            connection.close()
            self.assertEqual(1, len(results))
            self.assertEqual(1, results[0].updated_threads)
            self.assertEqual("glm-5.3-flash", values["open"])
            self.assertEqual("glm-5.3-flash", values["same"])
            self.assertEqual("gpt-5.6-sol", values["old"])
            self.assertEqual("gpt-5.6-sol", values["other"])
            self.assertTrue(results[0].backup.exists())

    def test_thread_model_sync_can_cover_managed_provider_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            database = home / "state_1.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, model TEXT, archived INTEGER)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?)",
                (("stable", "cch_gz", "old", 0), ("official", "openai", "old", 0), ("other", "other", "old", 0)),
            )
            connection.commit()
            connection.close()
            results = sync_thread_models(home, "openai", "gpt-5.6-sol", provider_keys={"cch_gz"})
            connection = sqlite3.connect(database)
            values = dict(connection.execute("SELECT id, model FROM threads"))
            connection.close()
            self.assertEqual(1, len(results))
            self.assertEqual("gpt-5.6-sol", values["stable"])
            self.assertEqual("gpt-5.6-sol", values["official"])
            self.assertEqual("old", values["other"])

    def test_manual_config_save_syncs_local_profile_and_credential(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            home = root / ".codex"
            home.mkdir()
            settings = AppSettings(
                codex_home=str(home),
                setup_complete=True,
                stable_provider_key="cch_gz",
                preserve_provider_key=True,
            )
            store = SettingsStore(root / "data")
            store.save(settings, {})
            controller = ApplicationController(store)
            config = '''model_provider = "custom"\nmodel = "glm-5.3-flash"\n\n[model_providers.custom]\nname = "GLM"\nbase_url = "https://open.bigmodel.cn/api/v1/"\nwire_api = "responses"\nrequires_openai_auth = false\nexperimental_bearer_token = "manual-key"\n'''
            controller.save_config_text(config)
            glm = controller.settings.profiles["glm"]
            self.assertEqual("custom", controller.settings.stable_provider_key)
            self.assertEqual("glm", controller.settings.active_profile_id)
            self.assertEqual("GLM", glm.display_name)
            self.assertEqual("https://open.bigmodel.cn/api/v1", glm.base_url)
            self.assertEqual("manual-key", controller.credentials["glm"])


class ControllerTests(unittest.TestCase):
    def test_disconnect_does_nothing_when_active_provider_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            home = root / ".codex"
            home.mkdir()
            original = (
                'model_provider = "external_provider"\n'
                'model = "external-model"\n\n'
                '[model_providers.external_provider]\n'
                'name = "External"\n'
                'base_url = "https://external.example/v1"\n'
                'wire_api = "responses"\n'
                'experimental_bearer_token = "redacted"\n'
            )
            config_path = home / "config.toml"
            config_path.write_text(original, encoding="utf-8")
            settings = AppSettings(
                codex_home=str(home),
                setup_complete=True,
                stable_provider_key="cch_gz",
                auto_restart=False,
                auto_sync_history=False,
            )
            store = SettingsStore(root / "data")
            store.save(settings, {"relay1": "redacted"})
            controller = ApplicationController(store)

            result = controller.disconnect_other_providers()

            self.assertEqual(original, config_path.read_text(encoding="utf-8"))
            self.assertIsNone(result.backup_path)
            self.assertTrue(any("无法识别" in item for item in result.warnings))

    def test_active_probe_follows_config_provider_not_cached_profile(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            home = root / ".codex"
            home.mkdir()
            (home / "config.toml").write_text(
                'model_provider = "custom_glm"\nmodel = "custom-model"\n\n'
                '[model_providers.custom_glm]\n'
                'name = "Team GLM"\n'
                'base_url = "https://open.bigmodel.cn/api/v1"\n'
                'wire_api = "responses"\n'
                'experimental_bearer_token = "redacted"\n',
                encoding="utf-8",
            )
            settings = AppSettings(
                codex_home=str(home),
                setup_complete=True,
                active_profile_id="relay1",
                auto_restart=False,
                auto_sync_history=False,
            )
            settings.profiles["glm"].base_url = "https://open.bigmodel.cn/api/v1"
            store = SettingsStore(root / "data")
            store.save(settings, {"glm": "redacted"})
            controller = ApplicationController(store)
            seen: list[str] = []

            def fake_check(profile, *args, **kwargs):
                seen.append(profile.profile_id)
                return ProviderTelemetry(profile.profile_id, HealthResult(profile.profile_id, "healthy", "ok"))

            with patch("provider_switch.controller.check_provider", side_effect=fake_check):
                result = controller.check_active()
            self.assertEqual(["glm"], seen)
            self.assertEqual(["glm"], list(result))

    def test_official_glm_round_trip_preserves_auth_and_restores_direct_route(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            home = root / ".codex"
            home.mkdir()
            (home / "config.toml").write_text('model = "gpt-5.6-sol"\n', encoding="utf-8")
            (home / "auth.json").write_text('{"auth": "redacted"}', encoding="utf-8")
            settings = AppSettings(
                codex_home=str(home),
                setup_complete=True,
                stable_provider_key="cch_gz",
                preserve_provider_key=True,
                retain_official_auth=True,
                auto_restart=False,
                auto_sync_history=False,
            )
            settings.profiles["glm"].base_url = "https://open.bigmodel.cn/api/v1"
            settings.profiles["glm"].model = "glm-5.3-flash"
            store = SettingsStore(root / "data")
            store.save(settings, {"glm": "redacted"})
            controller = ApplicationController(store)
            controller.switch_profile("glm", disconnect_others=True)
            glm_config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual("cch_gz", glm_config["model_provider"])
            self.assertEqual("glm-5.3-flash", glm_config["model"])
            self.assertTrue(glm_config.get("model_catalog_json"))
            self.assertTrue((home / "auth.json").exists())

            with patch.object(controller, "openai_available", return_value=True):
                controller.switch_profile("openai", disconnect_others=True)
            direct_config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            self.assertNotIn("model_provider", direct_config)
            self.assertNotIn("model_catalog_json", direct_config)
            self.assertTrue((home / "auth.json").exists())
            self.assertEqual("redacted", controller.credentials["glm"])

    def test_isolated_switch_writes_profile_and_catalog_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            home = root / ".codex"
            home.mkdir()
            (home / "config.toml").write_text(
                'model_provider = "cch_gz"\nmodel = "gpt-5.6-sol"\n\n'
                '[model_providers.cch_gz]\nname = "Relay"\nbase_url = "https://relay.example/v1"\n'
                'wire_api = "responses"\nrequires_openai_auth = true\nexperimental_bearer_token = "old"\n',
                encoding="utf-8",
            )
            settings = AppSettings(
                codex_home=str(home),
                setup_complete=True,
                stable_provider_key="cch_gz",
                auto_restart=False,
                auto_sync_history=False,
            )
            settings.profiles["glm"].base_url = "https://open.bigmodel.cn/api/v1"
            settings.profiles["glm"].model = "glm-5.3"
            store = SettingsStore(root / "data")
            store.save(settings, {"glm": "glm-test-key"})
            controller = ApplicationController(store)
            result = controller.switch_profile("glm")
            parsed = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual("cch_gz", result.provider_key)
            self.assertEqual("glm-5.3", parsed["model"])
            self.assertEqual("https://open.bigmodel.cn/api/v1", parsed["model_providers"]["cch_gz"]["base_url"])
            self.assertTrue((home / "models.json").exists())
            reloaded, credentials = store.load()
            self.assertEqual("glm", reloaded.active_profile_id)
            self.assertEqual("glm-test-key", credentials["glm"])


if __name__ == "__main__":
    unittest.main()
