from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import bisect
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth_manager import AuthStatus, CodexAuthManager
from .constants import APP_VERSION
from .models import (
    HealthResult,
    ProviderProfile,
    ProviderTelemetry,
    QuotaWindow,
    TokenUsage,
)


class MonitoringError(RuntimeError):
    pass


def _now_label() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _http_json(
    url: str,
    api_key: str = "",
    *,
    raw_authorization: bool = False,
    timeout: float = 10,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, Any, int]:
    headers = {
        "Accept": "application/json",
        "User-Agent": f"CodexProviderSwitch/{APP_VERSION}",
    }
    if api_key:
        headers["Authorization"] = api_key if raw_authorization else f"Bearer {api_key}"
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read(2 * 1024 * 1024)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise MonitoringError(str(exc)) from exc
    latency = round((time.perf_counter() - started) * 1000)
    if not raw:
        return status, {}, latency
    try:
        return status, json.loads(raw.decode("utf-8", errors="replace")), latency
    except json.JSONDecodeError:
        return status, {}, latency


def _glm_quota_request(profile: ProviderProfile) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlsplit(profile.quota_url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    headers: dict[str, str] = {}
    organization = profile.quota_organization_id.strip()
    project = profile.quota_project_id.strip()
    if organization and project:
        type_index = next((index for index, (key, _) in enumerate(query) if key.lower() == "type"), -1)
        if type_index >= 0:
            query[type_index] = (query[type_index][0], "2")
        else:
            query.append(("type", "2"))
        headers["bigmodel-organization"] = organization
        headers["bigmodel-project"] = project
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    ), headers


def _models_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/models"


def _check_openai(
    codex_home: Path | None = None,
    retain_official_auth: bool = True,
    auth_status: AuthStatus | None = None,
) -> HealthResult:
    status_result = auth_status or CodexAuthManager(codex_home or Path.home() / ".codex").status()
    login_ok = retain_official_auth and status_result.official_account_logged_in
    login_message = status_result.label if retain_official_auth else "官方登录态未保留"
    try:
        status, _, latency = _http_json("https://api.openai.com/v1/models", timeout=8)
        reachable = status in {200, 401, 403, 429}
    except MonitoringError as exc:
        return HealthResult("openai", "offline", f"网络不可达：{exc}", checked_at=_now_label())
    state = "healthy" if reachable and login_ok else "warning"
    message = f"OpenAI 可达 / {login_message}" if reachable else "OpenAI 网络异常"
    return HealthResult("openai", state, message, latency, _now_label())


def _parse_glm_quota(data: Any) -> tuple[list[QuotaWindow], str]:
    if not isinstance(data, dict):
        return [], "额度响应格式无法识别"
    body = data.get("data", data)
    if not isinstance(body, dict):
        return [], "额度响应缺少 data"
    level = str(body.get("level", "")).strip()
    windows: list[QuotaWindow] = []
    labels = {3: "5 小时", 6: "周额度"}
    for item in body.get("limits", []):
        if not isinstance(item, dict):
            continue
        limit_type = str(item.get("type", "")).upper()
        if limit_type not in {"TOKENS_LIMIT", "CREDIT_LIMIT", "TIME_LIMIT"}:
            continue
        unit = item.get("unit")
        label = labels.get(unit, "月额度" if limit_type == "TIME_LIMIT" else "额度")
        percentage = item.get("percentage")
        try:
            used = max(0.0, min(100.0, float(percentage)))
        except (TypeError, ValueError):
            used = None
        reset_at = ""
        reset_ms = item.get("nextResetTime")
        if isinstance(reset_ms, (int, float)) and reset_ms > 0:
            try:
                reset_at = datetime.fromtimestamp(reset_ms / 1000, tz=timezone.utc).astimezone().strftime(
                    "%m-%d %H:%M"
                )
            except (OSError, OverflowError, ValueError):
                reset_at = ""
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used,
                remaining=(100.0 - used) if used is not None else None,
                total=100.0 if used is not None else None,
                unit="%",
                reset_at=reset_at,
            )
        )
    order = {"5 小时": 0, "周额度": 1, "月额度": 2, "额度": 3}
    windows.sort(key=lambda item: order.get(item.label, 9))
    if not windows:
        if isinstance(data, dict) and data.get("success") is False:
            return [], str(data.get("msg") or "额度业务查询失败")
        return [], "团队套餐响应为空，请检查组织 ID 和项目 ID"
    return windows, f"GLM {level}".strip()


