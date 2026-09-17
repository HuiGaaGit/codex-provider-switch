from __future__ import annotations

import json
import os
import re
import shutil
import threading
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import BACKUP_DIRECTORY_NAME, DEFAULT_CODEX_HOME
from .models import ConfigSnapshot, ProviderProfile


class ConfigError(RuntimeError):
    pass


TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+\s*=")
TABLE_RE = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")


def discover_codex_homes() -> list[Path]:
    candidates: list[Path] = []
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        candidates.append(Path(env_home).expanduser())
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / ".codex")
    candidates.extend((Path.home() / ".codex", DEFAULT_CODEX_HOME))
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.resolve(strict=False)).casefold()
        if normalized not in seen:
            seen.add(normalized)
            result.append(candidate.resolve(strict=False))
    result.sort(key=lambda path: (not (path / "config.toml").exists(), str(path)))
    return result


def _decode(raw: bytes) -> tuple[str, str]:
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        return raw.decode(encoding), encoding
    except UnicodeDecodeError as exc:
        raise ConfigError("config.toml 必须使用 UTF-8 编码。") from exc


def _toml_value(value: str | bool | int | float) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _first_table_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if TABLE_RE.match(line.rstrip("\r\n")) and not line.lstrip().startswith("#"):
            return index
    return len(lines)


def _set_top_level(text: str, key: str, value: str | bool | None) -> str:
    lines = text.splitlines(keepends=True)
    boundary = _first_table_index(lines)
    matcher = re.compile(rf"^(?P<indent>\s*)(?P<comment>#\s*)?{re.escape(key)}\s*=")
    matches = [index for index in range(boundary) if matcher.match(lines[index])]
    active = [index for index in matches if not lines[index].lstrip().startswith("#")]
    if value is None:
        for index in active:
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
            lines[index] = f"{indent}# {lines[index][len(indent):]}"
        return "".join(lines)
    rendered = f"{key} = {_toml_value(value)}"
    if active:
        index = active[0]
        lines[index] = rendered + (_line_ending(lines[index]) or "\n")
        for duplicate in active[1:]:
            lines[duplicate] = f"# {lines[duplicate]}"
    elif matches:
        index = matches[0]
        lines[index] = rendered + (_line_ending(lines[index]) or "\n")
    else:
        ending = "\r\n" if "\r\n" in text else "\n"
        insertion = rendered + ending
        if boundary and lines[boundary - 1].strip():
            insertion += ending
        lines.insert(boundary, insertion)
    return "".join(lines)


def _table_bounds(lines: list[str], table_name: str) -> tuple[int, int] | None:
    start = -1
    for index, line in enumerate(lines):
        match = TABLE_RE.match(line.rstrip("\r\n"))
        if match and not line.lstrip().startswith("#"):
            if start >= 0:
                return start, index
            if match.group(1).strip() == table_name:
                start = index
    return (start, len(lines)) if start >= 0 else None


def _upsert_provider_table(text: str, provider_key: str, values: dict[str, Any]) -> str:
    lines = text.splitlines(keepends=True)
    table_name = f"model_providers.{provider_key}"
    bounds = _table_bounds(lines, table_name)
    ending = "\r\n" if "\r\n" in text else "\n"
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append(ending)
        lines.append(f"[{table_name}]{ending}")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}{ending}")
        return "".join(lines)
    start, end = bounds
    for key, value in values.items():
        matcher = re.compile(rf"^\s*(?:#\s*)?{re.escape(key)}\s*=")
        found = None
        for index in range(start + 1, end):
            if matcher.match(lines[index]):
                found = index
                break
        rendered = f"{key} = {_toml_value(value)}{ending}"
        if found is None:
            lines.insert(end, rendered)
            end += 1
        else:
            lines[found] = rendered
    return "".join(lines)


def _remove_provider_table_keys(text: str, provider_key: str, keys: tuple[str, ...]) -> str:
    """Remove stale authentication/header fields from a managed provider table."""
    lines = text.splitlines(keepends=True)
    bounds = _table_bounds(lines, f"model_providers.{provider_key}")
    if bounds is None:
        return text
    start, end = bounds
    for key in keys:
        matcher = re.compile(rf"^\s*(?:#\s*)?{re.escape(key)}\s*=")
        for index in range(end - 1, start, -1):
            if matcher.match(lines[index]):
                del lines[index]
                end -= 1
    return "".join(lines)


