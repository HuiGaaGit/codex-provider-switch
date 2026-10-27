"""Offline configuration load and Qt previews, using temporary profiles only."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from codex_api_provider_switch import build_demo_controller
from provider_switch.auth_manager import AuthStatus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", type=Path)
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--tk-smoke", action="store_true")
    parser.add_argument("--balance-demo", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="provider-switch-ds-audit-") as temporary:
        controller = build_demo_controller(Path(temporary))
        controller.settings.auto_restart = False
        controller.settings.auto_sync_history = False
        controller.settings.close_to_tray = False
        controller.credentials.update(deepseek="local-test-placeholder", glm="local-test-placeholder")
        controller.auth.status = lambda: AuthStatus("signed_out", False, controller.codex_home / "auth.json")
        for pid in ("deepseek", "glm", "relay1"):
            controller.switch_profile(pid)
            if args.codex:
                env = os.environ.copy()
                env["CODEX_HOME"] = str(controller.codex_home)
                env.pop("OPENAI_API_KEY", None)
                result = subprocess.run(
                    [str(args.codex), "features", "list"],
                    cwd=temporary, env=env, capture_output=True, text=True,
                    timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if result.returncode:
                    raise RuntimeError(f"Codex config load failed for {pid}: {result.stderr[:1800]}")
                print(f"Codex offline configuration load passed: {pid}")
        if args.tk_smoke:
            from provider_switch.ui import ProviderSwitchApp, SetupWizard as TkSetupWizard
            root = ProviderSwitchApp(controller, start_monitor=False, show_wizard=False)
            root.withdraw()
            root.native_profile_var.set("deepseek")
            root._select_native_profile()
            root.load_catalog_from_disk()
            assert "deepseek-flash" in root.catalog_text.get("1.0", "end-1c")
            wizard = TkSetupWizard(root, controller, on_complete=lambda: None)
            wizard.withdraw()
            wizard.vars["native_profile_id"].set("deepseek")
            wizard.step = 1
            wizard.render_step()
            wizard.step = 2
            wizard.render_step()
            wizard.step = 3
            wizard.render_step()
            root.update_idletasks()
            wizard.destroy()
            root.destroy()
            print("Tk DeepSeek selector, catalog and setup smoke passed")
        if args.preview_dir:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QFont, QFontDatabase
            from provider_switch.qt_ui import ProviderSwitchWindow, SetupWizard

            app = QApplication.instance() or QApplication([])
            # The offscreen Windows platform does not enumerate system fonts.
            for font_name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
                font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_name
                if font_path.is_file():
                    QFontDatabase.addApplicationFont(str(font_path))
            app.setFont(QFont("Microsoft YaHei", 10))
            window = ProviderSwitchWindow(controller, start_monitor=False, show_wizard=False)
            window.resize(1180, 850)
            window.show()
            limit = time.monotonic() + 0.5
            while time.monotonic() < limit:
                app.processEvents()
                window._drain_jobs()
            combo = window.native_profile_combo
            combo.setCurrentIndex(combo.findData("deepseek"))
            if args.balance_demo:
                from provider_switch.balances import parse_deepseek_balance
                from provider_switch.models import HealthResult, ProviderTelemetry
                balances, available = parse_deepseek_balance({
                    "is_available": True, "balance_infos": [{"currency": "CNY",
                        "total_balance": "1234.56", "granted_balance": "10.00",
                        "topped_up_balance": "1224.56"}],
                })
                window._handle_quota_query("deepseek", ProviderTelemetry(
                    "deepseek", HealthResult("deepseek", "unknown", "演示查询"),
                    quota_message="演示余额，非真实账户", balances=balances,
                    balance_available=available, balance_checked_at="12:30:00",
                ))
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            for page in ("dashboard", "providers", "catalog"):
                window.show_page(page)
                if page == "providers":
                    window.profile_combo.setCurrentIndex(window.profile_combo.findData("deepseek"))
                if page == "catalog":
                    window.catalog_profile_combo.setCurrentIndex(window.catalog_profile_combo.findData("deepseek"))
                app.processEvents()
                window.grab().save(str(args.preview_dir / f"deepseek-{page}.png"))
                if page == "dashboard" and args.balance_demo:
                    window.resize(900, 620)
                    app.processEvents()
                    window.grab().save(str(args.preview_dir / "deepseek-dashboard-small.png"))
                    window.resize(1180, 850)
            wizard = SetupWizard(window)
            wizard.wizard_native_combo.setCurrentIndex(1)
            wizard._page_index = 1
            wizard._render_step()
            wizard.show()
            app.processEvents()
            wizard.grab().save(str(args.preview_dir / "deepseek-setup.png"))
            wizard.close()
            window.close()
            app.processEvents()
            print(f"Qt preview screenshots written to {args.preview_dir}")


if __name__ == "__main__":
    main()