def _dig(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _parse_generic_quota(profile: ProviderProfile, data: Any) -> tuple[list[QuotaWindow], str]:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return [], str(error["message"])
        if data.get("isValid") is False:
            return [], str(data.get("msg") or data.get("message") or "额度不可用")
    remaining_paths = [profile.quota_remaining_path, "data.remaining", "remaining", "data.balance", "balance"]
    total_paths = [profile.quota_total_path, "data.total", "total", "data.limit", "limit"]
    remaining = next((_dig(data, path) for path in remaining_paths if path and _dig(data, path) is not None), None)
    total = next((_dig(data, path) for path in total_paths if path and _dig(data, path) is not None), None)
    try:
        remaining_number = float(remaining)
    except (TypeError, ValueError):
        return [], "额度接口已响应，但字段路径未适配"
    try:
        total_number = float(total) if total is not None else None
    except (TypeError, ValueError):
        total_number = None
    unit = str(data.get("unit", "")).strip() if isinstance(data, dict) else ""
    used_percent = None
    if total_number and total_number > 0:
        used_percent = max(0.0, min(100.0, (total_number - remaining_number) * 100 / total_number))
    return [QuotaWindow("剩余额度", used_percent, remaining_number, total_number, unit)], "额度已更新"


def _relay_quota_url(profile: ProviderProfile) -> str:
    if profile.quota_url:
        return profile.quota_url
    parsed = urllib.parse.urlsplit(profile.base_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/v1/usage", "days=30", ""))


def check_provider(
    profile: ProviderProfile,
    api_key: str = "",
    *,
    codex_home: Path | None = None,
    retain_official_auth: bool = True,
    auth_status: AuthStatus | None = None,
) -> ProviderTelemetry:
    if not profile.enabled:
        health = HealthResult(profile.profile_id, "disabled", "已停用", checked_at=_now_label())
        return ProviderTelemetry(profile.profile_id, health, quota_message="监控已停用")
    if profile.kind == "official":
        health = _check_openai(codex_home, retain_official_auth, auth_status)
        return ProviderTelemetry(profile.profile_id, health, quota_message="订阅额度由 Codex 显示")
    if not profile.base_url or not api_key:
        missing = "API 地址" if not profile.base_url else "API Key"
        health = HealthResult(profile.profile_id, "unconfigured", f"缺少{missing}", checked_at=_now_label())
        return ProviderTelemetry(profile.profile_id, health, quota_message="尚未配置")

    quota: list[QuotaWindow] = []
    quota_message = "未配置额度接口"
    quota_latency: int | None = None
    if profile.kind == "glm" and profile.quota_url:
        try:
            quota_url, extra_headers = _glm_quota_request(profile)
            status, payload, quota_latency = _http_json(
                quota_url, api_key, raw_authorization=True, extra_headers=extra_headers
            )
            if status in {401, 403}:
                status, payload, quota_latency = _http_json(
                    quota_url, api_key, extra_headers=extra_headers
                )
            if status == 200:
                quota, quota_message = _parse_glm_quota(payload)
                if (
                    not quota
                    and not profile.quota_organization_id
                    and not profile.quota_project_id
                    and "coding plan" in quota_message.lower()
                ):
                    quota_message = "这是团队套餐 Key：请在供应商配置填写组织 ID 和项目 ID"
                if quota:
                    health = HealthResult(
                        profile.profile_id,
                        "healthy",
                        "GLM 链路与凭据正常",
                        quota_latency,
                        _now_label(),
                    )
                    return ProviderTelemetry(profile.profile_id, health, quota, quota_message)
                if (
                    isinstance(payload, dict)
                    and payload.get("success") is False
                    and quota_message != "这是团队套餐 Key：请在供应商配置填写组织 ID 和项目 ID"
                ):
                    quota_message = str(payload.get("msg") or "额度业务查询失败")
            elif status in {401, 403}:
                health = HealthResult(
                    profile.profile_id, "auth_error", "GLM Key 无效或无权限", quota_latency, _now_label()
                )
                return ProviderTelemetry(profile.profile_id, health, quota_message="额度认证失败")
            else:
                quota_message = f"额度接口 HTTP {status}"
        except MonitoringError as exc:
            quota_message = f"额度查询失败：{exc}"

    try:
        status, _, latency = _http_json(_models_url(profile.base_url), api_key)
    except MonitoringError as exc:
        health = HealthResult(profile.profile_id, "offline", f"链路不可达：{exc}", checked_at=_now_label())
        return ProviderTelemetry(profile.profile_id, health, quota, quota_message)
    if status == 200:
        health = HealthResult(profile.profile_id, "healthy", "链路与凭据正常", latency, _now_label())
    elif status in {401, 403}:
        health = HealthResult(profile.profile_id, "auth_error", "链路可达，Key 被拒绝", latency, _now_label())
    elif status in {404, 405}:
        health = HealthResult(profile.profile_id, "warning", "链路可达，未提供模型清单", latency, _now_label())
    elif status == 429:
        health = HealthResult(profile.profile_id, "warning", "链路可达，当前请求受限", latency, _now_label())
    else:
        health = HealthResult(profile.profile_id, "offline", f"服务返回 HTTP {status}", latency, _now_label())

    if profile.kind != "glm":
        try:
            quota_status, payload, _ = _http_json(_relay_quota_url(profile), api_key)
            if quota_status == 200:
                quota, quota_message = _parse_generic_quota(profile, payload)
            elif quota_status in {401, 403}:
                quota_message = "额度接口认证失败"
            else:
                if quota_status == 503 and isinstance(payload, dict):
                    error = payload.get("error", {})
                    detail = error.get("message", "") if isinstance(error, dict) else ""
                    quota_message = "中转未返回额度：上游暂无可用通道" if detail else f"额度接口 HTTP {quota_status}"
                else:
                    quota_message = f"额度接口 HTTP {quota_status}"
        except MonitoringError as exc:
            quota_message = f"额度查询失败：{exc}"
    return ProviderTelemetry(profile.profile_id, health, quota, quota_message)


def check_all(
    profiles: dict[str, ProviderProfile],
    credentials: dict[str, str],
    codex_home: Path | None = None,
    retain_official_auth: bool = True,
) -> dict[str, ProviderTelemetry]:
    enabled = [profile for profile in profiles.values() if profile.enabled]
    results: dict[str, ProviderTelemetry] = {}
    auth_status = CodexAuthManager(codex_home or Path.home() / ".codex").status()
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(enabled)))) as executor:
        pending = {
            executor.submit(
                check_provider,
                profile,
                credentials.get(profile.profile_id, ""),
                codex_home=codex_home,
                retain_official_auth=retain_official_auth,
                auth_status=auth_status,
            ): profile.profile_id
            for profile in enabled
        }
        for future in as_completed(pending):
            profile_id = pending[future]
            try:
                results[profile_id] = future.result()
            except Exception as exc:
                health = HealthResult(profile_id, "offline", f"监控异常：{exc}", checked_at=_now_label())
                results[profile_id] = ProviderTelemetry(profile_id, health, quota_message="查询失败")
    return results


