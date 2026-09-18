from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from provider_switch.catalog import CatalogError, ModelCatalogManager, validate_catalog_text
from provider_switch.config_manager import ConfigManager
from provider_switch.models import AppSettings, ConfigSnapshot, ProviderProfile
from provider_switch.settings import SettingsStore


SAMPLE_CONFIG = '''# model settings
model_provider = "cch_gz"
model = "gpt-5.6-sol"
notify = ["helper.exe", "turn-ended"]
service_tier = "default"

[model_providers.cch_gz]
name = "Old relay"
base_url = "https://relay.example/v1"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "old-secret"
custom_flag = "keep-me"

[features]
steer = true
'''


class ConfigManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / ".codex"
        self.home.mkdir()
        (self.home / "config.toml").write_text(SAMPLE_CONFIG, encoding="utf-8")
        self.manager = ConfigManager(self.home)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_relay_switch_keeps_stable_provider_and_unrelated_config(self) -> None:
        profile = ProviderProfile(
            "relay2", "备用中转", "relay", "relay_2", "https://relay2.example/v1", "gpt-5.6-sol", requires_openai_auth=True
        )
        backup, provider_key = self.manager.apply_profile(profile, "new-secret", True, "cch_gz")
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("cch_gz", provider_key)
        self.assertEqual("cch_gz", parsed["model_provider"])
        self.assertEqual("https://relay2.example/v1", parsed["model_providers"]["cch_gz"]["base_url"])
        self.assertEqual("new-secret", parsed["model_providers"]["cch_gz"]["experimental_bearer_token"])
        self.assertEqual("keep-me", parsed["model_providers"]["cch_gz"]["custom_flag"])
        self.assertTrue(parsed["features"]["steer"])
        self.assertEqual("default", parsed["service_tier"])
        self.assertIsNotNone(backup)
        self.assertEqual(SAMPLE_CONFIG, backup.read_text(encoding="utf-8"))

    def test_non_preserving_switch_registers_new_provider(self) -> None:
        profile = ProviderProfile(
            "relay2", "备用中转", "relay", "relay_2", "https://relay2.example/v1", "gpt-5.6-sol"
        )
        self.manager.apply_profile(profile, "new-secret", False, "cch_gz")
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("relay_2", parsed["model_provider"])
        self.assertIn("cch_gz", parsed["model_providers"])
        self.assertIn("relay_2", parsed["model_providers"])

    def test_glm_switch_injects_model_catalog_and_responses_provider(self) -> None:
        catalog = self.home / "models.json"
        catalog.write_text('{"models": [{"slug": "glm-5.3", "display_name": "glm-5.3", "context_window": 1000}]}', encoding="utf-8")
        profile = ProviderProfile(
            "glm", "GLM", "glm", "ZAI", "https://open.bigmodel.cn/api/v1", "glm-5.3"
        )
        self.manager.apply_profile(profile, "glm-secret", True, "cch_gz", catalog)
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("cch_gz", parsed["model_provider"])
        self.assertEqual("glm-5.3", parsed["model"])
        self.assertEqual("max", parsed["model_reasoning_effort"])
        self.assertEqual(catalog.resolve().as_posix(), parsed["model_catalog_json"])
        self.assertEqual("responses", parsed["model_providers"]["cch_gz"]["wire_api"])

    def test_glm_switch_removes_stale_auth_and_header_fields(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "custom"\n'
            'model = "glm-5.3-flash"\n\n'
            '[model_providers.custom]\n'
            'name = "GLM"\n'
            'base_url = "https://open.bigmodel.cn/api/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "OPENAI_API_KEY"\n'
            'requires_openai_auth = false\n'
            'http_headers = { x-openai-actor-authorization = "local-image-extension" }\n'
            'experimental_bearer_token = "old"\n',
            encoding="utf-8",
        )
        manager = ConfigManager(self.home)
        catalog = self.home / "models.json"
        catalog.write_text('{"models": []}', encoding="utf-8")
        profile = ProviderProfile(
            "glm", "GLM", "glm", "ZAI", "https://open.bigmodel.cn/api/v1", "glm-5.3-flash"
        )
        manager.apply_profile(profile, "glm-secret", True, "custom", catalog)
        text = (self.home / "config.toml").read_text(encoding="utf-8")
        parsed = tomllib.loads(text)
        provider = parsed["model_providers"]["custom"]
        self.assertNotIn("env_key", provider)
        self.assertNotIn("http_headers", provider)
        self.assertEqual("glm-secret", provider["experimental_bearer_token"])
        self.assertNotIn("OPENAI_API_KEY", text)
        self.assertNotIn("x-openai-actor-authorization", text)

    def test_openai_switch_keeps_custom_registration(self) -> None:
        profile = ProviderProfile("openai", "OpenAI 直连", "official", "openai", model="gpt-5.6-sol")
        self.manager.apply_profile(profile, "", True, "cch_gz")
        text = (self.home / "config.toml").read_text(encoding="utf-8")
        parsed = tomllib.loads(text)
        self.assertNotIn("model_provider", parsed)
        self.assertIn("cch_gz", parsed["model_providers"])
        self.assertIn('# model_provider = "cch_gz"', text)

    def test_disconnect_keeps_non_auth_custom_headers(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "cch_gz"\n'
            'model = "gpt-5.6-sol"\n\n'
            '[model_providers.cch_gz]\n'
            'name = "Relay"\n'
            'base_url = "https://relay.example/v1"\n'
            'wire_api = "responses"\n'
            'experimental_bearer_token = "old-secret"\n'
            'http_headers = { x-tenant = "keep-me", authorization = "remove-me" }\n',
            encoding="utf-8",
        )
        manager = ConfigManager(self.home)
        manager.clear_provider_keys(["cch_gz"])
        provider = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))["model_providers"]["cch_gz"]
        self.assertNotIn("experimental_bearer_token", provider)
        self.assertEqual({"x-tenant": "keep-me"}, provider["http_headers"])


