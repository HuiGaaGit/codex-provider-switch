"""Codex Provider Switch v1.2.12 entry point."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from provider_switch import APP_NAME, APP_VERSION
from provider_switch.controller import ApplicationController
from provider_switch.models import AppSettings
from provider_switch.settings import SettingsStore


def build_demo_controller(root: Path) -> ApplicationController:
    codex_home = root / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        'model_provider = "cch_gz"\n'
        'model = "gpt-5.6-sol"\n\n'
        '[model_providers.cch_gz]\n'
        'name = "API1"\n'
        'base_url = "https://example.invalid/v1"\n'
        'wire_api = "responses"\n'
        'requires_openai_auth = true\n'
        'experimental_bearer_token = "demo-key"\n',
        encoding="utf-8",
    )
    store = SettingsStore(root / "app-data")
    settings = AppSettings(
        codex_home=str(codex_home),
        setup_complete=True,
        stable_provider_key="cch_gz",
        active_profile_id="relay1",
    )
    for profile_id in ("relay1", "relay2"):
        profile = settings.profiles[profile_id]
        profile.base_url = "https://example.invalid/v1"
        profile.model = "gpt-5.6-sol"
    store.save(settings, {"relay1": "demo-key", "relay2": "demo-key", "glm": "demo-key"})
    return ApplicationController(store)


def run_smoke_test() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-provider-switch-smoke-") as name:
        controller = build_demo_controller(Path(name))
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from provider_switch.qt_ui import run_qt_smoke_test

            return run_qt_smoke_test(controller)
        except ImportError as exc:
            Path(tempfile.gettempdir(), "codex-provider-switch-ui-fallback.log").write_text(
                f"PySide6 UI unavailable; using Tk compatibility UI. {exc!r}\n", encoding="utf-8"
            )
            from provider_switch.ui import ProviderSwitchApp

            app = ProviderSwitchApp(controller, start_monitor=False, show_wizard=False)
            app.withdraw()
            app.update_idletasks()
            if APP_VERSION not in app.title() or len(app.pages) != 6:
                app.destroy()
                raise RuntimeError("Tk UI smoke test failed")
            app.destroy()
    print(f"{APP_NAME} {APP_VERSION} smoke test passed")
    return 0


def run_tray_smoke_test() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-provider-switch-tray-smoke-") as name:
        controller = build_demo_controller(Path(name))
        from provider_switch.qt_ui import run_qt_tray_smoke_test

        return run_qt_tray_smoke_test(controller)


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--smoke-test", action="store_true", help="create and close an isolated UI")
    parser.add_argument(
        "--tray-smoke-test",
        action="store_true",
        help="verify the native system tray lifecycle with isolated data",
    )
    parser.add_argument("--demo", action="store_true", help="open the UI with isolated demo data")
    parser.add_argument("--tray", action="store_true", help="start minimized in the system tray")
    args = parser.parse_args()
    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if args.smoke_test:
        return run_smoke_test()
    if args.tray_smoke_test:
        return run_tray_smoke_test()
    if args.demo:
        temporary = tempfile.TemporaryDirectory(prefix="codex-provider-switch-demo-")
        controller = build_demo_controller(Path(temporary.name))
        try:
            from provider_switch.qt_ui import run_qt_application

            return run_qt_application(
                controller,
                start_monitor=False,
                show_wizard=False,
                start_hidden=args.tray,
                single_instance=False,
            )
        except ImportError as exc:
            Path(tempfile.gettempdir(), "codex-provider-switch-ui-fallback.log").write_text(
                f"PySide6 UI unavailable; using Tk compatibility UI. {exc!r}\n", encoding="utf-8"
            )
            from provider_switch.ui import ProviderSwitchApp

            app = ProviderSwitchApp(controller, start_monitor=False, show_wizard=False)
            app._demo_temporary = temporary  # Keep the isolated directory alive for the window lifetime.
            app.mainloop()
            return 0
    else:
        try:
            from provider_switch.qt_ui import run_qt_application

            return run_qt_application(start_hidden=args.tray)
        except ImportError as exc:
            Path(tempfile.gettempdir(), "codex-provider-switch-ui-fallback.log").write_text(
                f"PySide6 UI unavailable; using Tk compatibility UI. {exc!r}\n", encoding="utf-8"
            )
            from provider_switch.ui import ProviderSwitchApp

            app = ProviderSwitchApp()
            app.mainloop()
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
