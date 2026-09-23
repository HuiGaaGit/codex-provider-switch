from __future__ import annotations

import json
import os
import re
import shutil
import threading
import tomllib
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import BACKUP_DIRECTORY_NAME, DEFAULT_CODEX_HOME, MODEL_CATALOG_NAME
from .models import ConfigSnapshot, ProviderProfile


class ConfigError(RuntimeError):
    pass


TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+\s*=")
TABLE_RE = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")

# aqyimin.chat uses this non-secret header to opt into its image extension.
# Keep the allow-list narrow so a provider's bearer/API credentials can never
# be copied into the compatibility snapshot.
AQYIMIN_IMAGE_HEADERS = {"x-openai-actor-authorization"}
AQYIMIN_SAFE_IMAGE_HEADER_VALUES = {"local-image-extension"}


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



_COMMON_TOML_STRING_KEYS = {
    "model_provider",
    "model",
    "model_reasoning_effort",
    "plan_mode_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
    "approvals_reviewer",
    "preferred_auth_method",
    "sandbox_mode",
    "approval_policy",
    "service_tier",
    "name",
    "base_url",
    "wire_api",
    "experimental_bearer_token",
    "env_key",
    "model_catalog_json",
    "trust_level",
    "command",
    "source",
    "CODEX_HOME",
    "NODE_REPL_NODE_PATH",
    "NODE_REPL_NODE_MODULE_DIRS",
    "NODE_REPL_TRUSTED_CODE_PATHS",
}
_TOML_BASIC_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_TOML_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?P<key>[A-Za-z0-9_-]+)\s*=\s*)"
    r"(?P<value>[^#\r\n]*?)(?P<comment>\s+#.*)?(?P<newline>\r?\n)?$"
)
_TOML_ERROR_LOCATION_RE = re.compile(r"at line (?P<line>\d+), column (?P<column>\d+)")
_TOML_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?P<key>experimental_bearer_token|api[_-]?key|authorization|token|secret|password)"
    r"(\s*=\s*)(?P<value>\".*?\"|'[^']*'|[^,}\s]+)"
)


def _repair_basic_string_body(body: str) -> str:
    """Escape Windows backslashes while preserving valid TOML escapes."""
    path_like = bool(re.search(r"(?:[A-Za-z]:\\|\\\\\?|\\Users\\)", body))
    result: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        if index + 1 >= len(body):
            result.append(r"\\")
            index += 1
            continue
        next_char = body[index + 1]
        if path_like:
            if next_char == "\\":
                result.append(r"\\\\")
                index += 2
            else:
                result.append(r"\\")
                index += 1
            continue
        if next_char in "btnfr\\\"":
            result.extend(("\\", next_char))
            index += 2
            continue
        if next_char == "u" and re.fullmatch(r"[0-9A-Fa-f]{4}", body[index + 2 : index + 6]):
            result.extend(("\\", body[index + 1 : index + 6]))
            index += 6
            continue
        if next_char == "U" and re.fullmatch(r"[0-9A-Fa-f]{8}", body[index + 2 : index + 10]):
            result.extend(("\\", body[index + 1 : index + 10]))
            index += 10
            continue
        result.append(r"\\")
        index += 1
    return "".join(result)


def _repair_common_toml(text: str) -> str:
    """Repair only unambiguous string formatting mistakes common on Windows."""
    repaired_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        line = _TOML_BASIC_STRING_RE.sub(
            lambda match: '"' + _repair_basic_string_body(match.group(0)[1:-1]) + '"',
            line,
        )
        match = _TOML_ASSIGNMENT_RE.match(line)
        if match:
            key = match.group("key")
            value = match.group("value").strip()
            if (
                key in _COMMON_TOML_STRING_KEYS
                and value
                and value not in {"true", "false"}
                and not value.startswith(("\"", "'", "[", "{"))
                and not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value)
            ):
                line = (
                    match.group("prefix")
                    + _toml_value(value)
                    + (match.group("comment") or "")
                    + (match.group("newline") or "")
                )
        repaired_lines.append(line)
    return "".join(repaired_lines)


def _redact_toml_line(line: str) -> str:
    return _TOML_SENSITIVE_VALUE_RE.sub(
        lambda match: f"{match.group('key')} = <已隐藏>", line
    )


