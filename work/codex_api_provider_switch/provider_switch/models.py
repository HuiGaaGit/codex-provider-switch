from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .constants import (
    GLM_DEFAULT_BASE_URL,
    GLM_DEFAULT_MODEL,
    GLM_DEFAULT_QUOTA_URL,
    GLM_PROVIDER_KEY,
    MONITOR_INTERVAL_SECONDS,
    USAGE_LOOKBACK_DAYS,
)


@dataclass(slots=True)
class ProviderProfile:
    profile_id: str
    display_name: str
    kind: str
    provider_key: str
    base_url: str = ""
    model: str = ""
    wire_api: str = "responses"
    requires_openai_auth: bool = False
    quota_url: str = ""
    quota_remaining_path: str = ""
    quota_total_path: str = ""
    quota_organization_id: str = ""
    quota_project_id: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderProfile":
        known = {item.name for item in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in known})


def default_profiles() -> dict[str, ProviderProfile]:
    return {
        "openai": ProviderProfile(
            profile_id="openai",
            display_name="OpenAI 直连",
            kind="official",
            provider_key="openai",
        ),
        "relay1": ProviderProfile(
            profile_id="relay1",
            display_name="API1",
            kind="relay",
            provider_key="relay_1",
            requires_openai_auth=True,
        ),
        "relay2": ProviderProfile(
            profile_id="relay2",
            display_name="API2",
            kind="relay",
            provider_key="relay_2",
            requires_openai_auth=True,
        ),
        "glm": ProviderProfile(
            profile_id="glm",
            display_name="GLM",
            kind="glm",
            provider_key=GLM_PROVIDER_KEY,
            base_url=GLM_DEFAULT_BASE_URL,
            model=GLM_DEFAULT_MODEL,
            quota_url=GLM_DEFAULT_QUOTA_URL,
        ),
    }


@dataclass(slots=True)
class AppSettings:
    schema_version: int = 7
    setup_complete: bool = False
    codex_home: str = ""
    preserve_provider_key: bool = True
    stable_provider_key: str = "cch_gz"
    auto_restart: bool = True
    auto_sync_history: bool = True
    close_to_tray: bool = True
    tray_health_notifications: bool = True
    monitor_interval_seconds: int = MONITOR_INTERVAL_SECONDS
    usage_lookback_days: int = USAGE_LOOKBACK_DAYS
    active_profile_id: str = ""
    retain_official_auth: bool = True
    last_relay_profile_id: str = "relay1"
    profiles: dict[str, ProviderProfile] = field(default_factory=default_profiles)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["profiles"] = {
            key: profile.to_dict() for key, profile in self.profiles.items()
        }
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        settings = cls()
        source_schema = int(data.get("schema_version", 1))
        scalar_fields = (
            "schema_version",
            "setup_complete",
            "codex_home",
            "preserve_provider_key",
            "stable_provider_key",
            "auto_restart",
            "auto_sync_history",
            "close_to_tray",
            "tray_health_notifications",
            "monitor_interval_seconds",
            "usage_lookback_days",
            "active_profile_id",
            "retain_official_auth",
            "last_relay_profile_id",
        )
        for name in scalar_fields:
            if name in data:
                setattr(settings, name, data[name])
        raw_profiles = data.get("profiles", {})
        if isinstance(raw_profiles, dict):
            profiles = default_profiles()
            for key, value in raw_profiles.items():
                if isinstance(value, dict):
                    try:
                        profiles[key] = ProviderProfile.from_dict(value)
                    except TypeError:
                        continue
            settings.profiles = profiles
        if source_schema < 3 and "retain_official_auth" not in data:
            settings.retain_official_auth = any(
                profile.requires_openai_auth
                for profile_id, profile in settings.profiles.items()
                if profile_id in {"relay1", "relay2"}
            )
        if source_schema < 4:
            # Earlier releases could terminate themselves when launched from Codex.
            settings.auto_restart = False
        if source_schema < 6:
            legacy_names = {
                "relay1": {"中转 1", "Relay 1"},
                "relay2": {"中转 2", "Relay 2"},
            }
            for profile_id, names in legacy_names.items():
                profile = settings.profiles.get(profile_id)
                if profile is not None and profile.display_name in names:
                    profile.display_name = "API1" if profile_id == "relay1" else "API2"
            glm = settings.profiles.get("glm")
            if glm is not None and glm.model == "glm-5.3":
                glm.model = "glm-5.3-flash"
            settings.schema_version = 7
        return settings


@dataclass(slots=True)
class ConfigSnapshot:
    path: str
    model_provider: str = ""
    model: str = ""
    model_catalog_json: str = ""
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class HealthResult:
    profile_id: str
    state: str
    message: str
    latency_ms: int | None = None
    checked_at: str = ""


@dataclass(slots=True)
class QuotaWindow:
    label: str
    used_percent: float | None = None
    remaining: float | None = None
    total: float | None = None
    unit: str = ""
    reset_at: str = ""


@dataclass(slots=True)
class ProviderTelemetry:
    profile_id: str
    health: HealthResult
    quota: list[QuotaWindow] = field(default_factory=list)
    quota_message: str = ""


@dataclass(slots=True)
class TokenUsage:
    provider_key: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    sessions: int = 0
