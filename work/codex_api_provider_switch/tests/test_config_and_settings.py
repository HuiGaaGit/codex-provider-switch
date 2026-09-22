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

    def test_aqyimin_image_catalog_is_restored_after_glm_switch(self) -> None:
        original = (
            'model_provider = "custom"\n'
            'model = "gpt-5.6-sol"\n'
            'model_catalog_json = "C:/Users/test/.codex/gpt-models.json"\n\n'
            '[model_providers.custom]\n'
            'name = "aqyimin"\n'
            'base_url = "https://www.aqyimin.chat/v1"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = true\n'
        )
        (self.home / "config.toml").write_text(original, encoding="utf-8")
        glm_catalog = self.home / "models.json"
        glm_catalog.write_text(
            '{"models": [{"slug": "glm-5.3-flash", "display_name": "GLM", "context_window": 1000}]}',
            encoding="utf-8",
        )
        glm = ProviderProfile("glm", "GLM", "glm", "ZAI", "https://open.bigmodel.cn/api/v1", "glm-5.3-flash")
        self.manager.apply_profile(glm, "glm-secret", True, "custom", glm_catalog)
        glm_config = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(glm_catalog.resolve().as_posix(), glm_config["model_catalog_json"])

        aqyimin = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1", "gpt-5.6-sol", requires_openai_auth=True
        )
        self.manager.apply_profile(aqyimin, "api-secret", True, "custom")
        restored = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("C:/Users/test/.codex/gpt-models.json", restored["model_catalog_json"])
        self.assertEqual("https://www.aqyimin.chat/v1", restored["model_providers"]["custom"]["base_url"])

    def test_aqyimin_render_forces_api_key_auth_even_when_profile_requests_official(self) -> None:
        profile = ProviderProfile(
            "relay1",
            "API1",
            "relay",
            "relay_1",
            "https://www.aqyimin.chat/v1",
            "gpt-5.6-sol",
            requires_openai_auth=True,
        )
        self.manager.apply_profile(profile, "ap1-secret", True, "cch_gz")
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        provider = parsed["model_providers"]["cch_gz"]
        self.assertFalse(provider["requires_openai_auth"])
        self.assertEqual("ap1-secret", provider["experimental_bearer_token"])

    def test_aqyimin_switch_adds_missing_image_extension_defaults(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "custom"\nmodel = "gpt-6-astra"\n\n'
            '[model_providers.custom]\n'
            'base_url = "https://www.aqyimin.chat/v1"\n'
            'wire_api = "responses"\n',
            encoding="utf-8",
        )
        profile = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1", "gpt-6-astra"
        )
        self.manager.apply_profile(profile, "ap1-secret", True, "custom")
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertTrue(parsed["features"]["image_generation"])
        self.assertEqual(
            "local-image-extension",
            parsed["model_providers"]["custom"]["http_headers"]["x-openai-actor-authorization"],
        )

    def test_aqyimin_switch_keeps_explicit_image_disable(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "custom"\n\n[features]\nimage_generation = false\n\n'
            '[model_providers.custom]\n'
            'base_url = "https://www.aqyimin.chat/v1"\n'
            'http_headers = { x-openai-actor-authorization = "manual-marker" }\n',
            encoding="utf-8",
        )
        profile = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1", "gpt-6-astra"
        )
        self.manager.apply_profile(profile, "ap1-secret", True, "custom")
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertFalse(parsed["features"]["image_generation"])
        self.assertEqual(
            "manual-marker",
            parsed["model_providers"]["custom"]["http_headers"]["x-openai-actor-authorization"],
        )

    def test_existing_aqyimin_config_is_repaired_without_printing_or_replacing_key(self) -> None:
        original = (
            'model_provider = "custom"\n\n'
            '[model_providers.custom]\n'
            'base_url = "https://www.aqyimin.chat/v1"\n'
            'requires_openai_auth = true\n'
            'experimental_bearer_token = "ap1-secret"\n'
            'env_key = "OPENAI_API_KEY"\n'
        )
        (self.home / "config.toml").write_text(original, encoding="utf-8")
        changed = self.manager.normalize_active_provider_auth()
        self.assertTrue(changed)
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        provider = parsed["model_providers"]["custom"]
        self.assertFalse(provider["requires_openai_auth"])
        self.assertEqual("ap1-secret", provider["experimental_bearer_token"])
        self.assertNotIn("env_key", provider)
        self.assertTrue(parsed["features"]["image_generation"])
        self.assertEqual(
            "local-image-extension",
            provider["http_headers"]["x-openai-actor-authorization"],
        )
        self.assertNotIn("ap1-secret", self.manager.image_capability_backup.read_text(encoding="utf-8") if self.manager.image_capability_backup.exists() else "")

    def test_aqyimin_compatibility_round_trip_restores_image_header_and_service_tier(self) -> None:
        original = (
            'model_provider = "custom"\n'
            'service_tier = "priority"\n'
            'model = "gpt-6-astra"\n'
            'model_reasoning_effort = "max"\n\n'
            '[features]\n'
            'image_generation = true\n'
            'steer = true\n\n'
            '[model_providers.custom]\n'
            'name = "custom"\n'
            'base_url = "https://www.aqyimin.chat/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "OPENAI_API_KEY"\n'
            'requires_openai_auth = false\n'
            'http_headers = { x-openai-actor-authorization = "local-image-extension", x-tenant = "keep-me" }\n'
            'experimental_bearer_token = "ap1-secret-do-not-persist"\n'
        )
        (self.home / "config.toml").write_text(original, encoding="utf-8")
        glm_catalog = self.home / "models-glm.json"
        glm_catalog.write_text(
            '{"models": [{"slug": "glm-5.3-flash", "display_name": "GLM", "context_window": 1000}]}',
            encoding="utf-8",
        )
        glm = ProviderProfile("glm", "GLM", "glm", "ZAI", "https://open.bigmodel.cn/api/v1", "glm-5.3-flash")
        self.manager.apply_profile(glm, "glm-secret", True, "custom", glm_catalog)
        glm_text = (self.home / "config.toml").read_text(encoding="utf-8")
        glm_config = tomllib.loads(glm_text)
        self.assertNotIn("image_generation", glm_config.get("features", {}))
        self.assertNotIn("x-openai-actor-authorization", glm_text)
        self.assertTrue(glm_config["features"]["steer"])
        self.assertNotIn("ap1-secret-do-not-persist", self.manager.image_capability_backup.read_text(encoding="utf-8"))

        aqyimin = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1",
            "gpt-6-astra", requires_openai_auth=False,
        )
        self.manager.apply_profile(aqyimin, "new-ap1-secret", True, "custom")
        restored_text = (self.home / "config.toml").read_text(encoding="utf-8")
        restored = tomllib.loads(restored_text)
        self.assertEqual("priority", restored["service_tier"])
        self.assertEqual("gpt-6-astra", restored["model"])
        self.assertEqual("max", restored["model_reasoning_effort"])
        self.assertTrue(restored["features"]["image_generation"])
        self.assertTrue(restored["features"]["steer"])
        self.assertEqual(
            "local-image-extension",
            restored["model_providers"]["custom"]["http_headers"]["x-openai-actor-authorization"],
        )
        self.assertEqual("keep-me", restored["model_providers"]["custom"]["http_headers"]["x-tenant"])
        self.assertEqual("new-ap1-secret", restored["model_providers"]["custom"]["experimental_bearer_token"])
        self.assertNotIn("env_key", restored["model_providers"]["custom"])

    def test_switching_from_aqyimin_to_relay_removes_ap1_image_settings(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "custom"\n'
            'service_tier = "priority"\n'
            'model = "gpt-6-astra"\n\n'
            '[features]\n'
            'image_generation = true\n'
            'steer = true\n\n'
            '[model_providers.custom]\n'
            'base_url = "https://www.aqyimin.chat/v1"\n'
            'wire_api = "responses"\n'
            'http_headers = { x-openai-actor-authorization = "local-image-extension" }\n'
            'experimental_bearer_token = "old"\n',
            encoding="utf-8",
        )
        relay = ProviderProfile(
            "relay2", "API2", "relay", "relay_2", "https://relay.example/v1", "gpt-5.6-sol",
            requires_openai_auth=False,
        )
        self.manager.apply_profile(relay, "relay-secret", True, "custom")
        text = (self.home / "config.toml").read_text(encoding="utf-8")
        parsed = tomllib.loads(text)
        self.assertNotIn("image_generation", parsed.get("features", {}))
        self.assertNotIn("x-openai-actor-authorization", text)
        self.assertNotIn("service_tier", parsed)
        self.assertTrue(parsed["features"]["steer"])

        aqyimin = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1", "gpt-6-astra"
        )
        self.manager.apply_profile(aqyimin, "ap1-secret", True, "custom")
        restored = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("priority", restored["service_tier"])
        self.assertTrue(restored["features"]["image_generation"])
        self.assertEqual(
            "local-image-extension",
            restored["model_providers"]["custom"]["http_headers"]["x-openai-actor-authorization"],
        )

    def test_aqyimin_compatibility_round_trip_through_official_route(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "custom"\n'
            'service_tier = "priority"\n'
            'model = "gpt-6-astra"\n\n'
            '[features]\nimage_generation = true\n\n'
            '[model_providers.custom]\n'
            'base_url = "https://www.aqyimin.chat/v1"\n'
            'wire_api = "responses"\n'
            'http_headers = { x-openai-actor-authorization = "local-image-extension" }\n'
            'experimental_bearer_token = "old"\n',
            encoding="utf-8",
        )
        official = ProviderProfile(
            "openai", "OpenAI 直连", "official", "openai", model="gpt-6-astra"
        )
        self.manager.apply_profile(
            official, "", True, "custom", clear_other_keys=["custom"]
        )
        official_text = (self.home / "config.toml").read_text(encoding="utf-8")
        official_config = tomllib.loads(official_text)
        self.assertNotIn("model_provider", official_config)
        self.assertNotIn("service_tier", official_config)
        self.assertNotIn("image_generation", official_config.get("features", {}))
        self.assertNotIn("x-openai-actor-authorization", official_text)

        aqyimin = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1", "gpt-6-astra"
        )
        self.manager.apply_profile(aqyimin, "new", True, "custom")
        restored = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("priority", restored["service_tier"])
        self.assertTrue(restored["features"]["image_generation"])
        self.assertEqual(
            "local-image-extension",
            restored["model_providers"]["custom"]["http_headers"]["x-openai-actor-authorization"],
        )

    def test_aqyimin_restore_keeps_profile_model_when_legacy_snapshot_has_none(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "custom"\nmodel = "glm-5.3-flash"\n'
            'model_catalog_json = "C:/temp/models-glm.json"\n\n'
            '[model_providers.custom]\nbase_url = "https://open.bigmodel.cn/api/v1"\n'
            'wire_api = "responses"\n',
            encoding="utf-8",
        )
        self.manager.image_capability_backup.parent.mkdir(parents=True, exist_ok=True)
        self.manager.image_capability_backup.write_text(
            json.dumps(
                {
                    "schema": 3,
                    "provider_base_url": "https://www.aqyimin.chat/v1",
                    "values": {},
                    "features": {},
                    "http_headers": {},
                }
            ),
            encoding="utf-8",
        )
        aqyimin = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1", "gpt-6-astra"
        )
        self.manager.apply_profile(aqyimin, "new-key", True, "custom")
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("gpt-6-astra", parsed["model"])
        self.assertNotIn("model_catalog_json", parsed)

    def test_legacy_ambiguous_models_catalog_is_not_restored_to_api1(self) -> None:
        original = (
            'model_provider = "custom"\n'
            'model = "glm-5.3-flash"\n'
            'model_reasoning_effort = "max"\n'
            f'model_catalog_json = "{(self.home / "models.json").as_posix()}"\n\n'
            '[model_providers.custom]\n'
            'name = "GLM"\n'
            'base_url = "https://open.bigmodel.cn/api/v1"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = false\n'
        )
        (self.home / "config.toml").write_text(original, encoding="utf-8")
        self.home.joinpath("provider-switch-backups").mkdir()
        self.home.joinpath("provider-switch-backups/image-capability.json").write_text(
            f'{{"model_catalog_json":"{(self.home / "models.json").as_posix()}",'
            '"model_reasoning_effort":"max"}',
            encoding="utf-8",
        )
        profile = ProviderProfile(
            "relay1", "API1", "relay", "relay_1", "https://www.aqyimin.chat/v1", "gpt-5.6-sol", requires_openai_auth=True
        )
        self.manager.apply_profile(profile, "api-secret", True, "custom")
        parsed = tomllib.loads((self.home / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual("gpt-5.6-sol", parsed["model"])
        self.assertNotIn("model_catalog_json", parsed)
        self.assertNotIn("model_reasoning_effort", parsed)

    def test_glm_switch_removes_stale_auth_and_header_fields(self) -> None:
        (self.home / "config.toml").write_text(
            'model_provider = "custom"\n'
            'model = "glm-5.3-flash"\n\n'
            '[features]\n'
            'image_generation = true\n'
            'steer = true\n\n'
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
        self.assertNotIn("image_generation", parsed["features"])
        self.assertTrue(parsed["features"]["steer"])

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

    def test_bootstrap_never_treats_aqyimin_auth_flag_as_official_auth(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            snapshot = ConfigSnapshot(
                path=str(root / ".codex" / "config.toml"),
                model_provider="custom",
                model="gpt-6-astra",
                providers={
                    "custom": {
                        "name": "API1",
                        "base_url": "https://www.aqyimin.chat/v1",
                        "requires_openai_auth": True,
                        "experimental_bearer_token": "ap1-secret",
                    }
                },
            )
            settings, credentials = SettingsStore(root / "data").bootstrap(
                snapshot, root / "missing-legacy.json"
            )
            self.assertTrue(settings.retain_official_auth)
            self.assertFalse(settings.profiles["relay1"].requires_openai_auth)
            self.assertFalse(settings.profiles["relay2"].requires_openai_auth)
            self.assertEqual({"relay1": "ap1-secret"}, credentials)

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