class ConfigManager:
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home.expanduser().resolve(strict=False)
        self.config_path = self.codex_home / "config.toml"
        self.backup_directory = self.codex_home / BACKUP_DIRECTORY_NAME
        self._lock = threading.RLock()

    def ensure_config(self) -> None:
        if self.config_path.exists():
            return
        self.codex_home.mkdir(parents=True, exist_ok=True)
        initial = "# Codex configuration created by Codex Provider Switch\n"
        self.config_path.write_text(initial, encoding="utf-8", newline="\n")

    def read(self) -> tuple[str, str]:
        if not self.config_path.exists():
            raise ConfigError(f"未找到配置文件：{self.config_path}")
        try:
            return _decode(self.config_path.read_bytes())
        except OSError as exc:
            raise ConfigError(f"无法读取配置文件：{exc}") from exc

    def current_text(self) -> str:
        text, _ = self.read()
        return text

    def save_text(self, text: str) -> tuple[ConfigSnapshot, Path | None]:
        """Validate and save a user-edited config.toml with rollback support."""
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"配置 TOML 无效：{exc}") from exc
        providers = parsed.get("model_providers", {})
        if not isinstance(providers, dict):
            raise ConfigError("model_providers 必须是配置表。")
        self.ensure_config()
        with self._lock:
            original, encoding = self.read()
            if text == original:
                return self.snapshot(), None
            backup = self._create_backup()
            temporary = self.config_path.with_name("config.toml.provider-switch.tmp")
            try:
                temporary.write_text(text, encoding=encoding, newline="")
                os.replace(temporary, self.config_path)
                snapshot = self.snapshot()
            except Exception as exc:
                try:
                    shutil.copy2(backup, self.config_path)
                except OSError:
                    pass
                raise ConfigError(f"配置保存失败，已尝试恢复备份：{exc}") from exc
            finally:
                temporary.unlink(missing_ok=True)
            return snapshot, backup

    def snapshot(self) -> ConfigSnapshot:
        text, _ = self.read()
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config.toml 语法错误：{exc}") from exc
        providers = parsed.get("model_providers", {})
        return ConfigSnapshot(
            path=str(self.config_path),
            model_provider=str(parsed.get("model_provider", "")),
            model=str(parsed.get("model", "")),
            model_catalog_json=str(parsed.get("model_catalog_json", "")),
            providers=providers if isinstance(providers, dict) else {},
        )

    def render_profile(
        self,
        profile: ProviderProfile,
        api_key: str,
        preserve_provider_key: bool,
        stable_provider_key: str,
        catalog_path: Path | None = None,
    ) -> tuple[str, str]:
        original, _ = self.read()
        if profile.kind == "official":
            updated = _set_top_level(original, "model_provider", None)
            if profile.model:
                updated = _set_top_level(updated, "model", profile.model)
            updated = _set_top_level(updated, "model_catalog_json", None)
            return updated, "openai"
        if not profile.base_url:
            raise ConfigError(f"{profile.display_name} 尚未配置 API 地址。")
        if not profile.model:
            raise ConfigError(f"{profile.display_name} 尚未配置模型。")
        if not api_key:
            raise ConfigError(f"{profile.display_name} 尚未配置 API Key。")
        provider_key = stable_provider_key if preserve_provider_key else profile.provider_key
        updated = _set_top_level(original, "model_provider", provider_key)
        updated = _set_top_level(updated, "model", profile.model)
        if profile.kind == "glm":
            if catalog_path is None:
                raise ConfigError("GLM 切换缺少 models.json 路径。")
            updated = _set_top_level(updated, "model_reasoning_effort", "max")
            updated = _set_top_level(
                updated, "model_catalog_json", catalog_path.resolve(strict=False).as_posix()
            )
        else:
            updated = _set_top_level(updated, "model_catalog_json", None)
        provider_values = {
            "name": profile.display_name,
            "base_url": profile.base_url,
            "wire_api": "responses",
            "requires_openai_auth": profile.requires_openai_auth,
            "experimental_bearer_token": api_key,
        }
        updated = _upsert_provider_table(updated, provider_key, provider_values)
        # Older/manual configs may still ask Codex to read another env variable
        # even though this tool writes an explicit bearer token. Keep the auth
        # source unambiguous. GLM also must not inherit stale app-specific
        # actor headers from copied configs.
        updated = _remove_provider_table_keys(updated, provider_key, ("env_key",))
        if profile.kind == "glm":
            updated = _remove_provider_table_keys(updated, provider_key, ("http_headers",))
        self._validate_rendered(updated, provider_key)
        return updated, provider_key

    @staticmethod
    def _validate_rendered(text: str, expected_provider: str) -> None:
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"修改后的配置无效：{exc}") from exc
        if parsed.get("model_provider") != expected_provider:
            raise ConfigError("修改后的 model_provider 与目标不一致。")
        provider = parsed.get("model_providers", {}).get(expected_provider)
        if not isinstance(provider, dict):
            raise ConfigError(f"修改后的配置缺少 {expected_provider} 供应商段。")
        if provider.get("wire_api") != "responses":
            raise ConfigError("供应商 wire_api 必须为 responses。")

    def apply_profile(
        self,
        profile: ProviderProfile,
        api_key: str,
        preserve_provider_key: bool,
        stable_provider_key: str,
        catalog_path: Path | None = None,
    ) -> tuple[Path | None, str]:
        with self._lock:
            original, encoding = self.read()
            updated, provider_key = self.render_profile(
                profile, api_key, preserve_provider_key, stable_provider_key, catalog_path
            )
            if updated == original:
                return None, provider_key
            backup = self._create_backup()
            temporary = self.config_path.with_name("config.toml.provider-switch.tmp")
            try:
                temporary.write_text(updated, encoding=encoding, newline="")
                os.replace(temporary, self.config_path)
                self.snapshot()
            except Exception as exc:
                try:
                    shutil.copy2(backup, self.config_path)
                except OSError:
                    pass
                raise ConfigError(f"写入失败，已尝试恢复备份：{exc}") from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return backup, provider_key

    def _create_backup(self) -> Path:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.backup_directory / f"config-{stamp}.toml"
        shutil.copy2(self.config_path, target)
        return target
