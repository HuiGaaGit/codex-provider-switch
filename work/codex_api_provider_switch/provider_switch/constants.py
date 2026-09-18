from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "Codex Provider Switch"
APP_VERSION = "1.2.8"
APP_SLUG = "codex-provider-switch"

DEFAULT_CODEX_HOME = Path.home() / ".codex"
LEGACY_SETTINGS_NAME = "codex_provider_switch_labels.json"
SETTINGS_DIRECTORY_NAME = "codex-provider-switch"
SETTINGS_FILE_NAME = "settings.json"
CREDENTIALS_FILE_NAME = "credentials.dat"
SWITCH_LOG_NAME = "switch-history.jsonl"
MONITOR_HISTORY_DIRECTORY_NAME = "monitor-history"
BACKUP_DIRECTORY_NAME = "provider-switch-backups"
MODEL_CATALOG_NAME = "models.json"

THREADRIPPER_NAME = "codex-threadripper"
THREADRIPPER_REPOSITORY = "Wangnov/codex-threadripper"
THREADRIPPER_RELEASE_API = (
    "https://api.github.com/repos/Wangnov/codex-threadripper/releases/latest"
)
THREADRIPPER_FALLBACK_VERSION = "0.3.6"
THREADRIPPER_FALLBACK_URL = (
    "https://github.com/Wangnov/codex-threadripper/releases/download/"
    "v0.3.6/codex-threadripper-x86_64-pc-windows-msvc.zip"
)

GLM_PROVIDER_KEY = "ZAI"
GLM_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/v1"
GLM_DEFAULT_QUOTA_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
GLM_DEFAULT_MODEL = "glm-5.3-flash"

MONITOR_INTERVAL_SECONDS = 30
USAGE_LOOKBACK_DAYS = 30


def local_app_data() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return Path(root) if root else Path.home() / "AppData" / "Local"


def managed_tools_directory() -> Path:
    return local_app_data() / "CodexProviderSwitch" / "tools"


COLORS = {
    "window": "#eef1f3",
    "surface": "#ffffff",
    "surface_alt": "#f6f7f8",
    "sidebar": "#202428",
    "sidebar_hover": "#30363b",
    "sidebar_active": "#3a4248",
    "text": "#202529",
    "muted": "#687178",
    "border": "#d9dee2",
    "accent": "#1677ff",
    "accent_hover": "#0d63d7",
    "success": "#17845b",
    "warning": "#bd6b18",
    "danger": "#c63f4d",
    "openai": "#138a68",
    "relay1": "#1879b8",
    "relay2": "#c06a1b",
    "glm": "#7257b5",
}