def format_toml_error(text: str, error: tomllib.TOMLDecodeError) -> str:
    """Make parser errors actionable without exposing bearer/API credentials."""
    message = str(error)
    location = _TOML_ERROR_LOCATION_RE.search(message)
    if not location:
        return f"配置 TOML 无效：{message}"
    line_number = int(location.group("line"))
    column_number = int(location.group("column"))
    lines = text.splitlines()
    source = lines[line_number - 1] if 0 < line_number <= len(lines) else ""
    redacted = _redact_toml_line(source)
    pointer = " " * max(column_number - 1, 0) + "^"
    if "\\" in source:
        hint = "Windows 路径请使用正斜杠（C:/Users/...），或在双引号内把每个反斜杠写成 \\\\."
    elif "=" in source and re.search(r"=\s*[^\"'\[{#]+", source):
        hint = "字符串值需要加引号；布尔值只能写 true 或 false。"
    else:
        hint = "请检查该行的引号、逗号、括号和数组格式。"
    return (
        f"配置 TOML 无效：第 {line_number} 行，第 {column_number} 列。\n"
        f"{redacted}\n{pointer}\n{hint}\n解析器信息：{message}"
    )


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


def _set_table_key(text: str, table_name: str, key: str, value: str | bool | None) -> str:
    """Set or comment a scalar key inside an existing TOML table."""
    lines = text.splitlines(keepends=True)
    bounds = _table_bounds(lines, table_name)
    if bounds is None:
        if value is not None:
            ending = "\r\n" if "\r\n" in text else "\n"
            if lines and lines[-1].strip():
                lines.append(ending)
            lines.append(f"[{table_name}]{ending}")
            lines.append(f"{key} = {_toml_value(value)}{ending}")
            return "".join(lines)
        return text
    start, end = bounds
    matcher = re.compile(rf"^(?P<indent>\s*)(?P<comment>#\s*)?{re.escape(key)}\s*=")
    matches = [index for index in range(start + 1, end) if matcher.match(lines[index])]
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
        lines.insert(end, rendered + ending)
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


def _split_inline_items(body: str) -> list[str]:
    """Split a TOML inline table without breaking quoted commas."""
    items: list[str] = []
    start = 0
    quote = ""
    escaped = False
    depth = 0
    for index, char in enumerate(body):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            item = body[start:index].strip()
            if item:
                items.append(item)
            start = index + 1
    item = body[start:].strip()
    if item:
        items.append(item)
    return items


def _normalize_header_name(value: str) -> str:
    return value.strip().strip('"').strip("'").casefold().replace("_", "-")


def _merge_provider_headers(
    text: str,
    provider_key: str,
    updates: dict[str, str | None],
) -> str:
    """Merge selected headers in a provider's inline http_headers table.

    Codex emits this table as a one-line TOML inline table. Unknown headers are
    preserved verbatim; an update value of ``None`` removes only that header.
    """
    if not updates:
        return text
    normalized_updates = {
        _normalize_header_name(key): (key, value) for key, value in updates.items()
    }
    lines = text.splitlines(keepends=True)
    bounds = _table_bounds(lines, f"model_providers.{provider_key}")
    if bounds is None:
        return text
    start, end = bounds
    matcher = re.compile(
        r"^(?P<indent>\s*)http_headers\s*=\s*\{(?P<body>.*)\}(?P<ending>\r?\n)?\s*$"
    )
    header_index: int | None = None
    for index in range(start + 1, end):
        if matcher.match(lines[index]):
            header_index = index
            break

    existing: list[str] = []
    seen: set[str] = set()
    if header_index is not None:
        match = matcher.match(lines[header_index])
        assert match is not None
        for item in _split_inline_items(match.group("body")):
            if "=" not in item:
                continue
            raw_key = item.split("=", 1)[0].strip()
            normalized = _normalize_header_name(raw_key)
            if normalized in normalized_updates:
                _, value = normalized_updates[normalized]
                if value is None:
                    continue
                existing.append(f"{raw_key} = {_toml_value(value)}")
                seen.add(normalized)
            else:
                existing.append(item)

    for normalized, (key, value) in normalized_updates.items():
        if value is None or normalized in seen:
            continue
        existing.append(f"{key} = {_toml_value(value)}")

    if existing:
        ending = "\n"
        indent = ""
        if header_index is not None:
            match = matcher.match(lines[header_index])
            assert match is not None
            ending = match.group("ending") or ("\r\n" if "\r\n" in text else "\n")
            indent = match.group("indent")
        rendered = f"{indent}http_headers = {{ {', '.join(existing)} }}{ending}"
        if header_index is None:
            lines.insert(end, rendered)
        else:
            lines[header_index] = rendered
    elif header_index is not None:
        del lines[header_index]
    return "".join(lines)


