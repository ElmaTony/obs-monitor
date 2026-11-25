from __future__ import annotations

import ipaddress
import queue
import sys
import threading
import webbrowser
from datetime import datetime

import qrcode
from PySide6.QtCore import (
    QEasingCurve, QEvent, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from instance_guard import SingleInstance
from obs_controller import LABELS, OBSController, OBSRequestError, format_size
from pairing import PairingIdentity
from platform_support import AudioPlayer, animations_enabled, set_title_bar_theme
from remote_server import RemoteServer, lan_addresses
from settings import AppConfig
from version import VERSION

WINDOW_WIDTH = 840
WINDOW_HEIGHT = 560
MAX_ACTIVITY_ENTRIES = 300

PALETTES = {
    "light": dict(
        bg="#f5f1ea",
        surface="#fffdf9",
        surface_alt="#faf6ef",
        subtle="#eae4d9",
        line="#e3dcd0",
        line_strong="#cfc5b4",
        text="#1f1b18",
        text_secondary="#4a433d",
        muted="#7c7268",
        primary="#d95a36",
        primary_hover="#c44d2b",
        on_primary="#ffffff",
        primary_soft="#fcece6",
        accent="#2e6f95",
        soft="#e6f0f6",
        accent_bg="#e6f0f6",
        red="#c43d3d",
        red_bg="#fbecec",
        amber="#a86411",
        amber_bg="#fbf1df",
        disabled="#a89f95",
        qr_bg="#fffdf9",
        qr_fg="#1f1b18",
    ),
    "dark": dict(
        bg="#161514",
        surface="#161514",
        surface_alt="#100f0e",
        subtle="#24221f",
        line="#33302c",
        line_strong="#4a4640",
        text="#f3efe8",
        text_secondary="#cbc5bc",
        muted="#9a958e",
        primary="#f07a52",
        primary_hover="#f58d69",
        on_primary="#1f0d06",
        primary_soft="#3a241b",
        accent="#7ab3e0",
        soft="#1c2a36",
        accent_bg="#1c2a36",
        red="#ff4f6a",
        red_bg="#3d1a20",
        amber="#f0bc5e",
        amber_bg="#3a2e19",
        disabled="#6a655e",
        qr_bg="#f3efe8",
        qr_fg="#161514",
    ),
}


def _validate_obs_host(raw_host):
    address = raw_host.strip() or "127.0.0.1"
    if any(ord(c) < 32 or c.isspace() or c in "/?#@\\" for c in address):
        raise ValueError("OBS 地址请只填写 IP 或主机名")
    if "[" in address or "]" in address or ":" in address:
        candidate = address[1:-1] if address.startswith("[") and address.endswith("]") else address
        if "[" in candidate or "]" in candidate:
            raise ValueError("OBS 地址请只填写 IP 或主机名，端口请在下方单独填写")
        try:
            ipaddress.IPv6Address(candidate)
        except ValueError as exc:
            raise ValueError("OBS 地址请只填写 IP 或主机名，端口请在下方单独填写") from exc
    return address


def text(value, role="body"):
    result = QLabel(value)
    result.setProperty("role", role)
    if role == "clock":
        result.setMinimumHeight(46)
    elif role in ("metric", "metricSmall"):
        result.setMinimumHeight(28)
    return result


def card(margins=(18, 16, 18, 16), spacing=10):
    frame = QFrame()
    frame.setProperty("kind", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def icon(name, color, size=22):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 11, size / 11)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(color))
    if name in ("start", "logo"):
        painter.drawEllipse(QRectF(5, 5, 12, 12))
    elif name == "stop":
        painter.drawRoundedRect(QRectF(5.5, 5.5, 11, 11), 2.2, 2.2)
    elif name == "pause":
        painter.drawRoundedRect(QRectF(5.5, 5, 3.8, 12), 1.1, 1.1)
        painter.drawRoundedRect(QRectF(12.7, 5, 3.8, 12), 1.1, 1.1)
    elif name == "resume":
        path = QPainterPath()
        path.moveTo(7, 4.5)
        path.lineTo(17.5, 11)
        path.lineTo(7, 17.5)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "sun":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(7, 7, 8, 8))
        for i in range(8):
            painter.save()
            painter.translate(11, 11)
            painter.rotate(i * 45)
            painter.drawLine(0, -7.8, 0, -9.8)
            painter.restore()
    elif name == "moon":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(16.8, 14.8)
        path.cubicTo(8.2, 19.6, 0.8, 10.2, 10.0, 3.4)
        path.cubicTo(7.4, 10.0, 12.2, 15.6, 16.8, 14.8)
        painter.drawPath(path)
    elif name == "settings":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(5.5, 5.5, 11, 11))
        painter.drawEllipse(QRectF(9, 9, 4, 4))
        for i in range(8):
            painter.save()
            painter.translate(11, 11)
            painter.rotate(i * 45)
            painter.drawLine(0, -6.2, 0, -9.0)
            painter.restore()
    elif name == "copy":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(8, 7, 9.5, 11.5), 2, 2)
        painter.drawLine(5, 14, 5, 4.5)
        painter.drawLine(5, 4.5, 14, 4.5)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class ToggleSwitch(QCheckBox):
    def __init__(self):
        super().__init__()
        self.setFixedSize(38, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        owner = self.window()
        if owner and not hasattr(owner, "palette_values"):
            owner = owner.parentWidget()
        p = getattr(owner, "palette_values", PALETTES["light"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p["text_secondary"] if self.isChecked() else p["disabled"]))
        painter.drawRoundedRect(QRectF(1, 3, 36, 20), 10, 10)
        painter.setBrush(QColor(p["bg"] if self.isChecked() else p["surface"]))
        painter.drawEllipse(QRectF(20 if self.isChecked() else 4, 6, 14, 14))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p["accent"]), 1.2))
            painter.drawRoundedRect(QRectF(0.6, 0.6, 36.8, 22.8), 11, 11)
        painter.end()


