from __future__ import annotations

import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock

from provider_switch.auth_manager import AuthStatus, CodexAuthError, CodexAuthManager
from provider_switch.controller import ApplicationController
from provider_switch.models import AppSettings
from provider_switch.settings import SettingsError, SettingsStore


class AuthManagerTests(unittest.TestCase):
    def test_status_detects_api_key_without_returning_key_text(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            runner = Mock(
                return_value=subprocess.CompletedProcess(
                    [], 0, "Logged in using an API key - api-key-redacted", ""
                )
            )
            status = CodexAuthManager(Path(name), runner=runner, command="codex").status()
            self.assertEqual("api_key", status.state)
            self.assertTrue(status.logged_in)
            self.assertFalse(status.official_account_logged_in)
            self.assertNotIn("sensitive", status.label + status.detail)

    def test_unclassified_auth_file_does_not_unlock_official_account_route(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            (home / "auth.json").write_text('{"auth": "redacted"}', encoding="utf-8")
            runner = Mock(
                return_value=subprocess.CompletedProcess([], 0, "Logged in using a workspace credential", "")
            )
            status = CodexAuthManager(home, runner=runner, command="codex").status()
            self.assertEqual("authenticated", status.state)
            self.assertFalse(status.official_account_logged_in)

    def test_logout_ignores_external_api_key_after_auth_file_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            auth_path = home / "auth.json"
            auth_path.write_text('{"token":"secret"}', encoding="utf-8")
            runner = Mock(
                side_effect=(
                    subprocess.CompletedProcess([], 0, "Logged out", ""),
                    subprocess.CompletedProcess([], 0, "Logged in using an API key", ""),
                )
            )
            status = CodexAuthManager(home, runner=runner, command="codex").logout()
            self.assertFalse(auth_path.exists())
            self.assertEqual("api_key", status.state)
            self.assertFalse(status.official_account_logged_in)

    def test_logout_removes_residual_auth_file(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            auth_path = home / "auth.json"
            auth_path.write_text('{"token":"secret"}', encoding="utf-8")
            runner = Mock(
                side_effect=(
                    subprocess.CompletedProcess([], 0, "Logged out", ""),
                    subprocess.CompletedProcess([], 1, "Not logged in", ""),
                )
            )
            status = CodexAuthManager(home, runner=runner, command="codex").logout()
            self.assertFalse(auth_path.exists())
            self.assertEqual("signed_out", status.state)

    def test_interactive_login_requires_detectable_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            runner = Mock(
                return_value=subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", "")
            )
            process = Mock()
            process.wait.return_value = 0
            status = CodexAuthManager(
                Path(name), runner=runner, popen_factory=Mock(return_value=process), command="codex"
            ).login_interactive()
            self.assertEqual("chatgpt", status.state)

    def test_interactive_api_key_login_does_not_unlock_official_route(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            runner = Mock(
                return_value=subprocess.CompletedProcess(
                    [], 0, "Logged in using an API key", ""
                )
            )
            process = Mock()
            process.wait.return_value = 0
            with self.assertRaisesRegex(CodexAuthError, "官方账号"):
                CodexAuthManager(
                    Path(name), runner=runner, popen_factory=Mock(return_value=process), command="codex"
                ).login_interactive()


class FakeAuthManager:
    def __init__(self, home: Path, logged_in: bool = False) -> None:
        self.home = home
        self.logged_in = logged_in
        self.login_calls = 0
        self.logout_calls = 0

    def status(self) -> AuthStatus:
        return AuthStatus(
            "chatgpt" if self.logged_in else "signed_out",
            self.logged_in,
            self.home / "auth.json",
        )

    def login_interactive(self) -> AuthStatus:
        self.login_calls += 1
        self.logged_in = True
        return self.status()

    def logout(self) -> AuthStatus:
        self.logout_calls += 1
        self.logged_in = False
        return self.status()


def build_controller(root: Path, *, active_glm: bool = False) -> ApplicationController:
    home = root / ".codex"
    home.mkdir()
    if active_glm:
        config = (
            'model_provider = "cch_gz"\nmodel = "glm-5.3"\n\n'
            '[model_providers.cch_gz]\nname = "GLM"\n'
            'base_url = "https://open.bigmodel.cn/api/v1"\nwire_api = "responses"\n'
            'requires_openai_auth = false\nexperimental_bearer_token = "glm-key"\n'
        )
    else:
        config = '# OpenAI direct\nmodel = "gpt-5.6-sol"\n'
    (home / "config.toml").write_text(config, encoding="utf-8")
    settings = AppSettings(
        codex_home=str(home),
        setup_complete=True,
        stable_provider_key="cch_gz",
        auto_restart=False,
        auto_sync_history=False,
        active_profile_id="glm" if active_glm else "openai",
        retain_official_auth=False,
    )
    settings.profiles["relay1"].base_url = "https://relay.example/v1"
    settings.profiles["relay1"].model = "gpt-5.6-sol"
    settings.profiles["glm"].base_url = "https://open.bigmodel.cn/api/v1"
    settings.profiles["glm"].model = "glm-5.3"
    store = SettingsStore(root / "data")
    store.save(settings, {"relay1": "relay-key", "glm": "glm-key"})
    return ApplicationController(store)


class DeploymentPolicyTests(unittest.TestCase):
    def test_openai_switch_is_blocked_without_retained_login(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))
            controller.auth = FakeAuthManager(controller.codex_home, logged_in=False)  # type: ignore[assignment]
            with self.assertRaises(CodexAuthError):
                controller.switch_profile("openai")

    def test_openai_api_key_does_not_unlock_official_direct(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))
            controller.settings.retain_official_auth = True
            controller.save()
            controller.auth = Mock()
            controller.auth.status.return_value = AuthStatus(
                "api_key", False, controller.codex_home / "auth.json"
            )

            self.assertFalse(controller.openai_available(controller.auth.status()))
            self.assertFalse(controller.profile_ready("openai"))
            with self.assertRaises(CodexAuthError):
                controller.switch_profile("openai")

    def test_restore_from_glm_switches_to_relay_and_keeps_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name), active_glm=True)
            fake_auth = FakeAuthManager(controller.codex_home, logged_in=False)
            controller.auth = fake_auth  # type: ignore[assignment]
            original_url = controller.settings.profiles["relay1"].base_url
            switched, status = controller.restore_official_login()
            parsed = tomllib.loads((controller.codex_home / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual("relay1", switched.profile_id)
            self.assertEqual("https://relay.example/v1", parsed["model_providers"]["cch_gz"]["base_url"])
            self.assertEqual(original_url, controller.settings.profiles["relay1"].base_url)
            self.assertEqual("relay-key", controller.credentials["relay1"])
            self.assertTrue(status.logged_in)
            self.assertTrue(controller.settings.retain_official_auth)
            self.assertEqual(1, fake_auth.login_calls)

    def test_retained_login_survives_glm_switch_and_uses_glm_key(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))
            fake_auth = FakeAuthManager(controller.codex_home, logged_in=True)
            controller.auth = fake_auth  # type: ignore[assignment]
            controller._set_auth_retention(True)

            controller.switch_profile("glm")

            parsed = tomllib.loads((controller.codex_home / "config.toml").read_text(encoding="utf-8"))
            provider = parsed["model_providers"]["cch_gz"]
            self.assertEqual("glm-5.3", parsed["model"])
            self.assertFalse(provider["requires_openai_auth"])
            self.assertEqual("glm-key", provider["experimental_bearer_token"])
            self.assertTrue(fake_auth.status().logged_in)
            self.assertEqual(0, fake_auth.logout_calls)

    def test_restore_from_glm_keeps_login_and_rewrites_relay_policy(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name), active_glm=True)
            fake_auth = FakeAuthManager(controller.codex_home, logged_in=True)
            controller.auth = fake_auth  # type: ignore[assignment]

            switched, status = controller.restore_official_login()

            parsed = tomllib.loads((controller.codex_home / "config.toml").read_text(encoding="utf-8"))
            provider = parsed["model_providers"]["cch_gz"]
            self.assertEqual("relay1", switched.profile_id)
            self.assertTrue(provider["requires_openai_auth"])
            self.assertEqual("relay-key", provider["experimental_bearer_token"])
            self.assertTrue(status.logged_in)
            self.assertEqual(0, fake_auth.login_calls)
            self.assertEqual(0, fake_auth.logout_calls)

    def test_glm_to_openai_round_trip_does_not_logout(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))
            fake_auth = FakeAuthManager(controller.codex_home, logged_in=True)
            controller.auth = fake_auth  # type: ignore[assignment]
            controller._set_auth_retention(True)

            controller.switch_profile("glm")
            controller.switch_profile("openai")

            parsed = tomllib.loads((controller.codex_home / "config.toml").read_text(encoding="utf-8"))
            self.assertNotIn("model_provider", parsed)
            self.assertNotIn("model_catalog_json", parsed)
            self.assertTrue(fake_auth.status().logged_in)
            self.assertEqual(0, fake_auth.logout_calls)

    def test_openai_switch_disconnect_cleared_glm_bearer_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name), active_glm=True)
            controller.auth = FakeAuthManager(controller.codex_home, logged_in=True)  # type: ignore[assignment]
            controller._set_auth_retention(True)

            controller.switch_profile("openai", disconnect_others=True)

            parsed = tomllib.loads((controller.codex_home / "config.toml").read_text(encoding="utf-8"))
            provider = parsed["model_providers"]["cch_gz"]
            self.assertNotIn("experimental_bearer_token", provider)
            self.assertEqual("https://open.bigmodel.cn/api/v1", provider["base_url"])

    def test_openai_switch_keep_alive_preserves_glm_bearer(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name), active_glm=True)
            controller.auth = FakeAuthManager(controller.codex_home, logged_in=True)  # type: ignore[assignment]
            controller._set_auth_retention(True)

            controller.switch_profile("openai", disconnect_others=False)

            parsed = tomllib.loads((controller.codex_home / "config.toml").read_text(encoding="utf-8"))
            provider = parsed["model_providers"]["cch_gz"]
            self.assertEqual("glm-key", provider["experimental_bearer_token"])

    def test_disconnect_other_providers_keeps_active_glm_connection(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name), active_glm=True)
            controller.settings.preserve_provider_key = False
            controller.settings.profiles["glm"].provider_key = "ZAI"
            controller.switch_profile("glm", disconnect_others=False)

            result = controller.disconnect_other_providers()

            parsed = tomllib.loads((controller.codex_home / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual("ZAI", parsed["model_provider"])
            self.assertEqual("glm-key", parsed["model_providers"]["ZAI"]["experimental_bearer_token"])
            self.assertTrue(any("已断开" in item for item in result.warnings))

    def test_single_relay_setup_enables_only_configured_relay(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))

            self.assertTrue(controller.profile_ready("relay1"))
            self.assertFalse(controller.profile_ready("relay2"))
            result = controller.switch_profile("relay1")
            self.assertEqual("relay1", result.profile_id)

            with self.assertRaisesRegex(SettingsError, "API2.*缺少"):
                controller.switch_profile("relay2")

    def test_glm_and_single_configured_relay_switch_without_other_relay(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name), active_glm=True)
            controller.settings.profiles["relay2"].base_url = ""
            controller.settings.profiles["relay2"].model = ""
            controller.credentials.pop("relay2", None)

            first = controller.switch_profile("relay1")
            self.assertEqual("relay1", first.profile_id)
            second = controller.switch_profile("glm")
            self.assertEqual("glm", second.profile_id)
            self.assertFalse(controller.profile_ready("relay2"))

    def test_relay2_only_is_valid_and_used_as_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))
            relay1 = controller.settings.profiles["relay1"]
            relay1.base_url = ""
            relay1.model = ""
            controller.credentials.pop("relay1", None)
            relay2 = controller.settings.profiles["relay2"]
            relay2.base_url = "https://relay2.example/v1"
            relay2.model = "gpt-5.6-sol"
            controller.credentials["relay2"] = "relay2-key"
            controller.settings.last_relay_profile_id = "relay1"
            controller.save()

            self.assertFalse(controller.profile_ready("relay1"))
            self.assertTrue(controller.profile_ready("relay2"))
            self.assertEqual("relay2", controller.relay_fallback())
            result = controller.switch_profile("relay2")
            self.assertEqual("relay2", result.profile_id)

    def test_restore_from_glm_without_relay_does_not_start_login(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name), active_glm=True)
            relay1 = controller.settings.profiles["relay1"]
            relay1.base_url = ""
            relay1.model = ""
            controller.credentials.pop("relay1", None)
            fake_auth = FakeAuthManager(controller.codex_home, logged_in=False)
            controller.auth = fake_auth  # type: ignore[assignment]

            with self.assertRaisesRegex(SettingsError, "先完整配置 API1"):
                controller.restore_official_login()

            self.assertEqual(0, fake_auth.login_calls)
            self.assertFalse(controller.settings.retain_official_auth)

    def test_restart_also_runs_when_launched_from_codex(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))
            controller.settings.auto_restart = True
            process_controller = Mock()
            process_controller.locate_running.return_value = Mock()
            process_controller.launched_from_codex.return_value = True
            controller.process_controller = process_controller

            result = controller.switch_profile("relay1")

            self.assertEqual("relay1", controller.settings.active_profile_id)
            self.assertEqual("relay1", result.profile_id)
            process_controller.stop.assert_called_once_with()
            process_controller.start.assert_called_once()

    def test_restart_happens_only_after_configuration_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = build_controller(Path(name))
            controller.settings.auto_restart = True
            process_controller = Mock()
            process_controller.locate_running.return_value = Mock()
            process_controller.launched_from_codex.return_value = False

            def assert_config_saved() -> str:
                parsed = tomllib.loads(
                    (controller.codex_home / "config.toml").read_text(encoding="utf-8")
                )
                self.assertEqual("cch_gz", parsed["model_provider"])
                self.assertEqual("relay1", controller.settings.active_profile_id)
                return "stopped"

            process_controller.stop.side_effect = assert_config_saved
            process_controller.start.return_value = "restarted"
            controller.process_controller = process_controller

            result = controller.switch_profile("relay1")

            self.assertEqual("restarted", result.restart_summary)
            process_controller.stop.assert_called_once_with()
            process_controller.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
