from __future__ import annotations

import queue
import sys
import threading
import webbrowser
from datetime import datetime

import qrcode
from PySide6.QtCore import Qt, QTimer, QSize, QRectF, QEvent, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSpinBox,
    QToolButton, QVBoxLayout, QWidget, QAbstractSpinBox)

from obs_controller import LABELS, OBSController, format_size
from instance_guard import SingleInstance
from pairing import PairingIdentity
from platform_support import AudioPlayer, animations_enabled, set_title_bar_theme
from remote_server import RemoteServer, lan_addresses
from settings import AppConfig

from version import VERSION
PALETTES = {
    "light": dict(bg="#f3f4f6", surface="#fcfcfd", subtle="#eceef2", line="#dfe2e8", text="#252932", muted="#717887", accent="#4f6fae", soft="#eaf0fb", accent_bg="#eaf0fb", red="#bb5050", red_bg="#faeded", amber="#976823", amber_bg="#f6efdf", disabled="#a4aab6"),
    "dark": dict(bg="#14161b", surface="#1d2027", subtle="#262a33", line="#363c48", text="#eceef4", muted="#9da5b5", accent="#9fb7e9", soft="#2c364b", accent_bg="#2c364b", red="#eb9290", red_bg="#412c2b", amber="#dec18c", amber_bg="#3b3627", disabled="#687182"),
}


def text(value, role="body"):
    result = QLabel(value)
    result.setProperty("role", role)
    if role == "clock":
        result.setMinimumHeight(52)
    elif role in ("metric", "metricSmall"):
        result.setMinimumHeight(30)
    return result


def card(margins=(24, 22, 24, 22), spacing=12):
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
        painter.drawRoundedRect(QRectF(5, 5, 12, 12), 2, 2)
    elif name == "pause":
        painter.drawRoundedRect(QRectF(5, 5, 4, 12), 1, 1)
        painter.drawRoundedRect(QRectF(13, 5, 4, 12), 1, 1)
    elif name == "resume":
        path = QPainterPath()
        path.moveTo(7, 4)
        path.lineTo(18, 11)
        path.lineTo(7, 18)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "sun":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(7, 7, 8, 8))
        for i in range(8):
            painter.save(); painter.translate(11, 11); painter.rotate(i * 45)
            painter.drawLine(0, -8, 0, -10); painter.restore()
    elif name == "moon":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(); path.moveTo(17, 15)
        path.cubicTo(8, 20, 0, 10, 10, 3); path.cubicTo(7, 10, 12, 16, 17, 15)
        painter.drawPath(path)
    elif name == "settings":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(5, 5, 12, 12)); painter.drawEllipse(QRectF(9, 9, 4, 4))
        for i in range(8):
            painter.save(); painter.translate(11, 11); painter.rotate(i * 45)
            painter.drawLine(0, -6, 0, -9); painter.restore()
    elif name == "copy":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(8, 7, 10, 12), 2, 2)
        painter.drawLine(5, 14, 5, 4); painter.drawLine(5, 4, 14, 4)
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
        if not hasattr(owner, "palette_values"):
            owner = owner.parentWidget()
        p = getattr(owner, "palette_values", PALETTES["light"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p["accent"] if self.isChecked() else p["disabled"]))
        painter.drawRoundedRect(QRectF(1, 3, 36, 20), 10, 10)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(20 if self.isChecked() else 4, 6, 14, 14))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p["accent"]), 1))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 37, 23), 11, 11)
        painter.end()


