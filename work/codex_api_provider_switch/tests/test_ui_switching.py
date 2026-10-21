from __future__ import annotations

import os
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QPushButton
    from codex_api_provider_switch import build_demo_controller
    from provider_switch.auth_manager import AuthStatus
    from provider_switch.qt_ui import ProviderSwitchWindow
    from provider_switch.monitor_history import HealthRecord
except ImportError:  # pragma: no cover - exercised only on minimal Tk installs
    QApplication = None  # type: ignore[assignment]
    QPushButton = None  # type: ignore[assignment]
    build_demo_controller = None  # type: ignore[assignment]
    ProviderSwitchWindow = None  # type: ignore[assignment]


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed")
class QtSwitchEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="provider-switch-ui-test-")
        self.controller = build_demo_controller(Path(self.temp.name))
        self.window = ProviderSwitchWindow(
            self.controller,
            start_monitor=False,
            show_wizard=False,
        )
        self.window._start_switch = Mock()
        self.window.show_page = Mock()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_non_official_routes_skip_coexistence_prompt(self) -> None:
        with patch("provider_switch.qt_ui.QMessageBox") as message_box:
            for current_id, target_id in (
                ("relay1", "relay2"),
                ("relay1", "glm"),
                ("glm", "relay1"),
                ("deepseek", "glm"),
                ("glm", "deepseek"),
                ("deepseek", "relay2"),
            ):
                with self.subTest(current_id=current_id, target_id=target_id):
                    self.controller.detect_active_profile = Mock(return_value=current_id)
                    self.window._start_switch.reset_mock()
                    self.controller.credentials["deepseek"] = "test-deepseek-key"
                    self.window.confirm_switch(target_id)
                    self.window._start_switch.assert_called_once_with(target_id, True)
            message_box.assert_not_called()

    def test_native_card_choice_and_catalogs_are_independent(self) -> None:
        self.window._run_job = Mock()
        combo = self.window.native_profile_combo
        combo.setCurrentIndex(combo.findData("deepseek"))
        self.assertEqual("deepseek", self.controller.settings.native_profile_id)
        self.assertTrue(self.window.provider_cards["glm"].isHidden())
        self.assertFalse(self.window.provider_cards["deepseek"].isHidden())
        self.assertEqual(4, sum(not card.isHidden() for card in self.window.provider_cards.values()))
        profile_combo = self.window.profile_combo
        profile_combo.setCurrentIndex(profile_combo.findData("deepseek"))
        self.assertEqual("deepseek-flash", self.window.profile_model.text())
        self.assertTrue(self.window.profile_quota_org.isHidden())
        self.window.catalog_profile_combo.setCurrentIndex(self.window.catalog_profile_combo.findData("deepseek"))
        self.assertEqual(self.controller.deepseek_catalog.target_path, self.window._catalog_manager().target_path)
        self.assertIn('"deepseek-flash"', self.window.catalog_editor.toPlainText())
        self.assertNotIn('"glm-5.3-flash"', self.window.catalog_editor.toPlainText())

    def test_monitor_summary_counts_deepseek_requests(self) -> None:
        self.window._render_monitor_page((3600, [HealthRecord(time.time(), "deepseek", "healthy", 150)]))
        self.assertEqual("100.0%", self.window.monitor_availability_card._stat_value.text())
        self.assertEqual("1/5", self.window.monitor_providers_card._stat_value.text())

    def test_monitor_page_has_no_availability_timeline(self) -> None:
        """The monitoring page keeps request summaries without the old bar chart."""
        self.assertFalse(hasattr(self.window, "_timeline_widget"))
        self.assertFalse(hasattr(self.window, "_timeline_labels"))
        self.assertNotIn("时间线", self.window._monitor_scope_hint.text())

    def test_config_editor_exposes_current_provider_default_button(self) -> None:
        labels = {button.text() for button in self.window.findChildren(QPushButton)}
        self.assertIn("恢复当前默认", labels)
        self.assertIn("登录并切换直连", labels)
        self.assertIn("断开其他 API 供应商", labels)

    def test_direct_login_button_recovers_after_monitor_busy_state(self) -> None:
        self.controller.settings.retain_official_auth = False
        self.window.cached_auth = AuthStatus(
            "chatgpt", True, self.controller.codex_home / "auth.json"
        )
        self.window._busy_count = 1
        self.window._set_busy(False)
        self.assertTrue(self.window.auth_direct_button.isEnabled())

    def test_chatgpt_login_state_is_distinguished_from_active_route(self) -> None:
        self.controller.settings.retain_official_auth = False
        self.controller.detect_active_profile = Mock(return_value="relay1")
        self.window._busy_count = 0
        self.window._handle_auth_status(
            AuthStatus("chatgpt", True, self.controller.codex_home / "auth.json")
        )
        self.assertIn("当前路由：relay1", self.window.auth_status_label.text())
        self.assertIn("未启用 OpenAI 直连", self.window.policy_detail.text())
        self.assertTrue(self.window.auth_direct_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
