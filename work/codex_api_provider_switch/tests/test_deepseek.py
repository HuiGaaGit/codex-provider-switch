from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from codex_api_provider_switch import build_demo_controller
from provider_switch.auth_manager import AuthStatus
from provider_switch.catalog import validate_catalog_text
from provider_switch.models import AppSettings, ConfigSnapshot
from provider_switch.monitoring import check_provider, scan_token_usage_seconds


class DeepSeekIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="deepseek-switch-test-")
        self.controller = build_demo_controller(Path(self.temp.name))
        self.controller.settings.auto_restart = False
        self.controller.settings.auto_sync_history = False
        self.controller.credentials.update(glm="glm-only-key", deepseek="deepseek-only-key")
        self.home = self.controller.codex_home
        self.path = self.home / "config.toml"
        self.path.write_text(
            self.path.read_text(encoding="utf-8")
            + '\n[desktop]\nenabled-reasoning-efforts = [\n "low",\n "high"\n]\n'
            + 'theme = "system"\n', encoding="utf-8"
        )
        self.auth_bytes = b'{"auth_mode":"chatgpt","tokens":{"access_token":"fake-local-account"}}'
        (self.home / "auth.json").write_bytes(self.auth_bytes)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self):
        return tomllib.loads(self.path.read_text(encoding="utf-8"))

    def test_deepseek_uses_own_key_catalog_and_stable_provider(self) -> None:
        result = self.controller.switch_profile("deepseek")
        data = self.config()
        provider = data["model_providers"]["cch_gz"]
        self.assertEqual("cch_gz", result.provider_key)
        self.assertEqual("https://api.deepseek.com", provider["base_url"])
        self.assertEqual("deepseek-only-key", provider["experimental_bearer_token"])
        self.assertFalse(provider["requires_openai_auth"])
        self.assertEqual("responses", provider["wire_api"])
        self.assertEqual("deepseek-flash", data["model"])
        self.assertEqual("high", data["model_reasoning_effort"])
        self.assertEqual("api", data["forced_login_method"])
        self.assertEqual("disabled", data["web_search"])
        self.assertTrue(Path(data["model_catalog_json"]).is_file())
        self.assertEqual("models-deepseek.json", Path(data["model_catalog_json"]).name)
        self.assertNotIn("service_tier", data)
        self.assertNotIn("fast_mode", data.get("features", {}))
        self.assertEqual(self.auth_bytes, (self.home / "auth.json").read_bytes())

    def test_glm_deepseek_round_trip_keeps_independent_keys_and_catalogs(self) -> None:
        for pid, model, filename, key in (
            ("glm", "glm-5.3-flash", "models-glm.json", "glm-only-key"),
            ("deepseek", "deepseek-flash", "models-deepseek.json", "deepseek-only-key"),
            ("glm", "glm-5.3-flash", "models-glm.json", "glm-only-key"),
        ):
            self.controller.settings.profiles["relay1"].provider_key = "cch_gz"
            self.controller.switch_profile(pid)
            data = self.config()
            self.assertEqual(pid, self.controller.detect_active_profile())
            self.assertEqual(model, data["model"])
            self.assertEqual(filename, Path(data["model_catalog_json"]).name)
            self.assertEqual(key, data["model_providers"]["cch_gz"]["experimental_bearer_token"])
            self.assertEqual(pid, self.controller.settings.native_profile_id)
        self.assertNotIn("forced_login_method", self.config())
        self.assertEqual(["low", "high"], self.config()["desktop"]["enabled-reasoning-efforts"])

    def test_deepseek_to_official_restores_auth_search_desktop_and_preserves_login(self) -> None:
        self.path.write_text('web_search = "cached"\npreferred_auth_method = "chatgpt"\n' + self.path.read_text(), encoding="utf-8")
        self.controller.settings.retain_official_auth = True
        self.controller.auth = Mock()
        self.controller.auth.status.return_value = AuthStatus("chatgpt", True, self.home / "auth.json")
        self.controller.switch_profile("deepseek")
        self.controller.switch_profile("openai")
        data = self.config()
        self.assertNotIn("model_provider", data)
        self.assertNotIn("forced_login_method", data)
        self.assertNotIn("show_raw_agent_reasoning", data)
        self.assertEqual("cached", data["web_search"])
        self.assertEqual("chatgpt", data["preferred_auth_method"])
        self.assertEqual("system", data["desktop"]["theme"])
        self.assertEqual(["low", "high"], data["desktop"]["enabled-reasoning-efforts"])
        self.assertEqual(self.auth_bytes, (self.home / "auth.json").read_bytes())
        for provider in data["model_providers"].values():
            self.assertNotIn("experimental_bearer_token", provider)

    def test_default_preview_and_pro_selection_do_not_write_live_config(self) -> None:
        self.controller.settings.profiles["deepseek"].model = "deepseek-v4-pro"
        self.controller.switch_profile("deepseek")
        before = self.path.read_bytes()
        preview, pid, _ = self.controller.default_config_text()
        self.assertEqual("deepseek", pid)
        self.assertEqual("deepseek-v4-pro", tomllib.loads(preview)["model"])
        self.assertEqual(before, self.path.read_bytes())

    def test_official_catalog_declares_flash_vision_and_pro_text(self) -> None:
        payload, count = validate_catalog_text(self.controller.deepseek_catalog.bundled_text())
        self.assertEqual(2, count)
        by_id = {model["slug"]: model for model in payload["models"]}
        self.assertIn("image", by_id["deepseek-flash"]["input_modalities"])
        self.assertEqual(["text"], by_id["deepseek-v4-pro"]["input_modalities"])
        self.assertFalse(by_id["deepseek-flash"]["prefer_websockets"])

    def test_probe_uses_models_only_and_never_calls_relay_usage_or_glm_quota(self) -> None:
        profile = self.controller.settings.profiles["deepseek"]
        with patch("provider_switch.monitoring._http_json", return_value=(200, {"data": [{"id": "deepseek-flash"}]}, 12)) as request:
            result = check_provider(profile, "deepseek-only-key")
        self.assertEqual("healthy", result.health.state)
        self.assertEqual(1, request.call_count)
        self.assertEqual("https://api.deepseek.com/models", request.call_args.args[0])
        self.assertTrue(self.controller.quota_ready("deepseek"))

    def test_old_settings_gain_deepseek_without_changing_glm(self) -> None:
        old = self.controller.settings.to_dict()
        old["schema_version"] = 7
        old.pop("native_profile_id")
        old["profiles"].pop("deepseek")
        old["profiles"]["glm"]["model"] = "glm-custom"
        migrated = AppSettings.from_dict(old)
        self.assertEqual("glm", migrated.native_profile_id)
        self.assertEqual("glm-custom", migrated.profiles["glm"].model)
        self.assertEqual("deepseek-flash", migrated.profiles["deepseek"].model)

    def test_import_existing_deepseek_does_not_copy_key_into_api1(self) -> None:
        snapshot = ConfigSnapshot(str(self.path), "custom", "deepseek-flash", providers={"custom": {
            "base_url": "https://api.deepseek.com", "experimental_bearer_token": "import-ds-key"
        }})
        settings, keys = self.controller.store.bootstrap(snapshot, self.home / "missing-legacy.json")
        self.assertEqual("deepseek", settings.active_profile_id)
        self.assertEqual("import-ds-key", keys["deepseek"])
        self.assertNotIn("relay1", keys)
        self.assertFalse(settings.profiles["relay1"].base_url)

    def test_token_deltas_distinguish_deepseek_and_glm_with_one_provider_label(self) -> None:
        now = datetime.now(timezone.utc)
        session_dir = self.home / "sessions"
        session_dir.mkdir()
        events = [{"type": "session_meta", "payload": {"model_provider": "cch_gz"}}]
        for seconds, input_count, output_count in ((180, 100, 10), (60, 180, 25)):
            events.append({"timestamp": (now - timedelta(seconds=seconds)).isoformat(), "type": "event_msg", "payload": {
                "type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": input_count, "output_tokens": output_count,
                    "total_tokens": input_count + output_count,
                }}
            }})
        (session_dir / "rollout.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        switch_log = self.home / "native-switch-history.jsonl"
        switch_log.write_text("\n".join(json.dumps({
            "timestamp": (now - timedelta(seconds=seconds)).isoformat(), "profile_id": pid,
        }) for seconds, pid in ((240, "deepseek"), (120, "glm"))), encoding="utf-8")
        usage = scan_token_usage_seconds(self.home, 3600, switch_log)
        self.assertEqual(110, usage["deepseek"].total_tokens)
        self.assertEqual(95, usage["glm"].total_tokens)


if __name__ == "__main__":
    unittest.main()
