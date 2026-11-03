from __future__ import annotations

import queue
import sys
import webbrowser
from datetime import datetime

import qrcode
from PySide6.QtCore import Qt, QTimer, QSize, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSpinBox,
    QToolButton, QVBoxLayout, QWidget)

from obs_controller import LABELS, OBSController, format_size
from instance_guard import SingleInstance
from pairing import PairingIdentity
from platform_support import AudioPlayer
from remote_server import RemoteServer, lan_addresses
from settings import AppConfig

from version import VERSION
PALETTES = {
    "light": dict(bg="#f4f6fa", surface="#ffffff", subtle="#f6f8fc", line="#e4e9f2", text="#17233b", muted="#7d8a9f", accent="#4263eb", soft="#eef2ff", green="#14866a", green_bg="#e8f7f0", red="#d7475e", red_bg="#fff0f2", amber="#a66e17", amber_bg="#fff6e5", disabled="#a9b3c3"),
    "dark": dict(bg="#10151f", surface="#192130", subtle="#202a3b", line="#2d384c", text="#edf2fb", muted="#98a7bf", accent="#8ca5ff", soft="#253450", green="#65d8b0", green_bg="#1d3c36", red="#ff8797", red_bg="#422b39", amber="#edc17a", amber_bg="#3c352c", disabled="#59677e"),
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
        painter.end()


class ChoiceCheck(QCheckBox):
    def __init__(self, title):
        super().__init__(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

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
        painter.end()


class MonitorApp(QMainWindow):
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
        self.setWindowTitle("OBS Monitor · 录制工作台")
        self.setWindowIcon(icon("logo", "#4263eb"))
        self.resize(880, 600)
        self.setMinimumSize(780, 540)
        self._build_ui()
        self.apply_theme(self.config.theme, save=False)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(120)
        if start_services:
            self.controller.start()
            self._start_remote()
        self._log("欢迎使用 OBS Monitor，打开 OBS 后会自动连接。")
        self._tick()

    def _build_ui(self):
        root = QWidget(); root.setObjectName("workspace"); self.setCentralWidget(root)
        page = QVBoxLayout(root); page.setContentsMargins(20, 18, 20, 14); page.setSpacing(14)
        header = QHBoxLayout(); header.setSpacing(10)
        mark = QLabel(); mark.setObjectName("brandMark"); mark.setFixedSize(34, 34)
        mark.setPixmap(icon("logo", "#ffffff").pixmap(22, 22)); mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(mark)
        brand = QVBoxLayout(); brand.setSpacing(0)
        brand.addWidget(text("OBS Monitor", "brand")); brand.addWidget(text("录制控制与实时大小", "small"))
        header.addLayout(brand); header.addStretch()
        self.connection_label = text("●  正在连接", "badge"); header.addWidget(self.connection_label)
        group = QFrame(); group.setObjectName("themeGroup")
        row = QHBoxLayout(group); row.setContentsMargins(4, 4, 4, 4); row.setSpacing(2)
        self.theme_buttons = {}
        for theme, title in (("light", "浅色"), ("dark", "深色")):
            b = QPushButton(title); b.setCheckable(True); b.setProperty("kind", "theme"); b.setFixedSize(58, 28)
            b.clicked.connect(lambda checked=False, value=theme: self.apply_theme(value))
            row.addWidget(b); self.theme_buttons[theme] = b
        header.addWidget(group)
        self.settings_button = QPushButton("设置"); self.settings_button.setProperty("kind", "quiet")
        self.settings_button.clicked.connect(self.open_settings); header.addWidget(self.settings_button)
        help_button = QPushButton("?"); help_button.setFixedSize(28, 30); help_button.setProperty("kind", "quiet")
        help_button.setToolTip("使用说明"); help_button.clicked.connect(self.show_help); header.addWidget(help_button)
        page.addLayout(header)
        columns = QHBoxLayout(); columns.setSpacing(14)
        left = QVBoxLayout(); left.setSpacing(12)
        right = QVBoxLayout(); right.setSpacing(12); right.setContentsMargins(0, 0, 0, 0)
        right_widget = QWidget(); right_widget.setFixedWidth(234); right_widget.setLayout(right)
        hero, layout = card((18, 16, 18, 16), 5)
        hero.setMinimumHeight(244)
        heading = QHBoxLayout(); heading.addWidget(text("当前录制", "section")); heading.addStretch()
        self.state_label = text("●  等待连接", "badge"); heading.addWidget(self.state_label)
        layout.addLayout(heading); layout.addSpacing(3)
        self.clock_label = text("00:00:00", "clock"); layout.addWidget(self.clock_label)
        self.clock_caption = text("录制时长", "muted"); layout.addWidget(self.clock_caption); layout.addSpacing(3)
        divider = QFrame(); divider.setObjectName("divider"); divider.setFixedHeight(1); layout.addWidget(divider)
        metrics = QHBoxLayout(); size_col = QVBoxLayout(); size_col.setSpacing(5)
        size_col.addWidget(text("实时录制大小", "muted")); self.size_label = text("0 B", "metric"); size_col.addWidget(self.size_label)
        self.size_exact_label = text("尚未收到 OBS 数据", "small"); size_col.addWidget(self.size_exact_label)
        self.size_label.setToolTip("直接读取 OBS 的累计输出字节计数；与最终文件的封装大小可能存在差异。")
        metrics.addLayout(size_col, 1); limit_col = QVBoxLayout(); limit_col.setSpacing(5)
        limit_col.addWidget(text("大小提醒阈值", "muted")); self.threshold_label = text("2 GB", "metricSmall"); limit_col.addWidget(self.threshold_label)
        limit_col.addWidget(text("本次录制提醒一次", "small"))
        metrics.addLayout(limit_col, 1); layout.addLayout(metrics)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); self.progress.setTextVisible(False); self.progress.setFixedHeight(4)
        layout.addWidget(self.progress); self.limit_label = text("从 OBS 直接读取录制数据", "small"); layout.addWidget(self.limit_label)
        left.addWidget(hero)
        control_heading = QHBoxLayout(); control_heading.addWidget(text("录制控制", "section")); control_heading.addStretch()
        self.command_label = text("OBS 确认后生效", "small"); control_heading.addWidget(self.command_label); left.addLayout(control_heading)
        controls = QGridLayout(); controls.setSpacing(8); self.action_buttons = {}
        for i, (action, title) in enumerate((("start", "开始录制"), ("pause", "暂停录制"), ("resume", "继续录制"), ("stop", "结束录制"))):
            b = QPushButton(title); b.setProperty("kind", "action"); b.setProperty("action", action)
            b.setIconSize(QSize(16, 16)); b.setFixedHeight(38); b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda checked=False, value=action: self.perform(value)); controls.addWidget(b, 0, i)
            self.action_buttons[action] = b
        left.addLayout(controls)
        activity, layout = card((16, 12, 16, 12), 6)
        row = QHBoxLayout(); row.addWidget(text("活动记录", "section")); row.addStretch(); row.addWidget(text("最近的录制操作", "small")); layout.addLayout(row)
        self.log_text = QPlainTextEdit(); self.log_text.setReadOnly(True); self.log_text.setMaximumBlockCount(160); self.log_text.setMinimumHeight(44)
        layout.addWidget(self.log_text); left.addWidget(activity, 1)
        mobile, layout = card((14, 14, 14, 14), 8)
        row = QHBoxLayout(); row.addWidget(text("手机遥控", "section")); row.addStretch()
        self.mobile_status = text("准备中", "small"); row.addWidget(self.mobile_status); layout.addLayout(row)
        self.qr_label = QLabel("手机遥控未启动"); self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedHeight(116); self.qr_label.setToolTip("手机与电脑连接同一 Wi-Fi 后扫码"); self.qr_label.setWordWrap(True); layout.addWidget(self.qr_label)
        self.address_combo = QComboBox(); self.address_combo.setMinimumHeight(28)
        self.address_combo.currentTextChanged.connect(self._refresh_qr); layout.addWidget(self.address_combo)
        self.copy_button = QPushButton("复制链接"); self.copy_button.setFixedHeight(32); self.copy_button.setProperty("kind", "secondary")
        self.copy_button.clicked.connect(self.copy_link)
        self.open_button = QPushButton("预览"); self.open_button.setProperty("kind", "quiet"); self.open_button.setFixedHeight(32)
        self.open_button.clicked.connect(self.open_remote)
        phone_buttons = QHBoxLayout(); phone_buttons.setSpacing(6); phone_buttons.addWidget(self.copy_button, 1); phone_buttons.addWidget(self.open_button)
        layout.addLayout(phone_buttons); right.addWidget(mobile)
        sound, layout = card((14, 12, 14, 12), 8)
        row = QHBoxLayout(); row.addWidget(text("操作提示音", "section")); row.addStretch()
        self.sound_switch = ToggleSwitch(); self.sound_switch.setObjectName("masterSwitch"); self.sound_switch.setAccessibleName("操作提示音总开关")
        self.sound_switch.setChecked(self.config.sound_enabled); self.sound_switch.toggled.connect(self._toggle_sound); row.addWidget(self.sound_switch)
        layout.addLayout(row)
        self.sound_switch.setToolTip("四种操作成功后，在电脑播放声音")
        grid = QGridLayout(); grid.setHorizontalSpacing(6); grid.setVerticalSpacing(7)
        self.sound_vars = {}; self.preview_buttons = {}
        for i, (action, title) in enumerate((("start", "开始"), ("stop", "结束"), ("pause", "暂停"), ("resume", "继续"))):
            row = QHBoxLayout(); item = ChoiceCheck(title); item.setChecked(self.config.sound_actions[action])
            item.toggled.connect(self._save_sound_actions); self.sound_vars[action] = item; row.addWidget(item)
            preview = QToolButton(); preview.setToolTip(f"试听{title}提示音"); preview.setAccessibleName(f"试听{title}提示音"); preview.setFixedSize(25, 25)
            preview.clicked.connect(lambda checked=False, value=action: self._preview(value)); self.preview_buttons[action] = preview
            row.addWidget(preview); row.addStretch(); grid.addLayout(row, i // 2, i % 2)
        layout.addLayout(grid); right.addWidget(sound); right.addStretch()
        columns.addLayout(left, 1); columns.addWidget(right_widget); page.addLayout(columns, 1)
        footer = QHBoxLayout(); self.status_bar = text("正在连接 OBS…", "small"); footer.addWidget(self.status_bar, 1)
        footer.addWidget(text(f"OBS Monitor  {VERSION}", "small")); page.addLayout(footer)

    def apply_theme(self, theme, save=True):
        if theme not in PALETTES:
            return
        if save:
            try:
                self.controller.set_theme(theme)
            except OSError as exc:
                self._log(f"主题保存失败：{exc}"); return
        else:
            self.config.theme = theme
        p = self.palette_values = PALETTES[theme]
        self.setStyleSheet(f"""
QMainWindow, QDialog, #workspace {{background:{p['bg']};}}
QWidget {{color:{p['text']};font-family:'Microsoft YaHei UI';font-size:13px;}}
QLabel {{background:transparent;border:0;}}
QLabel[role='brand'] {{font-family:'Segoe UI';font-size:17px;font-weight:700;}}
QLabel[role='title'] {{font-size:22px;font-weight:700;}}
QLabel[role='section'] {{font-size:13px;font-weight:600;}}
QLabel[role='muted'] {{color:{p['muted']};font-size:12px;}}
QLabel[role='small'] {{color:{p['muted']};font-size:11px;}}
QLabel[role='clock'] {{font-family:'Consolas';font-size:42px;font-weight:600;}}
QLabel[role='metric'] {{font-family:'Segoe UI';font-size:22px;font-weight:600;}}
QLabel[role='metricSmall'] {{font-family:'Segoe UI';font-size:21px;font-weight:600;}}
QLabel[role='badge'] {{border-radius:12px;padding:5px 10px;background:{p['subtle']};font-size:11px;color:{p['muted']};}}
#brandMark {{background:#4263eb;border-radius:12px;}}
QFrame[kind='card'] {{background:{p['surface']};border:1px solid {p['line']};border-radius:12px;}}
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
QPushButton[action='start'] {{background:#4263eb;border-color:#4263eb;color:white;}}
QPushButton[action='start']:hover {{background:#3555dc;}}
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
            color = p["disabled"] if not b.isEnabled() else "#ffffff" if action == "start" else p["red"] if action == "stop" else p["text"]
            b.setIcon(icon(action, color))
        for b in self.preview_buttons.values():
            b.setIcon(icon("resume", p["muted"], 14))
        self._tick()

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
        self.connection_label.setStyleSheet(f"color:{p['green'] if live else p['muted']};background:{p['green_bg'] if live else p['subtle']};padding:5px 10px;border-radius:12px;")
        mode = "amber" if state["paused"] and live else "red" if state["active"] and live else "green" if live else None
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
                color = p["disabled"] if not enabled else "#ffffff" if action == "start" else p["red"] if action == "stop" else p["text"]
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
                if self.pairing_identity is None:
                    self.pairing_identity = PairingIdentity.persistent()
                remote = RemoteServer(self.controller, port=self.config.remote_port, identity=self.pairing_identity); remote.start(); self.remote = remote
                self._log(f"手机遥控已开启，网页端口 {remote.port}。")
            except OSError as exc:
                error = f"手机遥控启动失败：{exc}"; self._log(error)
        self.address_combo.clear()
        for address in lan_addresses() or ["127.0.0.1"]:
            self.address_combo.addItem(f"{address}:{self.config.remote_port}", address)
        if self.remote:
            self.mobile_status.setText("已开启"); self._refresh_qr()
        else:
            self.mobile_status.setText("未开启"); self.qr_label.setPixmap(QPixmap()); self.qr_label.setText(error)
        self.copy_button.setEnabled(self.remote is not None); self.open_button.setEnabled(self.remote is not None)

    def _refresh_qr(self, *args):
        if not self.remote or not self.address_combo.currentData():
            return
        code = qrcode.QRCode(box_size=4, border=3); code.add_data(self.remote.url(self.address_combo.currentData())); code.make(fit=True)
        image = code.make_image(fill_color="#17233b", back_color="#ffffff").convert("RGB")
        data = image.tobytes(); qt_image = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
        self.qr_label.setPixmap(QPixmap.fromImage(qt_image).scaled(116, 116, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))

    def copy_link(self):
        if self.remote:
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
        layout = QVBoxLayout(dialog); layout.setContentsMargins(28, 26, 28, 24); layout.setSpacing(18)
        layout.addWidget(text("连接与提醒", "title"))
        hint = text("在 OBS「工具 → WebSocket 服务器设置」中启用服务器。", "muted"); hint.setWordWrap(True); layout.addWidget(hint)
        form = QFormLayout(); form.setVerticalSpacing(16)
        host = QLineEdit(self.config.obs_host); port = QSpinBox(); port.setRange(1, 65535); port.setValue(self.config.obs_port)
        password = QLineEdit(self.config.obs_password); password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("与 OBS WebSocket 密码保持一致")
        remote_port = QSpinBox(); remote_port.setRange(1, 65535); remote_port.setValue(self.config.remote_port)
        threshold = QDoubleSpinBox(); threshold.setRange(0.1, 102400); threshold.setDecimals(1); threshold.setSuffix(" GB"); threshold.setValue(self.config.threshold_gb)
        for title, widget in (("OBS 地址", host), ("OBS 端口", port), ("OBS 密码", password), ("手机网页端口", remote_port), ("大小提醒阈值", threshold)):
            form.addRow(title, widget)
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
                previous = self.controller.apply_connection_config(candidate)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(dialog, "设置未保存", str(exc)); return
            remote_changed = candidate.remote_port != previous.remote_port or candidate.remote_enabled != previous.remote_enabled
            connection_changed = any(getattr(candidate, key) != getattr(previous, key) for key in ("obs_host", "obs_port", "obs_password"))
            self.config = candidate
            if connection_changed:
                self.controller.reconnect()
            if remote_changed or candidate.remote_enabled and not self.remote:
                self._start_remote()
            self._log("设置已保存" + ("，正在重新连接 OBS。" if connection_changed else "。")); dialog.accept()
        save_button.clicked.connect(apply); dialog.show()

    def show_help(self):
        QMessageBox.information(self, "使用说明", "1. 打开 OBS，启用「工具 → WebSocket 服务器设置」。\n\n2. 在本软件「设置」中填写相同的端口和密码。\n\n3. 使用桌面或手机控制录制。声音由电脑播放，可分别关闭。\n\n4. 手机与电脑连接同一 Wi-Fi 后扫码；多网卡时可切换 IP。被防火墙拦截时，请允许本软件通过专用网络。\n\n5. 暂停需要 OBS 使用支持暂停的录制编码器。\n\n关闭本软件会停止手机网页，不会结束 OBS 的录制。")

    def closeEvent(self, event):
        if not self.closed:
            self.closed = True; self.timer.stop()
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
