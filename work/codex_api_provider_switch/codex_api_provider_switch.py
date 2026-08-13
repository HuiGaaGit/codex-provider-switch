"""Codex Provider Switch（v1.0.16）。

只按 TOML 中的语义标识定位 cch_gz 供应商，不使用任何固定行号。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from queue import Empty, Queue
from pathlib import Path
from tkinter import messagebox, ttk


APP_NAME = "Codex Provider Switch"
APP_VERSION = "1.0.16"
CONFIG_PATH = Path.home() / ".codex" / "config.toml"
PROVIDER_KEY = "cch_gz"
THREADRIPPER_NAME = "codex-threadripper"
RESTART_HELPER_NAME = "restart-codex-app.bat"
RESTART_HELPER_TIMEOUT_SECONDS = 70
CODEX_PACKAGE_PREFIX = "OpenAI.Codex_"
CODEX_APP_EXECUTABLE = "ChatGPT.exe"


def resource_path(relative_path: str) -> Path:
    """兼容直接运行源码与 PyInstaller 单文件运行时的内置资源路径。"""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path

MODEL_PROVIDER_RE = re.compile(
    rf"^(?P<indent>\s*)(?P<comment>#\s*)?model_provider\s*=\s*['\"]{PROVIDER_KEY}['\"](?P<tail>.*)$"
)
PROVIDER_TABLE_RE = re.compile(
    rf"^(?P<indent>\s*)(?P<comment>#\s*)?\[model_providers\.{PROVIDER_KEY}\](?P<tail>.*)$"
)
PROVIDER_SETTING_RE = re.compile(
    r"^\s*(?:name|base_url|wire_api|requires_openai_auth|experimental_bearer_token)\s*="
)
PROVIDER_SETTING_NAMES = (
    "name",
    "base_url",
    "wire_api",
    "requires_openai_auth",
    "experimental_bearer_token",
)
GLOBAL_SETTING_RE = re.compile(r"^\s*(?:notify|service_tier)\s*=")


class ConfigError(RuntimeError):
    """配置文件缺少本工具需要的语义标识。"""


def _is_commented(line: str) -> bool:
    return line.lstrip().startswith("#")


def _uncomment(line: str) -> str:
    return re.sub(r"^(\s*)#\s?", r"\1", line, count=1)


def _comment(line: str) -> str:
    if _is_commented(line):
        return line
    indent = line[: len(line) - len(line.lstrip())]
    return f"{indent}#{line[len(indent):]}"


def _provider_block_assignment_indexes(lines: list[str], table_index: int) -> list[int]:
    """仅取得 cch_gz 段内明确允许切换的五个供应商字段。"""
    indexes: list[int] = []
    for index in range(table_index + 1, len(lines)):
        raw = lines[index]
        content = raw.lstrip()
        if content.startswith("#"):
            content = content[1:].lstrip()
        if not content.strip() or content.startswith("["):
            break
        # 只改供应商本身的五个键；顶层 notify、service_tier 等一律不动。
        if PROVIDER_SETTING_RE.match(content):
            indexes.append(index)
    return indexes


def _move_global_settings_before_provider(lines: list[str], table_index: int) -> list[str]:
    """修复旧布局：TOML 表开始后不能再回到顶层，需把顶层设置前移一次。"""
    global_indexes: list[int] = []
    for index in range(table_index + 1, len(lines)):
        content = lines[index].lstrip()
        if content.startswith("["):
            break
        if not content.startswith("#") and GLOBAL_SETTING_RE.match(content):
            global_indexes.append(index)
    if not global_indexes:
        return lines
    global_lines = [lines[index] for index in global_indexes]
    for index in reversed(global_indexes):
        lines.pop(index)
    # 保留原文本和值；只在首次修复时调整位置，之后不会再移动。
    lines[table_index:table_index] = global_lines
    return lines


def inspect_config(text: str) -> tuple[str, int, int]:
    """返回状态及语义标识位置；关闭只取消默认使用，不注销历史供应商。"""
    lines = text.splitlines(keepends=True)
    model_indexes = [i for i, line in enumerate(lines) if MODEL_PROVIDER_RE.match(line.rstrip("\r\n"))]
    table_indexes = [i for i, line in enumerate(lines) if PROVIDER_TABLE_RE.match(line.rstrip("\r\n"))]
    if len(model_indexes) != 1 or len(table_indexes) != 1:
        raise ConfigError(
            f"需要且只能找到一条 model_provider = \"{PROVIDER_KEY}\" 和一个 "
            f"[model_providers.{PROVIDER_KEY}] 配置段。"
        )
    model_active = not _is_commented(lines[model_indexes[0]])
    table_active = not _is_commented(lines[table_indexes[0]])
    setting_indexes = _provider_block_assignment_indexes(lines, table_indexes[0])
    registration_active = (
        table_active
        and len(setting_indexes) == len(PROVIDER_SETTING_NAMES)
        and all(not _is_commented(lines[index]) for index in setting_indexes)
    )
    if model_active and registration_active:
        status = "enabled"
    elif not model_active and registration_active:
        status = "disabled"
    else:
        status = "partial"
    return status, model_indexes[0], table_indexes[0]


def transform_config(text: str, enable: bool) -> str:
    """切换默认供应商；始终保留 cch_gz 注册，确保历史会话能够重新加载。"""
    lines = text.splitlines(keepends=True)
    _, model_index, table_index = inspect_config(text)
    setting_indexes = _provider_block_assignment_indexes(lines, table_index)
    if len(setting_indexes) != len(PROVIDER_SETTING_NAMES):
        raise ConfigError("cch_gz 段必须包含完整的五个供应商字段，无法安全修复。")
    lines[model_index] = _uncomment(lines[model_index]) if enable else _comment(lines[model_index])
    # 历史会话会保存 provider key。关闭仅表示不再作为默认模型，不能注销该 key。
    lines[table_index] = _uncomment(lines[table_index])
    for index in setting_indexes:
        lines[index] = _uncomment(lines[index])
    return "".join(_move_global_settings_before_provider(lines, table_index))


def _read_config() -> tuple[str, str]:
    raw = CONFIG_PATH.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    return raw.decode(encoding), encoding


def _validate_toml(text: str) -> None:
    # Python 3.11+ 内置解析器；在实际覆盖前阻止无效 TOML 写入。
    import tomllib

    tomllib.loads(text)


def _validate_cch_registration(text: str) -> None:
    """保证已同步到 cch_gz 的旧会话总能找到对应的供应商配置。"""
    import tomllib

    provider = tomllib.loads(text).get("model_providers", {}).get(PROVIDER_KEY)
    if not isinstance(provider, dict):
        raise ConfigError(f"配置未注册 {PROVIDER_KEY}，历史会话无法加载。")
    missing = [name for name in PROVIDER_SETTING_NAMES if name not in provider]
    if missing:
        raise ConfigError(f"{PROVIDER_KEY} 缺少供应商字段：{', '.join(missing)}")


def _validate_global_settings(text: str) -> None:
    """避免 notify、service_tier 因表作用域而被误读为供应商字段。"""
    import tomllib

    parsed = tomllib.loads(text)
    for name in ("notify", "service_tier"):
        if any(GLOBAL_SETTING_RE.match(line) and line.lstrip().startswith(name) for line in text.splitlines()):
            if name not in parsed:
                raise ConfigError(f"{name} 被错误放入 {PROVIDER_KEY} 段，必须恢复为顶层设置。")


def update_config(enable: bool) -> str:
    """安全写入配置并返回切换后的状态。"""
    if not CONFIG_PATH.exists():
        raise ConfigError(f"未找到配置文件：{CONFIG_PATH}")
    original, encoding = _read_config()
    current, _, _ = inspect_config(original)
    desired = "enabled" if enable else "disabled"
    updated = transform_config(original, enable)
    if updated == original:
        return current
    _validate_toml(updated)
    _validate_cch_registration(updated)
    _validate_global_settings(updated)
    temporary = CONFIG_PATH.with_name("config.toml.api-provider-switch.tmp")
    temporary.write_text(updated, encoding=encoding, newline="")
    os.replace(temporary, CONFIG_PATH)
    return desired


def _threadripper_command() -> Path | None:
    """优先定位用户通过 npm 安装的 threadripper，再回退到 PATH。"""
    app_data = os.environ.get("APPDATA")
    if app_data:
        npm_command = Path(app_data) / "npm" / f"{THREADRIPPER_NAME}.cmd"
        if npm_command.exists():
            return npm_command
    command_on_path = shutil.which(THREADRIPPER_NAME)
    return Path(command_on_path) if command_on_path else None


def sync_history() -> str:
    """同步真实 Codex 主目录的会话，避免沙箱 HOME 指向错误目录。"""
    command = _threadripper_command()
    if command is None:
        raise RuntimeError("未找到 codex-threadripper，无法同步历史会话。")
    text, _ = _read_config()
    _validate_toml(text)
    _validate_cch_registration(text)
    _validate_global_settings(text)
    base_command = [str(command), "--codex-home", str(CONFIG_PATH.parent)]
    result = subprocess.run(
        [*base_command, "sync"],
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(f"历史会话同步失败：{output[-800:] or '未返回错误详情'}")
    status = subprocess.run(
        [*base_command, "status"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if status.returncode != 0:
        raise RuntimeError(f"同步已完成，但无法校验实际会话库：{(status.stdout or status.stderr).strip()[-800:]}")
    summary = next((line for line in reversed(output.splitlines()) if line.strip()), "同步完成")
    target = next((line.strip() for line in status.stdout.splitlines() if line.strip().startswith("Target provider:")), "目标供应商已校验")
    return f"{summary}；{target}"


def locate_restart_target() -> tuple[Path, str]:
    """在 Codex 仍运行时记录安装包根路径，供停止后的重新启动使用。"""
    script = (
        "Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.Path } | "
        f"Where-Object {{ $_ -like '*\\{CODEX_PACKAGE_PREFIX}*\\app\\{CODEX_APP_EXECUTABLE}' }} | "
        "Select-Object -First 1"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    executable = Path(result.stdout.strip()) if result.stdout.strip() else None
    if executable is None:
        raise RuntimeError("未找到正在运行的 Codex 桌面端，无法记录重新启动入口。")
    package_root = executable.parent.parent
    package_name = package_root.name
    if "__" not in package_name:
        raise RuntimeError(f"无法识别 Codex 安装包目录：{package_root}")
    return package_root, f"{package_name.split('_', 1)[0]}_{package_name.rsplit('__', 1)[1]}"


def run_restart_helper(mode: str, restart_target: tuple[Path, str] | None = None) -> str:
    """调用独立批处理助手，按完整 Codex 进程树停止或启动桌面端。"""
    if mode not in {"--stop-only", "--start-only"}:
        raise ValueError(f"不支持的重启助手模式：{mode}")
    helper = resource_path(f"assets/{RESTART_HELPER_NAME}")
    if not helper.exists():
        raise RuntimeError(f"未找到内置 Codex 重启助手：{helper}")
    command = ["cmd.exe", "/d", "/c", str(helper), mode]
    if mode == "--start-only":
        if restart_target is None:
            raise RuntimeError("缺少 Codex 重新启动入口。")
        command.extend([str(restart_target[0]), restart_target[1]])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=RESTART_HELPER_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or exc.stderr or "").strip()
        raise RuntimeError(f"Codex 重启助手超时：{output[-800:]}") from exc
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(f"Codex 重启助手失败：{output[-1200:] or '未返回错误详情'}")
    return next((line for line in reversed(output.splitlines()) if line.strip()), "重启助手已完成")


class SwitchApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.iconbitmap(default=str(resource_path("assets/codex_api_provider_switch_icon.ico")))
        self.resizable(False, False)
        self.configure(padx=28, pady=24, bg="#f7f8fa")
        self.status_var = tk.StringVar()
        self.history_var = tk.StringVar()
        self.detail_var = tk.StringVar(value=f"配置文件：{CONFIG_PATH}")
        self.event_queue: Queue[tuple[str, str]] = Queue()

        ttk.Style(self).configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        ttk.Style(self).configure("Status.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        ttk.Style(self).configure("Note.TLabel", foreground="#5d6470")
        ttk.Label(self, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self, text=f"v{APP_VERSION} · 供应商切换与历史会话同步", style="Note.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 18))
        self.status_label = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.grid(row=2, column=0, sticky="w")
        choices = ttk.Frame(self)
        choices.grid(row=3, column=0, sticky="ew", pady=(16, 12))
        choices.columnconfigure((0, 1), weight=1)
        self.enable_button = ttk.Button(choices, text="打开Pro 20x", command=lambda: self.confirm_toggle(True))
        self.enable_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), ipady=7)
        self.disable_button = ttk.Button(choices, text="打开Plus", command=lambda: self.confirm_toggle(False))
        self.disable_button.grid(row=0, column=1, sticky="ew", padx=(6, 0), ipady=7)
        self.sync_button = ttk.Button(self, text="仅同步历史会话（当前供应商）", command=self.confirm_history_sync)
        self.sync_button.grid(row=4, column=0, sticky="ew", ipady=6)
        ttk.Label(self, text="操作进度", style="Note.TLabel").grid(row=5, column=0, sticky="w", pady=(12, 3))
        self.progress_text = tk.Text(self, height=6, width=56, wrap="word", relief="solid", borderwidth=1, bg="#ffffff", fg="#343a40")
        self.progress_text.grid(row=6, column=0, sticky="ew")
        self.progress_text.configure(state="disabled")
        ttk.Label(self, textvariable=self.history_var, style="Note.TLabel", wraplength=440).grid(row=7, column=0, sticky="w", pady=(10, 0))
        ttk.Label(self, textvariable=self.detail_var, style="Note.TLabel", wraplength=440).grid(row=8, column=0, sticky="w", pady=(8, 0))
        ttk.Label(self, text="关闭只取消默认使用，不注销 cch_gz，确保历史会话可打开。会话同步由 Threadripper 在 .codex\\backups 中自行创建保护备份。", style="Note.TLabel", wraplength=440).grid(row=9, column=0, sticky="w", pady=(14, 0))
        self.refresh()
        self.after(100, self._drain_events)

    def _append_progress(self, message: str) -> None:
        self.progress_text.configure(state="normal")
        self.progress_text.insert("end", f"{message}\n")
        self.progress_text.see("end")
        self.progress_text.configure(state="disabled")

    def _clear_progress(self) -> None:
        self.progress_text.configure(state="normal")
        self.progress_text.delete("1.0", "end")
        self.progress_text.configure(state="disabled")

    def _publish(self, kind: str, message: str) -> None:
        self.event_queue.put((kind, message))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, message = self.event_queue.get_nowait()
                if kind == "stage":
                    self.status_var.set(message)
                    self._append_progress(message)
                elif kind == "done":
                    self._done(message)
                elif kind == "failed":
                    self._failed(message)
        except Empty:
            pass
        self.after(100, self._drain_events)

    def refresh(self) -> None:
        try:
            # 操作完成后恢复两个供应商入口；配置异常时才统一禁用。
            self.enable_button.configure(state="normal")
            self.disable_button.configure(state="normal")
            self.sync_button.configure(state="normal" if _threadripper_command() else "disabled")
            self.history_var.set(
                "历史会话同步：codex-threadripper 已就绪" if _threadripper_command() else "历史会话同步：未找到 codex-threadripper"
            )
            text, _ = _read_config()
            status, _, _ = inspect_config(text)
            if status == "enabled":
                self.status_var.set("当前状态：已启用Pro 20x")
                self.status_label.configure(foreground="#16794c")
            elif status == "disabled":
                self.status_var.set("当前状态：已启用Plus")
                self.status_label.configure(foreground="#8a4b00")
            else:
                self.status_var.set("当前状态：配置不完整，请点击打开或关闭以修复")
                self.status_label.configure(foreground="#a33131")
        except Exception as exc:
            self.status_var.set("无法识别配置")
            self.status_label.configure(foreground="#a33131")
            self.enable_button.configure(state="disabled")
            self.disable_button.configure(state="disabled")
            self.sync_button.configure(state="disabled")
            self.detail_var.set(str(exc))

    def confirm_toggle(self, want_enable: bool) -> None:
        action = "打开 Pro 20x" if want_enable else "打开 Plus"
        message = f"确认{action}吗？\n\n将修改 config.toml，并自动重启 Codex。"
        if not want_enable:
            message += "\n\n关闭只取消默认使用；为保证已同步的历史会话可打开，cch_gz 注册配置会保留。"
        if not messagebox.askyesno(APP_NAME, message):
            return
        self._start_operation(f"准备{action}…")
        threading.Thread(target=self._apply, args=(want_enable,), daemon=True).start()

    def _apply(self, want_enable: bool) -> None:
        try:
            self._publish("stage", "1/4 正在关闭 Codex（独立重启助手）…")
            restart_target = locate_restart_target()
            stop_summary = run_restart_helper("--stop-only")
            self._publish("stage", f"1/4 Codex 已完全关闭：{stop_summary}")
            self._publish("stage", "2/4 正在写入供应商设置…")
            state = update_config(want_enable)
            self._publish("stage", f"2/4 配置写入完成：供应商已{'启用' if state == 'enabled' else '关闭'}。")
            self._sync_and_start(restart_target)
        except Exception as exc:
            self._publish("failed", str(exc))

    def confirm_history_sync(self) -> None:
        if not messagebox.askyesno(APP_NAME, "确认同步全部历史会话到当前供应商吗？\n\n同步期间 Codex 会自动重启。"):
            return
        self._start_operation("准备同步历史会话…")
        threading.Thread(target=self._sync_only, daemon=True).start()

    def _sync_only(self) -> None:
        try:
            self._publish("stage", "1/3 正在关闭 Codex（独立重启助手）…")
            restart_target = locate_restart_target()
            stop_summary = run_restart_helper("--stop-only")
            self._publish("stage", f"1/3 Codex 已完全关闭：{stop_summary}")
            self._sync_and_start(restart_target, sync_step="2/3", start_step="3/3")
        except Exception as exc:
            self._publish("failed", str(exc))

    def _sync_and_start(self, restart_target: tuple[Path, str], sync_step: str = "3/4", start_step: str = "4/4") -> None:
        self._publish("stage", f"{sync_step} 正在同步历史会话…")
        history_summary = sync_history()
        self._publish("stage", f"{sync_step} 历史会话同步完成。")
        self._publish("stage", f"{start_step} 正在通过 Windows 应用入口启动 Codex…")
        start_summary = run_restart_helper("--start-only", restart_target)
        self._publish("done", f"操作完成：Codex 已重新启动。\n{start_summary}\n历史会话：{history_summary}")

    def _start_operation(self, initial_status: str) -> None:
        self.enable_button.configure(state="disabled")
        self.disable_button.configure(state="disabled")
        self.sync_button.configure(state="disabled")
        self._clear_progress()
        self.status_var.set(initial_status)
        self._append_progress(initial_status)

    def _done(self, detail: str) -> None:
        self.detail_var.set(detail)
        self.enable_button.configure(state="normal")
        self.disable_button.configure(state="normal")
        self.sync_button.configure(state="normal" if _threadripper_command() else "disabled")
        self.refresh()
        messagebox.showinfo(APP_NAME, "操作完成，Codex 已重新启动。")

    def _failed(self, detail: str) -> None:
        self._append_progress(f"操作失败：{detail}")
        self.detail_var.set(detail)
        self.enable_button.configure(state="normal")
        self.disable_button.configure(state="normal")
        self.sync_button.configure(state="normal" if _threadripper_command() else "disabled")
        self.refresh()
        messagebox.showerror(APP_NAME, f"未能完成切换：\n{detail}")


if __name__ == "__main__":
    SwitchApp().mainloop()
