from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .auth_manager import AuthStatus, CodexAuthError, CodexAuthManager
from .catalog import ModelCatalogManager
from .codex_process import CodexProcessController
from .config_manager import ConfigError, ConfigManager, discover_codex_homes
from .constants import SWITCH_LOG_NAME
from .models import AppSettings, ConfigSnapshot, HealthResult, ProviderTelemetry, TokenUsage
from .monitoring import check_provider, scan_token_usage, scan_token_usage_seconds
from .monitor_history import HealthRecord, MonitorHistory
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
        if self.config.config_path.exists():
            # Older bootstrap versions copied AP1's provider auth flag into
            # the global official-login policy. Repair that one-way migration
            # before Codex reads the config, while keeping a normal backup.
            try:
                self.config.normalize_active_provider_auth()
            except (ConfigError, OSError):
                # A malformed or locked config is reported by the normal
                # preview/switch path; startup must remain usable for repair.
                pass
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
        profile.requires_openai_auth = (
            False
            if ConfigManager.is_aqyimin_url(base_url)
            else bool(provider.get("requires_openai_auth", False))
        )
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

    def quota_ready(self, profile_id: str) -> bool:
        """Return whether a provider has enough data for a quota-only request.

        A quota lookup does not need a model selection. Keeping this separate
        from ``profile_ready`` lets an unused provider expose its quota while
        it is still being configured for switching.
        """
        profile = self.settings.profiles.get(profile_id)
        if profile is None or not profile.enabled or profile.kind == "official":
            return False
        return bool(
            self.credentials.get(profile_id, "").strip()
            and (profile.quota_url.strip() or profile.base_url.strip())
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
            profile = self.settings.profiles[profile_id]
            # aqyimin.chat explicitly uses API-key-only auth. Retaining the
            # official OpenAI session must not rewrite that provider contract.
            profile.requires_openai_auth = (
                False if ConfigManager.is_aqyimin_url(profile.base_url.strip()) else retain
            )

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
        provider_key = snapshot.model_provider.strip()
        if not provider_key or provider_key.casefold() in {"openai", "official"}:
            return "openai"
        provider = snapshot.providers.get(provider_key, {})
        if not isinstance(provider, dict):
            provider = {}
        base_url = str(provider.get("base_url", "")).rstrip("/").casefold()
        model = snapshot.model.casefold()
        provider_key_cf = provider_key.casefold()
        provider_name = str(provider.get("name", "")).casefold()
        # A manually edited config may use a custom provider key. Prefer an
        # exact configured key match before falling back to endpoint matching.
        for profile_id in ("relay1", "relay2", "glm"):
            profile = self.settings.profiles[profile_id]
            if profile.provider_key and provider_key_cf == profile.provider_key.casefold():
                return profile_id
            if profile.base_url.rstrip("/").casefold() == base_url and base_url:
                return profile_id
        # GLM can be manually renamed and may use a stable provider label, so
        # identify it from the endpoint/key/name as a fallback. Endpoint
        # matches above win when a relay serves a GLM-named model.
        if (
            model.startswith("glm-")
            or "bigmodel.cn" in base_url
            or provider_key_cf in {"glm", "zai"}
            or "glm" in provider_name
            or "智谱" in provider_name
        ):
            return "glm"
        # API1/API2 are occasionally configured with the same endpoint. In
        # that ambiguous case the cached profile is only used if it still
        # describes the actual config; it never overrides a distinct config.
        cached = self.settings.active_profile_id
        if cached in {"relay1", "relay2", "glm"}:
            cached_profile = self.settings.profiles.get(cached)
            if cached_profile is not None and (
                cached_profile.provider_key.casefold() == provider_key_cf
                or cached_profile.base_url.rstrip("/").casefold() == base_url
            ):
                return cached
        return ""

    def _managed_provider_keys(self, exclude: set[str]) -> list[str]:
        keys: set[str] = set()
        for profile in self.settings.profiles.values():
            if profile.provider_key:
                keys.add(profile.provider_key)
        if self.settings.stable_provider_key:
            keys.add(self.settings.stable_provider_key)
        return sorted(keys - exclude)

    def _target_provider_key(self, profile: ProviderProfile) -> str:
        if profile.kind == "official":
            return "openai"
        if self.settings.preserve_provider_key and self.settings.stable_provider_key:
            return self.settings.stable_provider_key
        return profile.provider_key

    def switch_profile(
        self,
        profile_id: str,
        progress: ProgressCallback | None = None,
        disconnect_others: bool = True,
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
            profile.requires_openai_auth = (
                False
                if ConfigManager.is_aqyimin_url(profile.base_url.strip())
                else self.settings.retain_official_auth
            )
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
        target_key = self._target_provider_key(profile)
        if disconnect_others:
            exclude = set() if profile.kind == "official" else {target_key}
            clear_keys = self._managed_provider_keys(exclude)
        else:
            clear_keys = []
        backup, provider_key = self.config.apply_profile(
            profile,
            api_key,
            self.settings.preserve_provider_key,
            self.settings.stable_provider_key,
            catalog_path,
            clear_keys,
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
                        provider_keys=set(self._managed_provider_keys(set())) | {provider_key, "openai"},
                    )
                    result.thread_model_sync = sum(item.updated_threads for item in sync_results)
                except (ThreadStateError, OSError) as exc:
                    result.warnings.append(f"会话模型同步失败，新会话仍会使用目标模型：{exc}")
                result.restart_summary = self.process_controller.start(restart_target)
            except Exception as exc:
                result.warnings.append(f"配置已保存，但 Codex 自动重启失败：{exc}")
        return result

    def disconnect_other_providers(self, progress: ProgressCallback | None = None) -> OperationResult:
        """Drop live bearer tokens from every provider table except the active one."""
        active = self.detect_active_profile()
        if not active:
            result = OperationResult(profile_id="", provider_key="")
            result.warnings.append("无法识别当前 config.toml 的供应商，未执行断开操作。请先在配置预览中校验。")
            return result
        profile = self.settings.profiles.get(active)
        active_key = self._target_provider_key(profile) if profile is not None and profile.kind != "official" else None
        keys = self._managed_provider_keys({active_key} if active_key else set())
        result = OperationResult(profile_id=active, provider_key=active_key or "openai")
        if not keys:
            result.warnings.append("没有其他已配置的 API 供应商需要断开。")
            return result
        if progress:
            progress("正在断开其他 API 供应商的连接")
        backup, cleared = self.config.clear_provider_keys(keys)
        result.backup_path = backup
        result.warnings.append(f"已断开：{ '、'.join(cleared) if cleared else '无（原本就没有活跃连接）' }")
        if self.settings.auto_restart:
            try:
                restart_target = self.process_controller.locate_running()
                if restart_target is not None:
                    if progress:
                        progress("正在安全重启 Codex 以生效断开")
                    self.process_controller.stop()
                    active_profile = self.settings.profiles.get(active)
                    if active_profile is not None and active_profile.kind != "official":
                        try:
                            sync_thread_models(self.codex_home, active_key or "", active_profile.model)
                        except (ThreadStateError, OSError):
                            pass
                    result.restart_summary = self.process_controller.start(restart_target)
            except Exception as exc:
                result.warnings.append(f"自动重启失败：{exc}")
        return result

    def sync_history(self) -> str:
        return sync_history(self.codex_home)

    def threadripper_status(self) -> tuple[Path | None, str]:
        command = find_threadripper()
        return command, threadripper_version(command) if command else ""

    def telemetry(self) -> dict[str, ProviderTelemetry]:
        """Probe only the active provider; callers should not fan out API checks."""
        return self.check_active()

    def request_health(self, max_age_seconds: float = 3600.0) -> dict[str, ProviderTelemetry]:
        """Build health telemetry from Codex request outcomes without probing a provider.

        The desktop UI uses this path for its health dashboard so refreshing the
        application cannot create synthetic requests or alter provider state.
        """
        try:
            active_id = self.detect_active_profile() or "unknown"
        except (OSError, ValueError):
            active_id = self.settings.active_profile_id or "unknown"
        records = self.request_records(max_age_seconds)
        latest: dict[str, Any] = {}
        for record in records:
            profile_id = self._request_record_profile_id(record.profile_id, active_id)
            if profile_id:
                latest[profile_id] = record
        result: dict[str, ProviderTelemetry] = {}
        messages = {
            "healthy": "最近一次 Codex 请求成功",
            "warning": "最近一次 Codex 请求已中止",
            "offline": "最近一次 Codex 请求失败",
        }
        for profile_id, record in latest.items():
            if active_id in self.settings.profiles and profile_id != active_id:
                continue
            try:
                checked_at = datetime.fromtimestamp(record.timestamp).strftime("%H:%M:%S")
            except (OSError, OverflowError, ValueError):
                checked_at = ""
            result[profile_id] = ProviderTelemetry(
                profile_id,
                HealthResult(
                    profile_id,
                    record.state,
                    messages.get(record.state, "最近一次 Codex 请求已记录"),
                    record.latency_ms,
                    checked_at,
                ),
                quota_message="额度请使用卡片上的“查额度”单独查询",
            )
        return result

    def request_records(self, max_age_seconds: float = 3600.0) -> list[HealthRecord]:
        """Read and normalize Codex request outcomes for the monitoring UI."""
        try:
            active_id = self.detect_active_profile() or "unknown"
            config_mtime = (
                self.config.config_path.stat().st_mtime
                if self.config.config_path.exists()
                else None
            )
        except (OSError, ValueError):
            active_id = self.settings.active_profile_id or "unknown"
            config_mtime = None
        raw_records = MonitorHistory.load_codex_request_records(
            self.codex_home,
            max(60.0, float(max_age_seconds)),
            current_profile_id=active_id,
            config_mtime=config_mtime,
        )
        normalized: list[HealthRecord] = []
        for record in raw_records:
            profile_id = self._request_record_profile_id(record.profile_id, active_id)
            if not profile_id:
                continue
            normalized.append(
                HealthRecord(record.timestamp, profile_id, record.state, record.latency_ms)
            )
        return normalized

    def _request_record_profile_id(self, raw_id: str, active_id: str) -> str:
        value = str(raw_id or "").strip()
        if value in self.settings.profiles:
            return value
        for profile_id, profile in self.settings.profiles.items():
            if value and value.casefold() == profile.provider_key.casefold():
                return active_id if profile.provider_key == self.settings.stable_provider_key else profile_id
        if value and value.casefold() == self.settings.stable_provider_key.casefold():
            return active_id if active_id in self.settings.profiles else ""
        return ""

    def check_active(self) -> dict[str, ProviderTelemetry]:
        """Only health-check the currently active provider (CCH-style on-demand probe)."""
        try:
            active_id = self.detect_active_profile()
        except Exception:
            active_id = self.settings.active_profile_id or "openai"
        if not active_id:
            health = HealthResult("unknown", "offline", "无法识别 config.toml 中的当前供应商")
            return {"unknown": ProviderTelemetry("unknown", health, quota_message="未执行额度查询")}
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
        except Exception as exc:
            health = ProviderTelemetry(
                profile.profile_id,
                HealthResult(profile.profile_id, "offline", f"监控异常：{exc}"),
                quota_message="查询失败",
            )
            return {profile.profile_id: health}
        return {profile.profile_id: item}

    def query_quota(self, profile_id: str) -> ProviderTelemetry:
        """Query a provider's quota without switching or probing its health."""
        profile = self.settings.profiles.get(profile_id)
        if profile is None:
            raise ValueError("供应商不存在")
        if profile.kind == "official":
            raise ValueError("OpenAI 官方订阅额度由 Codex 显示")
        if not self.quota_ready(profile_id):
            raise SettingsError(f"{profile.display_name} 缺少额度地址或 API Key。")
        return check_provider(
            profile,
            self.credentials.get(profile_id, ""),
            codex_home=self.codex_home,
            retain_official_auth=self.settings.retain_official_auth,
            probe_health=False,
        )

    def token_usage(self) -> dict[str, TokenUsage]:
        switch_log = self.store.data_root / SWITCH_LOG_NAME
        active = self.detect_active_profile()
        try:
            config_mtime = self.config.config_path.stat().st_mtime
        except OSError:
            config_mtime = None
        return scan_token_usage_seconds(
            self.codex_home,
            self.settings.usage_lookback_days * 86400,
            switch_log,
            current_profile_id=active,
            config_mtime=config_mtime,
        )

    def token_usage_seconds(self, lookback_seconds: float) -> dict[str, TokenUsage]:
        switch_log = self.store.data_root / SWITCH_LOG_NAME
        active = self.detect_active_profile()
        try:
            config_mtime = self.config.config_path.stat().st_mtime
        except OSError:
            config_mtime = None
        return scan_token_usage_seconds(
            self.codex_home,
            lookback_seconds,
            switch_log,
            current_profile_id=active,
            config_mtime=config_mtime,
        )

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
