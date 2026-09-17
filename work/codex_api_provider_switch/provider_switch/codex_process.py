from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .catalog import resource_path


RESTART_HELPER = "assets/restart-codex-app.bat"
CODEX_PACKAGE_PREFIX = "OpenAI.Codex_"
CODEX_APP_EXECUTABLE = "ChatGPT.exe"


class CodexProcessError(RuntimeError):
    pass


@dataclass(slots=True)
class RestartTarget:
    package_root: Path
    app_user_model_id: str


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class CodexProcessController:
    def launched_from_codex(self) -> bool:
        """Return true when restarting Codex could also terminate this application."""
        if os.name != "nt":
            return False
        script = (
            f"$cursor = Get-CimInstance Win32_Process -Filter 'ProcessId = {os.getpid()}' "
            "-ErrorAction SilentlyContinue; "
            "while ($cursor -and $cursor.ParentProcessId -gt 0) { "
            "$cursor = Get-CimInstance Win32_Process "
            "-Filter ('ProcessId = ' + $cursor.ParentProcessId) -ErrorAction SilentlyContinue; "
            f"if ($cursor -and $cursor.ExecutablePath -like '*\\{CODEX_PACKAGE_PREFIX}*\\app\\{CODEX_APP_EXECUTABLE}') "
            "{ Write-Output 'true'; break } }"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=_creation_flags(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return result.returncode != 0 or result.stdout.strip().casefold() == "true"

    def locate_running(self) -> RestartTarget | None:
        if os.name != "nt":
            return None
        script = (
            "Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | "
            "ForEach-Object { $_.Path } | "
            f"Where-Object {{ $_ -like '*\\{CODEX_PACKAGE_PREFIX}*\\app\\{CODEX_APP_EXECUTABLE}' }} | "
            "Select-Object -First 1"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_creation_flags(),
            check=False,
        )
        raw = result.stdout.strip()
        if not raw:
            return None
        executable = Path(raw)
        package_root = executable.parent.parent
        package_name = package_root.name
        if "__" not in package_name:
            raise CodexProcessError(f"无法识别 Codex 安装目录：{package_root}")
        app_id = f"{package_name.split('_', 1)[0]}_{package_name.rsplit('__', 1)[1]}"
        return RestartTarget(package_root=package_root, app_user_model_id=app_id)

    def stop(self) -> str:
        return self._run_helper("--stop-only", None)

    def start(self, target: RestartTarget) -> str:
        return self._run_helper("--start-only", target)

    @staticmethod
    def _run_helper(mode: str, target: RestartTarget | None) -> str:
        helper = resource_path(RESTART_HELPER)
        if not helper.exists():
            raise CodexProcessError(f"缺少 Codex 重启助手：{helper}")
        command = ["cmd.exe", "/d", "/c", str(helper), mode]
        if mode == "--start-only":
            if target is None:
                raise CodexProcessError("缺少 Codex 启动目标。")
            command.extend([str(target.package_root), target.app_user_model_id])
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=75,
                creationflags=_creation_flags(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CodexProcessError(f"Codex 重启助手无法执行：{exc}") from exc
        output = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            raise CodexProcessError(f"Codex 重启助手失败：{output[-800:] or '无错误详情'}")
        return next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "完成")
