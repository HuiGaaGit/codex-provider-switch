from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .auth_manager import AuthStatus, CodexAuthError, CodexAuthManager
from .catalog import ModelCatalogManager
from .codex_process import CodexProcessController
from .config_manager import ConfigManager, discover_codex_homes
from .constants import SWITCH_LOG_NAME
from .models import AppSettings, ConfigSnapshot, ProviderTelemetry, TokenUsage
from .monitoring import check_all, check_provider, scan_token_usage
from .settings import SettingsError, SettingsStore, validate_settings
from .thread_state import ThreadStateError, sync_thread_models
from .threadripper import find_threadripper, sync_history, threadripper_version


ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class OperationResult:
    profile_id: str
    provider_key: str
    backup_path: Path | None = None
    history_summary: str = ""
    restart_summary: str = ""
    thread_model_sync: int = 0
    warnings: list[str] = field(default_factory=list)


class ApplicationController:
    def __init__(self, store: SettingsStore | None = None) -> None:
        self.store = store or SettingsStore()
        self.settings, self.credentials = self._load_initial_state()
        self.process_controller = CodexProcessController()
        self._bind_home()

    def _load_initial_state(self) -> tuple[AppSettings, dict[str, str]]:
        if self.store.exists():
            return self.store.load()
        home = discover_codex_homes()[0]
        manager = ConfigManager(home)
        if manager.config_path.exists():
            snapshot = manager.snapshot()
        else:
            snapshot = ConfigSnapshot(path=str(manager.config_path))
        return self.store.bootstrap(snapshot, home / "codex_provider_switch_labels.json")

    def _bind_home(self) -> None:
        home = Path(self.settings.codex_home).expanduser()
        self.config = ConfigManager(home)
        self.catalog = ModelCatalogManager(home)
        self.auth = CodexAuthManager(home)

    @property
    def codex_home(self) -> Path:
        return self.config.codex_home

    def set_codex_home(self, path: Path, import_existing: bool = False) -> ConfigSnapshot:
        manager = ConfigManager(path)
        manager.ensure_config()
        snapshot = manager.snapshot()
        self.settings.codex_home = str(manager.codex_home)
        self._bind_home()
        if import_existing:
            imported, legacy_credentials = self.store.bootstrap(
                snapshot, manager.codex_home / "codex_provider_switch_labels.json"
            )
            imported.setup_complete = self.settings.setup_complete
            self.settings = imported
            self.credentials.update(legacy_credentials)
            self._bind_home()
        return snapshot

    def preview_codex_home(self, path: Path) -> ConfigSnapshot:
        manager = ConfigManager(path)
        if manager.config_path.exists():
            return manager.snapshot()
        return ConfigSnapshot(path=str(manager.config_path))

    @staticmethod
    def preview_auth_status(path: Path) -> AuthStatus:
        return CodexAuthManager(path).status()

    def change_codex_home(self, path: Path, *, create: bool = False) -> ConfigSnapshot:
        self.settings.codex_home = str(path.expanduser().resolve(strict=False))
        self._bind_home()
        if create:
            self.config.ensure_config()
        return self.current_snapshot() if self.config.config_path.exists() else ConfigSnapshot(
            path=str(self.config.config_path)
        )

    def save(self) -> None:
        validate_settings(self.settings)
        self.store.save(self.settings, self.credentials)

    def current_snapshot(self) -> ConfigSnapshot:
        self.config.ensure_config()
        return self.config.snapshot()

    def save_config_text(self, text: str) -> ConfigSnapshot:
        """Save manual config edits and keep local provider profiles compatible."""
        snapshot, _ = self.config.save_text(text)
        self._sync_profiles_from_snapshot(snapshot)
        self.save()
        return snapshot

    def _sync_profiles_from_snapshot(self, snapshot: ConfigSnapshot) -> None:
        if not snapshot.model_provider:
            self.settings.active_profile_id = "openai"
            if snapshot.model:
                self.settings.profiles["openai"].model = snapshot.model
            return
        provider = snapshot.providers.get(snapshot.model_provider, {})
        if not isinstance(provider, dict):
            return
        if self.settings.preserve_provider_key:
            self.settings.stable_provider_key = snapshot.model_provider
        active = self.detect_active_profile()
        if active not in {"relay1", "relay2", "glm"}:
            active = self.settings.active_profile_id if self.settings.active_profile_id in {
                "relay1", "relay2", "glm"
            } else "relay1"
        profile = self.settings.profiles[active]
        name = str(provider.get("name", "")).strip()
        base_url = str(provider.get("base_url", "")).strip()
        model = snapshot.model.strip()
        if name:
            profile.display_name = name[:40]
        if base_url:
            profile.base_url = base_url.rstrip("/")
        if model:
            profile.model = model[:120]
        profile.wire_api = str(provider.get("wire_api", profile.wire_api) or "responses")
        profile.requires_openai_auth = bool(provider.get("requires_openai_auth", False))
        token = provider.get("experimental_bearer_token", "")
        if isinstance(token, str) and token.strip():
            self.credentials[active] = token.strip()
        self.settings.active_profile_id = active

    def auth_status(self) -> AuthStatus:
        return self.auth.status()

    def openai_available(self, status: AuthStatus | None = None) -> bool:
        current = status or self.auth_status()
        return self.settings.retain_official_auth and current.official_account_logged_in

    def profile_ready(self, profile_id: str, status: AuthStatus | None = None) -> bool:
        profile = self.settings.profiles.get(profile_id)
        if profile is None or not profile.enabled:
            return False
        if profile.kind == "official":
            return self.openai_available(status)
        return bool(
            profile.base_url
            and profile.model
            and self.credentials.get(profile_id, "").strip()
        )

    def relay_fallback(self) -> str:
        preferred = self.settings.last_relay_profile_id
        order = (preferred, "relay2" if preferred == "relay1" else "relay1")
        for profile_id in order:
            profile = self.settings.profiles[profile_id]
            if profile.base_url and profile.model and self.credentials.get(profile_id):
                return profile_id
        raise SettingsError("请先完整配置 API1 或 API2，再管理官方登录态。")

    def _set_auth_retention(self, retain: bool) -> None:
        self.settings.retain_official_auth = retain
        for profile_id in ("relay1", "relay2"):
            self.settings.profiles[profile_id].requires_openai_auth = retain

    def deploy_profile(
        self,
        profile_id: str,
        retain_official_auth: bool,
        progress: ProgressCallback | None = None,
    ) -> OperationResult:
        self._set_auth_retention(retain_official_auth)
        if profile_id == "openai" and not self.openai_available():
            raise CodexAuthError("OpenAI 直连需要先完成官方账号登录。")
        result = self.switch_profile(profile_id, progress)
        if not retain_official_auth:
            if progress:
                progress("正在清除 OpenAI 官方登录态")
            self.auth.logout()
            self.save()
        return result

    def restore_official_login(
        self, progress: ProgressCallback | None = None
    ) -> tuple[OperationResult | None, AuthStatus]:
        switched: OperationResult | None = None
        active = self.detect_active_profile()
        fallback = self.relay_fallback() if active == "glm" else None
        status = self.auth.status()
        if not status.official_account_logged_in:
            if progress:
                progress("请在新窗口和浏览器中完成 OpenAI 登录")
            status = self.auth.login_interactive()
        self._set_auth_retention(True)
        if fallback is not None:
            if progress:
                progress(f"正在从 GLM 切回 {self.settings.profiles[fallback].display_name}")
            switched = self.switch_profile(fallback, progress)
        self.save()
        return switched, status

    def clear_official_login(
        self, progress: ProgressCallback | None = None
    ) -> tuple[OperationResult | None, AuthStatus]:
        active = self.detect_active_profile()
        switched: OperationResult | None = None
        self._set_auth_retention(False)
        if active == "openai":
            fallback = self.relay_fallback()
            if progress:
                progress(f"正在切换到 {self.settings.profiles[fallback].display_name}")
            switched = self.switch_profile(fallback, progress)
        elif active in {"relay1", "relay2"}:
            if progress:
                progress("正在将当前中转改为独立密钥认证")
            switched = self.switch_profile(active, progress)
        if progress:
            progress("正在清除 OpenAI 官方登录态")
        status = self.auth.logout()
        self.save()
        return switched, status

    def detect_active_profile(self) -> str:
        snapshot = self.current_snapshot()
        if not snapshot.model_provider:
            return "openai"
        provider = snapshot.providers.get(snapshot.model_provider, {})
        base_url = str(provider.get("base_url", "")).rstrip("/").casefold()
        if snapshot.model.casefold().startswith("glm-"):
            return "glm"
        for profile_id in ("relay1", "relay2", "glm"):
            profile = self.settings.profiles[profile_id]
            if profile.base_url.rstrip("/").casefold() == base_url and base_url:
                return profile_id
        return self.settings.active_profile_id or ""

    def switch_profile(
        self, profile_id: str, progress: ProgressCallback | None = None
    ) -> OperationResult:
        if profile_id not in self.settings.profiles:
            raise ValueError(f"未知供应商：{profile_id}")
        validate_settings(self.settings)
        profile = self.settings.profiles[profile_id]
        if profile_id == "openai" and not self.openai_available():
            raise CodexAuthError("OpenAI 直连当前不可用，请先在“部署”中登录官方账号。")
        if profile_id != "openai" and not self.profile_ready(profile_id):
            raise SettingsError(f"{profile.display_name} 缺少 API 地址、模型或 Key。")
        if profile_id in {"relay1", "relay2"}:
            profile.requires_openai_auth = self.settings.retain_official_auth
        api_key = self.credentials.get(profile_id, "")
        catalog_path: Path | None = None
        if profile.kind == "glm":
            if progress:
                progress("正在校验 GLM 模型目录")
            self.catalog.ensure_model_profile(profile.model)
            catalog_path = self.catalog.target_path

        result = OperationResult(profile_id=profile_id, provider_key="")
        if progress:
            progress("正在备份并写入 config.toml")
        backup, provider_key = self.config.apply_profile(
            profile,
            api_key,
            self.settings.preserve_provider_key,
            self.settings.stable_provider_key,
            catalog_path,
        )
        result.provider_key = provider_key
        result.backup_path = backup
        self.settings.active_profile_id = profile_id
        if profile_id in {"relay1", "relay2"}:
            self.settings.last_relay_profile_id = profile_id
        self.save()
        self._record_switch(profile_id, provider_key)
        if self.settings.auto_sync_history:
            command = find_threadripper()
            if command is None:
                result.warnings.append("Threadripper 未安装，已保留 provider 注册但未执行会话同步。")
            else:
                if progress:
                    progress("正在同步历史会话")
                try:
                    result.history_summary = sync_history(self.codex_home, command)
                except Exception as exc:
                    result.warnings.append(str(exc))

        if self.settings.auto_restart:
            try:
                restart_target = self.process_controller.locate_running()
                if restart_target is None:
                    return result
                if progress:
                    progress("配置已保存，正在安全重启 Codex")
                self.process_controller.stop()
                try:
                    if progress:
                        progress("正在同步已打开会话的模型")
                    sync_results = sync_thread_models(
                        self.codex_home,
                        provider_key,
                        profile.model,
                    )
                    result.thread_model_sync = sum(item.updated_threads for item in sync_results)
                except (ThreadStateError, OSError) as exc:
                    result.warnings.append(f"会话模型同步失败，新会话仍会使用目标模型：{exc}")
                result.restart_summary = self.process_controller.start(restart_target)
            except Exception as exc:
                result.warnings.append(f"配置已保存，但 Codex 自动重启失败：{exc}")
        return result

    def sync_history(self) -> str:
        return sync_history(self.codex_home)

    def threadripper_status(self) -> tuple[Path | None, str]:
        command = find_threadripper()
        return command, threadripper_version(command) if command else ""

    def telemetry(self) -> dict[str, ProviderTelemetry]:
        return check_all(
            self.settings.profiles,
            self.credentials,
            self.codex_home,
            self.settings.retain_official_auth,
        )

    def check_active(self) -> dict[str, ProviderTelemetry]:
        """Only health-check the currently active provider (CCH-style on-demand probe)."""
        active_id = self.settings.active_profile_id or "openai"
        profile = self.settings.profiles.get(active_id)
        if profile is None:
            return {}
        auth_status: AuthStatus | None = None
        if profile.kind == "official":
            auth_status = self.auth_status()
        try:
            item = check_provider(
                profile,
                self.credentials.get(profile.profile_id, ""),
                codex_home=self.codex_home,
                retain_official_auth=self.settings.retain_official_auth,
                auth_status=auth_status,
            )
        except Exception:
            return {}
        return {profile.profile_id: item}

    def token_usage(self) -> dict[str, TokenUsage]:
        return scan_token_usage(self.codex_home, self.settings.usage_lookback_days)

    def _record_switch(self, profile_id: str, provider_key: str) -> None:
        self.store.data_root.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "profile_id": profile_id,
            "provider_key": provider_key,
            "config_path": str(self.config.config_path),
        }
        with (self.store.data_root / SWITCH_LOG_NAME).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