class ChoiceCheck(QCheckBox):
    def __init__(self, title):
        super().__init__(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        owner = self.window()
        if not hasattr(owner, "palette_values"):
            owner = owner.parentWidget()
        p = getattr(owner, "palette_values", PALETTES["light"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - 16) / 2
        painter.setPen(QPen(QColor(p["accent"] if self.isChecked() else p["line"]), 1))
        painter.setBrush(QColor(p["accent"] if self.isChecked() else p["subtle"]))
        painter.drawRoundedRect(QRectF(0.5, y, 16, 16), 5, 5)
        if self.isChecked():
            painter.setPen(QPen(QColor("white" if getattr(owner, "config", AppConfig()).theme == "light" else "#17233b"), 1.6))
            painter.drawLine(4, int(y + 8), 7, int(y + 11))
            painter.drawLine(7, int(y + 11), 12, int(y + 5))
        painter.setPen(QColor(p["text"]))
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
        row.setContentsMargins(3, 3, 3, 3); row.setSpacing(3)
        row.addWidget(spin, 1)
        self.minus = QToolButton(); self.plus = QToolButton()
        for button, label, step in ((self.minus, "减少", -1), (self.plus, "增加", 1)):
            button.setText("−" if step < 0 else "+")
            button.setProperty("kind", "stepper")
            button.setFixedSize(26, 26)
            button.setAccessibleName(label + title); button.setToolTip(label + title)
            button.setAutoRepeat(True); button.setAutoRepeatDelay(350); button.setAutoRepeatInterval(90)
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
            self.style().unpolish(self); self.style().polish(self)
        return super().eventFilter(watched, event)


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
        self._known_addresses = []
        self._address_probe_running = False
        self._address_manually_selected = False
        self.addresses_ready.connect(self._on_addresses_ready)
        self._theme_animation = None
        self._theme_overlays = []
        self._title_colors = None
        self.setWindowTitle("OBS Monitor · 录制工作台")
        self.setWindowIcon(icon("logo", "#4f6fae"))
        self.resize(880, 600)
        self.setMinimumSize(780, 540)
        self._build_ui()
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
        self._log("欢迎使用 OBS Monitor，打开 OBS 后会自动连接。")
        self._tick()

    def _build_ui(self):
        root = QWidget(); root.setObjectName("workspace"); self.setCentralWidget(root)
        page = QVBoxLayout(root); page.setContentsMargins(22, 18, 22, 14); page.setSpacing(16)
        header = QHBoxLayout(); header.setSpacing(10)
        mark = QLabel(); mark.setObjectName("brandMark"); mark.setFixedSize(34, 34)
        mark.setPixmap(icon("logo", "#ffffff").pixmap(22, 22)); mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(mark)
        brand = QVBoxLayout(); brand.setSpacing(2)
        brand.addWidget(text("OBS Monitor", "brand")); brand.addWidget(text("录制控制台", "small"))
        header.addLayout(brand); header.addStretch()
        self.connection_label = text("●  正在连接", "badge"); header.addWidget(self.connection_label)
        group = QFrame(); group.setObjectName("themeGroup")
        row = QHBoxLayout(group); row.setContentsMargins(3, 3, 3, 3); row.setSpacing(2)
        self.theme_buttons = {}
        for theme, title in (("light", "浅色"), ("dark", "深色")):
            button = QPushButton(title); button.setCheckable(True); button.setProperty("kind", "theme"); button.setFixedSize(57, 28)
            button.clicked.connect(lambda checked=False, value=theme: self.apply_theme(value))
            row.addWidget(button); self.theme_buttons[theme] = button
        header.addWidget(group)
        self.settings_button = QPushButton("设置"); self.settings_button.setProperty("kind", "quiet")
        self.settings_button.clicked.connect(self.open_settings); header.addWidget(self.settings_button)
        help_button = QPushButton("?"); help_button.setFixedSize(28, 30); help_button.setProperty("kind", "quiet")
        help_button.setAccessibleName("使用说明"); help_button.clicked.connect(self.show_help); header.addWidget(help_button)
        page.addLayout(header)
        columns = QHBoxLayout(); columns.setSpacing(14)
        left = QVBoxLayout(); left.setSpacing(12)
        right_widget = QWidget(); right_widget.setFixedWidth(224)
        right = QVBoxLayout(right_widget); right.setContentsMargins(0, 0, 0, 0); right.setSpacing(12)
        hero, layout = card((22, 18, 22, 18), 5); hero.setObjectName("sessionCard")
        heading = QHBoxLayout(); heading.addWidget(text("当前录制", "eyebrow")); heading.addStretch()
        self.state_label = text("●  等待连接", "badge"); heading.addWidget(self.state_label)
        layout.addLayout(heading); layout.addSpacing(8)
        self.clock_label = text("00:00:00", "clock"); layout.addWidget(self.clock_label)
        self.clock_caption = text("录制时长", "muted"); layout.addWidget(self.clock_caption); layout.addSpacing(13)
        divider = QFrame(); divider.setObjectName("divider"); divider.setFixedHeight(1); layout.addWidget(divider); layout.addSpacing(10)
        metrics = QHBoxLayout(); size_col = QVBoxLayout(); size_col.setSpacing(3)
        size_col.addWidget(text("实时输出大小", "muted")); self.size_label = text("—", "metric"); size_col.addWidget(self.size_label)
        self.size_exact_label = text("尚未收到 OBS 数据", "small"); size_col.addWidget(self.size_exact_label)
        self.size_label.setToolTip("OBS 的原始累计输出字节数；录制文件显示 0 KB 时仍能更新。")
        metrics.addLayout(size_col, 3); limit_col = QVBoxLayout(); limit_col.setSpacing(3)
        limit_col.addWidget(text("大小提醒", "muted")); self.threshold_label = text("2 GB", "metricSmall"); limit_col.addWidget(self.threshold_label)
        limit_col.addWidget(text("每次录制提醒一次", "small")); metrics.addLayout(limit_col, 2)
        layout.addLayout(metrics); layout.addSpacing(8)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); self.progress.setTextVisible(False); self.progress.setFixedHeight(4)
        layout.addWidget(self.progress); self.limit_label = text("从 OBS 直接读取", "small"); layout.addWidget(self.limit_label)
        left.addWidget(hero)
        control, layout = card((16, 12, 16, 14), 10)
        heading = QHBoxLayout(); heading.addWidget(text("录制控制", "section")); heading.addStretch()
        self.command_label = text("OBS 确认后生效", "small"); heading.addWidget(self.command_label); layout.addLayout(heading)
        controls = QHBoxLayout(); controls.setSpacing(8); self.action_buttons = {}
        for action, title in (("start", "开始"), ("pause", "暂停"), ("resume", "继续"), ("stop", "结束")):
            button = QPushButton(title); button.setProperty("kind", "action"); button.setProperty("action", action)
            button.setAccessibleName(LABELS[action]); button.setToolTip(LABELS[action])
            button.setIconSize(QSize(14, 14)); button.setFixedHeight(38)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, value=action: self.perform(value)); controls.addWidget(button, 1)
            self.action_buttons[action] = button
        layout.addLayout(controls); left.addWidget(control)
        activity, layout = card((16, 12, 16, 10), 6)
        row = QHBoxLayout(); row.addWidget(text("活动记录", "section")); row.addStretch(); row.addWidget(text("最近操作", "small")); layout.addLayout(row)
        self.log_text = QPlainTextEdit(); self.log_text.setReadOnly(True); self.log_text.setMaximumBlockCount(160); self.log_text.setMinimumHeight(34)
        layout.addWidget(self.log_text); left.addWidget(activity, 1)
        mobile, layout = card((16, 14, 16, 14), 9)
        row = QHBoxLayout(); row.addWidget(text("手机遥控", "section")); row.addStretch()
        self.mobile_status = text("准备中", "small"); row.addWidget(self.mobile_status); layout.addLayout(row)
        self.qr_label = QLabel("手机遥控未启动"); self.qr_label.setObjectName("pairingCode")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self.qr_label.setFixedHeight(122)
        self.qr_label.setWordWrap(True); layout.addWidget(self.qr_label)
        hint = text("同一网络 · 扫码连接", "small"); hint.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(hint)
        self.address_combo = QComboBox(); self.address_combo.setMinimumHeight(28)
        self.address_combo.activated.connect(self._select_network_address)
        self.address_combo.currentTextChanged.connect(self._refresh_qr); layout.addWidget(self.address_combo)
        self.copy_button = QPushButton("复制链接"); self.copy_button.setFixedHeight(32); self.copy_button.setProperty("kind", "secondary")
        self.copy_button.clicked.connect(self.copy_link)
        self.open_button = QPushButton("预览"); self.open_button.setProperty("kind", "quiet"); self.open_button.setFixedHeight(32)
        self.open_button.clicked.connect(self.open_remote)
        buttons = QHBoxLayout(); buttons.setSpacing(6); buttons.addWidget(self.copy_button, 1); buttons.addWidget(self.open_button)
        layout.addLayout(buttons); right.addWidget(mobile)
        sound, layout = card((16, 12, 16, 12), 7)
        row = QHBoxLayout(); row.addWidget(text("操作提示音", "section")); row.addStretch()
        self.sound_switch = ToggleSwitch(); self.sound_switch.setObjectName("masterSwitch"); self.sound_switch.setAccessibleName("操作提示音总开关")
        self.sound_switch.setChecked(self.config.sound_enabled); self.sound_switch.toggled.connect(self._toggle_sound); row.addWidget(self.sound_switch)
        layout.addLayout(row); self.sound_switch.setToolTip("四种操作成功后，在电脑播放提示音")
        self.sound_vars = {}; self.preview_buttons = {}
        for action, title in (("start", "开始录制"), ("pause", "暂停录制"), ("resume", "继续录制"), ("stop", "结束录制")):
            row = QHBoxLayout(); item = ChoiceCheck(title); item.setMinimumHeight(23); item.setChecked(self.config.sound_actions[action])
            item.toggled.connect(self._save_sound_actions); self.sound_vars[action] = item; row.addWidget(item, 1)
            preview = QToolButton(); preview.setToolTip(f"试听{title}提示音"); preview.setAccessibleName(f"试听{title}提示音"); preview.setFixedSize(25, 23)
            preview.clicked.connect(lambda checked=False, value=action: self._preview(value)); self.preview_buttons[action] = preview
            row.addWidget(preview); layout.addLayout(row)
        right.addWidget(sound); right.addStretch()
        columns.addLayout(left, 1); columns.addWidget(right_widget); page.addLayout(columns, 1)
        footer = QHBoxLayout(); self.status_bar = text("正在连接 OBS…", "small"); footer.addWidget(self.status_bar, 1)
        footer.addWidget(text(f"OBS MONITOR  /  {VERSION}", "small")); page.addLayout(footer)

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
                self._log(f"主题保存失败：{exc}")
                for name, button in self.theme_buttons.items():
                    button.setChecked(name == self.config.theme)
                return
        else:
            self.config.theme = theme
        previous = getattr(self, "palette_values", None)
        snapshots = []
        if previous is not PALETTES[theme] and previous and self.isVisible() and animations_enabled():
            for target in (self.centralWidget(), self.settings_window):
                if target and target.isVisible():
                    snapshots.append((target, target.grab()))
        start_colors = self._title_colors or previous
        self._finish_theme_transition(settle=False)
        p = self.palette_values = PALETTES[theme]
        if QApplication.platformName() == "windows":
            QApplication.instance().styleHints().setColorScheme(
                Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light)
        self.setStyleSheet(f"""
QMainWindow, QDialog, #workspace {{background:{p['bg']};}}
QWidget {{color:{p['text']};font-family:'Microsoft YaHei UI';font-size:13px;}}
QLabel {{background:transparent;border:0;}}
QLabel[role='brand'] {{font-family:'Segoe UI';font-size:17px;font-weight:700;}}
QLabel[role='title'] {{font-size:22px;font-weight:700;}}
QLabel[role='eyebrow'] {{color:{p['muted']};font-size:12px;font-weight:600;}}
QLabel[role='section'] {{font-size:13px;font-weight:600;}}
QLabel[role='muted'] {{color:{p['muted']};font-size:12px;}}
QLabel[role='small'] {{color:{p['muted']};font-size:11px;}}
QLabel[role='clock'] {{font-family:'Consolas';font-size:48px;font-weight:400;}}
QLabel[role='metric'] {{font-family:'Segoe UI';font-size:27px;font-weight:600;}}
QLabel[role='metricSmall'] {{font-family:'Segoe UI';font-size:21px;font-weight:600;}}
QLabel[role='badge'] {{border-radius:12px;padding:5px 10px;background:{p['subtle']};font-size:11px;color:{p['muted']};}}
#brandMark {{background:#4f6fae;border-radius:10px;}}
#pairingCode {{background:#ffffff;border-radius:10px;color:#717887;}}
QFrame[kind='card'] {{background:{p['surface']};border:1px solid {p['line']};border-radius:16px;}}
#divider {{background:{p['line']};}}
#themeGroup {{background:{p['subtle']};border:1px solid {p['line']};border-radius:10px;}}
QPushButton {{border:1px solid {p['line']};border-radius:8px;padding:6px 10px;background:{p['surface']};color:{p['text']};}}
QPushButton:hover {{background:{p['subtle']};border-color:{p['accent']};}}
QPushButton:pressed {{background:{p['soft']};}}
QPushButton:focus {{border:1px solid {p['accent']};}}
QPushButton[kind='quiet'] {{border:0;background:transparent;padding:6px 9px;color:{p['muted']};}}
QPushButton[kind='theme'] {{border:0;background:transparent;padding:3px 4px;color:{p['muted']};font-size:11px;border-radius:7px;}}
QPushButton[kind='theme']:checked {{background:{p['surface']};color:{p['accent']};}}
QPushButton[kind='action'] {{border-radius:8px;font-size:12px;font-weight:600;}}
QPushButton[action='start'] {{background:{p['accent']};border-color:{p['accent']};color:{'#ffffff' if theme == 'light' else '#192231'};}}
QPushButton[action='start']:hover {{border-color:{p['text']};}}
QPushButton[action='pause'] {{color:{p['amber']};}}
QPushButton[action='resume'] {{color:{p['accent']};}}
QPushButton[action='stop'] {{color:{p['red']};}}
QPushButton[kind='action']:disabled {{color:{p['disabled']};background:{p['subtle']};border-color:{p['line']};}}
QPushButton[kind='secondary'] {{background:{p['soft']};color:{p['accent']};border-color:transparent;font-weight:600;}}
QPushButton[kind='link'] {{background:transparent;border:0;padding:3px;color:{p['muted']};font-size:11px;}}
QToolButton {{background:transparent;border:0;border-radius:6px;padding:3px;}}
QToolButton:hover {{background:{p['subtle']};}}
QCheckBox {{spacing:7px;font-size:12px;}}
QCheckBox::indicator {{width:15px;height:15px;border:1px solid {p['line']};border-radius:5px;background:{p['subtle']};}}
QCheckBox::indicator:checked {{background:{p['accent']};border:1px solid {p['accent']};}}
#masterSwitch::indicator {{width:33px;height:17px;border-radius:9px;background:{p['disabled']};border:0;}}
#masterSwitch::indicator:checked {{background:{p['accent']};}}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{background:{p['subtle']};border:1px solid {p['line']};border-radius:7px;padding:4px 8px;min-height:18px;}}
QComboBox:focus, QLineEdit:focus {{border-color:{p['accent']};}}
QFrame[kind='numberField'] {{background:{p['subtle']};border:1px solid {p['line']};border-radius:8px;}}
QFrame[kind='numberField'][focused='true'] {{border-color:{p['accent']};}}
QFrame[kind='numberField'] QSpinBox, QFrame[kind='numberField'] QDoubleSpinBox {{background:transparent;border:0;padding:0 5px;min-height:24px;}}
QFrame[kind='numberField'] QSpinBox::up-button, QFrame[kind='numberField'] QSpinBox::down-button,
QFrame[kind='numberField'] QDoubleSpinBox::up-button, QFrame[kind='numberField'] QDoubleSpinBox::down-button {{width:0;height:0;border:0;}}
QToolButton[kind='stepper'] {{background:{p['surface']};border:1px solid {p['line']};border-radius:5px;color:{p['muted']};font-family:'Segoe UI';font-size:17px;}}
QToolButton[kind='stepper']:hover {{background:{p['soft']};border-color:{p['accent']};color:{p['accent']};}}
QToolButton[kind='stepper']:pressed {{background:{p['soft']};color:{p['text']};}}
QToolButton[kind='stepper']:focus {{border-color:{p['accent']};}}
QToolButton[kind='stepper']:disabled {{color:{p['disabled']};background:{p['subtle']};}}
QComboBox::drop-down {{border:0;width:23px;}}
QComboBox QAbstractItemView {{background:{p['surface']};color:{p['text']};selection-background-color:{p['soft']};border:1px solid {p['line']};}}
QProgressBar {{background:{p['subtle']};border:0;border-radius:2px;}}
QProgressBar::chunk {{background:{p['accent']};border-radius:2px;}}
QPlainTextEdit {{background:transparent;color:{p['muted']};border:0;font-size:11px;selection-background-color:{p['soft']};}}
QScrollBar:vertical {{background:transparent;width:5px;margin:0;}}
QScrollBar::handle:vertical {{background:{p['line']};border-radius:2px;min-height:25px;}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
QToolTip {{background:{p['surface']};color:{p['text']};border:1px solid {p['line']};padding:5px;}}
""")
        for name, b in self.theme_buttons.items():
            b.setChecked(name == theme); b.setIcon(icon("sun" if name == "light" else "moon", p["accent"] if name == theme else p["muted"], 16))
        self.settings_button.setIcon(icon("settings", p["muted"], 18)); self.copy_button.setIcon(icon("copy", p["accent"], 18))
        for action, b in self.action_buttons.items():
            color = p["disabled"] if not b.isEnabled() else ("#ffffff" if self.config.theme == "light" else "#192231") if action == "start" else p["red"] if action == "stop" else p["amber"] if action == "pause" else p["accent"]
            b.setIcon(icon(action, color))
        for b in self.preview_buttons.values():
            b.setIcon(icon("resume", p["muted"], 14))
        self._tick()
        if snapshots:
            self._theme_overlays = [ThemeOverlay(target, snapshot) for target, snapshot in snapshots]
            for overlay in self._theme_overlays:
                overlay.show(); overlay.raise_()
            animation = self._theme_animation = QVariantAnimation(self)
            animation.setDuration(240)
            animation.setStartValue(0.0); animation.setEndValue(1.0)
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
            self._theme_animation.stop(); self._theme_animation.deleteLater()
            self._theme_animation = None
        for overlay in self._theme_overlays:
            overlay.hide(); overlay.deleteLater()
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
                    set_title_bar_theme(int(window.winId()), self.config.theme == "dark", self._title_colors["bg"], self._title_colors["text"])

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate) and isinstance(watched, QWidget) and watched.isWindow():
            if watched is self or self.isAncestorOf(watched):
                # Qt can refresh the native caption after show/activation.
                # Reapply custom colors after that refresh, including mid-fade.
                self._sync_title_bars(self._title_colors)
                QTimer.singleShot(0, lambda: self._sync_title_bars(self._title_colors))
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        self._finish_theme_transition()
        super().resizeEvent(event)

    def _on_event(self, kind, payload):
        self.events.put((kind, payload))

    def _log(self, message):
        self.log_text.appendPlainText(f"{datetime.now():%H:%M:%S}    {message}")
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def _tick(self):
        if self.closed:
            return
        for _ in range(100):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "action":
                self._log(LABELS[payload] + "成功")
                if self.config.sound_enabled and self.config.sound_actions.get(payload, True):
                    self._preview(payload, log=False)
            elif kind == "size_alert":
                self._log(f"录制达到 {format_size(payload)}，已触发本次大小提醒。"); self._preview("size_alert", log=False)
            elif kind in ("error", "log"):
                self._log(str(payload))
            elif kind == "command_result":
                self._busy = False
                try:
                    payload.result()
                except Exception as exc:
                    self._log("操作未完成：" + str(exc))
        state = self.controller.snapshot(); p = self.palette_values
        live = state["connected"] and not state["stale"]
        self.connection_label.setText("●  OBS 已连接" if live else "●  数据已过期" if state["connected"] else "●  OBS 未连接")
        self.connection_label.setStyleSheet(f"color:{p['accent'] if live else p['muted']};background:{p['accent_bg'] if live else p['subtle']};padding:5px 10px;border-radius:12px;")
        mode = "amber" if state["paused"] and live else "red" if state["active"] and live else "accent" if live else None
        self.state_label.setText("●  " + state["state_text"])
        self.state_label.setStyleSheet(f"color:{p[mode] if mode else p['muted']};background:{p[mode+'_bg'] if mode else p['subtle']};padding:5px 10px;border-radius:12px;")
        self.clock_label.setText(state["duration_text"]); self.clock_caption.setText("上次录制时长" if not state["active"] and state["bytes"] else "录制时长")
        self.size_label.setText(state["size_text"]); self.threshold_label.setText(f"{self.config.threshold_gb:g} GB" if self.config.alert_enabled else "已关闭")
        self.size_exact_label.setText(state["size_exact_text"] + (" · 上次数据" if state["stale"] else ""))
        self.progress.setValue(min(1000, int(state["bytes"] / max(1, state["threshold_bytes"]) * 1000)))
        self.limit_label.setText("数据已过期，等待重新连接 OBS" if state["stale"] else "本次录制已触发大小提醒" if state["alerted"] else "OBS 累计输出量 · 不依赖目录显示大小")
        self.sound_switch.blockSignals(True); self.sound_switch.setChecked(state["sound_enabled"]); self.sound_switch.blockSignals(False)
        for action, b in self.action_buttons.items():
            enabled = state["allowed"][action] and not self._busy and not state["busy"]
            if b.isEnabled() != enabled:
                b.setEnabled(enabled)
                color = p["disabled"] if not enabled else ("#ffffff" if self.config.theme == "light" else "#192231") if action == "start" else p["red"] if action == "stop" else p["amber"] if action == "pause" else p["accent"]
                b.setIcon(icon(action, color))
        self.command_label.setText("正在等待 OBS 确认…" if self._busy or state["busy"] else "OBS 确认后生效")
        self.status_bar.setText(state["message"] or ("●  已连接，手机与电脑状态同步" if self.remote else "●  已连接，手机遥控未开启"))

    def _preview(self, action, log=True):
        if self.player:
            try:
                self.player.play(action)
                if log:
                    self._log("试听：" + LABELS.get(action, "大小提醒"))
            except (OSError, RuntimeError) as exc:
                self._log("提示音播放失败：" + str(exc))

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
            self._log("提示音保存失败：" + str(exc))

    def _save_sound_actions(self):
        try:
            self.controller.set_sound_actions({name: b.isChecked() for name, b in self.sound_vars.items()})
        except OSError as exc:
            for name, item in self.sound_vars.items():
                item.blockSignals(True); item.setChecked(self.config.sound_actions[name]); item.blockSignals(False)
            self._log("提示音保存失败：" + str(exc))

    def _start_remote(self):
        if self.remote:
            self.remote.stop(); self.remote = None
        error = "手机遥控已关闭"
        if self.config.remote_enabled:
            try:
                self.remote = self._create_remote(self.config.remote_port)
                remote = self.remote
                self._log(f"手机遥控已开启，网页端口 {remote.port}。")
            except (OSError, ValueError) as exc:
                error = f"手机遥控启动失败：{exc}"; self._log(error)
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
        previous_block = self.address_combo.blockSignals(True)
        try:
            self.address_combo.clear()
            for address in self._known_addresses:
                self.address_combo.addItem(f"{address}:{self.config.remote_port}", address)
            if not self._known_addresses:
                self.address_combo.addItem("等待局域网地址…", None)
            if self._address_manually_selected and selected in self._known_addresses:
                self.address_combo.setCurrentIndex(self.address_combo.findData(selected))
            elif selected not in self._known_addresses:
                self._address_manually_selected = False
        finally:
            self.address_combo.blockSignals(previous_block)
        if self.remote and self._known_addresses:
            self.mobile_status.setText("已开启"); self._refresh_qr()
        elif self.remote:
            self.mobile_status.setText("等待网络")
            self.qr_label.setPixmap(QPixmap())
            self.qr_label.setText("正在发现局域网地址\n请确认电脑已连接网络\n地址将自动刷新")
        else:
            self.mobile_status.setText("未开启"); self.qr_label.setPixmap(QPixmap()); self.qr_label.setText(error)
        self.copy_button.setEnabled(self.remote is not None and bool(self._known_addresses))
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
                self._log(f"手机遥控已开启，网页端口 {self.remote.port}。")
        return previous

    def _refresh_qr(self, *args):
        if not self.remote or not self.address_combo.currentData():
            return
        code = qrcode.QRCode(box_size=4, border=3); code.add_data(self.remote.url(self.address_combo.currentData())); code.make(fit=True)
        image = code.make_image(fill_color="#17233b", back_color="#ffffff").convert("RGB")
        data = image.tobytes(); qt_image = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
        self.qr_label.setPixmap(QPixmap.fromImage(qt_image).scaled(116, 116, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))

    def copy_link(self):
        if self.remote and self.address_combo.currentData():
            QApplication.clipboard().setText(self.remote.url(self.address_combo.currentData())); self._log("手机控制链接已复制，发送到自己的手机即可打开。")

    def open_remote(self):
        if self.remote:
            webbrowser.open(self.remote.url("127.0.0.1"))

    def reset_phone_pairing(self):
        if not self.remote:
            return
        answer = QMessageBox.question(self, "重置手机配对", "重置后，所有旧链接和已配对手机将失效，需要使用新链接重新连接。录制会继续。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.remote.reset_pairing()
        except OSError as exc:
            QMessageBox.warning(self, "配对未重置", str(exc)); return
        self._refresh_qr()
        self._log("手机配对已重置，旧链接失效；请使用新链接或二维码连接。")

    def open_settings(self):
        if self.settings_window and self.settings_window.isVisible():
            self.settings_window.raise_(); return
        dialog = self.settings_window = QDialog(self); dialog.setWindowTitle("连接与提醒设置"); dialog.resize(470, 540)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        def closed():
            self._finish_theme_transition()
            if self.settings_window is dialog:
                self.settings_window = None
        dialog.finished.connect(closed)
        layout = QVBoxLayout(dialog); layout.setContentsMargins(28, 26, 28, 24); layout.setSpacing(18)
        layout.addWidget(text("连接与提醒", "title"))
        hint = text("在 OBS「工具 → WebSocket 服务器设置」中启用服务器。", "muted"); hint.setWordWrap(True); layout.addWidget(hint)
        form = QFormLayout(); form.setVerticalSpacing(12)
        host = QLineEdit(self.config.obs_host); port = QSpinBox(); port.setRange(1, 65535); port.setValue(self.config.obs_port)
        password = QLineEdit(self.config.obs_password); password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("与 OBS WebSocket 密码保持一致")
        remote_port = QSpinBox(); remote_port.setRange(1, 65535); remote_port.setValue(self.config.remote_port)
        threshold = QDoubleSpinBox(); threshold.setRange(0.1, 102400); threshold.setDecimals(1); threshold.setSuffix(" GB"); threshold.setValue(self.config.threshold_gb)
        for title, widget in (("OBS 地址", host), ("OBS 端口", port), ("OBS 密码", password), ("手机网页端口", remote_port), ("大小提醒阈值", threshold)):
            field = NumberField(widget, title) if isinstance(widget, QAbstractSpinBox) else widget
            field.setMinimumHeight(36)
            form.addRow(title, field)
        layout.addLayout(form)
        remote_enabled = ChoiceCheck("开启手机局域网遥控"); remote_enabled.setChecked(self.config.remote_enabled)
        alert_enabled = ChoiceCheck("达到大小阈值后提醒一次"); alert_enabled.setChecked(self.config.alert_enabled)
        layout.addWidget(remote_enabled); layout.addWidget(alert_enabled); layout.addStretch()
        reset_pairing = QPushButton("重置所有手机配对"); reset_pairing.setProperty("kind", "quiet"); reset_pairing.setMinimumHeight(30)
        reset_pairing.setEnabled(self.remote is not None); reset_pairing.clicked.connect(self.reset_phone_pairing)
        reset_pairing.setToolTip("更换配对凭证，所有旧链接和已配对浏览器立即失效")
        layout.addWidget(reset_pairing)
        save_button = QPushButton("保存并应用"); save_button.setProperty("action", "start"); save_button.setMinimumHeight(42); layout.addWidget(save_button)
        def apply():
            try:
                address = host.text().strip() or "127.0.0.1"
                if any(c in address for c in "/?#@\\ \t\n"):
                    raise ValueError("OBS 地址请只填写 IP 或主机名")
                if port.value() == remote_port.value() and remote_enabled.isChecked():
                    raise ValueError("手机端口不能与 OBS WebSocket 端口相同")
                candidate = AppConfig(obs_host=address, obs_port=port.value(), obs_password=password.text(), remote_port=remote_port.value(),
                    remote_enabled=remote_enabled.isChecked(), threshold_gb=threshold.value(), alert_enabled=alert_enabled.isChecked(),
                    sound_enabled=self.config.sound_enabled, sound_actions=self.config.sound_actions.copy(), theme=self.config.theme)
                previous = self.apply_settings(candidate)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(dialog, "设置未保存", str(exc)); return
            connection_changed = any(getattr(candidate, key) != getattr(previous, key) for key in ("obs_host", "obs_port", "obs_password"))
            self._log("设置已保存" + ("，正在重新连接 OBS。" if connection_changed else "。")); dialog.accept()
        save_button.clicked.connect(apply); dialog.show()

    def show_first_run_help(self):
        if self.closed or not self.isVisible() or self.config.help_seen:
            return
        self.show_help()
        try:
            self.controller.mark_help_seen()
        except OSError as exc:
            self._log(f"使用说明展示状态未保存，下次启动会再次显示：{exc}")

    def show_help(self):
        if self.help_window:
            if not self.help_window.isVisible():
                self.help_window.open()
            self.help_window.raise_(); self.help_window.activateWindow()
            return
        dialog = self.help_window = QMessageBox(self)
        dialog.setWindowTitle("使用说明")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText("1. 打开 OBS，启用「工具 → WebSocket 服务器设置」。\n\n2. 在本软件「设置」中填写相同的端口和密码。\n\n3. 使用桌面或手机控制录制。声音由电脑播放，可分别关闭。\n\n4. 手机与电脑连接同一局域网后扫码；多网卡时可切换 IP。被防火墙拦截时，请允许本软件通过专用网络。\n\n5. 暂停需要 OBS 使用支持暂停的录制编码器。\n\n关闭本软件会停止手机网页，不会结束 OBS 的录制。")
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.button(QMessageBox.StandardButton.Ok).setText("知道了")
        dialog.open()

    def closeEvent(self, event):
        if not self.closed:
            QApplication.instance().removeEventFilter(self)
            self._finish_theme_transition()
            self.closed = True; self.timer.stop()
            self.network_timer.stop()
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
    application = QApplication(sys.argv); application.setApplicationName("OBS Monitor")
    application.setFont(QFont("Microsoft YaHei UI", 10)); application.setStyle("Fusion")
    instance = SingleInstance(parent=application)
    if not instance.claim():
        return
    window = MonitorApp(); available = application.primaryScreen().availableGeometry()
    def activate():
        if window.isMinimized():
            window.showNormal()
        window.show(); window.raise_(); window.activateWindow()
    instance.activate.connect(activate)
    application.aboutToQuit.connect(instance.close)
    window.resize(min(880, available.width() - 40), min(600, available.height() - 40)); window.show()
    sys.exit(application.exec())
