from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from codex_api_provider_switch import build_demo_controller
    from provider_switch.qt_ui import ProviderSwitchWindow
except ImportError:  # pragma: no cover - exercised only on minimal Tk installs
    QApplication = None  # type: ignore[assignment]
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


if __name__ == "__main__":
    unittest.main()
