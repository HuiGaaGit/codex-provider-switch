from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .auth_manager import AuthStatus, CodexAuthError
from .catalog import CatalogError, resource_path, validate_catalog_text
from .config_manager import ConfigManager, ConfigSnapshot, discover_codex_homes
from .constants import APP_NAME, APP_VERSION, COLORS
from .controller import ApplicationController, OperationResult
from .models import ProviderProfile, ProviderTelemetry, TokenUsage
from .monitoring import check_provider
from .settings import PROVIDER_KEY_RE, SettingsError, validate_settings
from .threadripper import install_threadripper


FONT = "Microsoft YaHei UI"
PROFILE_ORDER = ("openai", "relay1", "relay2", "glm")
PROFILE_COLORS = {
    "openai": COLORS["openai"],
    "relay1": COLORS["relay1"],
    "relay2": COLORS["relay2"],
    "glm": COLORS["glm"],
}


def format_tokens(value: int) -> str:
    millions = value / 1_000_000
    if millions >= 100:
        return f"{millions:,.0f}M"
    return f"{millions:,.1f}M"


def make_button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    *,
    primary: bool = False,
    danger: bool = False,
    width: int = 0,
) -> tk.Button:
    background = COLORS["accent"] if primary else COLORS["surface_alt"]
    foreground = "#ffffff" if primary else COLORS["text"]
    if danger:
        background, foreground = COLORS["danger"], "#ffffff"
    return tk.Button(
        parent,
        text=text,
        command=command,
        width=width,
        relief="flat",
        bd=0,
        highlightthickness=0,
        background=background,
        foreground=foreground,
        activebackground=COLORS["accent_hover"] if primary else COLORS["border"],
        activeforeground=foreground,
        disabledforeground="#a3a9ad",
        font=(FONT, 10, "bold" if primary else "normal"),
        cursor="hand2",
        padx=14,
        pady=8,
    )


