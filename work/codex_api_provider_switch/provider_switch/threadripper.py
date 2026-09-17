from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .constants import (
    APP_VERSION,
    THREADRIPPER_FALLBACK_URL,
    THREADRIPPER_FALLBACK_VERSION,
    THREADRIPPER_NAME,
    THREADRIPPER_RELEASE_API,
    managed_tools_directory,
)


class ThreadripperError(RuntimeError):
    pass


ProgressCallback = Callable[[str], None]


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _command_prefix(command: Path) -> list[str]:
    suffix = command.suffix.casefold()
    if suffix in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/c", str(command)]
    if suffix == ".ps1":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(command),
        ]
    return [str(command)]


def find_threadripper(managed_directory: Path | None = None) -> Path | None:
    managed = managed_directory or managed_tools_directory()
    managed_exe = managed / f"{THREADRIPPER_NAME}.exe"
    if managed_exe.exists():
        return managed_exe
    app_data = os.environ.get("APPDATA")
    if app_data:
        for extension in (".cmd", ".exe", ".ps1"):
            candidate = Path(app_data) / "npm" / f"{THREADRIPPER_NAME}{extension}"
            if candidate.exists():
                return candidate
    located = shutil.which(THREADRIPPER_NAME)
    return Path(located) if located else None


def threadripper_version(command: Path | None = None) -> str:
    executable = command or find_threadripper()
    if executable is None:
        return ""
    try:
        result = subprocess.run(
            [*_command_prefix(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_creation_flags(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (result.stdout or result.stderr).strip()
    return output if result.returncode == 0 else ""


def _download(url: str, destination: Path, progress: ProgressCallback | None = None) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"CodexProviderSwitch/{APP_VERSION}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0") or 0)
            received = 0
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                if progress and total:
                    progress(f"正在下载 Threadripper：{received * 100 // total}%")
    except (OSError, urllib.error.URLError) as exc:
        raise ThreadripperError(f"下载失败：{exc}") from exc


def _latest_assets() -> tuple[str, str | None, str]:
    request = urllib.request.Request(
        THREADRIPPER_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CodexProviderSwitch/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assets = payload.get("assets", [])
        zip_asset = next(
            item
            for item in assets
            if str(item.get("name", "")).endswith("x86_64-pc-windows-msvc.zip")
        )
        checksum_asset = next(
            (
                item
                for item in assets
                if item.get("name") == f"{zip_asset.get('name')}.sha256"
            ),
            None,
        )
        return (
            str(zip_asset["browser_download_url"]),
            str(checksum_asset["browser_download_url"]) if checksum_asset else None,
            str(payload.get("tag_name", "latest")),
        )
    except (StopIteration, KeyError, ValueError, OSError, urllib.error.URLError, json.JSONDecodeError):
        return THREADRIPPER_FALLBACK_URL, f"{THREADRIPPER_FALLBACK_URL}.sha256", f"v{THREADRIPPER_FALLBACK_VERSION}"


def install_threadripper(
    managed_directory: Path | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[Path, str]:
    if os.name != "nt":
        raise ThreadripperError("一键安装当前只支持 Windows x64。")
    target_directory = managed_directory or managed_tools_directory()
    target_directory.mkdir(parents=True, exist_ok=True)
    archive_url, checksum_url, release = _latest_assets()
    if progress:
        progress(f"准备安装 {release}")
    with tempfile.TemporaryDirectory(prefix="codex-threadripper-") as temporary_name:
        temporary = Path(temporary_name)
        archive = temporary / "threadripper.zip"
        _download(archive_url, archive, progress)
        if checksum_url:
            checksum_file = temporary / "threadripper.sha256"
            _download(checksum_url, checksum_file)
            expected = checksum_file.read_text(encoding="utf-8-sig").strip().split()[0].casefold()
            actual = hashlib.sha256(archive.read_bytes()).hexdigest().casefold()
            if not expected or expected != actual:
                raise ThreadripperError("Threadripper SHA-256 校验失败，已取消安装。")
        extract_root = temporary / "extract"
        extract_root.mkdir()
        try:
            with zipfile.ZipFile(archive) as package:
                for item in package.infolist():
                    destination = (extract_root / item.filename).resolve()
                    if extract_root.resolve() not in destination.parents and destination != extract_root.resolve():
                        raise ThreadripperError("安装包包含不安全路径。")
                package.extractall(extract_root)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ThreadripperError(f"安装包无法解压：{exc}") from exc
        source = next(extract_root.rglob(f"{THREADRIPPER_NAME}.exe"), None)
        if source is None:
            raise ThreadripperError("安装包中未找到 codex-threadripper.exe。")
        target = target_directory / f"{THREADRIPPER_NAME}.exe"
        staged = target.with_suffix(".exe.new")
        shutil.copy2(source, staged)
        os.replace(staged, target)
    version = threadripper_version(target)
    if not version:
        raise ThreadripperError("安装完成，但程序无法启动。")
    if progress:
        progress(f"安装完成：{version}")
    return target, version


def sync_history(codex_home: Path, command: Path | None = None) -> str:
    executable = command or find_threadripper()
    if executable is None:
        raise ThreadripperError("未找到 codex-threadripper，请先一键安装。")
    base = [*_command_prefix(executable), "--codex-home", str(codex_home)]
    try:
        result = subprocess.run(
            [*base, "sync"],
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=_creation_flags(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ThreadripperError(f"历史会话同步无法启动：{exc}") from exc
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise ThreadripperError(f"历史会话同步失败：{output[-600:] or '无错误详情'}")
    status = subprocess.run(
        [*base, "status"],
        capture_output=True,
        text=True,
        timeout=45,
        creationflags=_creation_flags(),
        check=False,
    )
    if status.returncode != 0:
        raise ThreadripperError("同步完成，但状态校验失败。")
    summary = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "同步完成")
    target = next(
        (line.strip() for line in status.stdout.splitlines() if line.strip().startswith("Target provider:")),
        "provider 已校验",
    )
    return f"{summary}；{target}"
