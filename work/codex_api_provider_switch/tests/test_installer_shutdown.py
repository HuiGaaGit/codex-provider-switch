from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallerConfigTests(unittest.TestCase):
    def test_installer_uses_filtered_force_close_and_shutdown_fallback(self) -> None:
        script = (PROJECT_ROOT / "installer" / "Codex Provider Switch-1.3.1.iss").read_text(
            encoding="utf-8"
        )
        self.assertIn("CloseApplications=force", script)
        self.assertIn("CloseApplicationsFilter=Codex Provider Switch.exe", script)
        self.assertIn("RestartApplications=no", script)
        self.assertIn("--installer-shutdown", script)


try:
    from provider_switch.qt_ui import request_application_shutdown
except ImportError:  # pragma: no cover - minimal Tk-only environments
    request_application_shutdown = None  # type: ignore[assignment]


@unittest.skipUnless(request_application_shutdown is not None, "PySide6 is not installed")
class ShutdownIpcTests(unittest.TestCase):
    def test_shutdown_command_is_sent_to_existing_instance(self) -> None:
        socket = MagicMock()
        socket.waitForConnected.return_value = True
        with patch("provider_switch.qt_ui.QLocalSocket", return_value=socket):
            self.assertTrue(request_application_shutdown())
        socket.connectToServer.assert_called_once_with("CodexProviderSwitch.BackgroundMonitor")
        command = socket.write.call_args.args[0]
        self.assertTrue(command.startswith(b"shutdown|"))
        socket.disconnectFromServer.assert_called_once_with()

    def test_missing_instance_is_not_an_error(self) -> None:
        socket = MagicMock()
        socket.waitForConnected.return_value = False
        with patch("provider_switch.qt_ui.QLocalSocket", return_value=socket):
            self.assertFalse(request_application_shutdown())
        socket.abort.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
