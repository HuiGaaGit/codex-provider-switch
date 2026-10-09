from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QPushButton
    from codex_api_provider_switch import build_demo_controller
    from provider_switch.auth_manager import AuthStatus
    from provider_switch.qt_ui import ProviderSwitchWindow
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
            ):
                with self.subTest(current_id=current_id, target_id=target_id):
                    self.controller.detect_active_profile = Mock(return_value=current_id)
                    self.window._start_switch.reset_mock()
                    self.window.confirm_switch(target_id)
                    self.window._start_switch.assert_called_once_with(target_id, True)
            message_box.assert_not_called()

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