class ChoiceCheck(QCheckBox):
    def __init__(self, title):
        super().__init__(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dimmed = False

    def set_dimmed(self, dimmed):
        if self._dimmed != dimmed:
            self._dimmed = dimmed
            self.update()

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        owner = self.window()
        if owner and not hasattr(owner, "palette_values"):
            owner = owner.parentWidget()
        p = getattr(owner, "palette_values", PALETTES["light"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - 16) / 2
        fill = p["subtle"] if self.isChecked() else p["surface_alt"]
        border = p["line"] if (self.isChecked() and self._dimmed) else p["line_strong"] if self.isChecked() else p["line"]
        painter.setPen(QPen(QColor(border), 1.1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(QRectF(0.6, y, 16, 16), 5, 5)
        if self.isChecked():
            check_color = p["disabled"] if self._dimmed else p["text"]
            painter.setPen(QPen(QColor(check_color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(4, int(y + 8), 7, int(y + 11))
            painter.drawLine(7, int(y + 11), 12, int(y + 5))
        painter.setPen(QColor(p["muted"] if self._dimmed else p["text"]))
        painter.drawText(self.rect().adjusted(24, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p["accent"]), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        painter.end()


class NumberField(QFrame):
    """Native numeric editing with larger, accessible decrement/increment controls."""

    def __init__(self, spin, title):
        super().__init__()
        self.setProperty("kind", "numberField")
        self.spin = spin
        # Qt's stylesheet style collapses the editor to 1 px with NoButtons.
        # Hide native buttons with subcontrol geometry instead.
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        spin.setAccessibleName(title)
        spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        spin.installEventFilter(self)
        self.setFocusProxy(spin)
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)
        row.addWidget(spin, 1)
        self.minus = QToolButton()
        self.plus = QToolButton()
        for button, label, step in ((self.minus, "减少", -1), (self.plus, "增加", 1)):
            button.setText("−" if step < 0 else "+")
            button.setProperty("kind", "stepper")
            button.setFixedSize(26, 26)
            button.setAccessibleName(label + title)
            button.setToolTip(label + title)
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(90)
            button.clicked.connect(lambda checked=False, n=step: spin.stepBy(n))
            row.addWidget(button)
        spin.valueChanged.connect(self._update_buttons)
        self._update_buttons()

    def _update_buttons(self):
        self.minus.setEnabled(self.spin.value() > self.spin.minimum())
        self.plus.setEnabled(self.spin.value() < self.spin.maximum())

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and not self.spin.hasFocus():
            event.ignore()
            return True
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.setProperty("focused", event.type() == QEvent.Type.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        return super().eventFilter(watched, event)


class ActivityDelegate(QStyledItemDelegate):
    """Structured event row with semantic status dot, tabular timestamp, and message."""

    ROLE_TIME = Qt.ItemDataRole.UserRole + 1
    ROLE_LEVEL = Qt.ItemDataRole.UserRole + 2
    ROLE_MESSAGE = Qt.ItemDataRole.UserRole + 3

    def __init__(self, owner, multiline=False, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.multiline = multiline
        self._time_font = QFont("Consolas", 9)
        self._body_font = QFont("Microsoft YaHei UI", 9)
        self._body_metrics = QFontMetrics(self._body_font)

    def sizeHint(self, option, index):
        if not self.multiline:
            return QSize(option.rect.width(), 25)
        view_width = option.rect.width()
        if view_width <= 0 and self.parent():
            view_width = self.parent().viewport().width()
        text_width = max(160, view_width - 96)
        bounds = self._body_metrics.boundingRect(
            QRect(0, 0, text_width, 2000),
            Qt.TextFlag.TextWordWrap,
            index.data(self.ROLE_MESSAGE) or "",
        )
        return QSize(view_width, max(32, bounds.height() + 16))

    def paint(self, painter, option, index):
        p = getattr(self.owner, "palette_values", PALETTES["light"])
        stamp = index.data(self.ROLE_TIME) or ""
        level = index.data(self.ROLE_LEVEL) or "info"
        message = index.data(self.ROLE_MESSAGE) or ""
        dot_color = {
            "accent": p["accent"],
            "amber": p["amber"],
            "red": p["red"],
        }.get(level, p["disabled"])

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row_rect = option.rect.adjusted(1, 1, -1, -1)
        if option.state & option.state.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p["subtle"]))
            painter.drawRoundedRect(QRectF(row_rect), 6, 6)
        elif index.row() % 2 == 1 and self.multiline:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p["surface_alt"]))
            painter.drawRoundedRect(QRectF(row_rect), 6, 6)

        dot_y = row_rect.top() + (14 if self.multiline else row_rect.height() / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(dot_color))
        painter.drawEllipse(QRectF(row_rect.left() + 7, dot_y - 3, 6, 6))

        painter.setFont(self._time_font)
        painter.setPen(QColor(p["muted"]))
        time_rect = QRect(row_rect.left() + 20, row_rect.top() + (5 if self.multiline else 0), 58, 18 if self.multiline else row_rect.height())
        painter.drawText(time_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, stamp)

        painter.setFont(self._body_font)
        text_color = p["red"] if level == "red" else p["amber"] if level == "amber" else p["text_secondary"]
        painter.setPen(QColor(text_color))
        msg_rect = QRect(row_rect.left() + 82, row_rect.top() + (5 if self.multiline else 0), max(20, row_rect.width() - 90), row_rect.height() - (10 if self.multiline else 0))
        if self.multiline:
            painter.drawText(msg_rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, message)
        else:
            elided = self._body_metrics.elidedText(message, Qt.TextElideMode.ElideRight, msg_rect.width())
            painter.drawText(msg_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        painter.restore()


class ActivityDialog(QDialog):
    """Resizable full activity log window that reflows wrapped rows on resize."""

    def __init__(self, parent, list_widget_getter):
        super().__init__(parent)
        self._list_widget_getter = list_widget_getter

    def _reflow(self):
        widget = self._list_widget_getter()
        if widget is not None:
            widget.doItemsLayout()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self, self._reflow)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()


class ThemeOverlay(QWidget):
    def __init__(self, parent, snapshot):
        super().__init__(parent)
        self.snapshot = snapshot
        self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(parent.rect())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(self.opacity)
        painter.drawPixmap(0, 0, self.snapshot)


class MonitorApp(QMainWindow):
    addresses_ready = Signal(object)

    def __init__(self, config=None, controller=None, start_services=True, player=None):
        super().__init__()
        self.config = config or AppConfig.load()
        self.events = queue.Queue()
        self.controller = controller or OBSController(self.config, self._on_event)
        self.controller.on_event = self._on_event
        self.player = player
        if self.player is None:
            try:
                self.player = AudioPlayer()
            except OSError as exc:
                self.events.put(("error", f"提示音初始化失败：{exc}"))
        self.remote = None
        self.pairing_identity = None
        self.closed = self._busy = False
        self.settings_window = None
        self.help_window = None
        self.activity_window = None
        self._activity_dialog_list = None
        self._activity_dialog_count = None
        self._activity_dialog_empty = None
        self._activity_dialog_copy = None
        self._activity_dialog_clear = None
        self.activity_entries = []
        self._known_addresses = []
        self._address_probe_running = False
        self._address_manually_selected = False
        self.addresses_ready.connect(self._on_addresses_ready)
        self._theme_animation = None
        self._theme_overlays = []
        self._title_colors = None
        self._style_cache = {}
        self._copy_link_timer = QTimer(self)
        self._copy_link_timer.setSingleShot(True)
        self._copy_link_timer.timeout.connect(lambda: self.copy_button.setText("复制链接"))
        self.setWindowTitle("OBS Monitor · 录制工作台")
        self.setWindowIcon(icon("logo", "#d95a36"))
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._build_ui()
        self._apply_fixed_window_size()
        self.apply_theme(self.config.theme, save=False)
        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(120)
        self.network_timer = QTimer(self)
        self.network_timer.timeout.connect(self._request_address_refresh)
        self.network_timer.start(5000)
        if start_services:
            self.controller.start()
            self._start_remote()
            QTimer.singleShot(0, self.show_first_run_help)
        self._log("欢迎使用 OBS Monitor，打开 OBS 后会自动连接。", level="info")
        self._tick()

    def _apply_fixed_window_size(self, available=None):
        if available is None:
            screen = QApplication.primaryScreen()
            available = screen.availableGeometry() if screen else None
        width, height = WINDOW_WIDTH, WINDOW_HEIGHT
        if available is not None and available.width() > 0 and available.height() > 0:
            width = min(WINDOW_WIDTH, max(760, available.width() - 32))
            height = min(WINDOW_HEIGHT, max(500, available.height() - 32))
        self.setFixedSize(width, height)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(20, 14, 20, 12)
        page.setSpacing(12)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)
        self.brand_mark = QLabel()
        self.brand_mark.setObjectName("brandMark")
        self.brand_mark.setFixedSize(32, 32)
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.brand_mark)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(text("OBS Monitor", "brand"))
        brand.addWidget(text("录制控制台", "small"))
        header.addLayout(brand)
        header.addStretch()

        self.connection_label = text("●  正在连接", "badge")
        self.connection_label.setFixedHeight(26)
        header.addWidget(self.connection_label)

        group = QFrame()
        group.setObjectName("themeGroup")
        row = QHBoxLayout(group)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self.theme_buttons = {}
        for theme, title in (("light", "浅色"), ("dark", "深色")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setProperty("kind", "theme")
            button.setFixedSize(56, 26)
            button.clicked.connect(lambda checked=False, value=theme: self.apply_theme(value))
            row.addWidget(button)
            self.theme_buttons[theme] = button
        header.addWidget(group)

        self.settings_button = QPushButton("设置")
        self.settings_button.setProperty("kind", "quiet")
        self.settings_button.setFixedHeight(30)
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)

        help_button = QPushButton("?")
        help_button.setFixedSize(28, 30)
        help_button.setProperty("kind", "quiet")
        help_button.setAccessibleName("使用说明")
        help_button.setToolTip("使用说明")
        help_button.clicked.connect(self.show_help)
        header.addWidget(help_button)
        page.addLayout(header)

        # Two balanced columns (left 462px == right 462px at 840x560)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(12)
        right_widget = QWidget()
        right_widget.setFixedWidth(244)
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)

        # Left Card 1: Current Session (192px)
        hero, layout = card((20, 16, 20, 16), 8)
        hero.setObjectName("sessionCard")
        hero.setFixedHeight(192)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(text("当前录制", "eyebrow"))
        heading.addStretch()
        self.state_label = text("●  等待连接", "badge")
        self.state_label.setFixedHeight(24)
        heading.addWidget(self.state_label)
        layout.addLayout(heading)

        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        clock_col = QVBoxLayout()
        clock_col.setSpacing(2)
        self.clock_label = text("00:00:00", "clock")
        self.clock_caption = text("录制时长", "muted")
        clock_col.addWidget(self.clock_label)
        clock_col.addWidget(self.clock_caption)
        metrics.addLayout(clock_col, 3)

        size_panel = QFrame()
        size_panel.setProperty("kind", "inset")
        size_col = QVBoxLayout(size_panel)
        size_col.setContentsMargins(14, 9, 14, 9)
        size_col.setSpacing(2)
        size_col.addWidget(text("实时输出大小", "small"))
        self.size_label = text("—", "metric")
        self.size_label.setToolTip("OBS 的原始累计输出字节数；录制文件显示 0 KB 时仍能更新。")
        self.size_exact_label = text("尚未收到 OBS 数据", "small")
        size_col.addWidget(self.size_label)
        size_col.addWidget(self.size_exact_label)
        metrics.addWidget(size_panel, 2)
        layout.addLayout(metrics)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        limit_head = QHBoxLayout()
        limit_head.setSpacing(8)
        self.limit_label = text("从 OBS 直接读取", "small")
        limit_head.addWidget(self.limit_label, 1)
        self.threshold_label = text("提醒于 2 GB · 每次一次", "pill")
        limit_head.addWidget(self.threshold_label)
        layout.addLayout(limit_head)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        layout.addWidget(self.progress)
        left.addWidget(hero)

        # Left Card 2: Recording Controls (92px)
        control, layout = card((18, 12, 18, 14), 10)
        control.setFixedHeight(92)
        heading = QHBoxLayout()
        heading.addWidget(text("录制控制", "section"))
        heading.addStretch()
        self.command_label = text("OBS 确认后生效", "small")
        heading.addWidget(self.command_label)
        layout.addLayout(heading)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.action_buttons = {}
        for action, title in (("start", "开始"), ("pause", "暂停"), ("resume", "继续"), ("stop", "结束")):
            button = QPushButton(title)
            button.setProperty("kind", "action")
            button.setProperty("action", action)
            button.setAccessibleName(LABELS[action])
            button.setToolTip(LABELS[action])
            button.setIconSize(QSize(14, 14))
            button.setFixedHeight(40)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, value=action: self.perform(value))
            controls.addWidget(button, 1)
            self.action_buttons[action] = button
        layout.addLayout(controls)
        left.addWidget(control)

        # Left Card 3: Structured Activity Feed (flex 154px)
        activity, layout = card((16, 12, 16, 10), 6)
        activity.setMinimumHeight(132)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(text("活动记录", "section"))
        self.activity_count_label = text("0 条", "pill")
        row.addWidget(self.activity_count_label)
        row.addStretch()
        self.activity_all_button = QPushButton("查看全部")
        self.activity_all_button.setProperty("kind", "inline")
        self.activity_all_button.setFixedHeight(22)
        self.activity_all_button.clicked.connect(self.open_activity_log)
        row.addWidget(self.activity_all_button)
        self.activity_clear_button = QPushButton("清空")
        self.activity_clear_button.setProperty("kind", "inline")
        self.activity_clear_button.setFixedHeight(22)
        self.activity_clear_button.clicked.connect(self.clear_activity_log)
        row.addWidget(self.activity_clear_button)
        layout.addLayout(row)

        self.activity_list = QListWidget()
        self.activity_list.setObjectName("activityList")
        self.activity_list.setFrameShape(QFrame.Shape.NoFrame)
        self.activity_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.activity_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.activity_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.activity_list.setItemDelegate(ActivityDelegate(self, multiline=False, parent=self.activity_list))
        self.activity_list.itemDoubleClicked.connect(lambda *_: self.open_activity_log())
        layout.addWidget(self.activity_list, 1)

        self.activity_empty_label = text("暂无活动记录", "small")
        self.activity_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.activity_empty_label.hide()
        layout.addWidget(self.activity_empty_label, 1)
        left.addWidget(activity, 1)

        # Right Card 1: Mobile Remote (278px — exact budget with generous breathing room below QR)
        mobile, layout = card((16, 14, 16, 14), 8)
        mobile.setFixedHeight(278)
        row = QHBoxLayout()
        row.addWidget(text("手机遥控", "section"))
        row.addStretch()
        self.mobile_status = text("准备中", "pill")
        row.addWidget(self.mobile_status)
        layout.addLayout(row)

        qr_holder = QHBoxLayout()
        qr_holder.setContentsMargins(0, 2, 0, 2)
        qr_holder.addStretch()
        self.qr_label = QLabel("手机遥控未启动")
        self.qr_label.setObjectName("pairingCode")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(116, 116)
        self.qr_label.setWordWrap(True)
        qr_holder.addWidget(self.qr_label)
        qr_holder.addStretch()
        layout.addLayout(qr_holder)
        layout.addSpacing(2)

        hint = text("同一局域网 · 扫码或复制链接", "small")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        self.address_combo = QComboBox()
        self.address_combo.setFixedHeight(30)
        self.address_combo.activated.connect(self._select_network_address)
        self.address_combo.currentTextChanged.connect(self._refresh_qr)
        layout.addWidget(self.address_combo)

        self.copy_button = QPushButton("复制链接")
        self.copy_button.setFixedHeight(32)
        self.copy_button.setProperty("kind", "secondary")
        self.copy_button.clicked.connect(self.copy_link)
        self.open_button = QPushButton("预览")
        self.open_button.setProperty("kind", "outline")
        self.open_button.setFixedSize(62, 32)
        self.open_button.clicked.connect(self.open_remote)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.copy_button, 1)
        buttons.addWidget(self.open_button)
        layout.addLayout(buttons)
        right.addWidget(mobile)

        # Right Card 2: Operation Sounds (flex 172px — aligns cleanly with left column)
        sound, layout = card((16, 12, 16, 12), 6)
        sound.setMinimumHeight(160)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(text("操作提示音", "section"))
        self.sound_muted_label = text("已静音", "pill")
        self.sound_muted_label.setVisible(not self.config.sound_enabled)
        row.addWidget(self.sound_muted_label)
        row.addStretch()
        self.sound_switch = ToggleSwitch()
        self.sound_switch.setObjectName("masterSwitch")
        self.sound_switch.setAccessibleName("操作提示音总开关")
        self.sound_switch.setChecked(self.config.sound_enabled)
        self.sound_switch.toggled.connect(self._toggle_sound)
        self.sound_switch.setToolTip("四种操作成功后，在电脑播放提示音")
        row.addWidget(self.sound_switch)
        layout.addLayout(row)

        sound_divider = QFrame()
        sound_divider.setObjectName("divider")
        sound_divider.setFixedHeight(1)
        layout.addWidget(sound_divider)

        self.sound_vars = {}
        self.preview_buttons = {}
        for action, title in (("start", "开始录制"), ("pause", "暂停录制"), ("resume", "继续录制"), ("stop", "结束录制")):
            row = QHBoxLayout()
            row.setSpacing(6)
            item = ChoiceCheck(title)
            item.setFixedHeight(24)
            item.setChecked(self.config.sound_actions[action])
            item.set_dimmed(not self.config.sound_enabled)
            item.toggled.connect(self._save_sound_actions)
            self.sound_vars[action] = item
            row.addWidget(item, 1)
            preview = QToolButton()
            preview.setProperty("kind", "preview")
            preview.setToolTip(f"试听{title}提示音")
            preview.setAccessibleName(f"试听{title}提示音")
            preview.setFixedSize(26, 22)
            preview.clicked.connect(lambda checked=False, value=action: self._preview(value))
            self.preview_buttons[action] = preview
            row.addWidget(preview)
            layout.addLayout(row)
        layout.addStretch()
        right.addWidget(sound, 1)

        columns.addLayout(left, 1)
        columns.addWidget(right_widget)
        page.addLayout(columns, 1)

        # Footer
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.status_bar = text("正在连接 OBS…", "small")
        footer.addWidget(self.status_bar, 1)
        footer.addWidget(text(f"OBS MONITOR  ·  v{VERSION}", "small"))
        page.addLayout(footer)

    def _set_cached_style(self, key, widget, stylesheet):
        if self._style_cache.get(key) != stylesheet:
            self._style_cache[key] = stylesheet
            widget.setStyleSheet(stylesheet)

    @staticmethod
    def _set_label_text(label, value):
        if label.text() != value:
            label.setText(value)

    def _update_copy_icon(self):
        p = getattr(self, "palette_values", PALETTES["light"])
        color = p["primary"] if self.copy_button.isEnabled() else p["disabled"]
        self.copy_button.setIcon(icon("copy", color, 16))

    def apply_theme(self, theme, save=True):
        if theme not in PALETTES:
            return
        if getattr(self, "palette_values", None) is PALETTES[theme] and self.config.theme == theme:
            for name, button in self.theme_buttons.items():
                button.setChecked(name == theme)
            return
        if save:
            try:
                self.controller.set_theme(theme)
            except OSError as exc:
                self._log(f"主题保存失败：{exc}", level="red")
                for name, button in self.theme_buttons.items():
                    button.setChecked(name == self.config.theme)
                return
        else:
            self.config.theme = theme
        previous = getattr(self, "palette_values", None)
        snapshots = []
        if previous is not PALETTES[theme] and previous and self.isVisible() and animations_enabled():
            for target in (self.centralWidget(), self.settings_window, self.activity_window):
                if target and target.isVisible():
                    snapshots.append((target, target.grab()))
        start_colors = self._title_colors or previous
        self._finish_theme_transition(settle=False)
        p = self.palette_values = PALETTES[theme]
        self._style_cache.clear()
        if QApplication.platformName() == "windows":
            QApplication.instance().styleHints().setColorScheme(
                Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light
            )
        self.setStyleSheet(f"""
QMainWindow, QDialog, #workspace {{background:{p['bg']};}}
QWidget {{color:{p['text']};font-family:'Microsoft YaHei UI';font-size:13px;}}
QLabel {{background:transparent;border:0;}}
QLabel[role='brand'] {{font-family:'Segoe UI';font-size:16px;font-weight:700;}}
QLabel[role='title'] {{font-size:20px;font-weight:700;}}
QLabel[role='eyebrow'] {{color:{p['muted']};font-size:12px;font-weight:600;}}
QLabel[role='section'] {{font-size:13px;font-weight:600;}}
QLabel[role='muted'] {{color:{p['muted']};font-size:12px;}}
QLabel[role='small'] {{color:{p['muted']};font-size:11px;}}
QLabel[role='clock'] {{font-family:'Consolas';font-size:44px;font-weight:400;letter-spacing:-1px;}}
QLabel[role='metric'] {{font-family:'Segoe UI';font-size:24px;font-weight:700;}}
QLabel[role='metricSmall'] {{font-family:'Segoe UI';font-size:19px;font-weight:600;}}
QLabel[role='badge'] {{border-radius:11px;padding:3px 10px;background:{p['subtle']};font-size:11px;font-weight:600;color:{p['muted']};}}
QLabel[role='pill'] {{border-radius:9px;padding:2px 8px;background:{p['surface_alt']};border:1px solid {p['line']};font-size:11px;color:{p['muted']};}}
#brandMark {{background:{p['primary']};border-radius:9px;}}
#pairingCode {{background:{p['qr_bg']};border:1px solid {p['line']};border-radius:12px;color:{p['muted']};font-size:11px;padding:4px;}}
QFrame[kind='card'] {{background:{p['surface']};border:1px solid {p['line']};border-radius:14px;}}
QFrame[kind='inset'] {{background:{p['surface_alt']};border:1px solid {p['line']};border-radius:11px;}}
#divider {{background:{p['line']};}}
#themeGroup {{background:{p['subtle']};border:1px solid {p['line']};border-radius:9px;}}
QPushButton {{border:1px solid {p['line']};border-radius:8px;padding:5px 10px;background:{p['surface']};color:{p['text']};}}
QPushButton:hover {{background:{p['subtle']};border-color:{p['line_strong']};}}
QPushButton:pressed {{background:{p['subtle']};}}
QPushButton:focus {{border:1px solid {p['accent']};}}
QPushButton[kind='quiet'] {{border:1px solid transparent;background:transparent;padding:4px 8px;color:{p['muted']};}}
QPushButton[kind='quiet']:hover {{background:{p['subtle']};border-color:{p['line']};color:{p['text']};}}
QPushButton[kind='inline'] {{border:1px solid transparent;background:transparent;padding:1px 7px;border-radius:6px;color:{p['muted']};font-size:11px;}}
QPushButton[kind='inline']:hover {{background:{p['subtle']};border-color:{p['line']};color:{p['text']};}}
QPushButton[kind='inline']:disabled {{color:{p['disabled']};background:transparent;border-color:transparent;}}
QPushButton[kind='outline'] {{border:1px solid {p['line']};background:{p['surface']};color:{p['text_secondary']};font-size:12px;}}
QPushButton[kind='outline']:hover {{background:{p['subtle']};border-color:{p['line_strong']};color:{p['text']};}}
QPushButton[kind='outline']:disabled {{color:{p['disabled']};background:{p['subtle']};border-color:{p['line']};}}
QPushButton[kind='theme'] {{border:0;background:transparent;padding:2px 4px;color:{p['muted']};font-size:11px;border-radius:6px;}}
QPushButton[kind='theme']:checked {{background:{p['surface']};color:{p['primary']};font-weight:600;}}
QPushButton[kind='action'] {{border-radius:9px;font-size:12px;font-weight:600;padding:6px 10px;}}
QPushButton[action='start'] {{background:{p['primary']};border-color:{p['primary']};color:{p['on_primary']};}}
QPushButton[action='start']:hover {{background:{p['primary_hover']};border-color:{p['primary_hover']};}}
QPushButton[action='pause'] {{background:{p['surface']};border-color:{p['amber']};color:{p['amber']};}}
QPushButton[action='pause']:hover {{background:{p['amber_bg']};border-color:{p['amber']};}}
QPushButton[action='resume'] {{background:{p['soft']};border-color:{p['accent']};color:{p['accent']};}}
QPushButton[action='resume']:hover {{background:{p['surface_alt']};border-color:{p['accent']};}}
QPushButton[action='stop'] {{background:{p['surface']};border-color:{p['red']};color:{p['red']};}}
QPushButton[action='stop']:hover {{background:{p['red_bg']};border-color:{p['red']};}}
QPushButton[kind='action']:disabled {{color:{p['disabled']};background:{p['subtle']};border-color:{p['line']};}}
QPushButton[kind='secondary'] {{background:{p['primary_soft']};color:{p['primary']};border:1px solid transparent;font-weight:600;font-size:12px;}}
QPushButton[kind='secondary']:hover {{border-color:{p['primary']};}}
QPushButton[kind='secondary']:disabled {{background:{p['subtle']};color:{p['disabled']};border-color:{p['line']};}}
QToolButton {{background:transparent;border:0;border-radius:6px;padding:3px;}}
QToolButton:hover {{background:{p['subtle']};}}
QToolButton[kind='preview']:hover {{background:{p['subtle']};}}
QCheckBox {{spacing:7px;font-size:12px;}}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{background:{p['surface_alt']};border:1px solid {p['line']};border-radius:8px;padding:4px 9px;min-height:18px;}}
QComboBox:hover, QLineEdit:hover {{border-color:{p['line_strong']};}}
QComboBox:focus, QLineEdit:focus {{border-color:{p['accent']};}}
QFrame[kind='numberField'] {{background:{p['surface_alt']};border:1px solid {p['line']};border-radius:8px;}}
QFrame[kind='numberField'][focused='true'] {{border-color:{p['accent']};}}
QFrame[kind='numberField'] QSpinBox, QFrame[kind='numberField'] QDoubleSpinBox {{background:transparent;border:0;padding:0 5px;min-height:24px;}}
QFrame[kind='numberField'] QSpinBox::up-button, QFrame[kind='numberField'] QSpinBox::down-button,
QFrame[kind='numberField'] QDoubleSpinBox::up-button, QFrame[kind='numberField'] QDoubleSpinBox::down-button {{width:0;height:0;border:0;}}
QToolButton[kind='stepper'] {{background:{p['surface']};border:1px solid {p['line']};border-radius:5px;color:{p['muted']};font-family:'Segoe UI';font-size:16px;}}
QToolButton[kind='stepper']:hover {{background:{p['subtle']};border-color:{p['line_strong']};color:{p['text']};}}
QToolButton[kind='stepper']:pressed {{background:{p['subtle']};color:{p['text']};}}
QToolButton[kind='stepper']:focus {{border-color:{p['accent']};}}
QToolButton[kind='stepper']:disabled {{color:{p['disabled']};background:{p['subtle']};}}
QComboBox::drop-down {{border:0;width:24px;}}
QComboBox QAbstractItemView {{background:{p['surface']};color:{p['text']};selection-background-color:{p['subtle']};border:1px solid {p['line']};}}
QProgressBar {{background:{p['subtle']};border:0;border-radius:2px;}}
QProgressBar::chunk {{background:{p['accent']};border-radius:2px;}}
QListWidget#activityList, QListWidget#activityDialogList {{background:transparent;border:0;outline:0;}}
QScrollBar:vertical {{background:transparent;width:5px;margin:2px 0;}}
QScrollBar::handle:vertical {{background:{p['line_strong']};border-radius:2px;min-height:24px;}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
QToolTip {{background:{p['surface']};color:{p['text']};border:1px solid {p['line_strong']};padding:5px 8px;border-radius:6px;}}
""")
        self.brand_mark.setPixmap(icon("logo", p["on_primary"], 20).pixmap(20, 20))
        for name, b in self.theme_buttons.items():
            b.setChecked(name == theme)
            b.setIcon(icon("sun" if name == "light" else "moon", p["primary"] if name == theme else p["muted"], 15))
        self.settings_button.setIcon(icon("settings", p["muted"], 16))
        self._update_copy_icon()
        for action, b in self.action_buttons.items():
            color = (
                p["disabled"] if not b.isEnabled()
                else p["on_primary"] if action == "start"
                else p["red"] if action == "stop"
                else p["amber"] if action == "pause"
                else p["accent"]
            )
            b.setIcon(icon(action, color))
        for b in self.preview_buttons.values():
            b.setIcon(icon("resume", p["muted"], 13))
        self.activity_list.viewport().update()
        if self._activity_dialog_list is not None:
            self._activity_dialog_list.viewport().update()
        self._refresh_qr()
        self._tick()
        if snapshots:
            self._theme_overlays = [ThemeOverlay(target, snapshot) for target, snapshot in snapshots]
            for overlay in self._theme_overlays:
                overlay.show()
                overlay.raise_()
            animation = self._theme_animation = QVariantAnimation(self)
            animation.setDuration(240)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
            animation.valueChanged.connect(lambda progress: self._theme_frame(progress, start_colors, p))
            animation.finished.connect(self._finish_theme_transition)
            self._theme_frame(0.0, start_colors, p)
            animation.start()
        else:
            self._sync_title_bars()

    def _theme_frame(self, progress, before, after):
        for overlay in self._theme_overlays:
            overlay.opacity = 1.0 - progress
            overlay.update()
        colors = {}
        for key in ("bg", "text"):
            old, new = QColor(before[key]), QColor(after[key])
            colors[key] = QColor(*(round(a + (b - a) * progress) for a, b in zip(old.getRgb()[:3], new.getRgb()[:3]))).name()
        self._sync_title_bars(colors)

    def _finish_theme_transition(self, settle=True):
        if self._theme_animation:
            self._theme_animation.stop()
            self._theme_animation.deleteLater()
            self._theme_animation = None
        for overlay in self._theme_overlays:
            overlay.hide()
            overlay.deleteLater()
        self._theme_overlays = []
        if settle and hasattr(self, "palette_values"):
            self._sync_title_bars()

    def _sync_title_bars(self, colors=None):
        if self.closed:
            return
        self._title_colors = colors or self.palette_values
        if QApplication.platformName() == "windows":
            for window in (self, *self.findChildren(QDialog)):
                if window.isVisible():
                    set_title_bar_theme(
                        int(window.winId()),
                        self.config.theme == "dark",
                        self._title_colors["bg"],
                        self._title_colors["text"],
                    )

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate) and isinstance(watched, QWidget) and watched.isWindow():
            if watched is self or self.isAncestorOf(watched):
                # Qt can refresh the native caption after show/activation.
                # Reapply custom colors after that refresh, including mid-fade.
                self._sync_title_bars(self._title_colors)
                QTimer.singleShot(0, self, lambda: self._sync_title_bars(self._title_colors))
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        self._finish_theme_transition()
        super().resizeEvent(event)

    def _on_event(self, kind, payload):
        self.events.put((kind, payload))

    def _make_activity_item(self, stamp, level, message):
        item = QListWidgetItem()
        item.setData(ActivityDelegate.ROLE_TIME, stamp)
        item.setData(ActivityDelegate.ROLE_LEVEL, level)
        item.setData(ActivityDelegate.ROLE_MESSAGE, message)
        item.setToolTip(f"{stamp}  {message}")
        return item

    def _sync_activity_chrome(self):
        count = len(self.activity_entries)
        self.activity_count_label.setText(f"{count} 条")
        self.activity_clear_button.setEnabled(count > 0)
        self.activity_list.setVisible(count > 0)
        self.activity_empty_label.setVisible(count == 0)
        if self._activity_dialog_count is not None:
            self._activity_dialog_count.setText(f"共 {count} 条记录")
        if self._activity_dialog_list is not None:
            self._activity_dialog_list.setVisible(count > 0)
        if self._activity_dialog_empty is not None:
            self._activity_dialog_empty.setVisible(count == 0)
        if self._activity_dialog_copy is not None:
            self._activity_dialog_copy.setEnabled(count > 0)
        if self._activity_dialog_clear is not None:
            self._activity_dialog_clear.setEnabled(count > 0)

    def _log(self, message, level="info"):
        stamp = f"{datetime.now():%H:%M:%S}"
        entry = (stamp, level, str(message))
        self.activity_entries.insert(0, entry)
        if len(self.activity_entries) > MAX_ACTIVITY_ENTRIES:
            self.activity_entries.pop()
            if self.activity_list.count() >= MAX_ACTIVITY_ENTRIES:
                self.activity_list.takeItem(self.activity_list.count() - 1)
            if self._activity_dialog_list is not None and self._activity_dialog_list.count() >= MAX_ACTIVITY_ENTRIES:
                self._activity_dialog_list.takeItem(self._activity_dialog_list.count() - 1)
        self.activity_list.insertItem(0, self._make_activity_item(*entry))
        self.activity_list.scrollToTop()
        if self._activity_dialog_list is not None:
            self._activity_dialog_list.insertItem(0, self._make_activity_item(*entry))
            self._activity_dialog_list.scrollToTop()
        self._sync_activity_chrome()

    def clear_activity_log(self):
        self.activity_entries.clear()
        self.activity_list.clear()
        if self._activity_dialog_list is not None:
            self._activity_dialog_list.clear()
        self._sync_activity_chrome()

    def open_activity_log(self):
        if self.activity_window and self.activity_window.isVisible():
            self.activity_window.raise_()
            self.activity_window.activateWindow()
            return
        dialog = self.activity_window = ActivityDialog(self, lambda: self._activity_dialog_list)
        dialog.setWindowTitle("活动记录")
        dialog.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        dialog.resize(520, 420)
        dialog.setMinimumSize(420, 300)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def closed():
            self._finish_theme_transition()
            if self.activity_window is dialog:
                self.activity_window = None
                self._activity_dialog_list = None
                self._activity_dialog_count = None
                self._activity_dialog_empty = None
                self._activity_dialog_copy = None
                self._activity_dialog_clear = None

        dialog.finished.connect(closed)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(text("活动记录", "title"))
        self._activity_dialog_count = text(f"共 {len(self.activity_entries)} 条记录", "small")
        title_box.addWidget(self._activity_dialog_count)
        top.addLayout(title_box)
        top.addStretch()

        copy_all = self._activity_dialog_copy = QPushButton("复制全部")
        copy_all.setProperty("kind", "outline")
        copy_all.setFixedHeight(30)
        copy_timer = QTimer(copy_all)
        copy_timer.setSingleShot(True)
        copy_timer.timeout.connect(lambda: copy_all.setText("复制全部"))

        def copy_entries():
            if not self.activity_entries:
                return
            lines = [f"{stamp}  {msg}" for stamp, _, msg in self.activity_entries]
            QApplication.clipboard().setText("\n".join(lines))
            copy_all.setText("已复制")
            copy_timer.start(1500)

        copy_all.clicked.connect(copy_entries)
        top.addWidget(copy_all)

        clear_btn = self._activity_dialog_clear = QPushButton("清空")
        clear_btn.setProperty("kind", "outline")
        clear_btn.setFixedHeight(30)
        clear_btn.clicked.connect(self.clear_activity_log)
        top.addWidget(clear_btn)
        layout.addLayout(top)

        list_card, card_layout = card((10, 10, 10, 10), 0)
        full_list = self._activity_dialog_list = QListWidget()
        full_list.setObjectName("activityDialogList")
        full_list.setFrameShape(QFrame.Shape.NoFrame)
        full_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        full_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        full_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        full_list.setItemDelegate(ActivityDelegate(self, multiline=True, parent=full_list))
        for entry in self.activity_entries:
            full_list.addItem(self._make_activity_item(*entry))
        card_layout.addWidget(full_list, 1)

        empty_label = self._activity_dialog_empty = text("暂无活动记录", "muted")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(empty_label, 1)
        layout.addWidget(list_card, 1)
        self._sync_activity_chrome()
        dialog.show()

    def _tick(self):
        if self.closed:
            return
        for _ in range(100):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "action":
                level = "amber" if payload == "pause" else "red" if payload == "stop" else "accent"
                self._log(LABELS[payload] + "成功", level=level)
                if self.config.sound_enabled and self.config.sound_actions.get(payload, True):
                    self._preview(payload, log=False)
            elif kind == "size_alert":
                self._log(f"录制达到 {format_size(payload)}，已触发本次大小提醒。", level="amber")
                self._preview("size_alert", log=False)
            elif kind == "error":
                self._log(str(payload), level="red")
            elif kind == "log":
                self._log(str(payload), level="accent")
            elif kind == "command_result":
                self._busy = False
                try:
                    payload.result()
                except OBSRequestError:
                    pass  # Already logged via controller error event.
                except Exception as exc:
                    self._log("操作未完成：" + str(exc), level="red")
        state = self.controller.snapshot()
        p = self.palette_values
        live = state["connected"] and not state["stale"]
        self._set_label_text(self.connection_label, "●  OBS 已连接" if live else "●  数据已过期" if state["connected"] else "●  OBS 未连接")
        self._set_cached_style(
            "connection",
            self.connection_label,
            f"color:{p['accent'] if live else p['muted']};"
            f"background:{p['accent_bg'] if live else p['subtle']};"
            "padding:3px 10px;border-radius:11px;font-weight:600;",
        )
        mode = "amber" if state["paused"] and live else "red" if state["active"] and live else "accent" if live else None
        self._set_label_text(self.state_label, "●  " + state["state_text"])
        self._set_cached_style(
            "state",
            self.state_label,
            f"color:{p[mode] if mode else p['muted']};"
            f"background:{p[mode + '_bg'] if mode else p['subtle']};"
            "padding:3px 10px;border-radius:11px;font-weight:600;",
        )
        self._set_label_text(self.clock_label, state["duration_text"])
        self._set_label_text(self.clock_caption, "上次录制时长" if not state["active"] and state["bytes"] else "录制时长")
        self._set_label_text(self.size_label, state["size_text"])
        self._set_label_text(
            self.threshold_label,
            f"提醒于 {self.config.threshold_gb:g} GB · 每次一次" if self.config.alert_enabled else "大小提醒已关闭",
        )
        self._set_label_text(
            self.size_exact_label,
            state["size_exact_text"] + (" · 上次数据" if state["stale"] and state["data_age_ms"] is not None else ""),
        )
        ratio = min(1000, int(state["bytes"] / max(1, state["threshold_bytes"]) * 1000)) if self.config.alert_enabled else 0
        if self.progress.value() != ratio:
            self.progress.setValue(ratio)
        chunk_color = p["red"] if self.config.alert_enabled and ratio >= 1000 else (p[mode] if mode else p["disabled"])
        self._set_cached_style(
            "progress",
            self.progress,
            f"QProgressBar{{background:{p['subtle']};border:0;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{chunk_color};border-radius:2px;}}",
        )
        self._set_label_text(
            self.limit_label,
            "数据已过期，等待重新连接 OBS" if state["stale"]
            else "本次录制已触发大小提醒" if state["alerted"]
            else "OBS 累计输出量 · 不依赖目录显示大小",
        )
        if self.sound_switch.isChecked() != state["sound_enabled"]:
            self.sound_switch.blockSignals(True)
            self.sound_switch.setChecked(state["sound_enabled"])
            self.sound_switch.blockSignals(False)
        if self.sound_muted_label.isVisible() == state["sound_enabled"]:
            self.sound_muted_label.setVisible(not state["sound_enabled"])
        for item in self.sound_vars.values():
            item.set_dimmed(not state["sound_enabled"])
        for action, b in self.action_buttons.items():
            enabled = state["allowed"][action] and not self._busy and not state["busy"]
            if b.isEnabled() != enabled:
                b.setEnabled(enabled)
                color = (
                    p["disabled"] if not enabled
                    else p["on_primary"] if action == "start"
                    else p["red"] if action == "stop"
                    else p["amber"] if action == "pause"
                    else p["accent"]
                )
                b.setIcon(icon(action, color))
        self._set_label_text(self.command_label, "正在等待 OBS 确认…" if self._busy or state["busy"] else "OBS 确认后生效")
        self._set_label_text(self.status_bar, state["message"] or ("●  已连接，手机与电脑状态同步" if self.remote else "●  已连接，手机遥控未开启"))

    def _preview(self, action, log=True):
        if self.player:
            try:
                self.player.play(action)
                if log:
                    self._log("试听：" + LABELS.get(action, "大小提醒"), level="info")
            except (OSError, RuntimeError) as exc:
                self._log("提示音播放失败：" + str(exc), level="red")

    def perform(self, action):
        if self._busy:
            return
        self._busy = True
        self.controller.submit(action).add_done_callback(lambda result: self.events.put(("command_result", result)))
        self._tick()

    def _toggle_sound(self, enabled):
        try:
            self.controller.set_sound_enabled(enabled)
        except OSError as exc:
            self.sound_switch.blockSignals(True)
            self.sound_switch.setChecked(self.config.sound_enabled)
            self.sound_switch.blockSignals(False)
            self._log("提示音保存失败：" + str(exc), level="red")
        self.sound_muted_label.setVisible(not self.config.sound_enabled)
        for item in self.sound_vars.values():
            item.set_dimmed(not self.config.sound_enabled)

    def _save_sound_actions(self):
        try:
            self.controller.set_sound_actions({name: b.isChecked() for name, b in self.sound_vars.items()})
        except OSError as exc:
            for name, item in self.sound_vars.items():
                item.blockSignals(True)
                item.setChecked(self.config.sound_actions[name])
                item.blockSignals(False)
            self._log("提示音保存失败：" + str(exc), level="red")

    def _start_remote(self):
        if self.remote:
            self.remote.stop()
            self.remote = None
        error = "手机遥控已关闭"
        if self.config.remote_enabled:
            try:
                self.remote = self._create_remote(self.config.remote_port)
                remote = self.remote
                self._log(f"手机遥控已开启，网页端口 {remote.port}。", level="accent")
            except (OSError, ValueError) as exc:
                error = f"手机遥控启动失败：{exc}"
                self._log(error, level="red")
        self._sync_remote_ui(error)

    def _create_remote(self, port):
        if self.pairing_identity is None:
            self.pairing_identity = PairingIdentity.persistent()
        remote = RemoteServer(self.controller, port=port, identity=self.pairing_identity)
        remote.start()
        return remote

    def _request_address_refresh(self):
        if self.closed or not self.remote or self._address_probe_running:
            return
        self._address_probe_running = True

        def discover():
            try:
                addresses = lan_addresses()
            except Exception:
                addresses = []
            try:
                if not self.closed:
                    self.addresses_ready.emit(addresses)
            except RuntimeError:
                pass  # QApplication may already be shutting down.

        threading.Thread(target=discover, daemon=True, name="lan-discovery").start()

    def _select_network_address(self, index):
        self._address_manually_selected = True

    def _on_addresses_ready(self, addresses):
        if self.closed:
            return
        self._address_probe_running = False
        if addresses != self._known_addresses:
            self._known_addresses = list(addresses)
            if self.remote:
                self._sync_remote_ui(discover=False)

    def _sync_remote_ui(self, error="手机遥控已关闭", discover=True):
        selected = self.address_combo.currentData()
        port = self.remote.port if self.remote else self.config.remote_port
        previous_block = self.address_combo.blockSignals(True)
        try:
            self.address_combo.clear()
            if not self.remote:
                self.address_combo.addItem("手机遥控已关闭", None)
            elif self._known_addresses:
                for address in self._known_addresses:
                    self.address_combo.addItem(f"{address}:{port}", address)
            else:
                self.address_combo.addItem("等待局域网地址…", None)
            if self.remote and self._address_manually_selected and selected in self._known_addresses:
                self.address_combo.setCurrentIndex(self.address_combo.findData(selected))
            elif selected not in self._known_addresses:
                self._address_manually_selected = False
        finally:
            self.address_combo.blockSignals(previous_block)
        self.address_combo.setEnabled(self.remote is not None and bool(self._known_addresses))
        if self.remote and self._known_addresses:
            self.mobile_status.setText("已开启")
            self._refresh_qr()
        elif self.remote:
            self.mobile_status.setText("等待网络")
            self.qr_label.setPixmap(QPixmap())
            self.qr_label.setText("正在发现局域网\n请确认已连网")
        else:
            self.mobile_status.setText("未开启")
            self.qr_label.setPixmap(QPixmap())
            self.qr_label.setText(error)
        self.copy_button.setEnabled(self.remote is not None and bool(self._known_addresses))
        self._update_copy_icon()
        self.open_button.setEnabled(self.remote is not None)
        if discover:
            self._request_address_refresh()

    def apply_settings(self, candidate):
        previous = self.controller.config
        remote_changed = (candidate.remote_port, candidate.remote_enabled) != (previous.remote_port, previous.remote_enabled)
        replace_remote = candidate.remote_enabled and (remote_changed or self.remote is None)
        prepared = None
        try:
            # Bind the replacement before saving settings or stopping the working
            # listener. A busy port or failed save must preserve the old service.
            if replace_remote:
                prepared = self._create_remote(candidate.remote_port)
            self.controller.apply_connection_config(candidate)
        except Exception:
            if prepared:
                prepared.stop()
            raise
        self.config = candidate
        if remote_changed or replace_remote:
            old, self.remote = self.remote, prepared
            if old:
                old.stop()
            self._sync_remote_ui()
            if self.remote:
                self._log(f"手机遥控已开启，网页端口 {self.remote.port}。", level="accent")
        return previous

    def _refresh_qr(self, *args):
        if not self.remote or not self.address_combo.currentData():
            return
        p = getattr(self, "palette_values", PALETTES["light"])
        code = qrcode.QRCode(box_size=4, border=2)
        code.add_data(self.remote.url(self.address_combo.currentData()))
        code.make(fit=True)
        image = code.make_image(fill_color=p["qr_fg"], back_color=p["qr_bg"]).convert("RGB")
        data = image.tobytes()
        qt_image = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
        self.qr_label.setPixmap(
            QPixmap.fromImage(qt_image).scaled(
                104, 104, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
            )
        )

    def copy_link(self):
        if self.remote and self.address_combo.currentData():
            QApplication.clipboard().setText(self.remote.url(self.address_combo.currentData()))
            self._log("手机控制链接已复制，发送到自己的手机即可打开。", level="info")
            self.copy_button.setText("已复制")
            self._copy_link_timer.start(1500)

    def open_remote(self):
        if self.remote:
            webbrowser.open(self.remote.url("127.0.0.1"))

    def reset_phone_pairing(self):
        if not self.remote:
            return
        parent = self.settings_window if (self.settings_window and self.settings_window.isVisible()) else self
        answer = QMessageBox.question(
            parent,
            "重置手机配对",
            "重置后，所有旧链接和已配对手机将失效，需要使用新链接重新连接。录制会继续。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.remote.reset_pairing()
        except OSError as exc:
            QMessageBox.warning(parent, "配对未重置", str(exc))
            return
        self._refresh_qr()
        self._log("手机配对已重置，旧链接失效；请使用新链接或二维码连接。", level="amber")

    def open_settings(self):
        if self.settings_window and self.settings_window.isVisible():
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        dialog = self.settings_window = QDialog(self)
        dialog.setWindowTitle("连接与提醒设置")
        dialog.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        dialog_height = 548
        if available is not None and available.height() > 0:
            dialog_height = min(548, max(420, available.height() - 48))
        dialog.setFixedSize(468, dialog_height)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def closed():
            self._finish_theme_transition()
            if self.settings_window is dialog:
                self.settings_window = None

        dialog.finished.connect(closed)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        head = QVBoxLayout()
        head.setSpacing(3)
        head.addWidget(text("连接与提醒", "title"))
        hint = text("在 OBS「工具 → WebSocket 服务器设置」中启用服务器。", "muted")
        hint.setWordWrap(True)
        head.addWidget(hint)
        layout.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget {background:transparent;border:0;}")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)

        obs_card, obs_layout = card((16, 14, 16, 14), 10)
        obs_layout.addWidget(text("OBS WebSocket 连接", "section"))
        obs_form = QFormLayout()
        obs_form.setVerticalSpacing(10)
        obs_form.setHorizontalSpacing(14)
        host = QLineEdit(self.config.obs_host)
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(self.config.obs_port)
        password = QLineEdit(self.config.obs_password)
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("与 OBS WebSocket 密码保持一致")
        for title, widget in (("OBS 地址", host), ("OBS 端口", port), ("OBS 密码", password)):
            field = NumberField(widget, title) if isinstance(widget, QAbstractSpinBox) else widget
            field.setMinimumHeight(34)
            obs_form.addRow(title, field)
        obs_layout.addLayout(obs_form)
        scroll_layout.addWidget(obs_card)

        remote_card, remote_layout = card((16, 14, 16, 14), 10)
        remote_layout.addWidget(text("手机遥控与大小提醒", "section"))
        remote_form = QFormLayout()
        remote_form.setVerticalSpacing(10)
        remote_form.setHorizontalSpacing(14)
        remote_port = QSpinBox()
        remote_port.setRange(1, 65535)
        remote_port.setValue(self.config.remote_port)
        threshold = QDoubleSpinBox()
        threshold.setRange(0.1, 102400)
        threshold.setDecimals(1)
        threshold.setSuffix(" GB")
        threshold.setValue(self.config.threshold_gb)
        for title, widget in (("手机网页端口", remote_port), ("大小提醒阈值", threshold)):
            field = NumberField(widget, title)
            field.setMinimumHeight(34)
            remote_form.addRow(title, field)
        remote_layout.addLayout(remote_form)
        remote_enabled = ChoiceCheck("开启手机局域网遥控")
        remote_enabled.setMinimumHeight(24)
        remote_enabled.setChecked(self.config.remote_enabled)
        alert_enabled = ChoiceCheck("达到大小阈值后提醒一次")
        alert_enabled.setMinimumHeight(24)
        alert_enabled.setChecked(self.config.alert_enabled)
        remote_layout.addWidget(remote_enabled)
        remote_layout.addWidget(alert_enabled)
        scroll_layout.addWidget(remote_card)
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        reset_pairing = QPushButton("重置所有手机配对")
        reset_pairing.setProperty("kind", "outline")
        reset_pairing.setFixedHeight(32)
        reset_pairing.setEnabled(self.remote is not None)
        reset_pairing.clicked.connect(self.reset_phone_pairing)
        reset_pairing.setToolTip("更换配对凭证，所有旧链接和已配对浏览器立即失效")
        layout.addWidget(reset_pairing)

        save_button = QPushButton("保存并应用")
        save_button.setProperty("action", "start")
        save_button.setFixedHeight(40)
        layout.addWidget(save_button)

        def apply():
            try:
                address = _validate_obs_host(host.text())
                if port.value() == remote_port.value() and remote_enabled.isChecked():
                    raise ValueError("手机端口不能与 OBS WebSocket 端口相同")
                candidate = AppConfig(
                    obs_host=address,
                    obs_port=port.value(),
                    obs_password=password.text(),
                    remote_port=remote_port.value(),
                    remote_enabled=remote_enabled.isChecked(),
                    threshold_gb=threshold.value(),
                    alert_enabled=alert_enabled.isChecked(),
                    sound_enabled=self.config.sound_enabled,
                    sound_actions=self.config.sound_actions.copy(),
                    theme=self.config.theme,
                )
                previous = self.apply_settings(candidate)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(dialog, "设置未保存", str(exc))
                return
            connection_changed = any(
                getattr(candidate, key) != getattr(previous, key)
                for key in ("obs_host", "obs_port", "obs_password")
            )
            self._log("设置已保存" + ("，正在重新连接 OBS。" if connection_changed else "。"), level="accent")
            dialog.accept()

        save_button.clicked.connect(apply)
        dialog.show()

    def show_first_run_help(self):
        if self.closed or not self.isVisible() or self.config.help_seen:
            return
        self.show_help()
        try:
            self.controller.mark_help_seen()
        except OSError as exc:
            self._log(f"使用说明展示状态未保存，下次启动会再次显示：{exc}", level="amber")

    def show_help(self):
        if self.help_window:
            if not self.help_window.isVisible():
                self.help_window.open()
            self.help_window.raise_()
            self.help_window.activateWindow()
            return
        dialog = self.help_window = QMessageBox(self)
        dialog.setWindowTitle("使用说明")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(
            "1. 打开 OBS，启用「工具 → WebSocket 服务器设置」。\n\n"
            "2. 在本软件「设置」中填写相同的端口和密码。\n\n"
            "3. 使用桌面或手机控制录制。声音由电脑播放，可分别关闭。\n\n"
            "4. 手机与电脑连接同一局域网后扫码；多网卡时可切换 IP。被防火墙拦截时，请允许本软件通过专用网络。\n\n"
            "5. 暂停需要 OBS 使用支持暂停的录制编码器。\n\n"
            "关闭本软件会停止手机网页，不会结束 OBS 的录制。"
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.button(QMessageBox.StandardButton.Ok).setText("知道了")
        dialog.open()

    def closeEvent(self, event):
        if not self.closed:
            QApplication.instance().removeEventFilter(self)
            self._finish_theme_transition()
            self.closed = True
            self.timer.stop()
            self.network_timer.stop()
            self._copy_link_timer.stop()
            for dialog in self.findChildren(QDialog):
                dialog.close()
            if self.remote:
                self.remote.stop()
            self.controller.stop()
            if self.player:
                self.player.stop()
        event.accept()


def main():
    if sys.platform != "win32":
        raise SystemExit("OBS Monitor 桌面版仅支持 Windows")
    application = QApplication(sys.argv)
    application.setApplicationName("OBS Monitor")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    application.setStyle("Fusion")
    instance = SingleInstance(parent=application)
    if not instance.claim():
        return
    window = MonitorApp()

    def activate():
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    instance.activate.connect(activate)
    application.aboutToQuit.connect(instance.close)
    window.show()
    sys.exit(application.exec())