def _is_auth_header_key(key: str) -> bool:
    normalized = key.strip().strip('"').strip("'").casefold().replace("_", "-")
    return (
        "authorization" in normalized
        or "api-key" in normalized
        or "apikey" in normalized
        or "bearer" in normalized
        or normalized in {"token", "access-token", "x-token"}
    )


def _remove_provider_auth_fields(
    text: str,
    provider_key: str,
    *,
    remove_credentials: bool = True,
    remove_headers: bool = False,
) -> str:
    """Remove managed credentials while preserving unrelated provider options."""
    updated = (
        _remove_provider_table_keys(text, provider_key, ("experimental_bearer_token", "env_key"))
        if remove_credentials
        else _remove_provider_table_keys(text, provider_key, ("env_key",))
    )
    if not remove_headers:
        return updated
    lines = updated.splitlines(keepends=True)
    bounds = _table_bounds(lines, f"model_providers.{provider_key}")
    if bounds is None:
        return updated
    start, end = bounds
    matcher = re.compile(r"^(?P<indent>\s*)http_headers\s*=\s*\{(?P<body>.*)\}(?P<ending>\r?\n)?\s*$")
    for index in range(end - 1, start, -1):
        match = matcher.match(lines[index])
        if not match:
            continue
        kept: list[str] = []
        for item in _split_inline_items(match.group("body")):
            key = item.split("=", 1)[0].strip() if "=" in item else item
            if not _is_auth_header_key(key):
                kept.append(item)
        if kept:
            ending = match.group("ending") or "\n"
            lines[index] = f"{match.group('indent')}http_headers = {{ {', '.join(kept)} }}{ending}"
        else:
            del lines[index]
        break
    return "".join(lines)


