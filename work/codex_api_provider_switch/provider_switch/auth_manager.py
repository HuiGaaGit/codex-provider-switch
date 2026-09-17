from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class CodexAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class AuthStatus:
    state: str
    auth_file_exists: bool
    auth_path: Path
    detail: str = ""

    @property
    def logged_in(self) -> bool:
        return self.state in {"chatgpt", "api_key", "authenticated"}

    @property
    def official_account_logged_in(self) -> bool:
        """Only ChatGPT/account credentials unlock the OpenAI direct profile."""
        if self.state == "chatgpt":
            return True
        return self.state == "authenticated" and self.auth_file_exists
    @property
    def label(self) -> str:
        return {
            "chatgpt": "ChatGPT 官方账号已登录",
            "api_key": "检测到 OpenAI API Key（非官方账号登录态）",
            "authenticated": "OpenAI 凭据已登录",
            "signed_out": "未登录 OpenAI",
            "unavailable": "Codex CLI 不可用",
            "unknown": "登录状态无法确认",
        }.get(self.state, "登录状态无法确认")


Runner = Callable[..., Any]
PopenFactory = Callable[..., Any]


def _hidden_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _login_flags() -> int:
    return subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0


class CodexAuthManager:
    """Manage Codex authentication through the supported CLI commands."""

    def __init__(
        self,
        codex_home: Path,
        *,
        runner: Runner = subprocess.run,
        popen_factory: PopenFactory = subprocess.Popen,
        command: str | None = None,
    ) -> None:
        self.codex_home = codex_home.expanduser().resolve(strict=False)
        self.auth_path = self.codex_home / "auth.json"
        self._runner = runner
        self._popen = popen_factory
        self.command = command or shutil.which("codex") or "codex"

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        return env

    def status(self) -> AuthStatus:
        exists = self.auth_path.exists()
        try:
            result = self._runner(
                [self.command, "login", "status"],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=_hidden_flags(),
                env=self._environment(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return AuthStatus("unavailable", exists, self.auth_path)
        output = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}".casefold()
        if getattr(result, "returncode", 1) == 0 and "logged in using" in output:
            if "chatgpt" in output:
                state = "chatgpt"
            elif "api key" in output:
                state = "api_key"
            else:
                state = "authenticated"
            return AuthStatus(state, exists, self.auth_path)
        if "not logged" in output or "login required" in output or not exists:
            return AuthStatus("signed_out", exists, self.auth_path)
        return AuthStatus("unknown", exists, self.auth_path)

    def logout(self) -> AuthStatus:
        try:
            result = self._runner(
                [self.command, "logout"],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=_hidden_flags(),
                env=self._environment(),
                check=False,
            )
        except FileNotFoundError:
            result = None
        except (OSError, subprocess.SubprocessError) as exc:
            raise CodexAuthError(f"无法执行 Codex 退出登录：{exc}") from exc
        if result is not None and getattr(result, "returncode", 1) != 0:
            output = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}".casefold()
            if "not logged" not in output:
                raise CodexAuthError("Codex 未能清除当前登录态，请在终端运行 codex logout。")
        try:
            self.auth_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CodexAuthError(f"无法删除 {self.auth_path.name}：{exc}") from exc
        status = self.status()
        if status.official_account_logged_in:
            raise CodexAuthError("凭据仍由系统凭据库或工作负载身份提供，未能完全退出。")
        return status

    def login_interactive(self) -> AuthStatus:
        try:
            process = self._popen(
                [self.command, "login"],
                env=self._environment(),
                creationflags=_login_flags(),
            )
            return_code = process.wait()
        except (OSError, subprocess.SubprocessError) as exc:
            raise CodexAuthError(f"无法启动 Codex 登录：{exc}") from exc
        if return_code != 0:
            raise CodexAuthError("Codex 登录未完成，请重试并完成浏览器授权。")
        status = self.status()
        if not status.logged_in:
            raise CodexAuthError("登录进程已结束，但未检测到有效 OpenAI 登录态。")
        return status
