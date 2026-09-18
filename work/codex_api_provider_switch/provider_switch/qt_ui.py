from __future__ import annotations

import os
import queue
import subprocess
import threading
import tomllib
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizeGrip,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .auth_manager import AuthStatus
from .catalog import resource_path, validate_catalog_text
from .constants import APP_NAME, APP_VERSION
from .controller import ApplicationController, OperationResult
from .models import ConfigSnapshot, ProviderProfile, ProviderTelemetry, TokenUsage
from .monitor_history import HealthRecord, MonitorHistory, ProviderSummary
from .monitoring import check_provider
from .threadripper import install_threadripper


PROFILE_COLORS = {
    "openai": "#51d39a",
    "relay1": "#63b3ff",
    "relay2": "#ffb35c",
    "glm": "#c89cff",
}

INSTANCE_SERVER_NAME = "CodexProviderSwitch.BackgroundMonitor"


def _format_int(value: int) -> str:
    millions = value / 1_000_000
    if millions >= 100:
        return f"{millions:,.0f}M"
    return f"{millions:,.1f}M"


def _format_quota_windows(items: list[Any]) -> str:
    lines: list[str] = []
    for item in items:
        if item.used_percent is None:
            continue
        reset = f" · 重置 {item.reset_at}" if item.reset_at else ""
        remaining = 100.0 - item.used_percent
        lines.append(f"{item.label}剩余 {remaining:.1f}%{reset}")
    return "\n".join(lines)


def _open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class MaterialBackdrop(QWidget):
    """Paint a calm, code-native backdrop behind the translucent panels."""

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor("#22292d"))
        gradient.setColorAt(0.48, QColor("#4b5457"))
        gradient.setColorAt(1.0, QColor("#292d32"))
        painter.fillRect(self.rect(), gradient)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 13))
        path = QPainterPath()
        path.moveTo(-80, self.height() * 0.23)
        path.lineTo(self.width() * 0.54, -50)
        path.lineTo(self.width() * 0.72, -50)
        path.lineTo(self.width() * 0.08, self.height() * 0.45)
        path.closeSubpath()
        painter.drawPath(path)

        painter.setBrush(QColor(116, 173, 184, 18))
        lower = QPainterPath()
        lower.moveTo(self.width() * 0.22, self.height() + 30)
        lower.lineTo(self.width() + 40, self.height() * 0.43)
        lower.lineTo(self.width() + 40, self.height() * 0.61)
        lower.lineTo(self.width() * 0.48, self.height() + 30)
        lower.closeSubpath()
        painter.drawPath(lower)

        painter.setPen(QColor(255, 255, 255, 7))
        for offset in range(-self.height(), self.width(), 38):
            painter.drawLine(offset, self.height(), offset + self.height(), 0)


class GlassPanel(QFrame):
    def __init__(self, parent: QWidget | None = None, *, strong: bool = False) -> None:
        super().__init__(parent)
        self.setProperty("glassStrong" if strong else "glass", True)


class StatusPill(QLabel):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setProperty("tone", "neutral")

    def set_tone(self, tone: str, text: str) -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class ProviderCard(GlassPanel):
    switch_requested = Signal(str)

    def __init__(self, profile_id: str) -> None:
        super().__init__()
        self.profile_id = profile_id
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(11)
        heading = QHBoxLayout()
        self.mark = QLabel(profile_id[:1].upper())
        self.mark.setFixedSize(38, 38)
        self.mark.setAlignment(Qt.AlignCenter)
        self.mark.setStyleSheet(
            f"background:{PROFILE_COLORS[profile_id]}; color:#172025; border-radius:10px; font-weight:800;"
        )
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.title = QLabel(profile_id)
        self.title.setProperty("class", "cardTitle")
        self.model = QLabel("尚未配置模型")
        self.model.setProperty("class", "muted")
        title_box.addWidget(self.title)
        title_box.addWidget(self.model)
        self.active_pill = StatusPill("待机")
        heading.addWidget(self.mark)
        heading.addLayout(title_box, 1)
        heading.addWidget(self.active_pill)
        root.addLayout(heading)

        self.health = QLabel("等待首次健康检查")
        self.health.setProperty("class", "body")
        self.health.setWordWrap(True)
        root.addWidget(self.health)

        self.usage = QLabel("近 30 天用量：暂无数据")
        self.usage.setProperty("class", "muted")
        root.addWidget(self.usage)
        self.quota = QProgressBar()
        self.quota.setRange(0, 100)
        self.quota.setValue(0)
        self.quota.setTextVisible(False)
        self.quota.setFixedHeight(5)
        root.addWidget(self.quota)
        self.quota_text = QLabel("额度：未查询")
        self.quota_text.setProperty("class", "muted")
        self.quota_text.setWordWrap(True)
        root.addWidget(self.quota_text)
        root.addStretch(1)

        footer = QHBoxLayout()
        self.latency = QLabel("-- ms")
        self.latency.setProperty("class", "muted")
        self.switch_button = QPushButton("切换")
        self.switch_button.setProperty("kind", "primary")
        self.switch_button.clicked.connect(lambda: self.switch_requested.emit(self.profile_id))
        footer.addWidget(self.latency)
        footer.addStretch(1)
        footer.addWidget(self.switch_button)
        root.addLayout(footer)

    def set_profile(self, profile: ProviderProfile) -> None:
        self.title.setText(profile.display_name)
        self.model.setText(profile.model or ("Codex 官方默认" if profile.kind == "official" else "尚未配置模型"))

    def set_active(self, active: bool, provider_key: str = "") -> None:
        if active:
            self.active_pill.set_tone("success", "使用中")
            self.switch_button.setText("当前供应商")
            self.switch_button.setProperty("kind", "current")
            self.switch_button.setEnabled(False)
            self.switch_button.setToolTip("")
            self.setToolTip("")
        else:
            self.active_pill.set_tone("neutral", "待机")
            self.switch_button.setText("切换")
            self.switch_button.setProperty("kind", "primary")
            self.switch_button.setEnabled(True)
            self.setToolTip("")

    def set_available(
        self,
        available: bool,
        *,
        active: bool = False,
        unavailable_text: str = "需登录",
    ) -> None:
        if not available:
            self.active_pill.set_tone("warning", unavailable_text)
            self.switch_button.setText(unavailable_text)
            self.switch_button.setProperty("kind", "primary")
            self.switch_button.setEnabled(False)
            self.setToolTip("")
        elif not active:
            self.active_pill.set_tone("neutral", "待机")
            self.switch_button.setText("切换")
            self.switch_button.setEnabled(True)

    def set_telemetry(self, telemetry: ProviderTelemetry | None) -> None:
        if telemetry is None:
            return
        health = telemetry.health
        tone = {
            "healthy": "success",
            "warning": "warning",
            "auth_error": "danger",
            "offline": "danger",
            "unconfigured": "neutral",
        }.get(health.state, "neutral")
        if self.active_pill.text() != "使用中":
            self.active_pill.set_tone(tone, {
                "healthy": "正常",
                "warning": "注意",
                "auth_error": "认证失败",
                "offline": "离线",
                "unconfigured": "未配置",
            }.get(health.state, health.state))
        self.health.setText(health.message)
        self.latency.setText(f"{health.latency_ms} ms" if health.latency_ms is not None else "-- ms")
        percent_quotas = [item for item in telemetry.quota if item.used_percent is not None]
        quota = percent_quotas[0] if percent_quotas else None
        if quota is not None:
            self.quota.setRange(0, 100)
            self.quota.setValue(round(quota.used_percent or 0))
            self.quota.setToolTip("\n".join(
                f"{item.label}已使用 {item.used_percent:.1f}%" for item in percent_quotas
            ))
            self.quota.setVisible(True)
            self.quota_text.setText(_format_quota_windows(percent_quotas))
        else:
            self.quota.setVisible(False)
            self.quota.setRange(0, 100)
            self.quota.setValue(0)
            if telemetry.quota:
                message = "额度接口正常；未返回套餐总量，无法计算百分比"
            else:
                message = telemetry.quota_message
            self.quota.setToolTip(message)
            self.quota_text.setText(f"额度：{message}")

    def set_usage(self, usage: TokenUsage | None, days: int) -> None:
        if usage is None:
            self.usage.setText(f"近 {days} 天用量：暂无本地会话数据")
            return
        self.usage.setText(
            f"近 {days} 天：{_format_int(usage.total_tokens)} tokens / {usage.sessions} 个会话"
        )


APP_STYLE = """
* {
    color: #f5f8fa;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QWidget { background: transparent; }
QFrame[glass="true"] {
    background: rgba(239, 246, 248, 24);
    border: 1px solid rgba(255, 255, 255, 42);
    border-radius: 16px;
}
QFrame[glassStrong="true"] {
    background: rgba(21, 27, 31, 126);
    border: 1px solid rgba(255, 255, 255, 48);
    border-radius: 18px;
}
QLabel[class="brand"] { font-size: 17px; font-weight: 750; }
QLabel[class="pageTitle"] { font-size: 24px; font-weight: 750; }
QLabel[class="sectionTitle"] { font-size: 16px; font-weight: 700; }
QLabel[class="cardTitle"] { font-size: 15px; font-weight: 700; }
QLabel[class="body"] { color: rgba(250, 252, 253, 224); }
QLabel[class="muted"] { color: rgba(234, 240, 243, 150); font-size: 12px; }
QLabel[tone="neutral"] {
    background: rgba(255,255,255,24); color: rgba(246,250,252,190);
    border: 1px solid rgba(255,255,255,32); border-radius: 9px;
    padding: 3px 9px; font-size: 11px;
}
QLabel[tone="success"] {
    background: rgba(62,210,145,34); color: #92f0c5;
    border: 1px solid rgba(81,211,154,80); border-radius: 9px;
    padding: 3px 9px; font-size: 11px;
}
QLabel[tone="warning"] {
    background: rgba(255,179,92,36); color: #ffd19b;
    border: 1px solid rgba(255,179,92,80); border-radius: 9px;
    padding: 3px 9px; font-size: 11px;
}
QLabel[tone="danger"] {
    background: rgba(255,105,115,34); color: #ffb0b7;
    border: 1px solid rgba(255,105,115,80); border-radius: 9px;
    padding: 3px 9px; font-size: 11px;
}
QPushButton {
    background: rgba(255,255,255,22); border: 1px solid rgba(255,255,255,40);
    border-radius: 8px; padding: 8px 13px; min-height: 18px;
}
QPushButton:hover { background: rgba(255,255,255,38); border-color: rgba(255,255,255,75); }
QPushButton:pressed { background: rgba(255,255,255,16); }
QPushButton:disabled { color: rgba(245,248,250,80); background: rgba(255,255,255,10); }
QPushButton[kind="primary"] {
    background: rgba(111,184,255,205); color: #12202a; border-color: rgba(178,220,255,220);
    font-weight: 700;
}
QPushButton[kind="primary"]:hover { background: rgba(138,200,255,230); }
QPushButton[kind="primary"]:disabled {
    background: rgba(255,179,92,35); color: #ffd19b; border-color: rgba(255,179,92,85);
}
QPushButton[kind="current"] {
    background: rgba(81, 211, 154, 28); color: #a5f4cf; border-color: rgba(81, 211, 154, 72);
}
QPushButton[kind="danger"] { color: #ffc0c5; border-color: rgba(255,105,115,95); }
QPushButton[kind="nav"] {
    text-align: left; border: 0; padding: 11px 13px; background: transparent;
    color: rgba(242,247,249,170);
}
QPushButton[kind="nav"]:hover { background: rgba(255,255,255,18); color: #ffffff; }
QPushButton[kind="nav"][selected="true"] {
    background: rgba(255,255,255,32); color: #ffffff;
    border: 1px solid rgba(255,255,255,34); font-weight: 700;
}
QPushButton[class="chip"] {
    background: rgba(255,255,255,14); border: 1px solid rgba(255,255,255,28);
    border-radius: 6px; padding: 4px 12px; font-size: 12px;
    color: rgba(242,247,249,180); min-width: 32px;
}
QPushButton[class="chip"]:checked {
    background: rgba(111,184,255,50); border-color: rgba(111,184,255,140);
    color: #ffffff; font-weight: 600;
}
QLabel[class="statValue"] { font-size: 26px; font-weight: 700; color: #ffffff; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
    background: rgba(10,16,20,82); border: 1px solid rgba(255,255,255,42);
    border-radius: 8px; padding: 8px 10px; selection-background-color: #5fa9df;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {
    border-color: rgba(120,199,255,180); background: rgba(10,16,20,105);
}
QComboBox::drop-down { border: 0; width: 25px; }
QComboBox QAbstractItemView {
    background: #30383d; border: 1px solid #677178; selection-background-color: #53636d;
}
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid rgba(255,255,255,75); border-radius: 5px; }
QCheckBox::indicator:checked { background: #6fb8ff; border-color: #a9d5ff; }
QProgressBar { background: rgba(255,255,255,18); border: 0; border-radius: 2px; }
QProgressBar::chunk { background: #71d3ae; border-radius: 2px; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,44); border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMessageBox { background: #30383d; }
QToolTip {
    background: rgba(28, 34, 38, 245);
    color: #eef6f9;
    border: 1px solid rgba(255, 255, 255, 70);
    padding: 6px 8px;
}
"""