def scan_token_usage(codex_home: Path, lookback_days: int = 30) -> dict[str, TokenUsage]:
    return scan_token_usage_seconds(codex_home, lookback_days * 86400)


def load_switch_timeline(switch_log_path: Path) -> list[tuple[float, str]]:
    """Return sorted (epoch_seconds, profile_id) switch events."""
    try:
        raw = switch_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events: list[tuple[float, str]] = []
    for line in raw:
        try:
            obj = json.loads(line)
            ts = datetime.fromisoformat(str(obj.get("timestamp", "")))
            events.append((ts.timestamp(), str(obj.get("profile_id", ""))))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    events.sort(key=lambda item: item[0])
    return events


def scan_token_usage_seconds(
    codex_home: Path,
    lookback_seconds: float = 30 * 86400,
    switch_log_path: Path | None = None,
) -> dict[str, TokenUsage]:
    cutoff = datetime.now().timestamp() - max(60.0, lookback_seconds)
    timeline = load_switch_timeline(switch_log_path) if switch_log_path is not None else []
    timeline_times = [item[0] for item in timeline]
    roots = [codex_home / "sessions", codex_home / "archived_sessions"]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(path for path in root.rglob("*.jsonl") if path.is_file())
    usage: dict[str, TokenUsage] = {}
    for path in files:
        try:
            if path.stat().st_mtime < cutoff:
                continue
            mtime = path.stat().st_mtime
        except OSError:
            continue
        provider_key = "unknown"
        snapshots: list[tuple[float, dict[str, int]]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                        if event.get("type") == "session_meta":
                            value = event.get("payload", {}).get("model_provider")
                            if isinstance(value, str) and value:
                                provider_key = value
                            continue
                        payload = event.get("payload", {})
                        if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                            continue
                        totals = payload.get("info", {}).get("total_token_usage")
                        if not isinstance(totals, dict):
                            continue
                        values = {
                            key: max(0, int(totals.get(key, 0) or 0))
                            for key in (
                                "input_tokens",
                                "cached_input_tokens",
                                "output_tokens",
                                "reasoning_output_tokens",
                            )
                        }
                        total = max(0, int(totals.get("total_tokens", 0) or 0))
                        if total == 0:
                            # Some compatible gateways omit total_tokens.  The
                            # Codex total is input plus output; cached input is
                            # already included in input and must not be added twice.
                            total = values["input_tokens"] + values["output_tokens"]
                        values["total_tokens"] = total
                        timestamp = event.get("timestamp")
                        try:
                            event_time = datetime.fromisoformat(str(timestamp)).timestamp()
                        except (TypeError, ValueError, OverflowError):
                            event_time = mtime
                        snapshots.append((event_time, values))
                    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
                        continue
        except OSError:
            continue
        if not snapshots:
            continue
        snapshots.sort(key=lambda item: item[0])
        previous = {key: 0 for key in snapshots[0][1]}
        session_owners: set[str] = set()
        for event_time, current in snapshots:
            if event_time < cutoff:
                previous = current
                continue
            delta: dict[str, int] = {}
            for key, value in current.items():
                # total_token_usage is cumulative for the session.  If a
                # gateway resets the counter, treat the new value as a fresh
                # increment instead of producing a negative total.
                delta[key] = value - previous.get(key, 0)
                if delta[key] < 0:
                    delta[key] = value
            previous = current
            if delta["total_tokens"] <= 0:
                continue
            attribution = "provider_key"
            owner = provider_key
            if timeline:
                idx = bisect.bisect_right(timeline_times, event_time) - 1
                if idx >= 0 and timeline[idx][1]:
                    attribution = "timeline"
                    owner = timeline[idx][1]
                else:
                    owner = "unknown"
            item = usage.get(owner)
            if item is None:
                item = TokenUsage(owner, attribution=attribution, model_provider=provider_key)
                usage[owner] = item
            item.input_tokens += delta["input_tokens"]
            item.cached_input_tokens += delta["cached_input_tokens"]
            item.output_tokens += delta["output_tokens"]
            item.reasoning_output_tokens += delta["reasoning_output_tokens"]
            item.total_tokens += delta["total_tokens"]
            session_owners.add(owner)
        for owner in session_owners:
            usage[owner].sessions += 1
    return usage