class ConfigManager:
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home.expanduser().resolve(strict=False)
        self.config_path = self.codex_home / "config.toml"
        self.backup_directory = self.codex_home / BACKUP_DIRECTORY_NAME
        self._lock = threading.RLock()
        self.last_repair_applied = False

    @property
    def image_capability_backup(self) -> Path:
        return self.backup_directory / "image-capability.json"

    @staticmethod
    def _is_aqyimin_url(value: Any) -> bool:
        try:
            host = (urllib.parse.urlsplit(str(value)).hostname or "").casefold()
        except ValueError:
            return False
        return host == "aqyimin.chat" or host.endswith(".aqyimin.chat")

    @classmethod
    def is_aqyimin_url(cls, value: Any) -> bool:
        """Public provider classifier shared by policy and rendering layers."""
        return cls._is_aqyimin_url(value)

    @classmethod
    def uses_aqyimin_compatibility(cls, profile: ProviderProfile) -> bool:
        """Return whether a relay endpoint gets aqyimin-only behavior.

        The compatibility contract belongs to the endpoint hostname, not to
        the API1 slot. Editing API1 to any other URL must therefore make it
        behave like the generic API2 relay.
        """
        return profile.kind == "relay" and cls._is_aqyimin_url(profile.base_url)

    @classmethod
    def _text_uses_aqyimin(cls, text: str) -> bool:
        try:
            parsed = tomllib.loads(text)
            provider_key = str(parsed.get("model_provider", ""))
            providers = parsed.get("model_providers", {})
            provider = providers.get(provider_key, {}) if isinstance(providers, dict) else {}
            return isinstance(provider, dict) and cls._is_aqyimin_url(provider.get("base_url"))
        except (TypeError, tomllib.TOMLDecodeError):
            return False

    @classmethod
    def _text_has_aqyimin_image_marker(cls, text: str) -> bool:
        """Detect a stale AP1 image marker after a manual URL edit."""
        try:
            parsed = tomllib.loads(text)
            provider_key = str(parsed.get("model_provider", ""))
            providers = parsed.get("model_providers", {})
            provider = providers.get(provider_key, {}) if isinstance(providers, dict) else {}
            headers = provider.get("http_headers", {}) if isinstance(provider, dict) else {}
            if not isinstance(headers, dict):
                return False
            for key, value in headers.items():
                if (
                    _normalize_header_name(str(key)) in AQYIMIN_IMAGE_HEADERS
                    and value in AQYIMIN_SAFE_IMAGE_HEADER_VALUES
                ):
                    return True
        except (TypeError, tomllib.TOMLDecodeError):
            pass
        return False

    def _capture_aqyimin_image_config(self, text: str) -> None:
        """Remember non-secret aqyimin compatibility settings before GLM."""
        try:
            parsed = tomllib.loads(text)
            provider_key = str(parsed.get("model_provider", ""))
            providers = parsed.get("model_providers", {})
            provider = providers.get(provider_key, {}) if isinstance(providers, dict) else {}
            if not isinstance(provider, dict) or not self._is_aqyimin_url(provider.get("base_url")):
                return

            values: dict[str, str | bool | int | float] = {}
            for key in ("model", "model_catalog_json", "model_reasoning_effort", "service_tier"):
                value = parsed.get(key)
                if isinstance(value, (str, bool, int, float)):
                    values[key] = value

            features: dict[str, bool] = {}
            feature_table = parsed.get("features", {})
            if isinstance(feature_table, dict) and isinstance(
                feature_table.get("image_generation"), bool
            ):
                features["image_generation"] = feature_table["image_generation"]

            headers: dict[str, str] = {}
            raw_headers = provider.get("http_headers", {})
            if isinstance(raw_headers, dict):
                for raw_key, value in raw_headers.items():
                    normalized = _normalize_header_name(str(raw_key))
                    if (
                        normalized in AQYIMIN_IMAGE_HEADERS
                        and isinstance(value, str)
                        and value in AQYIMIN_SAFE_IMAGE_HEADER_VALUES
                    ):
                        # Only the documented extension marker is copied;
                        # bearer/API credentials can never enter this snapshot.
                        headers[normalized] = value[:512]

            payload = {
                "schema": 3,
                "provider_base_url": str(provider.get("base_url", "")),
                "values": values,
                "features": features,
                "http_headers": headers,
            }
            if not values and not features and not headers:
                return
            self.backup_directory.mkdir(parents=True, exist_ok=True)
            temporary = self.image_capability_backup.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.image_capability_backup)
        except (OSError, TypeError, tomllib.TOMLDecodeError):
            return

    def _ensure_aqyimin_image_defaults(self, text: str, provider_key: str | None = None) -> str:
        """Add AP1's documented image-extension markers only when absent."""
        updated = text
        try:
            parsed = tomllib.loads(updated)
        except tomllib.TOMLDecodeError:
            return updated
        if provider_key is None:
            provider_key = str(parsed.get("model_provider", "")).strip()
        features = parsed.get("features", {})
        if not isinstance(features, dict) or "image_generation" not in features:
            updated = _set_table_key(updated, "features", "image_generation", True)
        providers = parsed.get("model_providers", {})
        provider = providers.get(provider_key, {}) if isinstance(providers, dict) else {}
        if not isinstance(provider, dict):
            return updated
        raw_headers = provider.get("http_headers", {})
        has_marker = False
        if isinstance(raw_headers, dict):
            has_marker = any(
                _normalize_header_name(str(key)) in AQYIMIN_IMAGE_HEADERS
                for key in raw_headers
            )
        if not has_marker and provider_key:
            updated = _merge_provider_headers(
                updated,
                provider_key,
                {"x-openai-actor-authorization": "local-image-extension"},
            )
        return updated

    def _restore_aqyimin_image_config(self, text: str, provider_key: str | None = None) -> str:
        if not self.image_capability_backup.exists():
            # A GLM switch may have just injected the GLM catalog.  Do not
            # leave that catalog attached to the GPT-compatible provider when
            # the original aqyimin config had no custom catalog.
            updated = _set_top_level(text, "model_catalog_json", None)
            return self._ensure_aqyimin_image_defaults(updated, provider_key)
        try:
            payload = json.loads(self.image_capability_backup.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("schema") in {2, 3}:
                values = payload.get("values", {})
                if not isinstance(values, dict):
                    return text
                updated = text
                # These fields were always captured by schema 2; clearing a
                # missing catalog/reasoning value prevents GLM metadata leaking
                # into API1 on old installations.
                if payload.get("schema") == 3 and "model" not in values:
                    # Keep the model selected by the profile when an older or
                    # hand-written AP1 config omitted a top-level model.
                    pass
                else:
                    updated = _set_top_level(updated, "model", values.get("model"))
                for key in ("model_catalog_json", "model_reasoning_effort"):
                    updated = _set_top_level(updated, key, values.get(key))
                if "service_tier" in values:
                    updated = _set_top_level(updated, "service_tier", values["service_tier"])
                if payload.get("schema") == 3:
                    features = payload.get("features", {})
                    if isinstance(features, dict) and "image_generation" in features:
                        value = features["image_generation"]
                        if isinstance(value, bool):
                            updated = _set_table_key(
                                updated, "features", "image_generation", value
                            )
                    headers = payload.get("http_headers", {})
                    if provider_key is None:
                        try:
                            provider_key = str(tomllib.loads(updated).get("model_provider", ""))
                        except tomllib.TOMLDecodeError:
                            provider_key = ""
                    if isinstance(headers, dict) and provider_key:
                        safe_headers = {
                            _normalize_header_name(str(key)): value
                            for key, value in headers.items()
                            if _normalize_header_name(str(key)) in AQYIMIN_IMAGE_HEADERS
                            and isinstance(value, str)
                            and value in AQYIMIN_SAFE_IMAGE_HEADER_VALUES
                        }
                        updated = _merge_provider_headers(updated, provider_key, safe_headers)
                return self._ensure_aqyimin_image_defaults(updated, provider_key)

            # Version 1.2.26 captured only a sparse map and could mistake the
            # ambiguous legacy models.json (which GLM also wrote) for an
            # aqyimin-owned catalog. Drop that stale pair instead of making
            # API1 display GLM models again.
            values = payload if isinstance(payload, dict) else {}
            updated = text
            legacy_catalog = values.get("model_catalog_json")
            is_legacy_glm_catalog = (
                isinstance(legacy_catalog, str)
                and Path(legacy_catalog).resolve(strict=False)
                == (self.codex_home / MODEL_CATALOG_NAME).resolve(strict=False)
            )
            if is_legacy_glm_catalog:
                updated = _set_top_level(updated, "model_catalog_json", None)
                updated = _set_top_level(updated, "model_reasoning_effort", None)
                return self._ensure_aqyimin_image_defaults(updated, provider_key)
            for key in ("model_catalog_json", "model_reasoning_effort"):
                if key in values:
                    updated = _set_top_level(updated, key, values[key])
            return self._ensure_aqyimin_image_defaults(updated, provider_key)
        except (OSError, ValueError, TypeError):
            return text

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

    @classmethod
    def parse_toml_text(
        cls, text: str, *, auto_repair: bool = False
    ) -> tuple[dict[str, Any], str, bool]:
        """Parse editor text and optionally repair unambiguous Windows string syntax."""
        try:
            return tomllib.loads(text), text, False
        except tomllib.TOMLDecodeError as exc:
            if auto_repair:
                repaired = _repair_common_toml(text)
                if repaired != text:
                    try:
                        return tomllib.loads(repaired), repaired, True
                    except tomllib.TOMLDecodeError:
                        pass
            raise ConfigError(format_toml_error(text, exc)) from exc

    def save_text(self, text: str) -> tuple[ConfigSnapshot, Path | None]:
        """Validate and save a user-edited config.toml with rollback support."""
        self.last_repair_applied = False
        parsed, candidate, repaired = self.parse_toml_text(text, auto_repair=True)
        self.last_repair_applied = repaired
        providers = parsed.get("model_providers", {})
        if not isinstance(providers, dict):
            raise ConfigError("model_providers 必须是配置表。")
        self.ensure_config()
        with self._lock:
            original, encoding = self.read()
            if candidate == original:
                return self.snapshot(), None
            backup = self._create_backup()
            temporary = self.config_path.with_name("config.toml.provider-switch.tmp")
            try:
                temporary.write_text(candidate, encoding=encoding, newline="")
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

    def normalize_active_provider_auth(self) -> bool:
        """Repair an AP1 config that was incorrectly marked as OpenAI-auth."""
        original, _ = self.read()
        try:
            parsed = tomllib.loads(original)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config.toml 语法错误：{exc}") from exc
        provider_key = str(parsed.get("model_provider", "")).strip()
        providers = parsed.get("model_providers", {})
        provider = providers.get(provider_key, {}) if isinstance(providers, dict) else {}
        if not isinstance(provider, dict) or not self._is_aqyimin_url(provider.get("base_url")):
            return False
        needs_auth_fix = provider.get("requires_openai_auth") is not False or (
            isinstance(provider.get("experimental_bearer_token"), str)
            and provider.get("experimental_bearer_token", "").strip()
            and "env_key" in provider
        )
        updated = self._ensure_aqyimin_image_defaults(original, provider_key)
        if needs_auth_fix:
            updated = _upsert_provider_table(updated, provider_key, {"requires_openai_auth": False})
        # A bearer written by this app is the single auth source. Remove an
        # inherited env_key only when a bearer is present; env-only hand-written
        # configs remain usable and are not silently stripped.
        if isinstance(provider.get("experimental_bearer_token"), str) and provider.get(
            "experimental_bearer_token", ""
        ).strip():
            updated = _remove_provider_table_keys(updated, provider_key, ("env_key",))
        if updated == original:
            return False
        self.save_text(updated)
        return True

    def render_profile(
        self,
        profile: ProviderProfile,
        api_key: str,
        preserve_provider_key: bool,
        stable_provider_key: str,
        catalog_path: Path | None = None,
        clear_other_keys: list[str] | None = None,
    ) -> tuple[str, str]:
        original, _ = self.read()
        original_aqyimin = self._text_uses_aqyimin(original)
        original_aqyimin_marker = self._text_has_aqyimin_image_marker(original)
        target_aqyimin = self.uses_aqyimin_compatibility(profile)
        leaving_aqyimin = not target_aqyimin and (
            original_aqyimin or original_aqyimin_marker
        )
        if original_aqyimin and not target_aqyimin:
            self._capture_aqyimin_image_config(original)
        if profile.kind == "official":
            updated = _set_top_level(original, "model_provider", None)
            if profile.model:
                updated = _set_top_level(updated, "model", profile.model)
            updated = _set_top_level(updated, "model_catalog_json", None)
            if leaving_aqyimin:
                updated = _set_top_level(updated, "service_tier", None)
                updated = _set_table_key(updated, "features", "image_generation", None)
            for key in clear_other_keys or []:
                updated = _remove_provider_auth_fields(updated, key, remove_headers=True)
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
        elif target_aqyimin:
            updated = self._restore_aqyimin_image_config(updated)
        else:
            updated = _set_top_level(updated, "model_catalog_json", None)
        if not target_aqyimin and (leaving_aqyimin or profile.kind == "glm"):
            # The image flag belongs to aqyimin's extension, not to GLM or a
            # generic relay. Keep unrelated feature flags untouched.
            updated = _set_table_key(updated, "features", "image_generation", None)
        if leaving_aqyimin:
            updated = _set_top_level(updated, "service_tier", None)
        provider_values = {
            "name": profile.display_name,
            "base_url": profile.base_url,
            "wire_api": "responses",
            # aqyimin.chat is an API-key/bearer provider even when the user
            # chooses to retain a separate official OpenAI login session.
            "requires_openai_auth": False if target_aqyimin else profile.requires_openai_auth,
            "experimental_bearer_token": api_key,
        }
        updated = _upsert_provider_table(updated, provider_key, provider_values)
        if target_aqyimin:
            # The provider table may have been created by the upsert above;
            # restore the allow-listed image header after it exists.
            updated = self._restore_aqyimin_image_config(updated, provider_key)
        else:
            # Do not let an AP1-only actor header bleed into API2 or GLM when
            # the stable provider label is reused.
            updated = _merge_provider_headers(
                updated,
                provider_key,
                {header: None for header in AQYIMIN_IMAGE_HEADERS},
            )
        # Older/manual configs may still ask Codex to read another env variable
        # even though this tool writes an explicit bearer token. Keep the auth
        # source unambiguous. GLM also must not inherit stale app-specific
        # actor headers from copied configs.
        updated = _remove_provider_table_keys(updated, provider_key, ("env_key",))
        if profile.kind == "glm":
            updated = _remove_provider_auth_fields(
                updated, provider_key, remove_credentials=False, remove_headers=True
            )
        for key in clear_other_keys or []:
            updated = _remove_provider_auth_fields(updated, key, remove_headers=True)
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
        clear_other_keys: list[str] | None = None,
    ) -> tuple[Path | None, str]:
        with self._lock:
            original, encoding = self.read()
            updated, provider_key = self.render_profile(
                profile,
                api_key,
                preserve_provider_key,
                stable_provider_key,
                catalog_path,
                clear_other_keys,
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

    def clear_provider_keys(self, keys: list[str]) -> tuple[Path | None, list[str]]:
        """Drop live connection fields from the given provider tables (keep entries)."""
        cleared: list[str] = []
        with self._lock:
            original, encoding = self.read()
            updated = original
            for key in keys:
                stripped = _remove_provider_auth_fields(updated, key, remove_headers=True)
                if stripped != updated:
                    cleared.append(key)
                updated = stripped
            if updated == original:
                return None, []
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
            return backup, cleared

    def _create_backup(self) -> Path:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.backup_directory / f"config-{stamp}.toml"
        shutil.copy2(self.config_path, target)
        return target
