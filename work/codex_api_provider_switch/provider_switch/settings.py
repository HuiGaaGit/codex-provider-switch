from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .constants import (
    CREDENTIALS_FILE_NAME,
    DEFAULT_CODEX_HOME,
    LEGACY_SETTINGS_NAME,
    SETTINGS_DIRECTORY_NAME,
    SETTINGS_FILE_NAME,
)
from .models import AppSettings, ConfigSnapshot


PROVIDER_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SettingsError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _protect_windows(data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Codex Provider Switch",
        None,
        None,
        None,
        0x01,
        ctypes.byref(result),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _unprotect_windows(data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    if isinstance(content, bytes):
        temporary.write_bytes(content)
    else:
        temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


class CredentialVault:
    """Store API keys with user-scoped DPAPI protection on Windows."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, str]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SettingsError(f"无法读取本地密钥：{exc}") from exc
        try:
            raw = base64.b64decode(payload["data"], validate=True)
            if payload.get("format") == "dpapi-v1":
                raw = _unprotect_windows(raw)
            elif payload.get("format") != "plain-v1":
                raise ValueError("unsupported credential format")
            decoded = json.loads(raw.decode("utf-8"))
        except (KeyError, ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SettingsError("本地密钥文件损坏或属于其他 Windows 用户。") from exc
        return {
            str(key): str(value)
            for key, value in decoded.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def save(self, credentials: dict[str, str]) -> None:
        clean = {
            str(key): str(value).strip()[:2000]
            for key, value in credentials.items()
            if str(value).strip()
        }
        raw = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        source_hash = hashlib.sha256(raw).hexdigest()
        if sys.platform == "win32":
            encoded = _protect_windows(raw)
            format_name = "dpapi-v1"
        else:
            encoded = raw
            format_name = "plain-v1"
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = {}
        if (
            existing.get("format") == format_name
            and existing.get("source_sha256") == source_hash
            and isinstance(existing.get("data"), str)
        ):
            return
        payload = json.dumps(
            {
                "format": format_name,
                "source_sha256": source_hash,
                "data": base64.b64encode(encoded).decode("ascii"),
            },
            ensure_ascii=True,
            indent=2,
        )
        _atomic_write(self.path, payload + "\n")


class SettingsStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root or DEFAULT_CODEX_HOME / SETTINGS_DIRECTORY_NAME
        self.settings_path = self.data_root / SETTINGS_FILE_NAME
        self.vault = CredentialVault(self.data_root / CREDENTIALS_FILE_NAME)

    def exists(self) -> bool:
        return self.settings_path.exists()

    def load(self) -> tuple[AppSettings, dict[str, str]]:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return AppSettings(codex_home=str(DEFAULT_CODEX_HOME)), {}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SettingsError(f"设置文件无法读取：{exc}") from exc
        if not isinstance(payload, dict):
            raise SettingsError("设置文件根节点必须是 JSON 对象。")
        settings = AppSettings.from_dict(payload)
        if not settings.codex_home:
            settings.codex_home = str(DEFAULT_CODEX_HOME)
        return settings, self.vault.load()

    def save(self, settings: AppSettings, credentials: dict[str, str]) -> None:
        validate_settings(settings)
        payload = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n"
        _atomic_write(self.settings_path, payload)
        self.vault.save(credentials)

    def bootstrap(
        self, snapshot: ConfigSnapshot, legacy_path: Path | None = None
    ) -> tuple[AppSettings, dict[str, str]]:
        settings = AppSettings(codex_home=str(Path(snapshot.path).parent))
        if snapshot.model_provider:
            settings.stable_provider_key = snapshot.model_provider
        current_provider = snapshot.providers.get(snapshot.model_provider, {})
        base_url = str(current_provider.get("base_url", ""))
        current_name = str(current_provider.get("name", "")).strip()
        current_model = snapshot.model
        retain_official_auth = bool(current_provider.get("requires_openai_auth", True))
        settings.retain_official_auth = retain_official_auth
        settings.profiles["openai"].model = current_model
        for profile_id in ("relay1", "relay2"):
            profile = settings.profiles[profile_id]
            profile.base_url = base_url
            profile.model = current_model
            profile.requires_openai_auth = retain_official_auth
        if current_name:
            settings.profiles["relay1"].display_name = current_name[:40]
        credentials: dict[str, str] = {}
        token = current_provider.get("experimental_bearer_token", "")
        if isinstance(token, str) and token:
            credentials["relay1"] = token
        legacy = self._read_legacy(legacy_path)
        if legacy:
            settings.profiles["relay1"].display_name = str(
                legacy.get("enable") or settings.profiles["relay1"].display_name
            )[:40]
            settings.profiles["relay2"].display_name = str(
                legacy.get("disable") or settings.profiles["relay2"].display_name
            )[:40]
            for old_key, new_key in (("api1", "relay1"), ("api2", "relay2")):
                if isinstance(legacy.get(old_key), str) and legacy[old_key].strip():
                    credentials[new_key] = legacy[old_key].strip()
        return settings, credentials

    @staticmethod
    def _read_legacy(path: Path | None) -> dict[str, Any]:
        source = path or DEFAULT_CODEX_HOME / LEGACY_SETTINGS_NAME
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def validate_settings(settings: AppSettings) -> None:
    home = Path(settings.codex_home).expanduser()
    if not settings.codex_home.strip():
        raise SettingsError("Codex Home 不能为空。")
    if not PROVIDER_KEY_RE.fullmatch(settings.stable_provider_key):
        raise SettingsError("稳定 provider 标签只能包含字母、数字、下划线和连字符。")
    settings.codex_home = str(home)
    settings.monitor_interval_seconds = max(10, min(3600, int(settings.monitor_interval_seconds)))
    settings.usage_lookback_days = max(1, min(3650, int(settings.usage_lookback_days)))
    if settings.last_relay_profile_id not in {"relay1", "relay2"}:
        settings.last_relay_profile_id = "relay1"
    for profile_id in ("relay1", "relay2"):
        settings.profiles[profile_id].requires_openai_auth = settings.retain_official_auth
    for profile in settings.profiles.values():
        profile.display_name = profile.display_name.strip()[:40]
        profile.provider_key = profile.provider_key.strip()
        profile.base_url = profile.base_url.strip().rstrip("/")
        profile.model = profile.model.strip()[:120]
        profile.quota_url = profile.quota_url.strip()
        profile.quota_organization_id = "".join(
            profile.quota_organization_id.split()
        )[:160]
        project_id = profile.quota_project_id.strip()
        project_id = re.sub(r"^(proj)[ \t]+", r"\1_", project_id, flags=re.IGNORECASE)
        profile.quota_project_id = "".join(project_id.split())[:160]
        if not profile.display_name:
            raise SettingsError(f"{profile.profile_id} 的显示名称不能为空。")
        if profile.kind != "official" and not PROVIDER_KEY_RE.fullmatch(profile.provider_key):
            raise SettingsError(f"{profile.display_name} 的 provider 标签格式无效。")
        for label, value in (("API 地址", profile.base_url), ("额度地址", profile.quota_url)):
            if value:
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise SettingsError(f"{profile.display_name} 的{label}无效。")