class SettingsAndCatalogTests(unittest.TestCase):
    def test_legacy_auto_restart_is_disabled_during_safe_migration(self) -> None:
        settings = AppSettings.from_dict(
            {
                "schema_version": 3,
                "codex_home": str(Path("legacy") / ".codex"),
                "auto_restart": True,
            }
        )
        self.assertEqual(7, settings.schema_version)
        self.assertFalse(settings.auto_restart)

    def test_new_install_restarts_by_default_after_safe_helper_fix(self) -> None:
        self.assertTrue(AppSettings().auto_restart)

    def test_legacy_default_relay_names_migrate_to_api_names(self) -> None:
        settings = AppSettings.from_dict(
            {
                "schema_version": 5,
                "profiles": {
                    "relay1": {"profile_id": "relay1", "display_name": "中转 1"},
                    "relay2": {"profile_id": "relay2", "display_name": "中转 2"},
                },
            }
        )
        self.assertEqual("API1", settings.profiles["relay1"].display_name)
        self.assertEqual("API2", settings.profiles["relay2"].display_name)

    def test_default_glm_model_and_existing_default_migrate_to_flash(self) -> None:
        self.assertEqual("glm-5.3-flash", AppSettings().profiles["glm"].model)
        settings = AppSettings.from_dict(
            {
                "schema_version": 5,
                "profiles": {
                    "glm": {
                        "profile_id": "glm",
                        "model": "glm-5.3",
                    }
                },
            }
        )
        self.assertEqual("glm-5.3-flash", settings.profiles["glm"].model)

    def test_team_quota_fields_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            store = SettingsStore(Path(name))
            settings = AppSettings(codex_home=str(Path(name) / ".codex"), setup_complete=True)
            glm = settings.profiles["glm"]
            glm.quota_organization_id = "org-round-trip"
            glm.quota_project_id = "proj-round-trip"
            store.save(settings, {})
            loaded, _ = store.load()
            self.assertEqual("org-round-trip", loaded.profiles["glm"].quota_organization_id)
            self.assertEqual("proj-round-trip", loaded.profiles["glm"].quota_project_id)

    def test_team_project_display_space_normalizes_to_underscore(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            store = SettingsStore(Path(name))
            settings = AppSettings(codex_home=str(Path(name) / ".codex"), setup_complete=True)
            glm = settings.profiles["glm"]
            glm.quota_organization_id = "org- test "
            glm.quota_project_id = "proj 46example"
            store.save(settings, {})
            loaded, _ = store.load()
            self.assertEqual("org-test", loaded.profiles["glm"].quota_organization_id)
            self.assertEqual("proj_46example", loaded.profiles["glm"].quota_project_id)

    def test_settings_round_trip_does_not_store_plaintext_key(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            store = SettingsStore(Path(name))
            settings = AppSettings(codex_home=str(Path(name) / ".codex"), setup_complete=True)
            store.save(settings, {"relay1": "top-secret-value"})
            loaded, credentials = store.load()
            self.assertTrue(loaded.setup_complete)
            self.assertEqual("top-secret-value", credentials["relay1"])
            self.assertNotIn("top-secret-value", store.vault.path.read_text(encoding="utf-8"))

    def test_unchanged_credentials_skip_dpapi_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            store = SettingsStore(Path(name))
            credentials = {"relay1": "top-secret-value"}
            store.vault.save(credentials)
            before = store.vault.path.stat().st_mtime_ns
            payload_before = store.vault.path.read_text(encoding="utf-8")
            store.vault.save(credentials)
            self.assertEqual(before, store.vault.path.stat().st_mtime_ns)
            self.assertEqual(payload_before, store.vault.path.read_text(encoding="utf-8"))

    def test_bootstrap_imports_current_provider_and_legacy_names(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            legacy = root / "legacy.json"
            legacy.write_text(
                json.dumps({"enable": "主中转", "disable": "备用中转", "api2": "second"}),
                encoding="utf-8",
            )
            snapshot = ConfigSnapshot(
                path=str(root / ".codex" / "config.toml"),
                model_provider="cch_gz",
                model="gpt-5.6-sol",
                providers={
                    "cch_gz": {
                        "name": "Existing",
                        "base_url": "https://relay.example/v1",
                        "experimental_bearer_token": "first",
                    }
                },
            )
            settings, credentials = SettingsStore(root / "data").bootstrap(snapshot, legacy)
            self.assertEqual("cch_gz", settings.stable_provider_key)
            self.assertEqual("主中转", settings.profiles["relay1"].display_name)
            self.assertEqual("https://relay.example/v1", settings.profiles["relay2"].base_url)
            self.assertEqual({"relay1": "first", "relay2": "second"}, credentials)

    def test_catalog_save_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            manager = ModelCatalogManager(Path(name))
            text = manager.bundled_text()
            _, count = validate_catalog_text(text)
            path, saved_count, _ = manager.save(text)
            self.assertEqual(count, saved_count)
            self.assertTrue(path.exists())
            with self.assertRaises(CatalogError):
                validate_catalog_text('{"models": []}')

    def test_catalog_upgrade_adds_image_support_without_overwriting_other_models(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            manager = ModelCatalogManager(Path(name))
            manager.target_path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "custom-model",
                                "display_name": "Custom",
                                "context_window": 1000,
                            },
                            {
                                "slug": "glm-5.3-flash",
                                "display_name": "glm-5.3-flash",
                                "context_window": 1000,
                                "input_modalities": ["text"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            changed, backup = manager.ensure_model_profile("glm-5.3-flash")
            payload = json.loads(manager.target_path.read_text(encoding="utf-8"))
            self.assertTrue(changed)
            self.assertIsNotNone(backup)
            self.assertEqual(["text", "image"], payload["models"][1]["input_modalities"])
            self.assertEqual("custom-model", payload["models"][0]["slug"])

    def test_bundled_flash_catalog_supports_images_by_default(self) -> None:
        manager = ModelCatalogManager(Path("unused"))
        payload, _ = validate_catalog_text(manager.bundled_text())
        flash = next(item for item in payload["models"] if item["slug"] == "glm-5.3-flash")
        self.assertEqual(["text", "image"], flash["input_modalities"])


if __name__ == "__main__":
    unittest.main()