class ProviderCard(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        profile_id: str,
        profile: ProviderProfile,
        on_switch: Callable[[str], None],
    ) -> None:
        super().__init__(
            parent,
            width=390,
            height=188,
            background=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        self.grid_propagate(False)
        self.profile_id = profile_id
        self.profile = profile
        self.columnconfigure(1, weight=1)
        self.accent = tk.Frame(self, width=5, background=PROFILE_COLORS[profile_id])
        self.accent.grid(row=0, column=0, rowspan=5, sticky="ns")
        self.name_var = tk.StringVar(value=profile.display_name)
        self.kind_var = tk.StringVar()
        self.health_var = tk.StringVar(value="等待检测")
        self.quota_var = tk.StringVar(value="额度：等待刷新")
        self.usage_var = tk.StringVar(value="近 30 天 Token：--")
        tk.Label(
            self,
            textvariable=self.name_var,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=(FONT, 13, "bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(16, 10), pady=(14, 1))
        self.state_label = tk.Label(
            self,
            textvariable=self.kind_var,
            background=COLORS["surface_alt"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            padx=8,
            pady=3,
        )
        self.state_label.grid(row=0, column=2, sticky="e", padx=(0, 14), pady=(14, 1))
        self.health_label = tk.Label(
            self,
            textvariable=self.health_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
            wraplength=330,
        )
        self.health_label.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(16, 14), pady=(10, 1))
        tk.Label(
            self,
            textvariable=self.quota_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(16, 14), pady=1)
        tk.Label(
            self,
            textvariable=self.usage_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=3, column=1, sticky="ew", padx=(16, 8), pady=(1, 12))
        self.switch_button = make_button(
            self, "切换", lambda: on_switch(self.profile_id), primary=True, width=7
        )
        self.switch_button.grid(row=3, column=2, sticky="e", padx=(0, 14), pady=(1, 12))

    def set_profile(self, profile: ProviderProfile) -> None:
        self.profile = profile
        self.name_var.set(profile.display_name)

    def set_active(self, active: bool, provider_key: str = "") -> None:
        self.kind_var.set(f"当前 · {provider_key}" if active else "可切换")
        self.state_label.configure(
            background=PROFILE_COLORS[self.profile_id] if active else COLORS["surface_alt"],
            foreground="#ffffff" if active else COLORS["muted"],
        )
        self.switch_button.configure(text="当前使用" if active else "切换", state="disabled" if active else "normal")

    def set_available(
        self,
        available: bool,
        *,
        active: bool = False,
        unavailable_text: str = "需登录",
    ) -> None:
        if available:
            return
        self.kind_var.set(f"当前 · {unavailable_text}" if active else unavailable_text)
        self.state_label.configure(background=COLORS["surface_alt"], foreground=COLORS["warning"])
        self.switch_button.configure(
            text=unavailable_text,
            state="disabled",
        )

    def set_telemetry(self, telemetry: ProviderTelemetry | None) -> None:
        if telemetry is None:
            self.health_var.set("等待检测")
            self.health_label.configure(foreground=COLORS["muted"])
            return
        health = telemetry.health
        latency = f" · {health.latency_ms} ms" if health.latency_ms is not None else ""
        self.health_var.set(f"{health.message}{latency} · {health.checked_at}")
        health_color = {
            "healthy": COLORS["success"],
            "warning": COLORS["warning"],
            "auth_error": COLORS["danger"],
            "offline": COLORS["danger"],
        }.get(health.state, COLORS["muted"])
        self.health_label.configure(foreground=health_color)
        if telemetry.quota:
            parts = []
            for item in telemetry.quota[:2]:
                if item.remaining is not None and item.unit == "%":
                    parts.append(f"{item.label}剩余 {item.remaining:.0f}%")
                elif item.remaining is not None:
                    parts.append(f"{item.label} {item.remaining:g}")
            self.quota_var.set("额度：" + " · ".join(parts))
        else:
            self.quota_var.set(f"额度：{telemetry.quota_message}")

    def set_usage(self, usage: TokenUsage | None, days: int, active: bool) -> None:
        if not active:
            self.usage_var.set(f"近 {days} 天 Token：切换后显示")
        elif usage is None:
            self.usage_var.set(f"近 {days} 天 Token：暂无记录")
        else:
            self.usage_var.set(
                f"近 {days} 天 Token：{format_tokens(usage.total_tokens)} · {usage.sessions} 个会话"
            )


class ProviderSwitchApp(tk.Tk):
    def __init__(
        self,
        controller: ApplicationController | None = None,
        *,
        start_monitor: bool = True,
        show_wizard: bool = True,
    ) -> None:
        super().__init__()
        self.controller = controller or ApplicationController()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        icon = resource_path("assets/codex_api_provider_switch_icon.ico")
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        self.geometry("1120x720")
        self.minsize(980, 650)
        self.configure(background=COLORS["window"])
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.telemetry_data: dict[str, ProviderTelemetry] = {}
        self.usage_data: dict[str, TokenUsage] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.pages: dict[str, tk.Frame] = {}
        self.provider_cards: dict[str, ProviderCard] = {}
        self.auth_status_data = AuthStatus(
            "unknown", False, self.controller.codex_home / "auth.json"
        )
        self._auth_running = False
        self._monitor_enabled = start_monitor
        self._setup_styles()
        self._build_shell()
        self._build_dashboard()
        self._build_providers_page()
        self._build_catalog_page()
        self._build_tools_page()
        self._build_deployment_page()
        self._build_settings_page()
        self.show_page("dashboard")
        self.refresh_all(local_only=True)
        self.after(100, self._drain_events)
        if start_monitor:
            self.after(700, self.refresh_monitoring)
        self.after(350, self.refresh_auth_status)
        if show_wizard and not self.controller.settings.setup_complete:
            self.after(250, self.open_setup_wizard)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "App.TEntry",
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            padding=7,
            font=(FONT, 10),
        )
        style.configure(
            "App.TCombobox",
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            padding=6,
            font=(FONT, 10),
        )
        style.configure(
            "App.TCheckbutton",
            background=COLORS["window"],
            foreground=COLORS["text"],
            font=(FONT, 10),
        )
        style.map("App.TCheckbutton", background=[("active", COLORS["window"])])

    def _build_shell(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        sidebar = tk.Frame(self, width=198, background=COLORS["sidebar"])
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        tk.Label(
            sidebar,
            text="CODEX",
            background=COLORS["sidebar"],
            foreground="#ffffff",
            font=("Segoe UI", 17, "bold"),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(25, 0))
        tk.Label(
            sidebar,
            text=f"Provider Switch  {APP_VERSION}",
            background=COLORS["sidebar"],
            foreground="#9da6ad",
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(1, 26))
        for page_id, label in (
            ("dashboard", "总览"),
            ("providers", "供应商"),
            ("catalog", "模型目录"),
            ("tools", "工具"),
            ("deployment", "部署"),
            ("settings", "设置"),
        ):
            button = tk.Button(
                sidebar,
                text=label,
                command=lambda selected=page_id: self.show_page(selected),
                anchor="w",
                relief="flat",
                bd=0,
                highlightthickness=0,
                background=COLORS["sidebar"],
                foreground="#dfe4e7",
                activebackground=COLORS["sidebar_hover"],
                activeforeground="#ffffff",
                font=(FONT, 11),
                padx=22,
                pady=11,
                cursor="hand2",
            )
            button.pack(fill="x", padx=9, pady=2)
            self.nav_buttons[page_id] = button
        tk.Label(
            sidebar,
            text="密钥仅保存在本机",
            background=COLORS["sidebar"],
            foreground="#7f8a91",
            font=(FONT, 8),
        ).pack(side="bottom", pady=18)
        right = tk.Frame(self, background=COLORS["window"])
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        self.page_host = tk.Frame(right, background=COLORS["window"])
        self.page_host.grid(row=0, column=0, sticky="nsew")
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.footer_var = tk.StringVar(value="就绪")
        footer = tk.Label(
            right,
            textvariable=self.footer_var,
            anchor="w",
            background="#e3e7ea",
            foreground=COLORS["muted"],
            font=(FONT, 9),
            padx=18,
            pady=7,
        )
        footer.grid(row=1, column=0, sticky="ew")

    def _new_page(self, page_id: str) -> tk.Frame:
        page = tk.Frame(self.page_host, background=COLORS["window"], padx=28, pady=22)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        self.pages[page_id] = page
        return page

    def _page_header(self, page: tk.Frame, title: str, subtitle: str) -> tk.Frame:
        header = tk.Frame(page, background=COLORS["window"])
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text=title,
            background=COLORS["window"],
            foreground=COLORS["text"],
            font=(FONT, 20, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=subtitle,
            background=COLORS["window"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        return header

    def show_page(self, page_id: str) -> None:
        self.pages[page_id].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(
                background=COLORS["sidebar_active"] if key == page_id else COLORS["sidebar"],
                foreground="#ffffff" if key == page_id else "#dfe4e7",
            )
        if page_id == "catalog":
            self._load_catalog_editor_if_needed()
        elif page_id == "tools":
            self.refresh_tools()
        elif page_id == "deployment":
            self.refresh_deployment_view()
            self.refresh_auth_status()

    def _build_dashboard(self) -> None:
        page = self._new_page("dashboard")
        header = self._page_header(page, "供应商总览", "一键切换、链路健康、额度与本机 Token 使用情况")
        self.dashboard_refresh_button = make_button(header, "立即刷新", self.refresh_monitoring)
        self.dashboard_refresh_button.grid(row=0, column=1, rowspan=2, sticky="e")
        route = tk.Frame(page, background=COLORS["surface"], padx=18, pady=14)
        route.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        route.columnconfigure(1, weight=1)
        tk.Label(
            route,
            text="当前路由",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
        ).grid(row=0, column=0, sticky="w")
        self.active_route_var = tk.StringVar(value="读取中")
        tk.Label(
            route,
            textvariable=self.active_route_var,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=(FONT, 14, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.route_detail_var = tk.StringVar()
        tk.Label(
            route,
            textvariable=self.route_detail_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="e",
        ).grid(row=0, column=1, rowspan=2, sticky="e")
        cards = tk.Frame(page, background=COLORS["window"])
        cards.grid(row=2, column=0, sticky="nsew")
        page.grid_rowconfigure(2, weight=1)
        cards.columnconfigure((0, 1), weight=1, uniform="provider")
        cards.rowconfigure((0, 1), weight=1, uniform="provider")
        for index, profile_id in enumerate(PROFILE_ORDER):
            card = ProviderCard(
                cards,
                profile_id,
                self.controller.settings.profiles[profile_id],
                self.confirm_switch,
            )
            card.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(0, 8) if index % 2 == 0 else (8, 0),
                pady=(0, 8) if index < 2 else (8, 0),
            )
            self.provider_cards[profile_id] = card

    def _build_providers_page(self) -> None:
        page = self._new_page("providers")
        self._page_header(page, "供应商", "维护显示名称、API 地址、模型、密钥与可选额度接口")
        tabs = tk.Frame(page, background=COLORS["window"])
        tabs.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.profile_tab_buttons: dict[str, tk.Button] = {}
        for profile_id in PROFILE_ORDER:
            button = make_button(
                tabs,
                self.controller.settings.profiles[profile_id].display_name,
                lambda selected=profile_id: self.select_profile_editor(selected),
            )
            button.pack(side="left", padx=(0, 8))
            self.profile_tab_buttons[profile_id] = button
        form = tk.Frame(page, background=COLORS["surface"], padx=22, pady=18)
        form.grid(row=2, column=0, sticky="nsew")
        page.grid_rowconfigure(2, weight=1)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        self.provider_form_vars = {
            "display_name": tk.StringVar(),
            "provider_key": tk.StringVar(),
            "base_url": tk.StringVar(),
            "model": tk.StringVar(),
            "api_key": tk.StringVar(),
            "quota_url": tk.StringVar(),
            "quota_remaining_path": tk.StringVar(),
            "quota_total_path": tk.StringVar(),
            "quota_organization_id": tk.StringVar(),
            "quota_project_id": tk.StringVar(),
            "requires_openai_auth": tk.BooleanVar(),
            "enabled": tk.BooleanVar(value=True),
            "reveal": tk.BooleanVar(value=False),
        }
        self.provider_entries: dict[str, ttk.Entry] = {}
        fields = (
            ("display_name", "显示名称", 0, 0),
            ("provider_key", "provider 标签", 0, 2),
            ("base_url", "API Base URL", 1, 0),
            ("model", "默认模型", 1, 2),
            ("api_key", "API Key", 2, 0),
            ("quota_url", "额度查询 URL", 3, 0),
            ("quota_remaining_path", "剩余额度字段路径", 4, 0),
            ("quota_total_path", "总额度字段路径", 4, 2),
            ("quota_organization_id", "GLM 团队组织 ID", 5, 0),
            ("quota_project_id", "GLM 团队项目 ID", 5, 2),
        )
        for name, label, row, column in fields:
            tk.Label(
                form,
                text=label,
                background=COLORS["surface"],
                foreground=COLORS["muted"],
                font=(FONT, 9),
                anchor="w",
            ).grid(row=row * 2, column=column, columnspan=2, sticky="w", pady=(6, 3))
            entry = ttk.Entry(form, textvariable=self.provider_form_vars[name], style="App.TEntry")
            entry.grid(
                row=row * 2 + 1,
                column=column,
                columnspan=2,
                sticky="ew",
                padx=(0, 14) if column == 0 else (0, 0),
                pady=(0, 6),
            )
            self.provider_entries[name] = entry
        self.provider_entries["api_key"].configure(show="*")
        options = tk.Frame(form, background=COLORS["surface"])
        options.grid(row=10, column=0, columnspan=4, sticky="ew", pady=(6, 12))
        ttk.Checkbutton(
            options,
            text="显示 API Key",
            variable=self.provider_form_vars["reveal"],
            command=self._toggle_key_visibility,
            style="App.TCheckbutton",
        ).pack(side="left")
        self.provider_auth_check = ttk.Checkbutton(
            options,
            text="requires_openai_auth",
            variable=self.provider_form_vars["requires_openai_auth"],
            style="App.TCheckbutton",
        )
        self.provider_auth_check.pack(side="left", padx=(18, 0))
        ttk.Checkbutton(
            options,
            text="启用监控",
            variable=self.provider_form_vars["enabled"],
            style="App.TCheckbutton",
        ).pack(side="left", padx=(18, 0))
        actions = tk.Frame(form, background=COLORS["surface"])
        actions.grid(row=11, column=0, columnspan=4, sticky="e")
        self.provider_test_button = make_button(actions, "检测当前", self.test_selected_profile)
        self.provider_test_button.pack(side="left", padx=(0, 8))
        self.provider_save_button = make_button(actions, "保存", self.save_profile_form, primary=True)
        self.provider_save_button.pack(side="left")
        self.provider_form_status = tk.StringVar(value="")
        tk.Label(
            form,
            textvariable=self.provider_form_status,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=12, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        self.selected_profile_id = "relay1"
        self.select_profile_editor("relay1")

    def _toggle_key_visibility(self) -> None:
        self.provider_entries["api_key"].configure(
            show="" if self.provider_form_vars["reveal"].get() else "*"
        )

    def select_profile_editor(self, profile_id: str) -> None:
        self.selected_profile_id = profile_id
        profile = self.controller.settings.profiles[profile_id]
        values = profile.to_dict()
        for name in (
            "display_name",
            "provider_key",
            "base_url",
            "model",
            "quota_url",
            "quota_remaining_path",
            "quota_total_path",
            "quota_organization_id",
            "quota_project_id",
        ):
            self.provider_form_vars[name].set(values.get(name, ""))
        self.provider_form_vars["api_key"].set(self.controller.credentials.get(profile_id, ""))
        self.provider_form_vars["requires_openai_auth"].set(profile.requires_openai_auth)
        self.provider_form_vars["enabled"].set(profile.enabled)
        self.provider_form_vars["reveal"].set(False)
        self._toggle_key_visibility()
        official = profile.kind == "official"
        for name in (
            "provider_key", "base_url", "api_key", "quota_url",
            "quota_remaining_path", "quota_total_path",
            "quota_organization_id", "quota_project_id",
        ):
            self.provider_entries[name].configure(state="disabled" if official else "normal")
        team_visible = profile.kind == "glm"
        state = "normal" if team_visible else "disabled"
        self.provider_entries["quota_organization_id"].configure(state=state)
        self.provider_entries["quota_project_id"].configure(state=state)
        self.provider_auth_check.configure(state="disabled")
        self.provider_form_status.set("OpenAI 直连使用 Codex 当前登录。" if official else "密钥使用 Windows DPAPI 加密保存在本机。")
        for key, button in self.profile_tab_buttons.items():
            button.configure(
                background=PROFILE_COLORS[key] if key == profile_id else COLORS["surface_alt"],
                foreground="#ffffff" if key == profile_id else COLORS["text"],
            )

    def save_profile_form(self) -> bool:
        profile = self.controller.settings.profiles[self.selected_profile_id]
        profile.display_name = self.provider_form_vars["display_name"].get()
        profile.model = self.provider_form_vars["model"].get()
        if profile.kind != "official":
            profile.provider_key = self.provider_form_vars["provider_key"].get()
            profile.base_url = self.provider_form_vars["base_url"].get()
            profile.quota_url = self.provider_form_vars["quota_url"].get()
            profile.quota_remaining_path = self.provider_form_vars["quota_remaining_path"].get()
            profile.quota_total_path = self.provider_form_vars["quota_total_path"].get()
            profile.quota_organization_id = self.provider_form_vars["quota_organization_id"].get().strip()
            profile.quota_project_id = self.provider_form_vars["quota_project_id"].get().strip()
            profile.requires_openai_auth = self.provider_form_vars["requires_openai_auth"].get()
            self.controller.credentials[self.selected_profile_id] = self.provider_form_vars["api_key"].get().strip()
        profile.enabled = self.provider_form_vars["enabled"].get()
        try:
            self.controller.save()
        except (SettingsError, OSError) as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return False
        self.provider_form_status.set("已保存")
        self.refresh_all(local_only=True)
        return True

    def test_selected_profile(self) -> None:
        if not self.save_profile_form():
            return
        profile_id = self.selected_profile_id
        profile = self.controller.settings.profiles[profile_id]
        key = self.controller.credentials.get(profile_id, "")
        self.provider_form_status.set("正在检测链路与额度…")
        threading.Thread(
            target=lambda: self.event_queue.put(
                (
                    "profile_test",
                    check_provider(
                        profile,
                        key,
                        codex_home=self.controller.codex_home,
                        retain_official_auth=self.controller.settings.retain_official_auth,
                        auth_status=self.auth_status_data,
                    ),
                )
            ),
            daemon=True,
        ).start()

    def _build_catalog_page(self) -> None:
        page = self._new_page("catalog")
        header = self._page_header(page, "模型目录", "编辑并注入当前 Codex Home 的 models.json")
        actions = tk.Frame(header, background=COLORS["window"])
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        make_button(actions, "读取本机", self.load_catalog_from_disk).pack(side="left", padx=(0, 6))
        make_button(actions, "恢复内置", self.load_bundled_catalog).pack(side="left", padx=(0, 6))
        make_button(actions, "校验", self.validate_catalog_editor).pack(side="left", padx=(0, 6))
        make_button(actions, "保存并注入", self.save_catalog_editor, primary=True).pack(side="left")
        editor_frame = tk.Frame(
            page,
            background=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        editor_frame.grid(row=1, column=0, sticky="nsew")
        page.grid_rowconfigure(1, weight=1)
        editor_frame.grid_columnconfigure(0, weight=1)
        editor_frame.grid_rowconfigure(0, weight=1)
        self.catalog_text = tk.Text(
            editor_frame,
            wrap="none",
            undo=True,
            maxundo=100,
            background="#fbfcfd",
            foreground=COLORS["text"],
            insertbackground=COLORS["accent"],
            selectbackground="#cfe2f7",
            selectforeground=COLORS["text"],
            relief="flat",
            bd=0,
            font=("Cascadia Mono", 10),
            padx=14,
            pady=12,
        )
        self.catalog_text.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(editor_frame, orient="vertical", command=self.catalog_text.yview)
        x_scroll = ttk.Scrollbar(editor_frame, orient="horizontal", command=self.catalog_text.xview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.catalog_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.catalog_status_var = tk.StringVar(value="尚未载入")
        tk.Label(
            page,
            textvariable=self.catalog_status_var,
            background=COLORS["window"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._catalog_loaded = False

    def _load_catalog_editor_if_needed(self) -> None:
        if not self._catalog_loaded:
            self.load_catalog_from_disk()

    def _set_catalog_text(self, text: str) -> None:
        self.catalog_text.delete("1.0", "end")
        self.catalog_text.insert("1.0", text)
        self.catalog_text.edit_modified(False)
        self._catalog_loaded = True

    def load_catalog_from_disk(self) -> None:
        try:
            text = self.controller.catalog.current_text()
            _, count = validate_catalog_text(text)
        except CatalogError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self._set_catalog_text(text)
        source = self.controller.catalog.target_path
        label = str(source) if source.exists() else "内置目录（本机尚未注入）"
        self.catalog_status_var.set(f"{label} · {count} 个模型")

    def load_bundled_catalog(self) -> None:
        try:
            text = self.controller.catalog.bundled_text()
            _, count = validate_catalog_text(text)
        except CatalogError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self._set_catalog_text(text)
        self.catalog_status_var.set(f"已载入内置目录 · {count} 个模型（尚未保存）")

    def validate_catalog_editor(self) -> None:
        try:
            _, count = validate_catalog_text(self.catalog_text.get("1.0", "end-1c"))
        except CatalogError as exc:
            self.catalog_status_var.set(str(exc))
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.catalog_status_var.set(f"校验通过 · {count} 个模型")

    def save_catalog_editor(self) -> None:
        try:
            path, count, _ = self.controller.catalog.save(self.catalog_text.get("1.0", "end-1c"))
        except CatalogError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.catalog_status_var.set(f"已注入 {path} · {count} 个模型")
        self.footer_var.set("models.json 已保存并注入")

    def _build_tools_page(self) -> None:
        page = self._new_page("tools")
        self._page_header(page, "工具", "Threadripper、历史会话同步、配置诊断与本地目录")
        tool_band = tk.Frame(page, background=COLORS["surface"], padx=20, pady=16)
        tool_band.grid(row=1, column=0, sticky="ew")
        tool_band.columnconfigure(0, weight=1)
        tk.Label(
            tool_band,
            text="Codex Threadripper",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=(FONT, 13, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.threadripper_status_var = tk.StringVar(value="检测中")
        tk.Label(
            tool_band,
            textvariable=self.threadripper_status_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        tool_actions = tk.Frame(tool_band, background=COLORS["surface"])
        tool_actions.grid(row=0, column=1, rowspan=2, sticky="e")
        self.threadripper_install_button = make_button(
            tool_actions, "一键安装 / 更新", self.install_threadripper_ui
        )
        self.threadripper_install_button.pack(side="left", padx=(0, 8))
        self.threadripper_sync_button = make_button(
            tool_actions, "同步历史会话", self.sync_history_ui, primary=True
        )
        self.threadripper_sync_button.pack(side="left")
        utility = tk.Frame(page, background=COLORS["window"])
        utility.grid(row=2, column=0, sticky="ew", pady=(14, 10))
        make_button(utility, "运行配置诊断", self.run_diagnostics).pack(side="left", padx=(0, 8))
        make_button(utility, "打开 Codex Home", lambda: self._open_path(self.controller.codex_home)).pack(
            side="left", padx=(0, 8)
        )
        make_button(
            utility, "打开备份目录", lambda: self._open_path(self.controller.config.backup_directory)
        ).pack(side="left")
        log_frame = tk.Frame(
            page,
            background=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        log_frame.grid(row=3, column=0, sticky="nsew")
        page.grid_rowconfigure(3, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        self.tools_log = tk.Text(
            log_frame,
            state="disabled",
            wrap="word",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            relief="flat",
            font=("Cascadia Mono", 9),
            padx=14,
            pady=12,
        )
        self.tools_log.grid(row=0, column=0, sticky="nsew")
        tools_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.tools_log.yview)
        tools_scroll.grid(row=0, column=1, sticky="ns")
        self.tools_log.configure(yscrollcommand=tools_scroll.set)

    def _append_tool_log(self, message: str) -> None:
        self.tools_log.configure(state="normal")
        self.tools_log.insert("end", f"{message}\n")
        self.tools_log.see("end")
        self.tools_log.configure(state="disabled")

    def refresh_tools(self) -> None:
        command, version = self.controller.threadripper_status()
        if command and version:
            self.threadripper_status_var.set(f"已就绪 · {version} · {command}")
            self.threadripper_sync_button.configure(state="normal")
        else:
            self.threadripper_status_var.set("未安装或无法启动")
            self.threadripper_sync_button.configure(state="disabled")

    def install_threadripper_ui(self) -> None:
        if self.busy:
            return
        self._set_busy(True, "正在安装 Threadripper")
        self._append_tool_log("正在从官方 GitHub Release 获取 Windows x64 安装包…")

        def worker() -> None:
            try:
                path, version = install_threadripper(
                    progress=lambda message: self.event_queue.put(("tool_log", message))
                )
                self.event_queue.put(("threadripper_installed", (path, version)))
            except Exception as exc:
                self.event_queue.put(("operation_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def sync_history_ui(self) -> None:
        if self.busy:
            return
        self._set_busy(True, "正在同步历史会话")

        def worker() -> None:
            try:
                result = self.controller.sync_history()
                self.event_queue.put(("history_synced", result))
            except Exception as exc:
                self.event_queue.put(("operation_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def run_diagnostics(self) -> None:
        self._append_tool_log("开始配置诊断")
        try:
            snapshot = self.controller.current_snapshot()
            text = self.controller.catalog.current_text()
            _, count = validate_catalog_text(text)
            command, version = self.controller.threadripper_status()
            self._append_tool_log(f"config.toml：通过 · {snapshot.path}")
            self._append_tool_log(f"当前 provider：{snapshot.model_provider or 'OpenAI 内置'}")
            self._append_tool_log(f"当前模型：{snapshot.model or '未设置'}")
            self._append_tool_log(f"models.json：通过 · {count} 个模型")
            self._append_tool_log(f"Threadripper：{version if command else '未安装'}")
            self._append_tool_log("诊断完成")
        except Exception as exc:
            self._append_tool_log(f"诊断失败：{exc}")

    @staticmethod
    def _open_path(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _build_deployment_page(self) -> None:
        page = self._new_page("deployment")
        header = self._page_header(page, "部署与登录", "管理 OpenAI 官方凭据和首次部署策略")
        self.auth_check_button = make_button(header, "检查状态", self.refresh_auth_status)
        self.auth_check_button.grid(row=0, column=1, rowspan=2, sticky="e")

        account = tk.Frame(page, background=COLORS["surface"], padx=22, pady=20)
        account.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        account.columnconfigure(0, weight=1)
        self.auth_status_var = tk.StringVar(value="正在检查")
        self.auth_policy_var = tk.StringVar()
        self.auth_path_var = tk.StringVar()
        tk.Label(
            account,
            text="OpenAI 官方登录态",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.auth_status_label = tk.Label(
            account,
            textvariable=self.auth_status_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 11),
            anchor="w",
        )
        self.auth_status_label.grid(row=1, column=0, sticky="w", pady=(10, 3))
        tk.Label(
            account,
            textvariable=self.auth_policy_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=2)
        tk.Label(
            account,
            textvariable=self.auth_path_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=3, column=0, sticky="w", pady=2)
        auth_actions = tk.Frame(account, background=COLORS["surface"])
        auth_actions.grid(row=0, column=1, rowspan=4, sticky="e")
        self.auth_login_button = make_button(
            auth_actions, "登录官方账号", self.login_official_ui, primary=True
        )
        self.auth_login_button.pack(side="left", padx=(0, 8))
        self.auth_logout_button = make_button(
            auth_actions, "清空登录态", self.clear_official_auth_ui, danger=True
        )
        self.auth_logout_button.pack(side="left")

        routing = tk.Frame(page, background=COLORS["surface"], padx=22, pady=18)
        routing.grid(row=2, column=0, sticky="ew")
        routing.columnconfigure(1, weight=1)
        self.deployment_route_var = tk.StringVar()
        self.deployment_relay_var = tk.StringVar()
        for row, (title, variable) in enumerate(
            (("当前路由", self.deployment_route_var), ("登录前回退中转", self.deployment_relay_var))
        ):
            tk.Label(
                routing,
                text=title,
                background=COLORS["surface"],
                foreground=COLORS["muted"],
                font=(FONT, 9),
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=5)
            tk.Label(
                routing,
                textvariable=variable,
                background=COLORS["surface"],
                foreground=COLORS["text"],
                font=(FONT, 10, "bold"),
                anchor="w",
            ).grid(row=row, column=1, sticky="w", pady=5)
        make_button(routing, "重新运行部署向导", self.open_setup_wizard).grid(
            row=0, column=2, rowspan=2, sticky="e"
        )
        self.deployment_status_var = tk.StringVar(value="")
        tk.Label(
            page,
            textvariable=self.deployment_status_var,
            background=COLORS["window"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.refresh_deployment_view()

    def refresh_deployment_view(self) -> None:
        status = self.auth_status_data
        retained = self.controller.settings.retain_official_auth
        self.auth_status_var.set(status.label)
        self.auth_policy_var.set(
            "策略：保留官方登录态" if retained else "策略：不保留，OpenAI 直连已停用"
        )
        file_state = "存在" if status.auth_file_exists else "未发现（凭据也可能位于系统凭据库）"
        self.auth_path_var.set(f"auth.json：{file_state} · {status.auth_path}")
        color = COLORS["success"] if status.official_account_logged_in else COLORS["warning"]
        self.auth_status_label.configure(foreground=color)
        try:
            active = self.controller.detect_active_profile()
        except Exception:
            active = ""
        profile = self.controller.settings.profiles.get(active)
        self.deployment_route_var.set(profile.display_name if profile else "未识别")
        relay_id = self.controller.settings.last_relay_profile_id
        self.deployment_relay_var.set(self.controller.settings.profiles[relay_id].display_name)
        self.auth_login_button.configure(
            text="启用官方直连" if status.official_account_logged_in else "登录官方账号"
        )
        self.auth_logout_button.configure(
            state="normal"
            if status.official_account_logged_in or status.auth_file_exists
            else "disabled"
        )

    def refresh_auth_status(self) -> None:
        if self._auth_running:
            return
        self._auth_running = True
        if hasattr(self, "auth_check_button"):
            self.auth_check_button.configure(state="disabled", text="检查中")

        def worker() -> None:
            try:
                self.event_queue.put(("auth_data", self.controller.auth_status()))
            except Exception as exc:
                self.event_queue.put(("auth_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def login_official_ui(self) -> None:
        if self.busy:
            return
        try:
            active = self.controller.detect_active_profile()
            fallback = self.controller.relay_fallback() if active == "glm" else ""
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        if active == "glm" and not messagebox.askyesno(
            APP_NAME,
            f"登录前将从 GLM 切换到 {self.controller.settings.profiles[fallback].display_name}。\n\n继续吗？",
            parent=self,
        ):
            return
        self._set_busy(True, "正在准备 OpenAI 官方登录")

        def worker() -> None:
            try:
                result = self.controller.restore_official_login(
                    progress=lambda message: self.event_queue.put(("operation_progress", message))
                )
                self.event_queue.put(("auth_login_done", result))
            except Exception as exc:
                self.event_queue.put(("operation_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def clear_official_auth_ui(self) -> None:
        if self.busy:
            return
        if not messagebox.askyesno(
            APP_NAME,
            "将清除 Codex 的 OpenAI 官方登录态，并停用 OpenAI 直连。\n中转和 GLM 的配置与本地 Key 不会删除。\n\n继续吗？",
            parent=self,
        ):
            return
        self._set_busy(True, "正在清除 OpenAI 官方登录态")

        def worker() -> None:
            try:
                result = self.controller.clear_official_login(
                    progress=lambda message: self.event_queue.put(("operation_progress", message))
                )
                self.event_queue.put(("auth_logout_done", result))
            except Exception as exc:
                self.event_queue.put(("operation_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _build_settings_page(self) -> None:
        page = self._new_page("settings")
        self._page_header(page, "设置", "配置位置、历史兼容、自动重启与监控频率")
        form = tk.Frame(page, background=COLORS["surface"], padx=22, pady=20)
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        settings = self.controller.settings
        self.setting_vars = {
            "codex_home": tk.StringVar(value=settings.codex_home),
            "stable_provider_key": tk.StringVar(value=settings.stable_provider_key),
            "preserve_provider_key": tk.BooleanVar(value=settings.preserve_provider_key),
            "auto_restart": tk.BooleanVar(value=settings.auto_restart),
            "auto_sync_history": tk.BooleanVar(value=settings.auto_sync_history),
            "monitor_interval_seconds": tk.IntVar(value=settings.monitor_interval_seconds),
            "usage_lookback_days": tk.IntVar(value=settings.usage_lookback_days),
        }
        tk.Label(
            form,
            text="Codex Home",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Entry(form, textvariable=self.setting_vars["codex_home"], style="App.TEntry").grid(
            row=0, column=1, sticky="ew"
        )
        make_button(form, "选择", self.browse_codex_home).grid(row=0, column=2, padx=(8, 0))
        tk.Label(
            form,
            text="稳定 provider 标签",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
        ).grid(row=1, column=0, sticky="w", padx=(0, 14), pady=(14, 0))
        ttk.Entry(
            form, textvariable=self.setting_vars["stable_provider_key"], style="App.TEntry", width=28
        ).grid(row=1, column=1, sticky="w", pady=(14, 0))
        checks = tk.Frame(form, background=COLORS["surface"])
        checks.grid(row=2, column=0, columnspan=3, sticky="w", pady=(18, 8))
        for key, label in (
            ("preserve_provider_key", "切换第三方供应商时保持同一 provider 标签"),
            ("auto_restart", "配置保存后自动重启 Codex（会关闭并重新打开 Codex）"),
            ("auto_sync_history", "切换后自动同步历史会话"),
        ):
            tk.Checkbutton(
                checks,
                text=label,
                variable=self.setting_vars[key],
                background=COLORS["surface"],
                foreground=COLORS["text"],
                activebackground=COLORS["surface"],
                selectcolor=COLORS["surface"],
                font=(FONT, 10),
            ).pack(anchor="w", pady=4)
        number_row = tk.Frame(form, background=COLORS["surface"])
        number_row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        tk.Label(
            number_row,
            text="健康刷新（秒）",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
        ).pack(side="left")
        ttk.Spinbox(
            number_row,
            from_=10,
            to=3600,
            increment=10,
            textvariable=self.setting_vars["monitor_interval_seconds"],
            width=8,
        ).pack(side="left", padx=(8, 24))
        tk.Label(
            number_row,
            text="Token 统计天数",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
        ).pack(side="left")
        ttk.Spinbox(
            number_row,
            from_=1,
            to=3650,
            textvariable=self.setting_vars["usage_lookback_days"],
            width=8,
        ).pack(side="left", padx=(8, 0))
        actions = tk.Frame(page, background=COLORS["window"])
        actions.grid(row=2, column=0, sticky="e", pady=(14, 0))
        make_button(actions, "重新运行部署向导", self.open_setup_wizard).pack(side="left", padx=(0, 8))
        make_button(actions, "保存设置", self.save_general_settings, primary=True).pack(side="left")
        self.settings_status_var = tk.StringVar()
        tk.Label(
            page,
            textvariable=self.settings_status_var,
            background=COLORS["window"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="e",
        ).grid(row=3, column=0, sticky="e", pady=(8, 0))

    def browse_codex_home(self) -> None:
        selected = filedialog.askdirectory(
            title="选择 Codex Home",
            initialdir=self.setting_vars["codex_home"].get() or str(Path.home()),
            mustexist=False,
            parent=self,
        )
        if not selected:
            return
        self.setting_vars["codex_home"].set(selected)
        try:
            snapshot = self.controller.preview_codex_home(Path(selected))
            status = (
                f"已发现 config.toml · provider {snapshot.model_provider or 'OpenAI 内置'}"
                if Path(snapshot.path).exists()
                else "目录中没有 config.toml，保存时将创建。"
            )
            self.settings_status_var.set(status)
        except Exception as exc:
            self.settings_status_var.set(str(exc))

    def save_general_settings(self) -> None:
        settings = self.controller.settings
        try:
            home = Path(self.setting_vars["codex_home"].get()).expanduser()
            self.controller.change_codex_home(home, create=True)
            settings.stable_provider_key = self.setting_vars["stable_provider_key"].get().strip()
            settings.preserve_provider_key = self.setting_vars["preserve_provider_key"].get()
            settings.auto_restart = self.setting_vars["auto_restart"].get()
            settings.auto_sync_history = self.setting_vars["auto_sync_history"].get()
            settings.monitor_interval_seconds = self.setting_vars["monitor_interval_seconds"].get()
            settings.usage_lookback_days = self.setting_vars["usage_lookback_days"].get()
            self.controller.save()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.settings_status_var.set("设置已保存")
        self._catalog_loaded = False
        self.refresh_all(local_only=True)

    def open_setup_wizard(self) -> None:
        existing = getattr(self, "setup_wizard", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return
        self.setup_wizard = SetupWizard(self, self.controller, self._setup_completed)

    def _setup_completed(self) -> None:
        settings = self.controller.settings
        self.setting_vars["codex_home"].set(settings.codex_home)
        self.setting_vars["stable_provider_key"].set(settings.stable_provider_key)
        self.setting_vars["preserve_provider_key"].set(settings.preserve_provider_key)
        self.setting_vars["auto_restart"].set(settings.auto_restart)
        self.setting_vars["auto_sync_history"].set(settings.auto_sync_history)
        self._catalog_loaded = False
        self.refresh_all(local_only=True)
        self.refresh_monitoring()

    def refresh_all(self, *, local_only: bool = False) -> None:
        try:
            snapshot = self.controller.current_snapshot()
            active_id = self.controller.detect_active_profile()
        except Exception as exc:
            self.active_route_var.set("配置不可用")
            self.route_detail_var.set(str(exc))
            return
        active_profile = self.controller.settings.profiles.get(active_id)
        self.active_route_var.set(active_profile.display_name if active_profile else "未识别的供应商")
        provider_label = snapshot.model_provider or "openai"
        self.route_detail_var.set(f"provider: {provider_label}")
        for profile_id, card in self.provider_cards.items():
            profile = self.controller.settings.profiles[profile_id]
            card.set_profile(profile)
            card.set_active(profile_id == active_id, provider_label if profile_id == active_id else "")
            if profile_id == "openai":
                card.set_available(
                    self.controller.openai_available(self.auth_status_data),
                    active=profile_id == active_id,
                )
            else:
                card.set_available(
                    profile_id == active_id or self.controller.profile_ready(profile_id),
                    active=profile_id == active_id,
                    unavailable_text="未配置",
                )
            card.set_telemetry(self.telemetry_data.get(profile_id))
            usage_key = "openai" if profile_id == "openai" else provider_label
            card.set_usage(
                self.usage_data.get(usage_key),
                self.controller.settings.usage_lookback_days,
                profile_id == active_id,
            )
            if profile_id in self.profile_tab_buttons:
                self.profile_tab_buttons[profile_id].configure(text=profile.display_name)
        if local_only and not getattr(self, "_usage_running", False):
            self._usage_running = True

            def usage_worker() -> None:
                try:
                    data = self.controller.token_usage()
                    self.event_queue.put(("usage_data", data))
                except Exception as exc:
                    self.event_queue.put(("tool_log", f"Token 统计失败：{exc}"))

            threading.Thread(target=usage_worker, daemon=True).start()

    def refresh_monitoring(self) -> None:
        if getattr(self, "_monitor_running", False) or self.busy:
            return
        self._monitor_running = True
        self.dashboard_refresh_button.configure(state="disabled", text="刷新中")
        self.footer_var.set("正在检查供应商链路与额度")

        def worker() -> None:
            try:
                telemetry = self.controller.telemetry()
                usage = self.controller.token_usage()
                auth_status = self.controller.auth_status()
                self.event_queue.put(("monitor_data", (telemetry, usage, auth_status)))
            except Exception as exc:
                self.event_queue.put(("monitor_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def confirm_switch(self, profile_id: str) -> None:
        if self.busy:
            return
        if profile_id == "openai" and not self.controller.openai_available(self.auth_status_data):
            messagebox.showwarning(
                APP_NAME,
                "OpenAI 直连当前不可用，请先在“部署”中登录并启用官方登录态。",
                parent=self,
            )
            self.show_page("deployment")
            return
        if profile_id != "openai" and not self.controller.profile_ready(profile_id):
            profile = self.controller.settings.profiles[profile_id]
            messagebox.showwarning(
                APP_NAME,
                f"{profile.display_name} 尚未填写完整的 API 地址、模型和 Key。",
                parent=self,
            )
            self.show_page("providers")
            return
        profile = self.controller.settings.profiles[profile_id]
        detail = "Codex 将自动重启。" if self.controller.settings.auto_restart else "切换后请手动重启 Codex。"
        if not messagebox.askyesno(
            APP_NAME,
            f"切换到 {profile.display_name}？\n\n{detail}\n写入前会自动备份 config.toml。",
            parent=self,
        ):
            return
        self._set_busy(True, f"正在切换到 {profile.display_name}")

        def worker() -> None:
            try:
                result = self.controller.switch_profile(
                    profile_id,
                    progress=lambda message: self.event_queue.put(("operation_progress", message)),
                )
                self.event_queue.put(("switch_done", result))
            except Exception as exc:
                self.event_queue.put(("operation_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.dashboard_refresh_button.configure(state=state)
        self.provider_save_button.configure(state=state)
        self.provider_test_button.configure(state=state)
        self.threadripper_install_button.configure(state=state)
        if hasattr(self, "auth_check_button"):
            self.auth_check_button.configure(state=state)
            self.auth_login_button.configure(state=state)
            self.auth_logout_button.configure(state=state)
        if busy:
            self.threadripper_sync_button.configure(state="disabled")
        else:
            self.refresh_tools()
            self.refresh_deployment_view()
        if busy:
            for card in self.provider_cards.values():
                card.switch_button.configure(state="disabled")
        else:
            self.refresh_all(local_only=True)
        if status:
            self.footer_var.set(status)

    def _handle_switch_done(self, result: OperationResult) -> None:
        self._set_busy(False)
        self.refresh_all(local_only=True)
        profile = self.controller.settings.profiles[result.profile_id]
        details = [f"已切换到 {profile.display_name}。"]
        if result.history_summary:
            details.append(result.history_summary)
        details.extend(result.warnings)
        self.footer_var.set(details[0])
        messagebox.showinfo(APP_NAME, "\n".join(details), parent=self)
        self.refresh_monitoring()

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.event_queue.get_nowait()
                if event == "operation_progress":
                    self.footer_var.set(str(payload))
                    self._append_tool_log(str(payload))
                elif event == "switch_done":
                    self._handle_switch_done(payload)  # type: ignore[arg-type]
                elif event == "operation_failed":
                    self._set_busy(False)
                    self.footer_var.set(f"操作失败：{payload}")
                    self._append_tool_log(f"操作失败：{payload}")
                    messagebox.showerror(APP_NAME, str(payload), parent=self)
                    self.refresh_auth_status()
                elif event == "tool_log":
                    self._append_tool_log(str(payload))
                elif event == "threadripper_installed":
                    path, version = payload  # type: ignore[misc]
                    self._set_busy(False)
                    self.refresh_tools()
                    self._append_tool_log(f"已安装 {version}：{path}")
                elif event == "history_synced":
                    self._set_busy(False)
                    self._append_tool_log(str(payload))
                    self.footer_var.set("历史会话同步完成")
                elif event == "profile_test":
                    telemetry = payload  # type: ignore[assignment]
                    self.telemetry_data[telemetry.profile_id] = telemetry
                    self.provider_form_status.set(telemetry.health.message)
                    self.refresh_all()
                elif event == "auth_data":
                    self._auth_running = False
                    self.auth_status_data = payload  # type: ignore[assignment]
                    self.auth_check_button.configure(state="normal", text="检查状态")
                    self.refresh_deployment_view()
                    self.refresh_all()
                elif event == "auth_failed":
                    self._auth_running = False
                    self.auth_check_button.configure(state="normal", text="检查状态")
                    self.deployment_status_var.set(f"登录态检查失败：{payload}")
                elif event == "auth_login_done":
                    _, status = payload  # type: ignore[misc]
                    self.auth_status_data = status
                    self._set_busy(False)
                    self.deployment_status_var.set("官方登录已启用，OpenAI 直连可切换")
                    self.refresh_all(local_only=True)
                    messagebox.showinfo(APP_NAME, "官方登录已启用。", parent=self)
                elif event == "auth_logout_done":
                    _, status = payload  # type: ignore[misc]
                    self.auth_status_data = status
                    self._set_busy(False)
                    self.deployment_status_var.set("官方登录态已清除，OpenAI 直连已停用")
                    self.refresh_all(local_only=True)
                    messagebox.showinfo(APP_NAME, "官方登录态已清除。", parent=self)
                elif event == "usage_data":
                    self._usage_running = False
                    self.usage_data = payload  # type: ignore[assignment]
                    self.refresh_all()
                elif event == "monitor_data":
                    self._monitor_running = False
                    self.telemetry_data, self.usage_data, self.auth_status_data = payload  # type: ignore[misc]
                    self.dashboard_refresh_button.configure(state="normal", text="立即刷新")
                    self.footer_var.set("供应商状态已更新")
                    self.refresh_deployment_view()
                    self.refresh_all()
                    if self._monitor_enabled:
                        delay = self.controller.settings.monitor_interval_seconds * 1000
                        self.after(delay, self.refresh_monitoring)
                elif event == "monitor_failed":
                    self._monitor_running = False
                    self.dashboard_refresh_button.configure(state="normal", text="立即刷新")
                    self.footer_var.set(f"监控刷新失败：{payload}")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)


class SetupWizard(tk.Toplevel):
    STEPS = ("环境", "供应商", "历史与模型", "完成部署")

    def __init__(
        self,
        parent: ProviderSwitchApp,
        controller: ApplicationController,
        on_complete: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.controller = controller
        self.on_complete = on_complete
        self.title(f"首次部署 · {APP_NAME}")
        self.geometry("840x700")
        self.minsize(800, 660)
        self.transient(parent)
        self.grab_set()
        self.configure(background=COLORS["window"])
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.step = 0
        self.detected_auth_status = AuthStatus(
            "unknown", False, controller.codex_home / "auth.json"
        )
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._build_variables()
        self._build_shell()
        self.render_step()
        self.after(100, self._drain_worker_events)

    def _build_variables(self) -> None:
        settings = self.controller.settings
        self.vars: dict[str, tk.Variable] = {
            "codex_home": tk.StringVar(value=settings.codex_home),
            "stable_provider_key": tk.StringVar(value=settings.stable_provider_key),
            "preserve_provider_key": tk.BooleanVar(value=settings.preserve_provider_key),
            "auto_restart": tk.BooleanVar(value=settings.auto_restart),
            "auto_sync_history": tk.BooleanVar(value=settings.auto_sync_history),
            "inject_catalog": tk.BooleanVar(value=True),
            "initial_profile": tk.StringVar(
                value=settings.active_profile_id
                or ("openai" if self.detected_auth_status.official_account_logged_in else "relay1")
            ),
            "retain_official_auth": tk.BooleanVar(value=settings.retain_official_auth),
        }
        for profile_id in ("relay1", "relay2", "glm"):
            profile = settings.profiles[profile_id]
            self.vars[f"{profile_id}_name"] = tk.StringVar(value=profile.display_name)
            self.vars[f"{profile_id}_base_url"] = tk.StringVar(value=profile.base_url)
            self.vars[f"{profile_id}_model"] = tk.StringVar(value=profile.model)
            self.vars[f"{profile_id}_key"] = tk.StringVar(
                value=self.controller.credentials.get(profile_id, "")
            )

    def _build_shell(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        rail = tk.Frame(self, width=178, background=COLORS["sidebar"])
        rail.grid(row=0, column=0, rowspan=2, sticky="ns")
        rail.grid_propagate(False)
        tk.Label(
            rail,
            text="首次部署",
            background=COLORS["sidebar"],
            foreground="#ffffff",
            font=(FONT, 16, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(28, 22))
        self.step_labels: list[tk.Label] = []
        for index, title in enumerate(self.STEPS, start=1):
            label = tk.Label(
                rail,
                text=f"{index:02d}  {title}",
                background=COLORS["sidebar"],
                foreground="#9da6ad",
                font=(FONT, 10),
                anchor="w",
                padx=20,
                pady=11,
            )
            label.pack(fill="x", pady=1)
            self.step_labels.append(label)
        self.content = tk.Frame(self, background=COLORS["window"], padx=28, pady=24)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        footer = tk.Frame(self, background="#e3e7ea", padx=20, pady=12)
        footer.grid(row=1, column=1, sticky="ew")
        self.cancel_button = make_button(footer, "稍后设置", self.close)
        self.cancel_button.pack(side="left")
        self.next_button = make_button(footer, "下一步", self.next_step, primary=True, width=10)
        self.next_button.pack(side="right")
        self.back_button = make_button(footer, "上一步", self.previous_step, width=10)
        self.back_button.pack(side="right", padx=(0, 8))

    def _clear_content(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def _heading(self, title: str, subtitle: str) -> None:
        tk.Label(
            self.content,
            text=title,
            background=COLORS["window"],
            foreground=COLORS["text"],
            font=(FONT, 19, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            self.content,
            text=subtitle,
            background=COLORS["window"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(35, 0))

    def render_step(self) -> None:
        self._clear_content()
        for index, label in enumerate(self.step_labels):
            active = index == self.step
            label.configure(
                background=COLORS["sidebar_active"] if active else COLORS["sidebar"],
                foreground="#ffffff" if active else "#9da6ad",
                font=(FONT, 10, "bold" if active else "normal"),
            )
        self.back_button.configure(state="normal" if self.step > 0 else "disabled")
        self.next_button.configure(text="完成部署" if self.step == 3 else "下一步")
        (self._render_environment, self._render_providers, self._render_history, self._render_summary)[
            self.step
        ]()

    def _render_environment(self) -> None:
        self._heading("定位 Codex 配置", "自动读取 config.toml，并导入当前 provider 与模型作为迁移基础")
        panel = tk.Frame(self.content, background=COLORS["surface"], padx=20, pady=20)
        panel.grid(row=1, column=0, sticky="nsew", pady=(24, 0))
        panel.columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text="Codex Home",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        path_row = tk.Frame(panel, background=COLORS["surface"])
        path_row.grid(row=1, column=0, sticky="ew", pady=(5, 16))
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=self.vars["codex_home"], style="App.TEntry").grid(
            row=0, column=0, sticky="ew"
        )
        make_button(path_row, "选择", self._browse_home).grid(row=0, column=1, padx=(8, 0))
        self.environment_status = tk.StringVar()
        tk.Label(
            panel,
            textvariable=self.environment_status,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=(FONT, 11),
            justify="left",
            anchor="nw",
        ).grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        panel.rowconfigure(2, weight=1)
        self._probe_environment()

    def _browse_home(self) -> None:
        selected = filedialog.askdirectory(
            title="选择 Codex Home",
            initialdir=str(self.vars["codex_home"].get() or Path.home()),
            mustexist=False,
            parent=self,
        )
        if selected:
            self.vars["codex_home"].set(selected)
            self._probe_environment()

    def _probe_environment(self) -> ConfigSnapshot | None:
        try:
            home = Path(str(self.vars["codex_home"].get())).expanduser()
            snapshot = self.controller.preview_codex_home(home)
            self.detected_auth_status = self.controller.preview_auth_status(home)
            exists = Path(snapshot.path).exists()
            details = [f"配置文件：{snapshot.path}"]
            if exists:
                details.extend(
                    (
                        f"当前 provider：{snapshot.model_provider or 'OpenAI 内置'}",
                        f"当前模型：{snapshot.model or '未设置'}",
                        f"已注册供应商：{len(snapshot.providers)} 个",
                    )
                )
            else:
                details.append("未找到 config.toml，完成部署时将新建。")
            details.extend(
                (
                    f"官方登录：{self.detected_auth_status.label}",
                    f"auth.json：{'存在' if self.detected_auth_status.auth_file_exists else '未发现'}",
                )
            )
            self.environment_status.set("\n\n".join(details))
            return snapshot
        except Exception as exc:
            self.environment_status.set(f"读取失败：{exc}")
            return None

    def _render_providers(self) -> None:
        self._heading("配置供应商", "中转名称与密钥可随时修改；GLM 已预填官方 Codex 接入地址")
        container = tk.Frame(self.content, background=COLORS["window"])
        container.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
        container.columnconfigure(0, weight=1)
        for row, profile_id in enumerate(("relay1", "relay2", "glm")):
            section = tk.Frame(
                container,
                background=COLORS["surface"],
                highlightbackground=COLORS["border"],
                highlightthickness=1,
                padx=14,
                pady=10,
            )
            section.grid(row=row, column=0, sticky="ew", pady=(0, 9))
            section.columnconfigure(1, weight=1)
            section.columnconfigure(3, weight=1)
            tk.Label(
                section,
                text={"relay1": "API1", "relay2": "API2", "glm": "GLM"}[profile_id],
                background=COLORS["surface"],
                foreground=PROFILE_COLORS[profile_id],
                font=(FONT, 11, "bold"),
            ).grid(row=0, column=0, sticky="w", padx=(0, 10))
            tk.Label(
                section,
                text="名称",
                background=COLORS["surface"],
                foreground=COLORS["muted"],
                font=(FONT, 8),
            ).grid(row=0, column=1, sticky="w")
            ttk.Entry(
                section, textvariable=self.vars[f"{profile_id}_name"], style="App.TEntry", width=15
            ).grid(row=1, column=1, sticky="ew", padx=(0, 10))
            tk.Label(
                section,
                text="模型",
                background=COLORS["surface"],
                foreground=COLORS["muted"],
                font=(FONT, 8),
            ).grid(row=0, column=2, sticky="w")
            ttk.Entry(
                section, textvariable=self.vars[f"{profile_id}_model"], style="App.TEntry", width=16
            ).grid(row=1, column=2, sticky="ew", padx=(0, 10))
            tk.Label(
                section,
                text="API Key",
                background=COLORS["surface"],
                foreground=COLORS["muted"],
                font=(FONT, 8),
            ).grid(row=0, column=3, sticky="w")
            ttk.Entry(
                section,
                textvariable=self.vars[f"{profile_id}_key"],
                style="App.TEntry",
                show="*",
                width=22,
            ).grid(row=1, column=3, sticky="ew")
            tk.Label(
                section,
                text="API Base URL",
                background=COLORS["surface"],
                foreground=COLORS["muted"],
                font=(FONT, 8),
            ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(7, 0))
            ttk.Entry(
                section,
                textvariable=self.vars[f"{profile_id}_base_url"],
                style="App.TEntry",
            ).grid(row=3, column=1, columnspan=3, sticky="ew")

    def _render_history(self) -> None:
        self._heading("历史与模型", "固定 provider 标签保留会话归属；Threadripper 负责补齐历史索引")
        panel = tk.Frame(self.content, background=COLORS["surface"], padx=20, pady=18)
        panel.grid(row=1, column=0, sticky="nsew", pady=(22, 0))
        panel.columnconfigure(1, weight=1)
        tk.Checkbutton(
            panel,
            text="保持同一 provider 标签（推荐）",
            variable=self.vars["preserve_provider_key"],
            background=COLORS["surface"],
            foreground=COLORS["text"],
            activebackground=COLORS["surface"],
            selectcolor=COLORS["surface"],
            font=(FONT, 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            panel,
            text="稳定标签",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
        ).grid(row=1, column=0, sticky="w", pady=(12, 0), padx=(0, 12))
        ttk.Entry(
            panel, textvariable=self.vars["stable_provider_key"], style="App.TEntry", width=28
        ).grid(row=1, column=1, sticky="w", pady=(12, 0))
        for row, key, label in (
            (2, "inject_catalog", "注入内置 GLM models.json"),
            (3, "auto_sync_history", "每次切换后自动同步历史会话"),
            (4, "auto_restart", "部署后自动重启 Codex（从 Codex 启动时自动跳过）"),
        ):
            tk.Checkbutton(
                panel,
                text=label,
                variable=self.vars[key],
                background=COLORS["surface"],
                foreground=COLORS["text"],
                activebackground=COLORS["surface"],
                selectcolor=COLORS["surface"],
                font=(FONT, 10),
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(12 if row == 2 else 7, 0))
        divider = tk.Frame(panel, height=1, background=COLORS["border"])
        divider.grid(row=5, column=0, columnspan=2, sticky="ew", pady=18)
        tk.Label(
            panel,
            text="Codex Threadripper",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=(FONT, 11, "bold"),
        ).grid(row=6, column=0, sticky="w")
        command, version = self.controller.threadripper_status()
        self.wizard_thread_status = tk.StringVar(
            value=f"已就绪 · {version}" if command else "尚未安装"
        )
        tk.Label(
            panel,
            textvariable=self.wizard_thread_status,
            background=COLORS["surface"],
            foreground=COLORS["success"] if command else COLORS["warning"],
            font=(FONT, 9),
        ).grid(row=7, column=0, sticky="w", pady=(4, 0))
        self.wizard_install_button = make_button(
            panel, "一键安装 / 更新", self._install_threadripper
        )
        self.wizard_install_button.grid(row=6, column=1, rowspan=2, sticky="e")

    def _install_threadripper(self) -> None:
        self.wizard_install_button.configure(state="disabled", text="安装中")
        self.wizard_thread_status.set("正在下载并校验官方安装包…")

        def worker() -> None:
            try:
                path, version = install_threadripper(
                    progress=lambda message: self.worker_queue.put(("install_progress", message))
                )
                self.worker_queue.put(("install_done", (path, version)))
            except Exception as exc:
                self.worker_queue.put(("install_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _render_summary(self) -> None:
        self._heading("完成部署", "选择首次启用的路由，确认后写入配置并进入日常总览")
        panel = tk.Frame(self.content, background=COLORS["surface"], padx=20, pady=18)
        panel.grid(row=1, column=0, sticky="nsew", pady=(22, 0))
        panel.columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text="首次启用",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        choices = tk.Frame(panel, background=COLORS["surface"])
        choices.grid(row=1, column=0, sticky="w", pady=(8, 18))
        names = {
            "openai": "OpenAI 直连",
            "relay1": str(self.vars["relay1_name"].get()),
            "relay2": str(self.vars["relay2_name"].get()),
            "glm": str(self.vars["glm_name"].get()),
        }
        self.setup_route_buttons: dict[str, tk.Radiobutton] = {}
        for profile_id in PROFILE_ORDER:
            button = tk.Radiobutton(
                choices,
                text=names[profile_id],
                value=profile_id,
                variable=self.vars["initial_profile"],
                background=COLORS["surface"],
                foreground=COLORS["text"],
                activebackground=COLORS["surface"],
                selectcolor=COLORS["surface"],
                font=(FONT, 10),
            )
            button.pack(side="left", padx=(0, 16))
            self.setup_route_buttons[profile_id] = button
        self.setup_auth_check = tk.Checkbutton(
            panel,
            text="保留 OpenAI 官方登录态",
            variable=self.vars["retain_official_auth"],
            command=self._sync_setup_auth_choice,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            activebackground=COLORS["surface"],
            selectcolor=COLORS["surface"],
            font=(FONT, 10, "bold"),
        )
        self.setup_auth_check.grid(row=2, column=0, sticky="w", pady=(0, 3))
        self.setup_auth_var = tk.StringVar(value=self.detected_auth_status.label)
        tk.Label(
            panel,
            textvariable=self.setup_auth_var,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=3, column=0, sticky="w", pady=(0, 12))
        self.setup_summary_var = tk.StringVar()
        tk.Label(
            panel,
            textvariable=self.setup_summary_var,
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            font=(FONT, 10),
            justify="left",
            anchor="nw",
            padx=14,
            pady=12,
        ).grid(row=4, column=0, sticky="ew")
        self.deploy_status = tk.StringVar(value="准备就绪")
        tk.Label(
            panel,
            textvariable=self.deploy_status,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=(FONT, 9),
            anchor="w",
        ).grid(row=5, column=0, sticky="ew", pady=(16, 0))
        self._sync_setup_auth_choice()

    def _sync_setup_auth_choice(self) -> None:
        authenticated = self.detected_auth_status.official_account_logged_in
        if not authenticated:
            self.vars["retain_official_auth"].set(False)
            self.setup_auth_check.configure(state="disabled")
        retain = authenticated and bool(self.vars["retain_official_auth"].get())
        self.setup_route_buttons["openai"].configure(state="normal" if retain else "disabled")
        if not retain and self.vars["initial_profile"].get() == "openai":
            self.vars["initial_profile"].set("relay1")
        auth_action = "保留，可随时切回 OpenAI" if retain else "部署时退出并停用 OpenAI 直连"
        self.setup_auth_var.set(f"{self.detected_auth_status.label} · {auth_action}")
        self.setup_summary_var.set(
            f"Codex Home\n{self.vars['codex_home'].get()}\n\n"
            f"provider 标签\n{self.vars['stable_provider_key'].get()}"
            f"（{'保持' if self.vars['preserve_provider_key'].get() else '按供应商切换'}）\n\n"
            f"官方登录态\n{auth_action}\n\n"
            f"模型目录\n{'注入内置 models.json' if self.vars['inject_catalog'].get() else '暂不注入'}"
        )

    def previous_step(self) -> None:
        if self.step > 0:
            self.step -= 1
            self.render_step()

    def next_step(self) -> None:
        if self.step == 3:
            self.finish_setup()
            return
        if not self._validate_step():
            return
        self.step += 1
        self.render_step()

    def _validate_step(self) -> bool:
        if self.step == 0:
            raw_home = str(self.vars["codex_home"].get()).strip()
            if not raw_home:
                messagebox.showerror(APP_NAME, "Codex Home 不能为空。", parent=self)
                return False
            snapshot = self._probe_environment()
            if snapshot is None:
                return False
            self.controller.change_codex_home(Path(raw_home), create=False)
            if snapshot.model_provider:
                self.vars["stable_provider_key"].set(snapshot.model_provider)
                self.vars["initial_profile"].set(
                    "glm" if snapshot.model.casefold().startswith("glm-") else "relay1"
                )
            else:
                self.vars["initial_profile"].set(
                    "openai"
                    if self.detected_auth_status.official_account_logged_in
                    else "relay1"
                )
            current = snapshot.providers.get(snapshot.model_provider, {})
            if current:
                if not str(self.vars["relay1_base_url"].get()).strip():
                    self.vars["relay1_base_url"].set(str(current.get("base_url", "")))
                if not str(self.vars["relay1_model"].get()).strip():
                    self.vars["relay1_model"].set(snapshot.model)
                if not str(self.vars["relay1_key"].get()).strip():
                    token = current.get("experimental_bearer_token", "")
                    if isinstance(token, str):
                        self.vars["relay1_key"].set(token)
            return True
        if self.step == 1:
            for profile_id in ("relay1", "relay2", "glm"):
                if not str(self.vars[f"{profile_id}_name"].get()).strip():
                    messagebox.showerror(APP_NAME, "供应商名称不能为空。", parent=self)
                    return False
            return True
        if self.step == 2:
            stable = str(self.vars["stable_provider_key"].get()).strip()
            if not PROVIDER_KEY_RE.fullmatch(stable):
                messagebox.showerror(
                    APP_NAME,
                    "provider 标签只能包含字母、数字、下划线和连字符。",
                    parent=self,
                )
                return False
        return True

    def _apply_variables(self) -> None:
        settings = self.controller.settings
        self.controller.change_codex_home(Path(str(self.vars["codex_home"].get())), create=True)
        settings.preserve_provider_key = bool(self.vars["preserve_provider_key"].get())
        settings.stable_provider_key = str(self.vars["stable_provider_key"].get()).strip()
        settings.auto_restart = bool(self.vars["auto_restart"].get())
        settings.auto_sync_history = bool(self.vars["auto_sync_history"].get())
        settings.retain_official_auth = bool(self.vars["retain_official_auth"].get())
        for profile_id in ("relay1", "relay2", "glm"):
            profile = settings.profiles[profile_id]
            profile.display_name = str(self.vars[f"{profile_id}_name"].get()).strip()
            profile.base_url = str(self.vars[f"{profile_id}_base_url"].get()).strip()
            profile.model = str(self.vars[f"{profile_id}_model"].get()).strip()
            self.controller.credentials[profile_id] = str(
                self.vars[f"{profile_id}_key"].get()
            ).strip()
        settings.setup_complete = True
        validate_settings(settings)

    def finish_setup(self) -> None:
        profile_id = str(self.vars["initial_profile"].get())
        retain_official_auth = bool(self.vars["retain_official_auth"].get())
        if profile_id == "openai" and not (
            retain_official_auth and self.detected_auth_status.official_account_logged_in
        ):
            messagebox.showerror(
                APP_NAME,
                "OpenAI 直连需要保留有效的官方登录态。",
                parent=self,
            )
            return
        if (
            self.detected_auth_status.official_account_logged_in
            and not retain_official_auth
        ):
            if not messagebox.askyesno(
                APP_NAME,
                "完成部署时将退出 OpenAI 官方账号并清除 auth.json。\n中转和 GLM 的配置与 Key 会保留。\n\n继续吗？",
                parent=self,
            ):
                return
        try:
            self._apply_variables()
            profile = self.controller.settings.profiles[profile_id]
            if profile.kind != "official":
                if not profile.base_url or not profile.model or not self.controller.credentials.get(profile_id):
                    raise SettingsError(f"{profile.display_name} 缺少 API 地址、模型或 Key。")
            if self.vars["inject_catalog"].get():
                self.controller.catalog.inject_bundled()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.next_button.configure(state="disabled", text="部署中")
        self.back_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.deploy_status.set("正在写入配置…")

        def worker() -> None:
            try:
                result = self.controller.deploy_profile(
                    profile_id,
                    retain_official_auth,
                    progress=lambda message: self.worker_queue.put(("deploy_progress", message)),
                )
                self.worker_queue.put(("deploy_done", result))
            except Exception as exc:
                self.worker_queue.put(("deploy_failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_worker_events(self) -> None:
        try:
            while True:
                event, payload = self.worker_queue.get_nowait()
                if event in {"install_progress", "deploy_progress"}:
                    if event == "install_progress" and hasattr(self, "wizard_thread_status"):
                        self.wizard_thread_status.set(str(payload))
                    elif hasattr(self, "deploy_status"):
                        self.deploy_status.set(str(payload))
                elif event == "install_done":
                    _, version = payload  # type: ignore[misc]
                    self.wizard_thread_status.set(f"已就绪 · {version}")
                    self.wizard_install_button.configure(state="normal", text="一键安装 / 更新")
                elif event == "install_failed":
                    self.wizard_thread_status.set(f"安装失败：{payload}")
                    self.wizard_install_button.configure(state="normal", text="重试安装")
                elif event == "deploy_done":
                    result = payload  # type: ignore[assignment]
                    profile = self.controller.settings.profiles[result.profile_id]
                    self.deploy_status.set(f"已启用 {profile.display_name}")
                    detail = "首次部署完成。"
                    if result.warnings:
                        detail += "\n\n" + "\n".join(result.warnings)
                    self.grab_release()
                    self.destroy()
                    self.on_complete()
                    messagebox.showinfo(APP_NAME, detail, parent=self.parent_app)
                    return
                elif event == "deploy_failed":
                    self.deploy_status.set(f"部署失败：{payload}")
                    self.next_button.configure(state="normal", text="重试部署")
                    self.back_button.configure(state="normal")
                    self.cancel_button.configure(state="normal")
                    messagebox.showerror(APP_NAME, str(payload), parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_worker_events)

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