class MonitorTimelineWidget(QWidget):
    """Draws per-provider uptime bars similar to CCH availability monitoring."""

    COLORS = {
        "up": QColor("#17845b"),
        "warn": QColor("#bd6b18"),
        "down": QColor("#c63f4d"),
        "none": QColor(255, 255, 255, 20),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._providers: dict[str, tuple[str, list[str]]] = {}
        self.setMinimumHeight(120)

    def set_provider(self, profile_id: str, label: str, buckets: list[str]) -> None:
        self._providers[profile_id] = (label, buckets)

    def set_ranges(self, seconds: float) -> None:
        pass

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row_height = 24
        bar_height = 14
        gap = 10
        x_start = 90
        margin = 8
        y = margin
        width = self.width() - x_start - margin
        painter.setFont(QFont("Segoe UI", 9))
        for pid, (label, buckets) in self._providers.items():
            painter.setPen(QColor("#c8d0d4"))
            painter.drawText(QRectF(0, y + 2, x_start - 8, row_height), Qt.AlignRight | Qt.AlignVCenter, label)
            if not buckets:
                buckets = ["none"] * 40
            count = len(buckets)
            seg_width = max(2, width / count) if count > 0 else width
            for i, state in enumerate(buckets):
                color = self.COLORS.get(state, self.COLORS["none"])
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                x = x_start + i * seg_width
                painter.drawRoundedRect(QRectF(x, y + 4, seg_width - 1, bar_height), 3, 3)
            y += row_height + gap
        painter.end()

    def sizeHint(self) -> Any:
        from PySide6.QtCore import QSize
        return QSize(600, len(self._providers) * 34 + 20)


class TokenGaugeWidget(QWidget):
    """Semicircular gauge showing token usage relative to peers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = ""
        self._tokens = 0
        self._ratio = 0.0
        self._color = QColor("#71d3ae")
        self._sessions = 0
        self.setMinimumSize(150, 168)

    def set_data(self, label: str, tokens: int, ratio: float, color: str, sessions: int) -> None:
        self._label = label
        self._tokens = tokens
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = QColor(color)
        self._sessions = sessions
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        margin = 10
        pen_width = 9
        arc_height = 58
        arc_width = min(w - 2 * margin, 120)
        rect = QRectF((w - arc_width) / 2, margin, arc_width, arc_height * 2)
        # Background arc (full semicircle)
        bg_pen = QPen(QColor(255, 255, 255, 30), pen_width)
        bg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(bg_pen)
        painter.drawArc(rect, 0, 180 * 16)
        # Foreground arc (ratio)
        if self._ratio > 0.001:
            fg_pen = QPen(self._color, pen_width)
            fg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(fg_pen)
            span = int(180 * 16 * self._ratio)
            painter.drawArc(rect, 180 * 16, -span)
        # Text stack below the arc
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        text_top = margin + arc_height + 6
        painter.drawText(QRectF(0, text_top, w, 24), Qt.AlignHCenter | Qt.AlignVCenter, _format_int(self._tokens))
        painter.setPen(QColor("#8fa0aa"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(QRectF(0, text_top + 24, w, 13), Qt.AlignHCenter | Qt.AlignVCenter, "tokens")
        # Bottom label
        painter.setPen(QColor("#c8d0d4"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        painter.drawText(QRectF(0, text_top + 37, w, 16), Qt.AlignHCenter | Qt.AlignVCenter, self._label)
        if self._sessions > 0:
            painter.setPen(QColor("#687178"))
            painter.setFont(QFont("Segoe UI", 7))
            painter.drawText(QRectF(0, text_top + 53, w, 12), Qt.AlignHCenter | Qt.AlignVCenter, f"{self._sessions} 会话")
        painter.end()


class ProviderSwitchWindow(QMainWindow):
    def __init__(
        self,
        controller: ApplicationController | None = None,
        *,
        start_monitor: bool = True,
        show_wizard: bool = True,
    ) -> None:
        super().__init__()
        self.controller = controller or ApplicationController()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.app_icon = QIcon(str(resource_path("assets/codex_api_provider_switch_icon.png")))
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)
        self.setMinimumSize(1040, 700)
        self.resize(1220, 790)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(APP_STYLE)

        self._jobs: queue.Queue[tuple[Callable[[Any], None] | None, Any, BaseException | None]] = queue.Queue()
        self._busy_count = 0
        self._monitoring_in_progress = False
        self._local_refresh_in_progress = False
        self._drag_origin: QPoint | None = None
        self._force_exit = False
        self._tray_hint_shown = False
        self._last_tray_issues: tuple[str, ...] = ()
        self._latest_total_tokens = 0
        self.cached_auth = AuthStatus("unknown", False, self.controller.codex_home / "auth.json")
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.provider_cards: dict[str, ProviderCard] = {}
        self._catalog_loaded = False
        self._config_loaded = False
        self.monitor_history = MonitorHistory(
            self.controller.codex_home / "codex-provider-switch" / "monitor-history"
        )

        self._build_shell()
        self._build_dashboard()
        self._build_monitoring_page()
        self._build_providers_page()
        self._build_catalog_page()
        self._build_tools_page()
        self._build_deployment_page()
        self._build_settings_page()
        self._build_tray()
        self.show_page("dashboard")
        self.refresh_all(local_only=True)

        self.job_timer = QTimer(self)
        self.job_timer.timeout.connect(self._drain_jobs)
        self.job_timer.start(90)
        if start_monitor:
            QTimer.singleShot(250, self.refresh_monitoring)
        if show_wizard and not self.controller.settings.setup_complete:
            QTimer.singleShot(300, self.open_setup_wizard)

    def _build_shell(self) -> None:
        backdrop = MaterialBackdrop()
        self.setCentralWidget(backdrop)
        outer = QVBoxLayout(backdrop)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        titlebar = GlassPanel(strong=True)
        titlebar.setFixedHeight(62)
        title_layout = QHBoxLayout(titlebar)
        title_layout.setContentsMargins(18, 9, 10, 9)
        brand_mark = QLabel()
        brand_mark.setFixedSize(36, 36)
        brand_mark.setAlignment(Qt.AlignCenter)
        brand_mark.setPixmap(self.app_icon.pixmap(36, 36))
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel(APP_NAME)
        brand.setProperty("class", "brand")
        version = QLabel(f"v{APP_VERSION} · Provider Control")
        version.setProperty("class", "muted")
        brand_box.addWidget(brand)
        brand_box.addWidget(version)
        self.active_header = StatusPill("正在读取配置")
        self.refresh_button = QPushButton("刷新监控")
        self.refresh_button.clicked.connect(self.refresh_monitoring)
        minimize = QPushButton("−")
        minimize.setFixedSize(34, 34)
        minimize.setToolTip("最小化")
        minimize.clicked.connect(self.showMinimized)
        close = QPushButton("×")
        close.setFixedSize(34, 34)
        close.setToolTip("隐藏到系统托盘")
        close.setProperty("kind", "danger")
        close.clicked.connect(self.close)
        title_layout.addWidget(brand_mark)
        title_layout.addLayout(brand_box)
        title_layout.addStretch(1)
        title_layout.addWidget(self.active_header)
        title_layout.addWidget(self.refresh_button)
        title_layout.addWidget(minimize)
        title_layout.addWidget(close)
        outer.addWidget(titlebar)

        body = QHBoxLayout()
        body.setSpacing(10)
        navigation = GlassPanel(strong=True)
        navigation.setFixedWidth(190)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(12, 14, 12, 14)
        nav_layout.setSpacing(5)
        destinations = (
            ("dashboard", "总览"),
            ("monitoring", "监控面板"),
            ("providers", "供应商配置"),
            ("catalog", "配置预览及调整"),
            ("tools", "会话工具"),
            ("deployment", "部署与登录"),
            ("settings", "通用设置"),
        )
        for page_id, label in destinations:
            button = QPushButton(label)
            button.setProperty("kind", "nav")
            button.clicked.connect(lambda checked=False, target=page_id: self.show_page(target))
            self.nav_buttons[page_id] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch(1)
        privacy = QLabel("API Key 仅以 Windows DPAPI\n加密保存在本机")
        privacy.setProperty("class", "muted")
        privacy.setWordWrap(True)
        nav_layout.addWidget(privacy)
        body.addWidget(navigation)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        footer = GlassPanel(strong=True)
        footer.setFixedHeight(38)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 5, 14, 5)
        self.footer_status = QLabel("就绪")
        self.footer_status.setProperty("class", "muted")
        footer_layout.addWidget(self.footer_status)
        footer_layout.addStretch(1)
        self.size_grip = QSizeGrip(footer)
        self.size_grip.setFixedSize(16, 16)
        footer_layout.addWidget(self.size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        outer.addWidget(footer)

    def _build_tray(self) -> None:
        self.tray_icon: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self.app_icon, self)
        tray.setToolTip(f"{APP_NAME} · 正在读取监控状态")
        menu = QMenu()
        show_action = QAction("打开控制台", menu)
        show_action.triggered.connect(self.restore_from_tray)
        refresh_action = QAction("立即刷新监控", menu)
        refresh_action.triggered.connect(self.refresh_monitoring)
        exit_action = QAction("退出后台监控", menu)
        exit_action.triggered.connect(self.exit_application)
        menu.addAction(show_action)
        menu.addAction(refresh_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._handle_tray_activation)
        tray.show()
        self.tray_menu = menu
        self.tray_icon = tray

    def _handle_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_from_tray()

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self) -> bool:
        if self.tray_icon is None or not self.tray_icon.isVisible():
            return False
        self.hide()
        if not self._tray_hint_shown:
            self.tray_icon.showMessage(
                APP_NAME,
                "控制台已在系统托盘后台运行，链路、额度和 Token 仍会定时刷新。",
                QSystemTrayIcon.MessageIcon.Information,
                4500,
            )
            self._tray_hint_shown = True
        return True

    def exit_application(self) -> None:
        self._force_exit = True
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event: Any) -> None:
        if (
            not self._force_exit
            and self.controller.settings.close_to_tray
            and self.hide_to_tray()
        ):
            event.ignore()
            return
        if self.tray_icon is not None:
            self.tray_icon.hide()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton and event.position().y() < 78:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def _page(self, page_id: str, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(14)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        heading = QLabel(title)
        heading.setProperty("class", "pageTitle")
        caption = QLabel(subtitle)
        caption.setProperty("class", "muted")
        title_box.addWidget(heading)
        title_box.addWidget(caption)
        header.addLayout(title_box)
        header.addStretch(1)
        layout.addLayout(header)
        self.pages[page_id] = page
        self.stack.addWidget(page)
        return page, layout

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("class", "sectionTitle")
        return label

    def _build_dashboard(self) -> None:
        page, layout = self._page(
            "dashboard",
            "供应商总览",
            "一眼查看当前路由、链路健康、额度和本地会话用量",
        )
        self.dashboard_summary = QLabel("正在读取 Codex 配置…")
        self.dashboard_summary.setProperty("class", "body")
        layout.addWidget(self.dashboard_summary)
        cards = QWidget()
        self.cards_grid = QGridLayout(cards)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(12)
        self.cards_grid.setVerticalSpacing(12)
        for index, profile_id in enumerate(("openai", "relay1", "relay2", "glm")):
            card = ProviderCard(profile_id)
            card.switch_requested.connect(self.confirm_switch)
            self.provider_cards[profile_id] = card
            self.cards_grid.addWidget(card, index // 2, index % 2)
        layout.addWidget(cards, 1)

        bottom = QHBoxLayout()
        self.token_summary = GlassPanel()
        token_layout = QVBoxLayout(self.token_summary)
        token_layout.setContentsMargins(16, 13, 16, 13)
        token_layout.addWidget(self._section_title("本地 Token 统计"))
        self.token_text = QLabel("暂无会话数据")
        self.token_text.setProperty("class", "body")
        self.token_text.setWordWrap(True)
        token_layout.addWidget(self.token_text)
        self.alert_summary = GlassPanel()
        alert_layout = QVBoxLayout(self.alert_summary)
        alert_layout.setContentsMargins(16, 13, 16, 13)
        alert_layout.addWidget(self._section_title("运行提示"))
        self.alert_text = QLabel("监控将在启动后自动运行")
        self.alert_text.setProperty("class", "body")
        self.alert_text.setWordWrap(True)
        alert_layout.addWidget(self.alert_text)
        self.disconnect_others_button = QPushButton("断开其他 API 供应商")
        self.disconnect_others_button.setToolTip("清除配置中其他供应商的 bearer 连接字段，仅保留当前供应商")
        self.disconnect_others_button.clicked.connect(self.confirm_disconnect_others)
        alert_layout.addWidget(self.disconnect_others_button, 0, Qt.AlignLeft)
        bottom.addWidget(self.token_summary, 1)
        bottom.addWidget(self.alert_summary, 1)
        layout.addLayout(bottom)

    def _build_monitoring_page(self) -> None:
        page, layout = self._page(
            "monitoring",
            "监控面板",
            "实时查看供应商可用性、延迟和 Token 使用量趋势",
        )
        # Summary cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self.monitor_availability_card = self._monitor_stat_card("系统可用性", "--")
        self.monitor_latency_card = self._monitor_stat_card("平均延迟", "--")
        self.monitor_error_card = self._monitor_stat_card("错误率", "--")
        self.monitor_providers_card = self._monitor_stat_card("活跃供应商", "--")
        cards_row.addWidget(self.monitor_availability_card, 1)
        cards_row.addWidget(self.monitor_latency_card, 1)
        cards_row.addWidget(self.monitor_error_card, 1)
        cards_row.addWidget(self.monitor_providers_card, 1)
        layout.addLayout(cards_row)

        # Time range selector
        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        range_label = QLabel("时间范围")
        range_label.setProperty("class", "muted")
        range_row.addWidget(range_label)
        self._monitor_range_group = QButtonGroup(self)
        self._monitor_range_group.setExclusive(True)
        ranges = (
            ("15m", 15 * 60),
            ("1h", 3600),
            ("6h", 6 * 3600),
            ("24h", 24 * 3600),
            ("7d", 7 * 24 * 3600),
        )
        self._monitor_ranges: dict[str, float] = {}
        for label, seconds in ranges:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setProperty("class", "chip")
            btn.clicked.connect(lambda checked=False, s=seconds: self._refresh_monitor_page())
            self._monitor_range_group.addButton(btn)
            range_row.addWidget(btn)
            self._monitor_ranges[label] = seconds
            if seconds == 3600:
                btn.setChecked(True)
        range_row.addStretch(1)
        layout.addLayout(range_row)

        # Timeline panel
        timeline_panel = GlassPanel(strong=True)
        timeline_layout = QVBoxLayout(timeline_panel)
        timeline_layout.setContentsMargins(16, 14, 16, 14)
        timeline_layout.addWidget(self._section_title("供应商可用性时间线"))
        self._timeline_labels: list[str] = []
        self._timeline_widget = MonitorTimelineWidget()
        timeline_layout.addWidget(self._timeline_widget, 1)
        layout.addWidget(timeline_panel, 1)

        # Token usage gauges
        token_panel = GlassPanel()
        token_layout = QVBoxLayout(token_panel)
        token_layout.setContentsMargins(16, 13, 16, 13)
        token_header = QHBoxLayout()
        token_header.addWidget(self._section_title("供应商 Token 使用量"))
        token_header.addStretch(1)
        self._token_range_group = QButtonGroup(self)
        self._token_range_group.setExclusive(True)
        token_ranges = (
            ("15m", 15 * 60),
            ("1h", 3600),
            ("6h", 6 * 3600),
            ("24h", 24 * 3600),
            ("7d", 7 * 86400),
            ("30d", 30 * 86400),
        )
        self._token_range_seconds: dict[str, float] = {}
        for label, seconds in token_ranges:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setProperty("class", "chip")
            btn.clicked.connect(lambda checked=False: self._refresh_token_gauges())
            self._token_range_group.addButton(btn)
            token_header.addWidget(btn)
            self._token_range_seconds[label] = seconds
            if seconds == 86400:
                btn.setChecked(True)
        token_layout.addLayout(token_header)
        self.token_gauges_grid = QHBoxLayout()
        self.token_gauges_grid.setSpacing(10)
        self._token_gauge_widgets: dict[str, TokenGaugeWidget] = {}
        gauge_colors = {"openai": "#138a68", "relay1": "#1879b8", "relay2": "#c06a1b", "glm": "#7257b5"}
        for pid in ("openai", "relay1", "relay2", "glm"):
            gauge = TokenGaugeWidget()
            gauge.set_data("", 0, 0.0, gauge_colors[pid], 0)
            self._token_gauge_widgets[pid] = gauge
            self.token_gauges_grid.addWidget(gauge, 1)
        token_layout.addLayout(self.token_gauges_grid)
        layout.addWidget(token_panel)

    def _monitor_stat_card(self, title: str, initial: str) -> GlassPanel:
        panel = GlassPanel()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 12, 16, 12)
        label = QLabel(title)
        label.setProperty("class", "muted")
        value = QLabel(initial)
        value.setProperty("class", "statValue")
        lay.addWidget(label)
        lay.addWidget(value)
        panel._stat_value = value
        return panel

    def _set_monitor_stat(self, panel: GlassPanel, value: str) -> None:
        panel._stat_value.setText(value)

    def _refresh_monitor_page(self) -> None:
        checked = self._monitor_range_group.checkedButton()
        seconds = 3600.0
        if checked is not None:
            seconds = self._monitor_ranges.get(checked.text(), 3600.0)
        records = self.monitor_history.load(max_age_seconds=seconds)
        profile_ids = ["openai", "relay1", "relay2", "glm"]
        summaries = self.monitor_history.summarize(records, profile_ids)
        availabilities = [s.availability for s in summaries.values() if s.availability is not None]
        overall_avail = sum(availabilities) / len(availabilities) if availabilities else None
        latencies = [s.avg_latency_ms for s in summaries.values() if s.avg_latency_ms is not None]
        overall_latency = sum(latencies) / len(latencies) if latencies else None
        errors = [s.error_rate for s in summaries.values() if s.error_rate is not None]
        overall_error = sum(errors) / len(errors) if errors else None
        active = sum(1 for s in summaries.values() if s.total_checks > 0)
        self._set_monitor_stat(
            self.monitor_availability_card,
            f"{overall_avail:.1f}%" if overall_avail is not None else "--",
        )
        self._set_monitor_stat(
            self.monitor_latency_card,
            f"{overall_latency:.0f} ms" if overall_latency is not None else "--",
        )
        self._set_monitor_stat(
            self.monitor_error_card,
            f"{overall_error:.1f}%" if overall_error is not None else "--",
        )
        self._set_monitor_stat(self.monitor_providers_card, f"{active}/{len(profile_ids)}")

        # Timeline
        self._timeline_widget.set_ranges(seconds)
        for pid in profile_ids:
            buckets = self.monitor_history.timeline_buckets(records, pid, seconds)
            profile = self.controller.settings.profiles.get(pid)
            label = profile.display_name if profile else pid
            self._timeline_widget.set_provider(pid, label, buckets)
        self._timeline_widget.repaint()

    def _record_monitor_history(self, telemetry: dict[str, ProviderTelemetry]) -> None:
        import time as _time
        now = _time.time()
        records = []
        for pid, item in telemetry.items():
            records.append(
                HealthRecord(
                    timestamp=now,
                    profile_id=pid,
                    state=item.health.state,
                    latency_ms=item.health.latency_ms,
                )
            )
        self.monitor_history.append(records)
        self._refresh_monitor_page()

    def _update_monitor_tokens(self, usage: dict[str, TokenUsage]) -> None:
        self._latest_full_usage = usage
        self._refresh_token_gauges()

    def _refresh_token_gauges(self) -> None:
        checked = self._token_range_group.checkedButton()
        seconds = 86400.0
        if checked is not None:
            seconds = self._token_range_seconds.get(checked.text(), 86400.0)
        def worker() -> dict[str, TokenUsage]:
            return self.controller.token_usage_seconds(seconds)

        def done(result: dict[str, TokenUsage] | BaseException) -> None:
            if isinstance(result, BaseException):
                return
            usage = result
            max_tokens = max((u.total_tokens for u in usage.values()), default=0)
            colors = {"openai": "#138a68", "relay1": "#1879b8", "relay2": "#c06a1b", "glm": "#7257b5"}
            for pid in ("openai", "relay1", "relay2", "glm"):
                profile = self.controller.settings.profiles.get(pid)
                gauge = self._token_gauge_widgets.get(pid)
                if profile is None or gauge is None:
                    continue
                item = usage.get(pid) or usage.get(profile.provider_key)
                tokens = item.total_tokens if item else 0
                sessions = item.sessions if item else 0
                ratio = (tokens / max_tokens) if max_tokens > 0 else 0.0
                gauge.set_data(profile.display_name, tokens, ratio, colors[pid], sessions)

        self._run_job(worker, done)

    def _build_providers_page(self) -> None:
        page, layout = self._page(
            "providers",
            "供应商配置",
            "API1 / API2 使用独立 API Key；provider 标签默认保持稳定",
        )
        panel = GlassPanel(strong=True)
        form = QFormLayout(panel)
        form.setContentsMargins(20, 18, 20, 18)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(250)
        self.profile_combo.currentIndexChanged.connect(self._load_profile_form)
        for profile_id in ("relay1", "relay2", "glm"):
            self.profile_combo.addItem(self.controller.settings.profiles[profile_id].display_name, profile_id)
        form.addRow("编辑供应商", self.profile_combo)
        self.profile_name = QLineEdit()
        form.addRow("显示名称", self.profile_name)
        self.profile_key = QLineEdit()
        self.profile_key.setPlaceholderText("例如 relay_1；保存后会保持该 provider 标签")
        form.addRow("Provider 标签", self.profile_key)
        self.profile_url = QLineEdit()
        self.profile_url.setPlaceholderText("https://…/v1")
        form.addRow("API 地址", self.profile_url)
        self.profile_model = QLineEdit()
        form.addRow("默认模型", self.profile_model)
        self.profile_secret = QLineEdit()
        self.profile_secret.setEchoMode(QLineEdit.Password)
        self.profile_secret.setPlaceholderText("留空表示保留已保存 Key")
        form.addRow("API Key", self.profile_secret)
        self.profile_quota = QLineEdit()
        self.profile_quota.setPlaceholderText("可选：额度 JSON 接口")
        form.addRow("额度查询地址", self.profile_quota)
        self.profile_quota_remaining = QLineEdit()
        self.profile_quota_remaining.setPlaceholderText("例如 data.remaining")
        form.addRow("剩余字段路径", self.profile_quota_remaining)
        self.profile_quota_total = QLineEdit()
        self.profile_quota_total.setPlaceholderText("例如 data.total")
        form.addRow("总量字段路径", self.profile_quota_total)
        self.profile_quota_org_label = QLabel("GLM 团队组织 ID")
        self.profile_quota_org = QLineEdit()
        self.profile_quota_org.setPlaceholderText("团队套餐选填，例如 org-xxxxxx")
        self.profile_quota_org.setMinimumHeight(34)
        self.profile_quota_org.setFont(QFont("Cascadia Code", 10))
        form.addRow(self.profile_quota_org_label, self.profile_quota_org)
        self.profile_quota_project_label = QLabel("GLM 团队项目 ID（proj_…）")
        self.profile_quota_project = QLineEdit()
        self.profile_quota_project.setPlaceholderText("团队套餐选填，例如 proj_xxxxxxx")
        self.profile_quota_project.setMinimumHeight(34)
        self.profile_quota_project.setFont(QFont("Cascadia Code", 10))
        form.addRow(self.profile_quota_project_label, self.profile_quota_project)
        self.save_profile_button = QPushButton("保存配置")
        self.save_profile_button.setProperty("kind", "primary")
        self.save_profile_button.clicked.connect(self.save_profile_form)
        self.test_profile_button = QPushButton("测试链路")
        self.test_profile_button.clicked.connect(self.test_selected_profile)
        button_row = QHBoxLayout()
        button_row.addWidget(self.save_profile_button)
        button_row.addWidget(self.test_profile_button)
        button_row.addStretch(1)
        form.addRow("", button_row)
        layout.addWidget(panel)
        note = QLabel(
            "中转默认自动探测 Sub2API /v1/usage 额度；也可填专用额度接口。"
            "GLM 团队套餐需同时填写组织 ID 和项目 ID；项目 ID 使用下划线格式 proj_xxxxxxx。"
            "官方直连登录态策略在“部署与登录”中统一管理。"
        )
        note.setProperty("class", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self._load_profile_form()

    def _selected_profile_id(self) -> str:
        return str(self.profile_combo.currentData() or "relay1")

    def _load_profile_form(self) -> None:
        if not hasattr(self, "profile_combo") or not hasattr(self, "profile_name"):
            return
        profile_id = self._selected_profile_id()
        profile = self.controller.settings.profiles[profile_id]
        self.profile_name.setText(profile.display_name)
        self.profile_key.setText(profile.provider_key)
        self.profile_url.setText(profile.base_url)
        self.profile_model.setText(profile.model)
        self.profile_secret.clear()
        self.profile_quota.setText(profile.quota_url)
        self.profile_quota_remaining.setText(profile.quota_remaining_path)
        self.profile_quota_total.setText(profile.quota_total_path)
        self.profile_quota_org.setText(profile.quota_organization_id)
        self.profile_quota_project.setText(profile.quota_project_id)
        team_visible = profile.kind == "glm"
        self.profile_quota_org_label.setVisible(team_visible)
        self.profile_quota_org.setVisible(team_visible)
        self.profile_quota_project_label.setVisible(team_visible)
        self.profile_quota_project.setVisible(team_visible)

    def save_profile_form(self) -> None:
        profile_id = self._selected_profile_id()
        profile = self.controller.settings.profiles[profile_id]
        profile.display_name = self.profile_name.text().strip()
        profile.provider_key = self.profile_key.text().strip()
        profile.base_url = self.profile_url.text().strip()
        profile.model = self.profile_model.text().strip()
        profile.quota_url = self.profile_quota.text().strip()
        profile.quota_remaining_path = self.profile_quota_remaining.text().strip()
        profile.quota_total_path = self.profile_quota_total.text().strip()
        if profile.kind == "glm":
            profile.quota_organization_id = self.profile_quota_org.text().strip()
            profile.quota_project_id = self.profile_quota_project.text().strip()
        secret = self.profile_secret.text().strip()
        if secret:
            self.controller.credentials[profile_id] = secret
        self.save_profile_button.setEnabled(False)
        self.test_profile_button.setEnabled(False)
        self._set_busy(True, f"正在保存 {profile.display_name}")
        self._run_job(
            self.controller.save,
            lambda result: self._handle_profile_saved(profile_id, profile.display_name),
            self._handle_profile_save_error,
        )

    def _handle_profile_saved(self, profile_id: str, display_name: str) -> None:
        index = self.profile_combo.findData(profile_id)
        if index >= 0:
            self.profile_combo.setItemText(index, display_name)
        self.profile_secret.clear()
        self.save_profile_button.setEnabled(True)
        self.test_profile_button.setEnabled(True)
        self._set_busy(False, f"已保存 {display_name}")
        self.refresh_all(local_only=True)

    def _handle_profile_save_error(self, exc: BaseException) -> None:
        self.save_profile_button.setEnabled(True)
        self.test_profile_button.setEnabled(True)
        self._set_busy(False, "保存失败")
        QMessageBox.warning(self, "保存失败", str(exc))

    def _legacy_save_profile_form(self) -> None:
        profile_id = self._selected_profile_id()
        profile = self.controller.settings.profiles[profile_id]
        profile.display_name = self.profile_name.text().strip()
        profile.provider_key = self.profile_key.text().strip()
        profile.base_url = self.profile_url.text().strip()
        profile.model = self.profile_model.text().strip()
        profile.quota_url = self.profile_quota.text().strip()
        profile.quota_remaining_path = self.profile_quota_remaining.text().strip()
        profile.quota_total_path = self.profile_quota_total.text().strip()
        if profile.kind == "glm":
            profile.quota_organization_id = self.profile_quota_org.text().strip()
            profile.quota_project_id = self.profile_quota_project.text().strip()
        secret = self.profile_secret.text().strip()
        if secret:
            self.controller.credentials[profile_id] = secret
        try:
            self.controller.save()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.profile_combo.setItemText(self.profile_combo.currentIndex(), profile.display_name)
        self.profile_secret.clear()
        self._set_footer(f"已保存 {profile.display_name}")
        self.refresh_all(local_only=True)

    def test_selected_profile(self) -> None:
        profile_id = self._selected_profile_id()
        profile = self.controller.settings.profiles[profile_id]
        self._set_busy(True, f"正在测试 {profile.display_name}")
        self._run_job(
            lambda: check_provider(
                profile,
                self.controller.credentials.get(profile_id, ""),
                codex_home=self.controller.codex_home,
                retain_official_auth=self.controller.settings.retain_official_auth,
            ),
            lambda result: self._handle_profile_test(result),
        )

    def _handle_profile_test(self, result: ProviderTelemetry) -> None:
        self._set_busy(False, "测试完成")
        QMessageBox.information(self, "链路测试", f"{result.health.message}\n延迟：{result.health.latency_ms or '--'} ms")

    def _build_catalog_page(self) -> None:
        page, layout = self._page(
            "catalog",
            "配置预览及调整",
            "编辑 GLM models.json 和当前供应商 config.toml；人工调整会用于后续切换",
        )
        panel = GlassPanel(strong=True)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        toolbar = QHBoxLayout()
        self.catalog_path_label = QLabel(str(self.controller.catalog.target_path))
        self.catalog_path_label.setProperty("class", "muted")
        toolbar.addWidget(self.catalog_path_label, 1)
        for label, slot in (
            ("读取本机", self.load_catalog_from_disk),
            ("恢复内置", self.load_bundled_catalog),
            ("校验", self.validate_catalog_editor),
            ("保存并注入", self.save_catalog_editor),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        panel_layout.addLayout(toolbar)
        self.catalog_editor = QPlainTextEdit()
        self.catalog_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.catalog_editor.setFont(QFont("Cascadia Code", 10))
        panel_layout.addWidget(self.catalog_editor, 1)
        layout.addWidget(panel, 1)
        self.catalog_hint = QLabel("尚未载入 models.json")
        self.catalog_hint.setProperty("class", "muted")
        layout.addWidget(self.catalog_hint)

        config_panel = GlassPanel(strong=True)
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(16, 14, 16, 14)
        config_toolbar = QHBoxLayout()
        self.config_scope_label = QLabel("当前 config.toml")
        self.config_scope_label.setProperty("class", "muted")
        config_toolbar.addWidget(self.config_scope_label, 1)
        for label, slot in (
            ("重新读取", self.load_config_from_disk),
            ("校验 TOML", self.validate_config_editor),
            ("保存并同步供应商", self.save_config_editor),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            config_toolbar.addWidget(button)
        config_layout.addLayout(config_toolbar)
        self.config_editor = QPlainTextEdit()
        self.config_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.config_editor.setFont(QFont("Cascadia Code", 10))
        config_layout.addWidget(self.config_editor, 1)
        layout.addWidget(config_panel, 1)
        self.config_hint = QLabel("尚未载入 config.toml")
        self.config_hint.setProperty("class", "muted")
        layout.addWidget(self.config_hint)

    def _set_catalog_text(self, text: str) -> None:
        self.catalog_editor.setPlainText(text)
        self._catalog_loaded = True
        self.catalog_path_label.setText(str(self.controller.catalog.target_path))

    def load_catalog_from_disk(self) -> None:
        try:
            self._set_catalog_text(self.controller.catalog.current_text())
            _, count = validate_catalog_text(self.catalog_editor.toPlainText())
            self.catalog_hint.setText(f"已读取 {count} 个模型 · {self.controller.catalog.target_path}")
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))

    def load_bundled_catalog(self) -> None:
        try:
            self._set_catalog_text(self.controller.catalog.bundled_text())
            _, count = validate_catalog_text(self.catalog_editor.toPlainText())
            self.catalog_hint.setText(f"已载入内置目录，共 {count} 个模型；点击保存并注入后才会写入本机")
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))

    def validate_catalog_editor(self) -> None:
        try:
            _, count = validate_catalog_text(self.catalog_editor.toPlainText())
            self.catalog_hint.setText(f"校验通过：{count} 个模型")
        except Exception as exc:
            QMessageBox.warning(self, "校验失败", str(exc))

    def save_catalog_editor(self) -> None:
        try:
            target, count, backup = self.controller.catalog.save(self.catalog_editor.toPlainText())
            self.catalog_hint.setText(f"已保存并注入 {count} 个模型：{target}")
            if backup:
                self._set_footer(f"models.json 已备份至 {backup.name}")
            else:
                self._set_footer("models.json 已注入")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def _set_config_text(self, text: str) -> None:
        self.config_editor.setPlainText(text)
        self._config_loaded = True
        self.config_scope_label.setText("当前 config.toml")

    def load_config_from_disk(self) -> None:
        try:
            self._set_config_text(self.controller.config.current_text())
            tomllib.loads(self.config_editor.toPlainText())
            self.config_hint.setText("已读取当前配置；可直接调整，保存后会同步本机供应商档案")
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))

    def validate_config_editor(self) -> None:
        try:
            tomllib.loads(self.config_editor.toPlainText())
            self.config_hint.setText("TOML 校验通过")
        except tomllib.TOMLDecodeError as exc:
            QMessageBox.warning(self, "校验失败", f"配置 TOML 无效：{exc}")

    def save_config_editor(self) -> None:
        text = self.config_editor.toPlainText()
        self._set_busy(True, "正在保存供应商配置")
        self._run_job(
            lambda: self.controller.save_config_text(text),
            lambda snapshot: self._handle_config_saved(snapshot),
            self._handle_config_save_error,
        )

    def _handle_config_saved(self, snapshot: ConfigSnapshot) -> None:
        self._set_busy(False, "供应商配置已保存")
        active = snapshot.model_provider or "openai"
        model = snapshot.model or "Codex 默认"
        self.config_hint.setText(f"已保存配置；当前供应商 {active} · 模型 {model}")
        self._set_footer("配置已保存，后续切换会保留人工兼容调整")
        self.refresh_all(local_only=True)

    def _handle_config_save_error(self, exc: BaseException) -> None:
        self._set_busy(False, "供应商配置保存失败")
        QMessageBox.warning(self, "保存失败", str(exc))

    def _build_tools_page(self) -> None:
        page, layout = self._page(
            "tools",
            "会话工具",
            "安装 Threadripper、同步历史对话并检查当前 provider 标签",
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        thread_panel = GlassPanel(strong=True)
        thread_layout = QVBoxLayout(thread_panel)
        thread_layout.setContentsMargins(18, 16, 18, 16)
        thread_layout.addWidget(self._section_title("Codex Threadripper"))
        self.thread_status = QLabel("正在检查安装状态…")
        self.thread_status.setProperty("class", "body")
        self.thread_status.setWordWrap(True)
        thread_layout.addWidget(self.thread_status)
        self.install_thread_button = QPushButton("一键安装 / 更新")
        self.install_thread_button.setProperty("kind", "primary")
        self.install_thread_button.clicked.connect(self.install_threadripper_ui)
        self.sync_history_button = QPushButton("同步历史会话")
        self.sync_history_button.clicked.connect(self.sync_history_ui)
        thread_layout.addWidget(self.install_thread_button)
        thread_layout.addWidget(self.sync_history_button)
        thread_layout.addStretch(1)
        diag_panel = GlassPanel(strong=True)
        diag_layout = QVBoxLayout(diag_panel)
        diag_layout.setContentsMargins(18, 16, 18, 16)
        diag_layout.addWidget(self._section_title("诊断"))
        self.diagnostics_text = QLabel("等待诊断")
        self.diagnostics_text.setProperty("class", "body")
        self.diagnostics_text.setWordWrap(True)
        diag_layout.addWidget(self.diagnostics_text)
        self.diagnostics_button = QPushButton("运行诊断")
        self.diagnostics_button.clicked.connect(self.run_diagnostics)
        diag_layout.addWidget(self.diagnostics_button)
        diag_layout.addStretch(1)
        grid.addWidget(thread_panel, 0, 0)
        grid.addWidget(diag_panel, 0, 1)
        layout.addLayout(grid)
        log_panel = GlassPanel()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(16, 12, 16, 12)
        log_layout.addWidget(self._section_title("操作日志"))
        self.tool_log = QPlainTextEdit()
        self.tool_log.setReadOnly(True)
        self.tool_log.setMaximumBlockCount(200)
        log_layout.addWidget(self.tool_log)
        layout.addWidget(log_panel, 1)
        self.refresh_tools()

    def _append_tool_log(self, message: str) -> None:
        self.tool_log.appendPlainText(message)

    def refresh_tools(self) -> None:
        try:
            command, version = self.controller.threadripper_status()
            self.thread_status.setText(
                f"已安装：{version}\n{command}" if command else "未安装。点击一键安装后可自动同步历史会话。"
            )
        except Exception as exc:
            self.thread_status.setText(f"状态检查失败：{exc}")

    def install_threadripper_ui(self) -> None:
        self._set_busy(True, "正在下载并校验 Threadripper")
        self.install_thread_button.setEnabled(False)
        self._run_job(
            lambda: install_threadripper(progress=lambda message: self._queue_status(message)),
            lambda result: self._handle_thread_install(result),
            lambda exc: self._handle_tool_error(exc),
        )

    def _handle_thread_install(self, result: tuple[Path, str]) -> None:
        self._set_busy(False, "Threadripper 安装完成")
        self.install_thread_button.setEnabled(True)
        self._append_tool_log(f"安装完成：{result[0]} ({result[1]})")
        self.refresh_tools()

    def sync_history_ui(self) -> None:
        self._set_busy(True, "正在同步历史会话")
        self._run_job(
            lambda: self.controller.sync_history(),
            lambda result: self._handle_tool_message(result),
            lambda exc: self._handle_tool_error(exc),
        )

    def run_diagnostics(self) -> None:
        self._set_busy(True, "正在运行诊断")
        self._run_job(
            lambda: self._diagnostics(),
            lambda result: self._handle_diagnostics(result),
        )

    def _diagnostics(self) -> dict[str, str]:
        command, version = self.controller.threadripper_status()
        auth = self.controller.auth_status()
        snapshot = self.controller.current_snapshot()
        return {
            "config": str(snapshot.path),
            "provider": snapshot.model_provider or "openai 官方默认",
            "threadripper": version or "未安装",
            "auth": auth.label,
            "catalog": str(self.controller.catalog.target_path),
        }

    def _handle_diagnostics(self, result: dict[str, str]) -> None:
        self._set_busy(False, "诊断完成")
        self.diagnostics_text.setText("\n".join(f"{key}：{value}" for key, value in result.items()))
        self._append_tool_log("诊断完成")

    def _handle_tool_message(self, result: str) -> None:
        self._set_busy(False, "操作完成")
        self._append_tool_log(result)
        QMessageBox.information(self, "操作完成", result)

    def _handle_tool_error(self, exc: BaseException) -> None:
        self._set_busy(False, "操作失败")
        self._append_tool_log(str(exc))
        QMessageBox.warning(self, "操作失败", str(exc))

    def _build_deployment_page(self) -> None:
        page, layout = self._page(
            "deployment",
            "部署与登录",
            "首次部署、官方账号登录态和中转认证策略集中在这里管理",
        )
        auth_panel = GlassPanel(strong=True)
        auth_layout = QVBoxLayout(auth_panel)
        auth_layout.setContentsMargins(18, 16, 18, 16)
        auth_layout.addWidget(self._section_title("OpenAI 官方登录态"))
        self.auth_status_label = QLabel("正在检查…")
        self.auth_status_label.setProperty("class", "body")
        self.auth_status_label.setWordWrap(True)
        auth_layout.addWidget(self.auth_status_label)
        self.auth_path_label = QLabel("")
        self.auth_path_label.setProperty("class", "muted")
        self.auth_path_label.setWordWrap(True)
        auth_layout.addWidget(self.auth_path_label)
        auth_buttons = QHBoxLayout()
        self.auth_login_button = QPushButton("登录 / 恢复官方账号")
        self.auth_login_button.setProperty("kind", "primary")
        self.auth_login_button.clicked.connect(self.login_official_ui)
        self.auth_clear_button = QPushButton("清空官方登录态")
        self.auth_clear_button.setProperty("kind", "danger")
        self.auth_clear_button.clicked.connect(self.clear_official_auth_ui)
        self.auth_check_button = QPushButton("重新检查")
        self.auth_check_button.clicked.connect(self.refresh_auth_status)
        auth_buttons.addWidget(self.auth_login_button)
        auth_buttons.addWidget(self.auth_clear_button)
        auth_buttons.addWidget(self.auth_check_button)
        auth_buttons.addStretch(1)
        auth_layout.addLayout(auth_buttons)

        policy_panel = GlassPanel(strong=True)
        policy_layout = QVBoxLayout(policy_panel)
        policy_layout.setContentsMargins(18, 16, 18, 16)
        policy_layout.addWidget(self._section_title("中转认证策略"))
        self.retain_auth_check = QCheckBox("使用 API1 / API2 时保留官方登录态")
        self.retain_auth_check.stateChanged.connect(self._apply_auth_policy)
        policy_layout.addWidget(self.retain_auth_check)
        self.policy_detail = QLabel("")
        self.policy_detail.setProperty("class", "muted")
        self.policy_detail.setWordWrap(True)
        policy_layout.addWidget(self.policy_detail)
        self.wizard_button = QPushButton("重新运行首次部署向导")
        self.wizard_button.clicked.connect(self.open_setup_wizard)
        policy_layout.addWidget(self.wizard_button)

        layout.addWidget(auth_panel)
        layout.addWidget(policy_panel)
        route_panel = GlassPanel()
        route_layout = QVBoxLayout(route_panel)
        route_layout.setContentsMargins(18, 14, 18, 14)
        route_layout.addWidget(self._section_title("当前配置位置"))
        self.route_label = QLabel("")
        self.route_label.setProperty("class", "body")
        self.route_label.setWordWrap(True)
        route_layout.addWidget(self.route_label)
        layout.addWidget(route_panel)
        layout.addStretch(1)
        self.refresh_deployment_view()

    def refresh_deployment_view(self) -> None:
        self.retain_auth_check.blockSignals(True)
        self.retain_auth_check.setChecked(self.controller.settings.retain_official_auth)
        self.retain_auth_check.blockSignals(False)
        self._update_policy_text()
        self.refresh_auth_status()
        try:
            snapshot = self.controller.current_snapshot()
            active = self.controller.detect_active_profile()
            self.route_label.setText(
                f"当前 provider：{snapshot.model_provider or 'openai 官方默认'}\n识别路由：{active}"
            )
        except Exception as exc:
            self.route_label.setText(str(exc))

    def _update_policy_text(self) -> None:
        if self.controller.settings.retain_official_auth:
            self.policy_detail.setText("保留官方凭据；中转和 GLM 仍使用各自的 bearer token，官方凭据只负责直连可用状态。")
        else:
            self.policy_detail.setText("独立 API Key 模式：中转不依赖官方登录态；OpenAI 直连按钮会保持禁用，直到重新登录。")

    def _apply_auth_policy(self, state: int) -> None:
        retain = bool(state)
        self.controller.settings.retain_official_auth = retain
        for profile_id in ("relay1", "relay2"):
            self.controller.settings.profiles[profile_id].requires_openai_auth = retain
        try:
            self.controller.save()
            self._update_policy_text()
            self._set_footer("认证策略已保存")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def refresh_auth_status(self) -> None:
        self.auth_check_button.setEnabled(False)
        self._run_job(self.controller.auth_status, self._handle_auth_status, self._handle_auth_error)

    def _handle_auth_status(self, status: AuthStatus) -> None:
        self.cached_auth = status
        self.auth_check_button.setEnabled(True)
        tone = "success" if status.official_account_logged_in else "warning"
        self.auth_status_label.setText(f"{status.label} · {status.state}")
        self.auth_status_label.setProperty("tone", tone)
        self.auth_path_label.setText(f"凭据位置：{status.auth_path}（文件存在：{'是' if status.auth_file_exists else '否'}）")
        self.auth_login_button.setEnabled(not status.official_account_logged_in)
        self.auth_clear_button.setEnabled(
            status.official_account_logged_in or status.auth_file_exists
        )
        self._update_openai_availability()

    def _handle_auth_error(self, exc: BaseException) -> None:
        self.auth_check_button.setEnabled(True)
        self.auth_status_label.setText(f"检查失败：{exc}")
        self._update_openai_availability()

    def login_official_ui(self) -> None:
        self._set_busy(True, "正在启动 Codex 官方登录")
        self.auth_login_button.setEnabled(False)
        self._run_job(
            lambda: self.controller.restore_official_login(progress=lambda message: self._queue_status(message)),
            lambda result: self._handle_login_result(result),
            lambda exc: self._handle_auth_operation_error(exc),
        )

    def _handle_login_result(self, result: tuple[OperationResult | None, AuthStatus]) -> None:
        self._set_busy(False, "官方登录态已恢复")
        self._handle_auth_status(result[1])
        self.refresh_all(local_only=True)
        QMessageBox.information(self, "官方登录", "OpenAI 官方登录态已恢复，直连按钮已重新可用。")

    def clear_official_auth_ui(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空官方登录态",
            "这会删除 Codex 官方 auth，并将当前官方/GLM 路由切回已配置的中转。是否继续？",
        )
        if answer != QMessageBox.Yes:
            return
        self._set_busy(True, "正在清除官方登录态")
        self.auth_clear_button.setEnabled(False)
        self._run_job(
            lambda: self.controller.clear_official_login(progress=lambda message: self._queue_status(message)),
            lambda result: self._handle_clear_result(result),
            lambda exc: self._handle_auth_operation_error(exc),
        )

    def _handle_clear_result(self, result: tuple[OperationResult | None, AuthStatus]) -> None:
        self._set_busy(False, "官方登录态已清除")
        self._handle_auth_status(result[1])
        self.refresh_all(local_only=True)
        QMessageBox.information(self, "已清除", "官方 auth 已清除；API1 / API2 的独立配置保持不变。")

    def _handle_auth_operation_error(self, exc: BaseException) -> None:
        self._set_busy(False, "登录态操作失败")
        self.auth_login_button.setEnabled(True)
        self.auth_clear_button.setEnabled(True)
        QMessageBox.warning(self, "登录态操作失败", str(exc))

    def _build_settings_page(self) -> None:
        page, layout = self._page(
            "settings",
            "通用设置",
            "调整 Codex Home、自动重启、历史同步和监控周期",
        )
        panel = GlassPanel(strong=True)
        form = QFormLayout(panel)
        form.setContentsMargins(20, 18, 20, 18)
        form.setVerticalSpacing(12)
        home_row = QHBoxLayout()
        self.home_edit = QLineEdit(self.controller.settings.codex_home)
        home_row.addWidget(self.home_edit, 1)
        home_button = QPushButton("选择")
        home_button.clicked.connect(self.browse_codex_home)
        home_row.addWidget(home_button)
        form.addRow("Codex Home", home_row)
        self.stable_key_edit = QLineEdit(self.controller.settings.stable_provider_key)
        form.addRow("稳定 provider 标签", self.stable_key_edit)
        self.preserve_key_check = QCheckBox("切换时保持稳定 provider 标签，保护历史对话归属")
        self.preserve_key_check.setChecked(self.controller.settings.preserve_provider_key)
        form.addRow("", self.preserve_key_check)
        self.auto_restart_check = QCheckBox("配置保存后自动重启 Codex（会关闭并重新打开 Codex）")
        self.auto_restart_check.setChecked(self.controller.settings.auto_restart)
        form.addRow("", self.auto_restart_check)
        self.auto_sync_check = QCheckBox("切换后自动同步历史会话")
        self.auto_sync_check.setChecked(self.controller.settings.auto_sync_history)
        form.addRow("", self.auto_sync_check)
        self.close_to_tray_check = QCheckBox("关闭主窗口时驻留系统托盘并继续后台监控")
        self.close_to_tray_check.setChecked(self.controller.settings.close_to_tray)
        self.close_to_tray_check.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        form.addRow("", self.close_to_tray_check)
        self.tray_notifications_check = QCheckBox("链路异常或恢复时显示系统托盘通知")
        self.tray_notifications_check.setChecked(
            self.controller.settings.tray_health_notifications
        )
        self.tray_notifications_check.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        form.addRow("", self.tray_notifications_check)
        self.monitor_spin = QSpinBox()
        self.monitor_spin.setRange(10, 3600)
        self.monitor_spin.setSuffix(" 秒")
        self.monitor_spin.setValue(self.controller.settings.monitor_interval_seconds)
        form.addRow("健康监控周期", self.monitor_spin)
        self.lookback_spin = QSpinBox()
        self.lookback_spin.setRange(1, 3650)
        self.lookback_spin.setSuffix(" 天")
        self.lookback_spin.setValue(self.controller.settings.usage_lookback_days)
        form.addRow("Token 统计窗口", self.lookback_spin)
        save_button = QPushButton("保存通用设置")
        save_button.setProperty("kind", "primary")
        self.save_general_button = save_button
        save_button.clicked.connect(self.save_general_settings)
        form.addRow("", save_button)
        layout.addWidget(panel)
        layout.addStretch(1)

    def browse_codex_home(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择 .codex 目录", self.home_edit.text())
        if selected:
            self.home_edit.setText(selected)

    def save_general_settings(self) -> None:
        path = Path(self.home_edit.text().strip()).expanduser()
        stable_key = self.stable_key_edit.text().strip()
        preserve_key = self.preserve_key_check.isChecked()
        auto_restart = self.auto_restart_check.isChecked()
        auto_sync = self.auto_sync_check.isChecked()
        close_to_tray = self.close_to_tray_check.isChecked()
        tray_notifications = self.tray_notifications_check.isChecked()
        monitor_seconds = self.monitor_spin.value()
        lookback_days = self.lookback_spin.value()
        self.save_general_button.setEnabled(False)
        self._set_busy(True, "正在保存通用设置")

        def worker() -> str:
            self.controller.change_codex_home(path, create=True)
            settings = self.controller.settings
            settings.stable_provider_key = stable_key
            settings.preserve_provider_key = preserve_key
            settings.auto_restart = auto_restart
            settings.auto_sync_history = auto_sync
            settings.close_to_tray = close_to_tray
            settings.tray_health_notifications = tray_notifications
            settings.monitor_interval_seconds = monitor_seconds
            settings.usage_lookback_days = lookback_days
            self.controller.save()
            return str(self.controller.config.config_path)

        self._run_job(
            worker,
            self._handle_general_settings_saved,
            self._handle_general_settings_error,
        )

    def _handle_general_settings_saved(self, config_path: str) -> None:
        self.save_general_button.setEnabled(True)
        self._set_busy(False, "通用设置已保存")
        self.refresh_deployment_view()
        self.refresh_all(local_only=True)

    def _handle_general_settings_error(self, exc: BaseException) -> None:
        self.save_general_button.setEnabled(True)
        self._set_busy(False, "保存失败")
        QMessageBox.warning(self, "保存失败", str(exc))

    def _legacy_save_general_settings(self) -> None:
        path = Path(self.home_edit.text().strip()).expanduser()
        try:
            self.controller.change_codex_home(path, create=True)
            self.controller.settings.stable_provider_key = self.stable_key_edit.text().strip()
            self.controller.settings.preserve_provider_key = self.preserve_key_check.isChecked()
            self.controller.settings.auto_restart = self.auto_restart_check.isChecked()
            self.controller.settings.auto_sync_history = self.auto_sync_check.isChecked()
            self.controller.settings.close_to_tray = self.close_to_tray_check.isChecked()
            self.controller.settings.tray_health_notifications = (
                self.tray_notifications_check.isChecked()
            )
            self.controller.settings.monitor_interval_seconds = self.monitor_spin.value()
            self.controller.settings.usage_lookback_days = self.lookback_spin.value()
            self.controller.save()
            self.refresh_deployment_view()
            self.refresh_all(local_only=True)
            self._set_footer("通用设置已保存")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def show_page(self, page_id: str) -> None:
        if page_id not in self.pages:
            return
        self.stack.setCurrentWidget(self.pages[page_id])
        for name, button in self.nav_buttons.items():
            button.setProperty("selected", name == page_id)
            button.style().unpolish(button)
            button.style().polish(button)
        if page_id == "catalog" and not self._catalog_loaded:
            self.load_catalog_from_disk()
        if page_id == "catalog" and not self._config_loaded:
            self.load_config_from_disk()
        if page_id == "deployment":
            self.refresh_deployment_view()

    def _set_footer(self, text: str) -> None:
        self.footer_status.setText(text)

    def _queue_status(self, text: str) -> None:
        self._jobs.put((lambda message: self._set_footer(str(message)), text, None))

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy_count = max(0, self._busy_count + (1 if busy else -1))
        if status:
            self._set_footer(status)
        self.refresh_button.setEnabled(self._busy_count == 0)
        if self._busy_count:
            self.active_header.set_tone("warning", "处理中")
        else:
            self.active_header.set_tone("success", "监控在线")

    def _run_job(
        self,
        worker: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        def execute() -> None:
            try:
                result = worker()
                self._jobs.put((success, result, None))
            except BaseException as exc:  # keep UI alive for service/network errors
                self._jobs.put((failure, None, exc))

        threading.Thread(target=execute, daemon=True).start()

    def _drain_jobs(self) -> None:
        while True:
            try:
                callback, result, error = self._jobs.get_nowait()
            except queue.Empty:
                return
            if callback is not None:
                callback(error if error is not None else result)

    def refresh_all(self, *, local_only: bool = False) -> None:
        if not local_only:
            self.refresh_monitoring()
            return
        if self._local_refresh_in_progress:
            return
        self._local_refresh_in_progress = True
        self._set_busy(True, "正在刷新配置与 Token")
        self._run_job(
            self._collect_local_state,
            self._handle_local_state,
            self._handle_local_state_error,
        )

    def _collect_local_state(
        self,
    ) -> tuple[ConfigSnapshot, str, dict[str, TokenUsage]]:
        return (
            self.controller.current_snapshot(),
            self.controller.detect_active_profile() or "openai",
            self.controller.token_usage(),
        )

    def _handle_local_state(
        self,
        state: tuple[ConfigSnapshot, str, dict[str, TokenUsage]],
    ) -> None:
        snapshot, active, usage = state
        self._local_refresh_in_progress = False
        self._set_busy(False, "配置与 Token 已刷新")
        try:
            self.dashboard_summary.setText(
                f"当前路由：{active or 'openai'}  ·  provider 标签：{snapshot.model_provider or '官方默认'}  ·  模型：{snapshot.model or 'Codex 默认'}"
            )
            for profile_id, card in self.provider_cards.items():
                profile = self.controller.settings.profiles[profile_id]
                is_active = profile_id == active
                card.set_profile(profile)
                card.set_active(is_active, snapshot.model_provider if is_active else "")
                if profile_id != "openai":
                    card.set_available(
                        is_active or self.controller.profile_ready(profile_id),
                        active=is_active,
                        unavailable_text="未配置",
                    )
            self._update_openai_availability()
            self._update_usage(usage)
        except Exception as exc:
            self.dashboard_summary.setText(f"读取配置失败：{exc}")

    def _handle_local_state_error(self, exc: BaseException) -> None:
        self._local_refresh_in_progress = False
        self._set_busy(False, "本地刷新失败")
        self.dashboard_summary.setText(f"读取配置失败：{exc}")

    def _update_usage(self, usage: dict[str, TokenUsage]) -> None:
        total = sum(item.total_tokens for item in usage.values())
        self._latest_total_tokens = total
        sessions = sum(item.sessions for item in usage.values())
        days = self.controller.settings.usage_lookback_days
        self.token_text.setText(f"近 {days} 天共 {_format_int(total)} tokens，{sessions} 个本地会话。")
        for profile_id, card in self.provider_cards.items():
            profile = self.controller.settings.profiles[profile_id]
            item = usage.get(profile.provider_key) or usage.get(profile_id)
            card.set_usage(item, days)

    def _update_openai_availability(self) -> None:
        available = self.controller.openai_available(self.cached_auth)
        active = False
        try:
            active = self.controller.detect_active_profile() == "openai"
        except Exception:
            pass
        card = self.provider_cards.get("openai")
        if card:
            card.set_active(active, "openai" if active else "")
            card.set_available(available, active=active)
        if not available:
            self.alert_text.setText("官方直连暂不可用：请到“部署与登录”完成 OpenAI 登录；中转和 GLM 配置不受影响。")
        else:
            self.alert_text.setText("官方直连可用；API1 / API2 和 GLM 可独立切换，当前监控会定期刷新。")

    def refresh_monitoring(self) -> None:
        if self._monitoring_in_progress:
            return
        self._monitoring_in_progress = True
        self._busy_count += 1
        self.refresh_button.setEnabled(False)
        self.active_header.set_tone("warning", "检查中")
        self._run_job(
            self._collect_monitoring,
            self._handle_monitoring_snapshot,
            self._handle_monitoring_error,
        )

    def _collect_monitoring(
        self,
    ) -> tuple[dict[str, ProviderTelemetry], dict[str, TokenUsage]]:
        return self.controller.check_active(), self.controller.token_usage()

    def _handle_monitoring_snapshot(
        self,
        snapshot: tuple[dict[str, ProviderTelemetry], dict[str, TokenUsage]],
    ) -> None:
        telemetry, usage = snapshot
        self._update_usage(usage)
        self._update_monitor_tokens(usage)
        self._handle_telemetry(telemetry)

    def _handle_telemetry(self, telemetry: dict[str, ProviderTelemetry]) -> None:
        self._monitoring_in_progress = False
        self._busy_count = max(0, self._busy_count - 1)
        self.refresh_button.setEnabled(self._busy_count == 0)
        if self._busy_count:
            self.active_header.set_tone("warning", "处理中")
        else:
            self.active_header.set_tone("success", "监控在线")
        for profile_id, card in self.provider_cards.items():
            card.set_telemetry(telemetry.get(profile_id))
        bad = [item.health.message for item in telemetry.values() if item.health.state in {"offline", "auth_error"}]
        self.alert_text.setText("；".join(bad[:2]) if bad else "所有已配置链路均已完成最近一次检查。")
        self._update_tray_status(telemetry, bad)
        self._record_monitor_history(telemetry)

    def _update_tray_status(
        self,
        telemetry: dict[str, ProviderTelemetry],
        bad: list[str],
    ) -> None:
        if self.tray_icon is None:
            return
        try:
            active_id = self.controller.detect_active_profile() or "openai"
        except Exception:
            active_id = self.controller.settings.active_profile_id or "openai"
        active = self.controller.settings.profiles.get(active_id)
        active_name = active.display_name if active is not None else active_id
        health = "链路正常" if not bad else f"{len(bad)} 个链路异常"
        days = self.controller.settings.usage_lookback_days
        self.tray_icon.setToolTip(
            f"{APP_NAME}\n{active_name} · {health}\n"
            f"近 {days} 天 {_format_int(self._latest_total_tokens)} tokens"
        )
        issues = tuple(sorted(item[:180] for item in bad))
        previous = self._last_tray_issues
        self._last_tray_issues = issues
        if not self.controller.settings.tray_health_notifications:
            return
        if issues and issues != previous:
            self.tray_icon.showMessage(
                "供应商链路异常",
                "\n".join(issues[:2]),
                QSystemTrayIcon.MessageIcon.Warning,
                6000,
            )
        elif previous and not issues:
            self.tray_icon.showMessage(
                "供应商链路已恢复",
                "所有已配置链路均已通过最近一次检查。",
                QSystemTrayIcon.MessageIcon.Information,
                4500,
            )

    def _handle_monitoring_error(self, exc: BaseException) -> None:
        self._monitoring_in_progress = False
        self._busy_count = max(0, self._busy_count - 1)
        self.refresh_button.setEnabled(self._busy_count == 0)
        self.alert_text.setText(f"监控失败：{exc}")

    def confirm_switch(self, profile_id: str) -> None:
        if profile_id == "openai" and not self.controller.openai_available(self.cached_auth):
            self.show_page("deployment")
            QMessageBox.information(self, "需要官方登录", "OpenAI 直连当前不可用，请先在部署模块完成官方账号登录。")
            return
        if profile_id != "openai" and not self.controller.profile_ready(profile_id):
            self.show_page("providers")
            profile = self.controller.settings.profiles[profile_id]
            QMessageBox.information(
                self,
                "供应商未配置",
                f"{profile.display_name} 尚未填写完整的 API 地址、模型和 Key。",
            )
            return
        profile = self.controller.settings.profiles[profile_id]
        others = [
            p.display_name
            for pid, p in self.controller.settings.profiles.items()
            if pid != profile_id and p.kind != "official" and self.controller.credentials.get(pid, "").strip()
        ]
        box = QMessageBox(self)
        box.setWindowTitle("确认切换")
        others_note = ""
        if others:
            others_note = f"\n\n当前还有已保存 Key 的 API 供应商：{'、'.join(others)}。"
        box.setText(f"切换到 {profile.display_name}？{others_note}")
        disconnect_button = box.addButton("断开其他供应商", QMessageBox.YesRole)
        keep_button = box.addButton("保留共存", QMessageBox.NoRole)
        cancel_button = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(keep_button if others else disconnect_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_button:
            return
        disconnect_others = clicked is disconnect_button
        self._set_busy(True, f"正在切换到 {profile.display_name}")
        self._run_job(
            lambda: self.controller.switch_profile(
                profile_id,
                progress=lambda message: self._queue_status(message),
                disconnect_others=disconnect_others,
            ),
            self._handle_switch_done,
            self._handle_switch_error,
        )

    def _handle_switch_done(self, result: OperationResult) -> None:
        self._set_busy(False, f"已切换到 {self.controller.settings.profiles[result.profile_id].display_name}")
        self.refresh_all(local_only=True)
        self.refresh_deployment_view()
        QTimer.singleShot(400, self.refresh_monitoring)
        if result.warnings:
            QMessageBox.information(self, "切换完成", "\n".join(result.warnings))

    def _handle_switch_error(self, exc: BaseException) -> None:
        self._set_busy(False, "切换失败")
        QMessageBox.warning(self, "切换失败", str(exc))

    def confirm_disconnect_others(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("断开其他 API")
        box.setText(
            "将清除配置中其他供应商的 bearer 连接字段（保留地址和模型，Key 仍保存在本机），\n"
            "仅保留当前供应商的连接。确定继续？"
        )
        confirm = box.addButton("断开", QMessageBox.YesRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not confirm:
            return
        self._set_busy(True, "正在断开其他 API 供应商")
        self._run_job(
            lambda: self.controller.disconnect_other_providers(
                progress=lambda message: self._queue_status(message)
            ),
            self._handle_disconnect_done,
            self._handle_switch_error,
        )

    def _handle_disconnect_done(self, result: OperationResult) -> None:
        self._set_busy(False, "已断开其他 API 供应商")
        self.refresh_all(local_only=True)
        if result.warnings:
            QMessageBox.information(self, "断开完成", "\n".join(result.warnings))

    def open_setup_wizard(self) -> None:
        wizard = SetupWizard(self)
        wizard.exec()
        self.refresh_all(local_only=True)
        self.refresh_deployment_view()
        self.refresh_tools()


class SetupWizard(QDialog):
    def __init__(self, host: ProviderSwitchWindow) -> None:
        super().__init__(host)
        self.host = host
        self.controller = host.controller
        self.setWindowTitle("首次部署设置")
        self.setMinimumSize(780, 610)
        self.resize(860, 680)
        self.setModal(True)
        self.setStyleSheet(APP_STYLE)
        self._page_index = 0
        self._jobs: queue.Queue[tuple[Callable[[Any], None] | None, Any, BaseException | None]] = queue.Queue()

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)
        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("首次部署设置")
        title.setProperty("class", "pageTitle")
        subtitle = QLabel("完成一次配置后，日常只需在总览页点击供应商按钮")
        subtitle.setProperty("class", "muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch(1)
        self.step_label = QLabel("1 / 4")
        self.step_label.setProperty("class", "muted")
        heading.addWidget(self.step_label)
        root.addLayout(heading)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self._build_environment_step()
        self._build_provider_step()
        self._build_options_step()
        self._build_summary_step()

        footer = QHBoxLayout()
        self.back_button = QPushButton("上一步")
        self.back_button.clicked.connect(self.previous_step)
        self.next_button = QPushButton("下一步")
        self.next_button.setProperty("kind", "primary")
        self.next_button.clicked.connect(self.next_step)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.next_button)
        root.addLayout(footer)

        self.worker_timer = QTimer(self)
        self.worker_timer.timeout.connect(self._drain_worker)
        self.worker_timer.start(90)
        self._render_step()
        self._probe_environment()

    def _build_environment_step(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        panel = GlassPanel(strong=True)
        form = QFormLayout(panel)
        form.setContentsMargins(20, 18, 20, 18)
        home_row = QHBoxLayout()
        self.wizard_home = QLineEdit(self.controller.settings.codex_home)
        home_row.addWidget(self.wizard_home, 1)
        browse = QPushButton("选择")
        browse.clicked.connect(self._browse_home)
        home_row.addWidget(browse)
        form.addRow("Codex Home", home_row)
        self.environment_status = QLabel("正在检查配置和官方登录态…")
        self.environment_status.setProperty("class", "body")
        self.environment_status.setWordWrap(True)
        form.addRow("检测结果", self.environment_status)
        layout.addWidget(panel)
        explanation = QLabel("Windows 默认位置为 C:\\Users\\<用户名>\\.codex；如果目录不存在，向导会创建 config.toml。")
        explanation.setProperty("class", "muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def _build_provider_step(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        panel = GlassPanel(strong=True)
        form = QFormLayout(panel)
        form.setContentsMargins(20, 18, 20, 18)
        form.setVerticalSpacing(11)
        self.wizard_relay1_name = QLineEdit()
        self.wizard_relay1_name.setPlaceholderText("API1")
        form.addRow("API1 名称", self.wizard_relay1_name)
        self.wizard_relay1_url = QLineEdit()
        form.addRow("API1 API 地址", self.wizard_relay1_url)
        self.wizard_relay1_key = QLineEdit()
        self.wizard_relay1_key.setEchoMode(QLineEdit.Password)
        self.wizard_relay1_key.setPlaceholderText("留空则保留当前本地 Key")
        form.addRow("API1 API Key", self.wizard_relay1_key)
        self.wizard_relay2_name = QLineEdit()
        self.wizard_relay2_name.setPlaceholderText("API2")
        form.addRow("API2 名称", self.wizard_relay2_name)
        self.wizard_relay2_url = QLineEdit()
        form.addRow("API2 API 地址", self.wizard_relay2_url)
        self.wizard_relay2_key = QLineEdit()
        self.wizard_relay2_key.setEchoMode(QLineEdit.Password)
        self.wizard_relay2_key.setPlaceholderText("留空则保留当前本地 Key")
        form.addRow("API2 API Key", self.wizard_relay2_key)
        self.wizard_glm_key = QLineEdit()
        self.wizard_glm_key.setEchoMode(QLineEdit.Password)
        self.wizard_glm_key.setPlaceholderText("可选；只保存在本机加密凭据库")
        form.addRow("GLM API Key", self.wizard_glm_key)
        layout.addWidget(panel)
        hint = QLabel("GLM 会自动注入内置 models.json；保存后可在“GLM 模型目录”继续修改。")
        hint.setProperty("class", "muted")
        layout.addWidget(hint)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def _build_options_step(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        panel = GlassPanel(strong=True)
        form = QFormLayout(panel)
        form.setContentsMargins(20, 18, 20, 18)
        self.wizard_retain_auth = QCheckBox("使用中转时保留 OpenAI 官方登录态")
        self.wizard_retain_auth.setChecked(False)
        form.addRow("登录态", self.wizard_retain_auth)
        self.wizard_preserve_label = QCheckBox("切换供应商时保持同一 provider 标签")
        self.wizard_preserve_label.setChecked(True)
        form.addRow("对话连续性", self.wizard_preserve_label)
        self.wizard_stable_label = QLineEdit(self.controller.settings.stable_provider_key)
        form.addRow("稳定标签", self.wizard_stable_label)
        self.wizard_auto_restart = QCheckBox("部署后自动重启 Codex")
        self.wizard_auto_restart.setChecked(self.controller.settings.auto_restart)
        form.addRow("进程管理", self.wizard_auto_restart)
        self.wizard_auto_sync = QCheckBox("切换后自动同步历史会话")
        self.wizard_auto_sync.setChecked(True)
        form.addRow("会话同步", self.wizard_auto_sync)
        self.wizard_install_threadripper = QCheckBox("安装 / 更新 Threadripper")
        self.wizard_install_threadripper.setChecked(True)
        form.addRow("工具", self.wizard_install_threadripper)
        self.wizard_route = QComboBox()
        self.wizard_route.addItem("优先使用当前配置", "current")
        self.wizard_route.addItem("API1", "relay1")
        self.wizard_route.addItem("API2", "relay2")
        self.wizard_route.addItem("GLM", "glm")
        self.wizard_route.addItem("OpenAI 官方直连（需已登录）", "openai")
        form.addRow("首次路由", self.wizard_route)
        layout.addWidget(panel)
        self.auth_choice_hint = QLabel("")
        self.auth_choice_hint.setProperty("class", "muted")
        self.auth_choice_hint.setWordWrap(True)
        layout.addWidget(self.auth_choice_hint)
        layout.addStretch(1)
        self.stack.addWidget(page)
        self.wizard_retain_auth.stateChanged.connect(self._sync_auth_choice)

    def _build_summary_step(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        panel = GlassPanel(strong=True)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 18, 20, 18)
        self.summary_text = QLabel("")
        self.summary_text.setProperty("class", "body")
        self.summary_text.setWordWrap(True)
        panel_layout.addWidget(self.summary_text)
        self.setup_progress = QProgressBar()
        self.setup_progress.setRange(0, 0)
        self.setup_progress.hide()
        panel_layout.addWidget(self.setup_progress)
        self.setup_result = QLabel("")
        self.setup_result.setProperty("class", "muted")
        self.setup_result.setWordWrap(True)
        panel_layout.addWidget(self.setup_result)
        layout.addWidget(panel)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def _browse_home(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择 .codex 目录", self.wizard_home.text())
        if selected:
            self.wizard_home.setText(selected)
            self._probe_environment()

    def _probe_environment(self) -> None:
        path = Path(self.wizard_home.text().strip()).expanduser()
        self.environment_status.setText("正在读取 config.toml 和 OpenAI 登录态…")
        self._run_job(lambda: (self.controller.preview_codex_home(path), self.controller.preview_auth_status(path)), self._handle_probe, self._handle_probe_error)

    def _handle_probe(self, result: tuple[Any, AuthStatus]) -> None:
        snapshot, auth = result
        self.probed_snapshot = snapshot
        self.probed_auth = auth
        route = snapshot.model_provider or (
            "openai 默认路由（无官方账号登录）"
            if not auth.official_account_logged_in
            else "openai 官方默认"
        )
        self.environment_status.setText(f"当前 provider：{route}\n官方账号登录：{auth.label}")
        self.wizard_retain_auth.setChecked(auth.official_account_logged_in)
        if auth.official_account_logged_in:
            self.wizard_route.setCurrentIndex(0)
        elif not snapshot.model_provider:
            self.wizard_route.setCurrentIndex(1)
        self._sync_auth_choice()
        self._load_existing_provider_values(snapshot)

    def _handle_probe_error(self, exc: BaseException) -> None:
        self.environment_status.setText(f"检测失败：{exc}")

    def _load_existing_provider_values(self, snapshot: Any) -> None:
        current = snapshot.providers.get(snapshot.model_provider, {}) if snapshot.model_provider else {}
        base_url = str(current.get("base_url", ""))
        token_exists = bool(current.get("experimental_bearer_token"))
        relay1 = self.controller.settings.profiles["relay1"]
        relay2 = self.controller.settings.profiles["relay2"]
        if base_url and not self.wizard_relay1_url.text():
            self.wizard_relay1_url.setText(base_url)
        self.wizard_relay1_name.setText(relay1.display_name)
        self.wizard_relay2_name.setText(relay2.display_name)
        self.wizard_relay2_url.setText(relay2.base_url)
        if token_exists and not self.wizard_relay1_key.text():
            self.wizard_relay1_key.setPlaceholderText("已检测到当前配置中的 Key；留空可导入并保留")

    def _sync_auth_choice(self) -> None:
        if getattr(self, "probed_auth", None) and self.probed_auth.official_account_logged_in:
            if self.wizard_retain_auth.isChecked():
                self.auth_choice_hint.setText("检测到官方登录态：将保留官方凭据；中转与 GLM 仍使用各自的 Key。")
            else:
                self.auth_choice_hint.setText("你选择不保留：完成部署后会清除 auth.json，OpenAI 直连按钮会暂时禁用。")
        else:
            self.wizard_retain_auth.setChecked(False)
            detected_api_key = (
                getattr(self, "probed_auth", None) is not None
                and self.probed_auth.state == "api_key"
            )
            if detected_api_key:
                self.auth_choice_hint.setText(
                    "检测到 OpenAI API Key，但它不是官方账号登录态；官方直连仍不可用。"
                )
            else:
                self.auth_choice_hint.setText(
                    "当前未检测到官方账号登录态：中转将使用独立 API Key，稍后可在部署模块重新登录。"
                )

    def _render_step(self) -> None:
        self.stack.setCurrentIndex(self._page_index)
        self.step_label.setText(f"{self._page_index + 1} / 4")
        self.back_button.setEnabled(self._page_index > 0)
        self.next_button.setText("完成部署" if self._page_index == 3 else "下一步")
        if self._page_index == 3:
            self._render_summary()

    def _resolved_wizard_route(self) -> str:
        route = str(self.wizard_route.currentData())
        if route != "current":
            return route
        snapshot = getattr(self, "probed_snapshot", None)
        if snapshot is not None and not snapshot.model_provider:
            auth = getattr(self, "probed_auth", None)
            return "openai" if auth is not None and auth.official_account_logged_in else "relay1"
        if snapshot is not None and str(snapshot.model).casefold().startswith("glm-"):
            return "glm"
        return "relay1"

    def _wizard_key_available(self, profile_id: str) -> bool:
        field = {
            "relay1": self.wizard_relay1_key,
            "relay2": self.wizard_relay2_key,
            "glm": self.wizard_glm_key,
        }[profile_id]
        if field.text().strip() or self.controller.credentials.get(profile_id):
            return True
        if profile_id == "relay1":
            snapshot = getattr(self, "probed_snapshot", None)
            if snapshot is not None and snapshot.model_provider:
                current = snapshot.providers.get(snapshot.model_provider, {})
                return bool(current.get("experimental_bearer_token"))
        return False

    def _render_summary(self) -> None:
        route = self._resolved_wizard_route()
        self.summary_text.setText(
            "即将执行：\n"
            f"• Codex Home：{self.wizard_home.text()}\n"
            f"• 首次路由：{route}\n"
            f"• 保留官方登录态：{'是' if self.wizard_retain_auth.isChecked() else '否'}\n"
            f"• 保持 provider 标签：{'是' if self.wizard_preserve_label.isChecked() else '否'}\n"
            f"• Threadripper：{'安装 / 更新' if self.wizard_install_threadripper.isChecked() else '跳过'}"
        )

    def previous_step(self) -> None:
        if self._page_index > 0:
            self._page_index -= 1
            self._render_step()

    def next_step(self) -> None:
        if not self._validate_step():
            return
        if self._page_index < 3:
            self._page_index += 1
            self._render_step()
        else:
            self._finish_setup()

    def _validate_step(self) -> bool:
        if self._page_index == 0 and not self.wizard_home.text().strip():
            QMessageBox.warning(self, "缺少路径", "请选择 Codex Home。")
            return False
        if self._page_index == 1:
            for profile_id, label, url_field, key_field in (
                ("relay1", "API1", self.wizard_relay1_url, self.wizard_relay1_key),
                ("relay2", "API2", self.wizard_relay2_url, self.wizard_relay2_key),
            ):
                has_url = bool(url_field.text().strip())
                has_new_key = bool(key_field.text().strip())
                if has_url and not self._wizard_key_available(profile_id):
                    QMessageBox.warning(self, "缺少 Key", f"{label} 已填写 API 地址，请补充 API Key。")
                    return False
                if has_new_key and not has_url:
                    QMessageBox.warning(self, "缺少地址", f"{label} 已填写 API Key，请补充 API 地址。")
                    return False
        if self._page_index == 3:
            route = self._resolved_wizard_route()
            if route == "openai":
                auth = getattr(self, "probed_auth", None)
                if not (
                    self.wizard_retain_auth.isChecked()
                    and auth
                    and auth.official_account_logged_in
                ):
                    QMessageBox.warning(self, "需要官方登录", "OpenAI 直连需要保留有效的官方登录态。")
                    return False
            elif route in {"relay1", "relay2"}:
                url_field = self.wizard_relay1_url if route == "relay1" else self.wizard_relay2_url
                label = "API1" if route == "relay1" else "API2"
                if not url_field.text().strip() or not self._wizard_key_available(route):
                    QMessageBox.warning(self, "供应商未配置", f"{label} 需要完整的 API 地址和 Key。")
                    return False
            elif route == "glm" and not self._wizard_key_available("glm"):
                QMessageBox.warning(self, "GLM 未配置", "首次启用 GLM 前需要填写 GLM API Key。")
                return False
        return True

    def _apply_wizard_values(self) -> str:
        path = Path(self.wizard_home.text().strip()).expanduser()
        existing = path.exists() and (path / "config.toml").exists()
        self.controller.set_codex_home(path, import_existing=existing)
        settings = self.controller.settings
        settings.setup_complete = True
        settings.preserve_provider_key = self.wizard_preserve_label.isChecked()
        settings.stable_provider_key = self.wizard_stable_label.text().strip() or settings.stable_provider_key
        settings.auto_restart = self.wizard_auto_restart.isChecked()
        settings.auto_sync_history = self.wizard_auto_sync.isChecked()
        settings.retain_official_auth = self.wizard_retain_auth.isChecked()

        relay1 = settings.profiles["relay1"]
        relay1.display_name = self.wizard_relay1_name.text().strip() or "API1"
        relay1.base_url = self.wizard_relay1_url.text().strip()
        relay1.model = relay1.model or "gpt-5.6-sol"
        relay2 = settings.profiles["relay2"]
        relay2.display_name = self.wizard_relay2_name.text().strip() or "API2"
        relay2.base_url = self.wizard_relay2_url.text().strip()
        relay2.model = relay2.model or relay1.model
        glm = settings.profiles["glm"]
        glm_key = self.wizard_glm_key.text().strip()
        for profile_id, value in (("relay1", self.wizard_relay1_key.text().strip()), ("relay2", self.wizard_relay2_key.text().strip()), ("glm", glm_key)):
            if value:
                self.controller.credentials[profile_id] = value
        for profile_id in ("relay1", "relay2"):
            settings.profiles[profile_id].requires_openai_auth = settings.retain_official_auth
        self.controller.save()
        if glm_key and not self.controller.catalog.target_path.exists():
            self.controller.catalog.inject_bundled()
        route = self._resolved_wizard_route()
        if route == "openai" and not self.controller.openai_available(getattr(self, "probed_auth", None)):
            raise RuntimeError("当前未检测到官方登录态，请先选择中转或在部署模块完成登录。")
        return str(route)

    def _finish_setup(self) -> None:
        self.next_button.setEnabled(False)
        self.back_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.setup_progress.show()
        self.setup_result.setText("正在应用配置…")
        self._run_job(self._perform_setup, self._handle_setup_done, self._handle_setup_error)

    def _perform_setup(self) -> tuple[str, OperationResult]:
        route = self._apply_wizard_values()
        if self.wizard_install_threadripper.isChecked():
            install_threadripper(progress=lambda message: self._queue_status(message))
        result = self.controller.deploy_profile(
            route,
            self.wizard_retain_auth.isChecked(),
            progress=lambda message: self._queue_status(message),
        )
        return route, result

    def _handle_setup_done(self, payload: tuple[str, OperationResult]) -> None:
        route, result = payload
        self.setup_progress.hide()
        suffix = f"\n{'；'.join(result.warnings)}" if result.warnings else ""
        self.setup_result.setText(f"部署完成，当前路由为 {route}。{suffix}")
        self.controller.settings.setup_complete = True
        self.controller.save()
        self.host._set_footer("首次部署完成")
        self.host.cached_auth = self.controller.auth_status()
        if result.warnings:
            QMessageBox.information(self, "部署完成", "\n".join(result.warnings))
        QTimer.singleShot(800, self.accept)

    def _handle_setup_error(self, exc: BaseException) -> None:
        self.setup_progress.hide()
        self.next_button.setEnabled(True)
        self.back_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.setup_result.setText(f"部署失败：{exc}")
        QMessageBox.warning(self, "部署失败", str(exc))

    def _run_job(
        self,
        worker: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        def execute() -> None:
            try:
                self._jobs.put((success, worker(), None))
            except BaseException as exc:
                self._jobs.put((failure, None, exc))

        threading.Thread(target=execute, daemon=True).start()

    def _queue_status(self, message: str) -> None:
        self._jobs.put((lambda value: self.setup_result.setText(str(value)), message, None))

    def _drain_worker(self) -> None:
        while True:
            try:
                callback, result, error = self._jobs.get_nowait()
            except queue.Empty:
                return
            if callback:
                callback(error if error is not None else result)


def _activate_existing_instance() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_SERVER_NAME)
    if not socket.waitForConnected(350):
        socket.abort()
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(350)
    socket.disconnectFromServer()
    return True


def run_qt_application(
    controller: ApplicationController | None = None,
    *,
    start_monitor: bool = True,
    show_wizard: bool = True,
    start_hidden: bool = False,
    single_instance: bool = True,
) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(resource_path("assets/codex_api_provider_switch_icon.png"))))
    if single_instance and _activate_existing_instance():
        return 0
    server: QLocalServer | None = None
    if single_instance:
        QLocalServer.removeServer(INSTANCE_SERVER_NAME)
        server = QLocalServer(app)
        if not server.listen(INSTANCE_SERVER_NAME):
            server = None
    window = ProviderSwitchWindow(controller, start_monitor=start_monitor, show_wizard=show_wizard)
    app.setQuitOnLastWindowClosed(False)
    if server is not None:
        def show_existing_window() -> None:
            while server is not None and server.hasPendingConnections():
                connection = server.nextPendingConnection()
                window.restore_from_tray()
                connection.disconnectFromServer()

        server.newConnection.connect(show_existing_window)
    if start_hidden and window.controller.settings.setup_complete and window.tray_icon is not None:
        window.hide()
    else:
        window.show()
    result = app.exec()
    if server is not None:
        server.close()
    return result


def run_qt_smoke_test(controller: ApplicationController | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(QIcon(str(resource_path("assets/codex_api_provider_switch_icon.png"))))
    if controller is None:
        controller = ApplicationController()
    window = ProviderSwitchWindow(controller, start_monitor=False, show_wizard=False)
    window.show()
    app.processEvents()
    if APP_VERSION not in window.windowTitle() or len(window.pages) != 7:
        window.exit_application()
        raise RuntimeError("Qt UI smoke test failed")
    window.exit_application()
    print(f"{APP_NAME} {APP_VERSION} Qt smoke test passed")
    return 0


def run_qt_tray_smoke_test(controller: ApplicationController) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(str(resource_path("assets/codex_api_provider_switch_icon.png"))))
    app.setQuitOnLastWindowClosed(False)
    window = ProviderSwitchWindow(controller, start_monitor=False, show_wizard=False)
    window.show()
    app.processEvents()
    if window.tray_icon is None or not window.tray_icon.isVisible():
        window.exit_application()
        raise RuntimeError("Windows system tray is unavailable")
    window.close()
    app.processEvents()
    if window.isVisible():
        window.exit_application()
        raise RuntimeError("Window did not hide to the system tray")
    window.restore_from_tray()
    app.processEvents()
    if not window.isVisible():
        window.exit_application()
        raise RuntimeError("Window could not be restored from the system tray")
    QTimer.singleShot(150, window.exit_application)
    result = app.exec()
    print(f"{APP_NAME} {APP_VERSION} tray smoke test passed")
    return result
