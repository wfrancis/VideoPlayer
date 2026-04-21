import argparse
import json
import logging
import os
import re
import sys
import threading
from bisect import bisect_right
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QSettings,
    QSignalBlocker,
    QSizeF,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPalette,
    QShortcut,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSlider,
    QStyle,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
)

from video_player_metadata import (
    APP_BUILD,
    APP_DISPLAY_NAME,
    APP_IDENTIFIER,
    APP_MIN_SYSTEM_VERSION,
    APP_NAME,
    APP_ORGANIZATION_DOMAIN,
    APP_ORGANIZATION_NAME,
    APP_SETTINGS_APP,
    APP_SETTINGS_ORG,
    APP_VERSION,
    DEFAULT_CONTROL_HOST,
    DEFAULT_CONTROL_PORT,
    LEGACY_SETTINGS_APP,
    LEGACY_SETTINGS_ORG,
)

SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
KEY_SPEEDS = (0.5, 0.75, 1.0, 1.5, 2.0)
SKIP_PRESETS = (5, 10, 30)
DEFAULT_SKIP = 5
VIEW_MODES = ("fit", "fill", "stretch")
SUBTITLE_EXTENSIONS = (".srt",)
SUBTITLE_DELAY_STEP_MS = 250
SUBTITLE_SIZE_OPTIONS = (18, 24, 30, 36)
SUBTITLE_COLORS = {
    "White": "#ffffff",
    "Yellow": "#ffd54a",
    "Cyan": "#70d6ff",
    "Green": "#8df27c",
}
DEFAULT_SUBTITLE_SIZE = 30
DEFAULT_SUBTITLE_COLOR = "White"
KEYBOARD_SKIP_MS = 5_000
KEYBOARD_MEDIUM_SKIP_MS = 10_000
KEYBOARD_LARGE_SKIP_MS = 30_000

RESUME_MIN_MS = 10_000
RESUME_END_CLEARANCE_MS = 30_000
OVERLAY_HIDE_MS = 2500
PAN_DRAG_THRESHOLD = 6

LOG = logging.getLogger("video_player")


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def resolve_control_host(value=None):
    host = value or os.environ.get("VIDEO_PLAYER_CONTROL_HOST", DEFAULT_CONTROL_HOST)
    host = str(host).strip()
    return host or DEFAULT_CONTROL_HOST


def resolve_control_port(value=None):
    raw_value = value
    if raw_value is None:
        raw_value = os.environ.get("VIDEO_PLAYER_CONTROL_PORT", DEFAULT_CONTROL_PORT)
    try:
        port = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_CONTROL_PORT
    if 1 <= port <= 65535:
        return port
    return DEFAULT_CONTROL_PORT


def format_ms(ms):
    total_seconds = max(0, int(ms) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_delay_ms(ms):
    sign = "+" if ms >= 0 else "−"
    return f"{sign}{abs(ms) / 1000:.2f}s"


_SRT_TIMECODE_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{1,3})"
)


def parse_subtitle_timestamp(value):
    hours, minutes, rest = value.replace(".", ",").split(":")
    seconds, milliseconds = rest.split(",")
    milliseconds = milliseconds.ljust(3, "0")[:3]
    total = (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )
    return total


def parse_srt(text):
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip())
    entries = []
    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n")]
        if not lines:
            continue

        match = None
        text_start_index = 0
        if len(lines) >= 1:
            match = _SRT_TIMECODE_RE.search(lines[0])
            if match:
                text_start_index = 1
        if match is None and len(lines) >= 2:
            match = _SRT_TIMECODE_RE.search(lines[1])
            if match:
                text_start_index = 2
        if match is None:
            continue

        subtitle_text = "\n".join(line for line in lines[text_start_index:] if line.strip())
        if not subtitle_text:
            continue

        entries.append(
            (
                parse_subtitle_timestamp(match.group("start")),
                parse_subtitle_timestamp(match.group("end")),
                subtitle_text,
            )
        )
    return entries


def configure_logging():
    log_root_path = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    if log_root_path:
        log_root = Path(log_root_path)
    else:
        log_root = Path.home() / "Library" / "Logs" / APP_NAME
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / "video-player.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    LOG.info("Logging to %s", log_path)
    return log_path


def install_excepthook():
    default_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            default_hook(exc_type, exc_value, exc_traceback)
            return
        LOG.exception(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        default_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _hook


class VideoApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self._pending_file = None
        self._window = None
        self.setApplicationName(APP_NAME)
        self.setApplicationDisplayName(APP_DISPLAY_NAME)
        self.setApplicationVersion(APP_VERSION)
        self.setOrganizationName(APP_ORGANIZATION_NAME)
        self.setOrganizationDomain(APP_ORGANIZATION_DOMAIN)
        self.log_path = configure_logging()
        install_excepthook()
        self._apply_theme()

    def _apply_theme(self):
        self.setStyle("Fusion")

        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#101318"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#eef3f8"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#11161d"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#161c25"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#11161d"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#eef3f8"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#eef3f8"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#171d26"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#eef3f8"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#0a84ff"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
        self.setPalette(palette)

        self.setStyleSheet(
            """
            QMenuBar {
                background: #101318;
                color: #eef3f8;
                border-bottom: 1px solid #1d2430;
            }
            QMenuBar::item {
                padding: 6px 10px;
                background: transparent;
            }
            QMenuBar::item:selected {
                background: #1b2330;
                border-radius: 6px;
            }
            QMenu {
                background: #121822;
                color: #eef3f8;
                border: 1px solid #273243;
            }
            QMenu::item:selected {
                background: #1e2a39;
            }
            QFrame#overlayShell {
                background: rgba(10, 12, 16, 104);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 14px;
            }
            QPushButton#controlButton {
                color: #eef3f8;
                background: transparent;
                border: none;
                border-radius: 9px;
                padding: 5px 10px;
                min-height: 26px;
                font-weight: 600;
            }
            QPushButton#controlButton:hover {
                background: rgba(255, 255, 255, 0.08);
            }
            QPushButton#controlButton:pressed {
                background: rgba(255, 255, 255, 0.14);
            }
            QPushButton#transportButton {
                color: #f6f9fc;
                background: rgba(255, 255, 255, 0.08);
                border: none;
                min-width: 36px;
                max-width: 36px;
                min-height: 36px;
                max-height: 36px;
                border-radius: 18px;
                padding: 0;
            }
            QPushButton#transportButton:hover {
                background: rgba(255, 255, 255, 0.14);
            }
            QPushButton#transportButton:pressed {
                background: rgba(255, 255, 255, 0.2);
            }
            QComboBox#controlCombo {
                color: #eef3f8;
                background: transparent;
                border: none;
                border-radius: 9px;
                padding: 4px 8px;
                min-width: 62px;
            }
            QComboBox#controlCombo:hover {
                background: rgba(255, 255, 255, 0.08);
            }
            QComboBox#controlCombo::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox#controlCombo QAbstractItemView {
                background: #121822;
                color: #eef3f8;
                border: 1px solid #273243;
                selection-background-color: #0a84ff;
            }
            QSlider#seekSlider::groove:horizontal {
                height: 3px;
                background: rgba(255, 255, 255, 0.12);
                border-radius: 2px;
            }
            QSlider#seekSlider::sub-page:horizontal {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0a84ff,
                    stop:1 #59b4ff
                );
                border-radius: 2px;
            }
            QSlider#seekSlider::add-page:horizontal {
                background: rgba(255, 255, 255, 0.12);
                border-radius: 2px;
            }
            QSlider#seekSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid rgba(10, 132, 255, 0.7);
                width: 10px;
                margin: -5px 0;
                border-radius: 6px;
            }
            QSlider#volumeSlider::groove:horizontal {
                height: 3px;
                background: rgba(255, 255, 255, 0.14);
                border-radius: 2px;
            }
            QSlider#volumeSlider::sub-page:horizontal {
                background: rgba(88, 180, 255, 0.95);
                border-radius: 2px;
            }
            QSlider#volumeSlider::add-page:horizontal {
                background: rgba(255, 255, 255, 0.12);
                border-radius: 2px;
            }
            QSlider#volumeSlider::handle:horizontal {
                background: #ffffff;
                width: 9px;
                margin: -5px 0;
                border-radius: 6px;
            }
            QLabel#fileLabel {
                color: #ffffff;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#metaLabel {
                color: rgba(231, 238, 246, 0.72);
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#timeLabel {
                color: #f4f7fb;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0 2px 0 6px;
            }
            QLabel#osdLabel {
                color: #ffffff;
                background: rgba(8, 12, 18, 210);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 18px;
                padding: 10px 16px;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#subtitleLabel {
                background: transparent;
                font-weight: 700;
            }
            """
        )

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file()
            if self._window is not None:
                self._window.open_file(path)
            else:
                self._pending_file = path
            return True
        return super().event(event)


class ControlHandler(BaseHTTPRequestHandler):
    player_ref = None

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/state":
            self._json(self.player_ref.get_state_snapshot())
        elif self.path == "/health":
            self._json({"ok": True, "app": APP_NAME, "version": APP_VERSION})
        else:
            self._json({"error": "unknown path"}, 404)

    def do_POST(self):
        data = self._body()
        action_map = {
            "/play": ("play", None),
            "/pause": ("pause", None),
            "/toggle": ("toggle", None),
            "/skip": ("skip", data.get("seconds")),
            "/seek": ("seek", data.get("seconds")),
            "/speed": ("speed", data.get("rate")),
            "/volume": ("volume", data.get("level")),
            "/mute": ("mute", data.get("on")),
            "/zoom": ("zoom", data.get("factor")),
            "/reset_zoom": ("reset_zoom", None),
            "/open": ("open", data.get("path")),
            "/pip": ("pip", data.get("on")),
            "/view_mode": ("view_mode", data.get("mode")),
            "/cycle_view_mode": ("cycle_view_mode", None),
            "/fullscreen": ("fullscreen", data.get("on")),
            "/subtitles/open": ("subtitles_open", data.get("path")),
            "/subtitles/toggle": ("subtitles_toggle", None),
            "/subtitles/enabled": ("subtitles_enabled", data.get("on")),
            "/subtitles/delay": ("subtitles_delay", data.get("ms")),
            "/subtitles/size": ("subtitles_size", data.get("size")),
            "/subtitles/color": ("subtitles_color", data.get("name")),
        }
        if self.path in action_map:
            action, arg = action_map[self.path]
            self.player_ref.control_signal.emit(action, arg)
            self._json({"ok": True, "action": action})
        else:
            self._json({"error": "unknown path"}, 404)

    def log_message(self, *args, **kwargs):
        pass


class ClickableSlider(QSlider):
    pointer_activity = pyqtSignal()

    def mousePressEvent(self, event):
        self.pointer_activity.emit()
        if event.button() == Qt.MouseButton.LeftButton:
            val = QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                int(event.position().x()),
                self.width(),
            )
            self.setValue(val)
            self.sliderMoved.emit(val)
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.pointer_activity.emit()
        super().mouseMoveEvent(event)


class VideoView(QGraphicsView):
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    pointer_activity = pyqtSignal()

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._suppress_click = False
        self._press_pos = None
        self._drag_moved = False
        self._pan_enabled = False
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setStyleSheet("background: black; border: 0;")
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def set_pan_enabled(self, enabled):
        self._pan_enabled = bool(enabled)
        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag
            if self._pan_enabled
            else QGraphicsView.DragMode.NoDrag
        )

    def mousePressEvent(self, event):
        self.pointer_activity.emit()
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            self._drag_moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.pointer_activity.emit()
        if self._press_pos is not None:
            if (event.position() - self._press_pos).manhattanLength() >= PAN_DRAG_THRESHOLD:
                self._drag_moved = True
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._suppress_click = True
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.pointer_activity.emit()
            dragged = self._drag_moved
            self._press_pos = None
            self._drag_moved = False
            super().mouseReleaseEvent(event)
            if self._suppress_click:
                self._suppress_click = False
            elif not dragged:
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self.pointer_activity.emit()
        event.ignore()


class GradientPanel(QFrame):
    def __init__(self, edge, parent=None):
        super().__init__(parent)
        self.edge = edge
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        if self.edge == "top":
            gradient.setColorAt(0.0, QColor(5, 8, 12, 235))
            gradient.setColorAt(1.0, QColor(5, 8, 12, 0))
        else:
            gradient.setColorAt(0.0, QColor(5, 8, 12, 0))
            gradient.setColorAt(0.35, QColor(5, 8, 12, 70))
            gradient.setColorAt(1.0, QColor(5, 8, 12, 205))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)


class Player(QMainWindow):
    control_signal = pyqtSignal(str, object)

    def __init__(self, *, control_host, control_port, control_server_enabled):
        super().__init__()
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1180, 760)
        self.setMinimumSize(720, 420)
        self.setAcceptDrops(True)

        self.control_host = resolve_control_host(control_host)
        self.control_port = resolve_control_port(control_port)
        self.control_server_enabled = bool(control_server_enabled)
        self.http_server = None

        self.settings = self._open_settings()
        self.skip_seconds = int(self.settings.value("skip_seconds", DEFAULT_SKIP))
        if self.skip_seconds not in SKIP_PRESETS:
            self.skip_seconds = DEFAULT_SKIP
        self.view_mode = str(self.settings.value("view_mode", "fit"))
        if self.view_mode not in VIEW_MODES:
            self.view_mode = "fit"
        self.subtitle_enabled = str(
            self.settings.value("subtitle_enabled", "true")
        ).lower() != "false"
        self.subtitle_delay_ms = int(self.settings.value("subtitle_delay_ms", 0))
        self.subtitle_size = int(self.settings.value("subtitle_size", DEFAULT_SUBTITLE_SIZE))
        if self.subtitle_size not in SUBTITLE_SIZE_OPTIONS:
            self.subtitle_size = DEFAULT_SUBTITLE_SIZE
        self.subtitle_color_name = str(
            self.settings.value("subtitle_color", DEFAULT_SUBTITLE_COLOR)
        )
        if self.subtitle_color_name not in SUBTITLE_COLORS:
            self.subtitle_color_name = DEFAULT_SUBTITLE_COLOR
        self.current_file = None
        self.user_zoom = 1.0
        self._resume_pending = False
        self._resume_path = None
        self._pip_mode = False
        self._pip_restore_geometry = None
        self._pip_restore_fullscreen = False
        self._overlay_visible = True
        self._shortcuts = []
        self.subtitle_entries = []
        self.subtitle_starts = []
        self.subtitle_file = None
        self.subtitle_text = ""
        self._pitch_compensation_enabled = None

        self._state_lock = threading.Lock()
        self._state = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "build": APP_BUILD,
            "log_path": str(getattr(QApplication.instance(), "log_path", "")),
            "position_ms": 0,
            "duration_ms": 0,
            "playing": False,
            "rate": 1.0,
            "volume": 0.85,
            "muted": False,
            "file": None,
            "display_name": None,
            "position_text": "0:00",
            "duration_text": "0:00",
            "view_mode": self.view_mode,
            "pip": False,
            "fullscreen": False,
            "zoom": 1.0,
            "skip_seconds": self.skip_seconds,
            "subtitle_loaded": False,
            "subtitle_enabled": self.subtitle_enabled,
            "subtitle_file": None,
            "subtitle_delay_ms": self.subtitle_delay_ms,
            "subtitle_size": self.subtitle_size,
            "subtitle_color": self.subtitle_color_name,
            "pitch_compensation": None,
            "control_host": self.control_host,
            "control_port": self.control_port,
            "control_server_enabled": self.control_server_enabled,
        }

        self.control_signal.connect(self._handle_control)

        self.scene = QGraphicsScene(self)
        self.video_item = QGraphicsVideoItem()
        self.scene.addItem(self.video_item)

        self.central = QWidget(self)
        self.central.setStyleSheet("background: black;")
        self.setCentralWidget(self.central)

        self.view = VideoView(self.scene, self.central)
        self.view.clicked.connect(self.toggle_play)
        self.view.double_clicked.connect(self.toggle_fullscreen)
        self.view.pointer_activity.connect(self._on_pointer_activity)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)

        self.top_panel = GradientPanel("top", self.central)
        self.bottom_panel = GradientPanel("bottom", self.central)
        self.top_panel.installEventFilter(self)
        self.bottom_panel.installEventFilter(self)

        self.top_effect = QGraphicsOpacityEffect(self.top_panel)
        self.bottom_effect = QGraphicsOpacityEffect(self.bottom_panel)
        self.top_panel.setGraphicsEffect(self.top_effect)
        self.bottom_panel.setGraphicsEffect(self.bottom_effect)
        self.top_effect.setOpacity(1.0)
        self.bottom_effect.setOpacity(1.0)

        self.top_anim = QPropertyAnimation(self.top_effect, b"opacity", self)
        self.bottom_anim = QPropertyAnimation(self.bottom_effect, b"opacity", self)
        for animation in (self.top_anim, self.bottom_anim):
            animation.setDuration(180)
            animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.osd_label = QLabel("", self.central)
        self.osd_label.setObjectName("osdLabel")
        self.osd_label.hide()

        self.subtitle_label = QLabel("", self.central)
        self.subtitle_label.setObjectName("subtitleLabel")
        self.subtitle_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
        )
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.hide()
        self._apply_subtitle_style()

        self.overlay_timer = QTimer(self)
        self.overlay_timer.setSingleShot(True)
        self.overlay_timer.timeout.connect(self._auto_hide_overlay)

        self.osd_timer = QTimer(self)
        self.osd_timer.setSingleShot(True)
        self.osd_timer.timeout.connect(self.osd_label.hide)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(float(self.settings.value("volume", 0.85)))
        self.audio.setMuted(str(self.settings.value("muted", "false")).lower() == "true")
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_item)
        if hasattr(self.player, "setPitchCompensation"):
            try:
                self.player.setPitchCompensation(True)
                if hasattr(self.player, "pitchCompensation"):
                    self._pitch_compensation_enabled = bool(self.player.pitchCompensation())
            except Exception:
                self._pitch_compensation_enabled = None

        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_error)
        self.video_item.nativeSizeChanged.connect(self._fit_video)

        self._build_controls()
        self._build_menu()
        self._build_shortcuts()
        self._refresh_skip_labels()
        self._refresh_meta_label()
        self._sync_zoom_ui()
        self._sync_subtitle_ui()
        self._sync_mute_ui()
        self._sync_window_mode_buttons()
        self._start_control_server()

        self._update_state(
            pitch_compensation=self._pitch_compensation_enabled,
            subtitle_enabled=self.subtitle_enabled,
            subtitle_delay_ms=self.subtitle_delay_ms,
            subtitle_size=self.subtitle_size,
            subtitle_color=self.subtitle_color_name,
        )

        saved_geometry = self.settings.value("window_geometry")
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)

    def _open_settings(self):
        settings = QSettings(APP_SETTINGS_ORG, APP_SETTINGS_APP)
        if str(settings.value("settings_migrated", "")).lower() == "true":
            return settings

        legacy_settings = QSettings(LEGACY_SETTINGS_ORG, LEGACY_SETTINGS_APP)
        for key in legacy_settings.allKeys():
            if settings.value(key) is None:
                settings.setValue(key, legacy_settings.value(key))
        settings.setValue("settings_migrated", "true")
        settings.sync()
        return settings

    def _start_control_server(self):
        ControlHandler.player_ref = self
        if not self.control_server_enabled:
            LOG.info("Control server disabled by configuration")
            self._update_state(control_server_enabled=False)
            return
        try:
            self.http_server = ThreadingHTTPServer(
                (self.control_host, self.control_port), ControlHandler
            )
        except OSError as e:
            LOG.warning(
                "Control server disabled on %s:%s: %s",
                self.control_host,
                self.control_port,
                e,
            )
            self.http_server = None
            self._update_state(control_server_enabled=False)
            return

        thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        thread.start()
        LOG.info(
            "Control server listening on http://%s:%s",
            self.control_host,
            self.control_port,
        )
        self._update_state(control_server_enabled=True)

    def get_state_snapshot(self):
        with self._state_lock:
            return dict(self._state)

    def _update_state(self, **kwargs):
        with self._state_lock:
            self._state.update(kwargs)

    def _build_controls(self):
        self.top_panel.hide()

        bottom_layout = QVBoxLayout(self.bottom_panel)
        bottom_layout.setContentsMargins(18, 6, 18, 10)
        bottom_layout.setSpacing(6)

        self.transport_shell = QFrame(self.bottom_panel)
        self.transport_shell.setObjectName("overlayShell")
        self.transport_shell.setMaximumWidth(640)
        shell_layout = QVBoxLayout(self.transport_shell)
        shell_layout.setContentsMargins(14, 8, 14, 8)
        shell_layout.setSpacing(6)

        self.timeline_shell = QFrame(self.bottom_panel)
        self.timeline_shell.setObjectName("overlayShell")
        self.timeline_shell.setMaximumWidth(880)
        timeline_shell_layout = QVBoxLayout(self.timeline_shell)
        timeline_shell_layout.setContentsMargins(14, 8, 14, 8)
        timeline_shell_layout.setSpacing(0)

        timeline_row = QHBoxLayout()
        timeline_row.setSpacing(10)

        self.slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("seekSlider")
        self.slider.pointer_activity.connect(self._on_pointer_activity)
        self.slider.sliderMoved.connect(self.player.setPosition)
        timeline_row.addWidget(self.slider, 1)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("timeLabel")
        timeline_row.addWidget(self.time_label)
        timeline_shell_layout.addLayout(timeline_row)

        timeline_wrap = QHBoxLayout()
        timeline_wrap.setContentsMargins(0, 0, 0, 0)
        timeline_wrap.addStretch(1)
        timeline_wrap.addWidget(self.timeline_shell)
        timeline_wrap.addStretch(1)
        bottom_layout.addLayout(timeline_wrap)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(8)

        style = self.style()

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("transportButton")
        self.play_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.setToolTip("Play or pause (Space or K)")
        self.play_btn.clicked.connect(self.toggle_play)
        controls_row.addWidget(self.play_btn)

        self.back_btn = QPushButton()
        self.back_btn.setObjectName("controlButton")
        self.back_btn.clicked.connect(lambda: self.skip(-self._skip_interval_ms()))
        controls_row.addWidget(self.back_btn)

        self.fwd_btn = QPushButton()
        self.fwd_btn.setObjectName("controlButton")
        self.fwd_btn.clicked.connect(lambda: self.skip(self._skip_interval_ms()))
        controls_row.addWidget(self.fwd_btn)

        self.skip_combo = QComboBox()
        self.skip_combo.setObjectName("controlCombo")
        for seconds in SKIP_PRESETS:
            self.skip_combo.addItem(f"{seconds}s", seconds)
        index = self.skip_combo.findData(self.skip_seconds)
        if index < 0:
            self.skip_combo.addItem(f"{self.skip_seconds}s", self.skip_seconds)
            index = self.skip_combo.count() - 1
        self.skip_combo.setCurrentIndex(index)
        self.skip_combo.setToolTip("Skip interval")
        self.skip_combo.currentIndexChanged.connect(self._on_skip_changed)
        self._on_skip_changed(self.skip_combo.currentIndex())
        controls_row.addWidget(self.skip_combo)

        self.mute_btn = QPushButton("Mute")
        self.mute_btn.setObjectName("controlButton")
        self.mute_btn.setCheckable(True)
        self.mute_btn.clicked.connect(lambda checked: self.set_muted(checked, show_osd=True))
        controls_row.addWidget(self.mute_btn)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(round(self.audio.volume() * 100)))
        self.volume_slider.setFixedWidth(92)
        self.volume_slider.valueChanged.connect(
            lambda value: self.set_volume(value / 100.0, show_osd=False)
        )
        controls_row.addWidget(self.volume_slider)

        self.speed_combo = QComboBox()
        self.speed_combo.setObjectName("controlCombo")
        for speed in SPEEDS:
            self.speed_combo.addItem(f"{speed:g}x", speed)
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        controls_row.addWidget(self.speed_combo)
        self.set_speed(1.0, show_osd=False)

        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setObjectName("controlButton")
        self.zoom_out_btn.setToolTip("Zoom out")
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_by(1 / 1.15))
        controls_row.addWidget(self.zoom_out_btn)

        self.zoom_reset_btn = QPushButton("100%")
        self.zoom_reset_btn.setObjectName("controlButton")
        self.zoom_reset_btn.setToolTip("Reset zoom")
        self.zoom_reset_btn.clicked.connect(self.reset_zoom)
        controls_row.addWidget(self.zoom_reset_btn)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setObjectName("controlButton")
        self.zoom_in_btn.setToolTip("Zoom in")
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_by(1.15))
        controls_row.addWidget(self.zoom_in_btn)

        shell_layout.addLayout(controls_row)

        controls_wrap = QHBoxLayout()
        controls_wrap.setContentsMargins(0, 0, 0, 0)
        controls_wrap.addStretch(1)
        controls_wrap.addWidget(self.transport_shell)
        controls_wrap.addStretch(1)
        bottom_layout.addLayout(controls_wrap)

        for widget in (
            self.play_btn,
            self.back_btn,
            self.fwd_btn,
            self.skip_combo,
            self.mute_btn,
            self.volume_slider,
            self.speed_combo,
            self.zoom_out_btn,
            self.zoom_reset_btn,
            self.zoom_in_btn,
        ):
            widget.installEventFilter(self)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("File")
        self.open_act = QAction("Open...", self)
        self.open_act.setShortcut(QKeySequence.StandardKey.Open)
        self.open_act.triggered.connect(lambda: self.open_file())
        file_menu.addAction(self.open_act)

        self.open_subtitles_act = QAction("Open Subtitles...", self)
        self.open_subtitles_act.setShortcut("Ctrl+Shift+O")
        self.open_subtitles_act.triggered.connect(lambda: self.load_subtitle_file())
        file_menu.addAction(self.open_subtitles_act)

        player_menu = self.menuBar().addMenu("Player")

        self.fullscreen_act = QAction("Fullscreen", self)
        self.fullscreen_act.setShortcut("F")
        self.fullscreen_act.setCheckable(True)
        self.fullscreen_act.triggered.connect(self.set_fullscreen)
        player_menu.addAction(self.fullscreen_act)

        self.pip_act = QAction("Mini Player", self)
        self.pip_act.setShortcut("P")
        self.pip_act.setCheckable(True)
        self.pip_act.triggered.connect(self.set_pip_mode)
        player_menu.addAction(self.pip_act)

        player_menu.addSeparator()

        crop_menu = player_menu.addMenu("Crop Mode")
        self.view_mode_group = QActionGroup(self)
        self.view_mode_group.setExclusive(True)
        self.view_mode_actions = {}
        for mode in VIEW_MODES:
            action = QAction(mode.title(), self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked, value=mode: self.set_view_mode(value)
            )
            crop_menu.addAction(action)
            self.view_mode_group.addAction(action)
            self.view_mode_actions[mode] = action

        self.cycle_view_mode_act = QAction("Cycle Crop Mode", self)
        self.cycle_view_mode_act.setShortcut("C")
        self.cycle_view_mode_act.triggered.connect(self.cycle_view_mode)
        player_menu.addAction(self.cycle_view_mode_act)

        subtitles_menu = self.menuBar().addMenu("Subtitles")

        self.subtitle_toggle_act = QAction("Enabled", self)
        self.subtitle_toggle_act.setCheckable(True)
        self.subtitle_toggle_act.triggered.connect(self.toggle_subtitles)
        subtitles_menu.addAction(self.subtitle_toggle_act)

        self.subtitle_open_act = QAction("Load .srt...", self)
        self.subtitle_open_act.triggered.connect(lambda: self.load_subtitle_file())
        subtitles_menu.addAction(self.subtitle_open_act)

        self.clear_subtitles_act = QAction("Clear External Subtitles", self)
        self.clear_subtitles_act.triggered.connect(lambda: self.clear_subtitles(show_osd=True))
        subtitles_menu.addAction(self.clear_subtitles_act)

        subtitles_menu.addSeparator()

        self.subtitle_delay_back_act = QAction("Delay -250ms", self)
        self.subtitle_delay_back_act.triggered.connect(
            lambda: self.adjust_subtitle_delay(-SUBTITLE_DELAY_STEP_MS)
        )
        subtitles_menu.addAction(self.subtitle_delay_back_act)

        self.subtitle_delay_fwd_act = QAction("Delay +250ms", self)
        self.subtitle_delay_fwd_act.triggered.connect(
            lambda: self.adjust_subtitle_delay(SUBTITLE_DELAY_STEP_MS)
        )
        subtitles_menu.addAction(self.subtitle_delay_fwd_act)

        subtitles_menu.addSeparator()

        size_menu = subtitles_menu.addMenu("Subtitle Size")
        self.subtitle_size_group = QActionGroup(self)
        self.subtitle_size_group.setExclusive(True)
        self.subtitle_size_actions = {}
        for size in SUBTITLE_SIZE_OPTIONS:
            action = QAction(f"{size}px", self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, value=size: self.set_subtitle_size(value))
            size_menu.addAction(action)
            self.subtitle_size_group.addAction(action)
            self.subtitle_size_actions[size] = action

        color_menu = subtitles_menu.addMenu("Subtitle Color")
        self.subtitle_color_group = QActionGroup(self)
        self.subtitle_color_group.setExclusive(True)
        self.subtitle_color_actions = {}
        for color_name in SUBTITLE_COLORS:
            action = QAction(color_name, self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked, value=color_name: self.set_subtitle_color(value)
            )
            color_menu.addAction(action)
            self.subtitle_color_group.addAction(action)
            self.subtitle_color_actions[color_name] = action

    def _show_context_menu(self, position):
        menu = QMenu(self)
        menu.addAction(self.open_act)
        menu.addAction(self.open_subtitles_act)

        subtitles_menu = menu.addMenu("Subtitles")
        subtitles_menu.addAction(self.subtitle_toggle_act)
        subtitles_menu.addAction(self.subtitle_open_act)
        subtitles_menu.addAction(self.clear_subtitles_act)
        subtitles_menu.addSeparator()
        subtitles_menu.addAction(self.subtitle_delay_back_act)
        subtitles_menu.addAction(self.subtitle_delay_fwd_act)

        size_menu = subtitles_menu.addMenu("Subtitle Size")
        for action in self.subtitle_size_actions.values():
            size_menu.addAction(action)

        color_menu = subtitles_menu.addMenu("Subtitle Color")
        for action in self.subtitle_color_actions.values():
            color_menu.addAction(action)

        player_menu = menu.addMenu("Player")
        player_menu.addAction(self.fullscreen_act)
        player_menu.addAction(self.pip_act)
        player_menu.addAction(self.cycle_view_mode_act)

        crop_menu = player_menu.addMenu("Crop Mode")
        for action in self.view_mode_actions.values():
            crop_menu.addAction(action)

        menu.exec(self.view.mapToGlobal(position))

    def _build_shortcuts(self):
        context = Qt.ShortcutContext.ApplicationShortcut

        def bind(keys, fn):
            sequence_list = keys if isinstance(keys, (list, tuple)) else [keys]
            for key in sequence_list:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setContext(context)
                shortcut.activated.connect(fn)
                self._shortcuts.append(shortcut)

        bind(["Space", "K"], self.toggle_play)
        bind(["Left", "J"], lambda: self.skip(-self._skip_interval_ms()))
        bind(["Right", "L"], lambda: self.skip(self._skip_interval_ms()))
        bind("Shift+Left", lambda: self.skip(-KEYBOARD_MEDIUM_SKIP_MS))
        bind("Shift+Right", lambda: self.skip(KEYBOARD_MEDIUM_SKIP_MS))
        bind("Alt+Left", lambda: self.skip(-KEYBOARD_LARGE_SKIP_MS))
        bind("Alt+Right", lambda: self.skip(KEYBOARD_LARGE_SKIP_MS))
        bind("M", lambda: self.set_muted(not self.audio.isMuted(), show_osd=True))
        bind("Up", lambda: self.set_volume(self.audio.volume() + 0.05, show_osd=True))
        bind("Down", lambda: self.set_volume(self.audio.volume() - 0.05, show_osd=True))
        bind("F", self.toggle_fullscreen)
        bind("Escape", self.exit_fullscreen)
        bind("P", lambda: self.set_pip_mode(not self._pip_mode))
        bind("C", self.cycle_view_mode)
        bind("Shift+C", lambda: self.cycle_view_mode(-1))
        bind("[", lambda: self.cycle_speed(-1))
        bind("]", lambda: self.cycle_speed(1))
        bind(["+", "="], lambda: self.zoom_by(1.15))
        bind("-", lambda: self.zoom_by(1 / 1.15))
        bind("Z", lambda: self.zoom_by(1.15))
        bind("Shift+Z", lambda: self.zoom_by(1 / 1.15))
        bind("0", self.reset_zoom)
        bind("S", self.toggle_subtitles)
        bind("Shift+S", lambda: self.load_subtitle_file())
        bind(",", lambda: self.adjust_subtitle_delay(-SUBTITLE_DELAY_STEP_MS))
        bind(".", lambda: self.adjust_subtitle_delay(SUBTITLE_DELAY_STEP_MS))
        for index, speed in enumerate(KEY_SPEEDS, start=1):
            bind(str(index), lambda s=speed: self.set_speed(s, show_osd=True))

    def open_file(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open Video",
                "",
                "Video Files (*.mp4 *.mov *.m4v *.mkv *.webm *.avi);;All Files (*)",
            )
        if not path:
            return

        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            LOG.warning("Open requested for missing file: %s", path)
            self.show_osd("File not found")
            return
        if self.current_file:
            self._save_position()

        self.current_file = path
        self._resume_pending = True
        self._resume_path = path
        self.user_zoom = 1.0
        LOG.info("Opening video: %s", path)

        self._autoload_sidecar_subtitles(path)
        self._refresh_meta_label()
        self._update_state(
            file=path,
            display_name=os.path.basename(path),
            duration_ms=0,
            duration_text="0:00",
            position_ms=0,
            position_text="0:00",
            zoom=self.user_zoom,
        )
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self._sync_zoom_ui()
        self.show_osd("Opening video")
        self._on_pointer_activity()

    def _handle_control(self, action, arg):
        try:
            if action == "play":
                self.player.play()
            elif action == "pause":
                self.player.pause()
            elif action == "toggle":
                self.toggle_play()
            elif action == "skip" and arg is not None:
                self.skip(int(float(arg) * 1000))
            elif action == "seek" and arg is not None:
                self.player.setPosition(int(float(arg) * 1000))
            elif action == "speed" and arg is not None:
                self.set_speed(float(arg), show_osd=False)
            elif action == "volume" and arg is not None:
                self.set_volume(float(arg), show_osd=False)
            elif action == "mute":
                self.set_muted(coerce_bool(arg), show_osd=False)
            elif action == "zoom" and arg is not None:
                self.zoom_by(float(arg), absolute=False)
            elif action == "reset_zoom":
                self.reset_zoom()
            elif action == "open" and arg:
                self.open_file(str(arg))
            elif action == "pip":
                self.set_pip_mode(
                    coerce_bool(arg) if arg is not None else not self._pip_mode
                )
            elif action == "view_mode" and arg:
                self.set_view_mode(str(arg))
            elif action == "cycle_view_mode":
                self.cycle_view_mode()
            elif action == "fullscreen":
                self.set_fullscreen(coerce_bool(arg))
            elif action == "subtitles_open":
                self.load_subtitle_file(str(arg) if arg else None)
            elif action == "subtitles_toggle":
                self.toggle_subtitles()
            elif action == "subtitles_enabled":
                self.set_subtitle_enabled(coerce_bool(arg), show_osd=False)
            elif action == "subtitles_delay" and arg is not None:
                self.set_subtitle_delay(int(arg))
            elif action == "subtitles_size" and arg is not None:
                self.set_subtitle_size(int(arg))
            elif action == "subtitles_color" and arg:
                self.set_subtitle_color(str(arg))
        except Exception as e:
            LOG.exception("Control action %r failed", action)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def skip(self, ms, *, show_osd=False):
        duration = self.player.duration()
        new_position = max(0, min(duration, self.player.position() + ms))
        self.player.setPosition(new_position)

        if show_osd:
            direction = "+" if ms >= 0 else ""
            self.show_osd(f"{direction}{ms / 1000:.0f}s")

    def _skip_interval_ms(self):
        current = self.skip_combo.currentData() if hasattr(self, "skip_combo") else None
        try:
            seconds = int(current)
        except (TypeError, ValueError):
            seconds = int(self.skip_seconds)
        return seconds * 1000

    def _on_skip_changed(self, _index):
        current = self.skip_combo.currentData()
        if current is None:
            return
        self.skip_seconds = int(current)
        self.settings.setValue("skip_seconds", self.skip_seconds)
        self._refresh_skip_labels()
        self._update_state(skip_seconds=self.skip_seconds)

    def _refresh_skip_labels(self):
        seconds = self.skip_seconds
        self.back_btn.setText(f"-{seconds}s")
        self.back_btn.setToolTip(f"Back {seconds} seconds")
        self.fwd_btn.setText(f"+{seconds}s")
        self.fwd_btn.setToolTip(f"Forward {seconds} seconds")

    def set_speed(self, speed, show_osd=True):
        speed = clamp(float(speed), min(SPEEDS), max(SPEEDS))
        self.player.setPlaybackRate(speed)
        combo_index = self.speed_combo.findData(speed)
        if combo_index >= 0:
            blocker = QSignalBlocker(self.speed_combo)
            self.speed_combo.setCurrentIndex(combo_index)
            del blocker
        self._refresh_meta_label()
        self._update_state(rate=speed)
        if show_osd:
            self.show_osd(f"{speed:g}x")

    def _on_speed_changed(self, _index):
        self.set_speed(float(self.speed_combo.currentData()), show_osd=True)

    def cycle_speed(self, direction):
        current = self._nearest_speed(self.player.playbackRate())
        index = SPEEDS.index(current)
        next_index = clamp(index + direction, 0, len(SPEEDS) - 1)
        self.set_speed(SPEEDS[next_index], show_osd=True)

    def _nearest_speed(self, value):
        return min(SPEEDS, key=lambda item: abs(item - value))

    def set_volume(self, level, show_osd=False):
        level = clamp(float(level), 0.0, 1.0)
        self.audio.setVolume(level)
        if level > 0 and self.audio.isMuted():
            self.audio.setMuted(False)
        blocker = QSignalBlocker(self.volume_slider)
        self.volume_slider.setValue(int(round(level * 100)))
        del blocker
        self.settings.setValue("volume", level)
        self._sync_mute_ui()
        self._update_state(volume=level, muted=self.audio.isMuted())
        if show_osd:
            self.show_osd(f"Volume {int(round(level * 100))}%")

    def set_muted(self, muted, show_osd=False):
        self.audio.setMuted(bool(muted))
        self.settings.setValue("muted", "true" if self.audio.isMuted() else "false")
        self._sync_mute_ui()
        self._update_state(volume=self.audio.volume(), muted=self.audio.isMuted())
        if show_osd:
            self.show_osd("Muted" if self.audio.isMuted() else "Sound on")

    def _sync_mute_ui(self):
        muted = self.audio.isMuted()
        blocker = QSignalBlocker(self.mute_btn)
        self.mute_btn.setChecked(muted)
        self.mute_btn.setText("Muted" if muted else "Mute")
        del blocker

    def _subtitle_file_candidates(self, video_path):
        base = os.path.splitext(video_path)[0]
        for extension in SUBTITLE_EXTENSIONS:
            yield base + extension
            yield base + extension.upper()

    def _autoload_sidecar_subtitles(self, video_path):
        self.clear_subtitles(show_osd=False)
        for candidate in self._subtitle_file_candidates(video_path):
            if os.path.exists(candidate):
                self.load_subtitle_file(candidate, auto=True)
                return
        self._sync_subtitle_ui()
        self._refresh_meta_label()

    def load_subtitle_file(self, path=None, auto=False):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open Subtitles",
                os.path.dirname(self.current_file) if self.current_file else "",
                "Subtitle Files (*.srt);;All Files (*)",
            )
        if not path:
            return False

        path = os.path.abspath(os.path.expanduser(path))
        try:
            try:
                text = Path(path).read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = Path(path).read_text(encoding="latin-1")
            entries = parse_srt(text)
        except Exception as exc:
            LOG.exception("Subtitle load failed for %s", path)
            if not auto:
                self.show_osd("Could not load subtitles")
            return False

        if not entries:
            if not auto:
                self.show_osd("No subtitle cues found")
            return False

        self.subtitle_entries = entries
        self.subtitle_starts = [start for start, _, _ in entries]
        self.subtitle_file = path
        LOG.info("Loaded subtitles: %s (%s entries)", path, len(entries))
        if not auto:
            self.subtitle_enabled = True
        self.settings.setValue("subtitle_enabled", "true" if self.subtitle_enabled else "false")
        self._sync_subtitle_ui()
        self._refresh_meta_label()
        self._update_state(
            subtitle_loaded=True,
            subtitle_enabled=self.subtitle_enabled,
            subtitle_file=path,
        )
        self._update_subtitle_display(self.player.position())
        if not auto:
            self.show_osd(f"Loaded subtitles ({len(entries)})")
        return True

    def clear_subtitles(self, show_osd=False):
        self.subtitle_entries = []
        self.subtitle_starts = []
        self.subtitle_file = None
        self.subtitle_text = ""
        self.subtitle_label.hide()
        self._sync_subtitle_ui()
        self._refresh_meta_label()
        self._update_state(
            subtitle_loaded=False,
            subtitle_enabled=self.subtitle_enabled,
            subtitle_file=None,
        )
        if show_osd:
            self.show_osd("Subtitles cleared")

    def toggle_subtitles(self):
        if not self.subtitle_entries:
            return self.load_subtitle_file()
        self.set_subtitle_enabled(not self.subtitle_enabled, show_osd=True)
        return True

    def set_subtitle_enabled(self, enabled, show_osd=False):
        enabled = bool(enabled) and bool(self.subtitle_entries)
        self.subtitle_enabled = enabled
        self.settings.setValue("subtitle_enabled", "true" if enabled else "false")
        self._sync_subtitle_ui()
        self._refresh_meta_label()
        self._update_state(subtitle_enabled=self.subtitle_enabled)
        self._update_subtitle_display(self.player.position())
        if show_osd and self.subtitle_entries:
            self.show_osd("Subtitles on" if enabled else "Subtitles off")

    def adjust_subtitle_delay(self, delta_ms):
        self.set_subtitle_delay(self.subtitle_delay_ms + int(delta_ms))

    def set_subtitle_delay(self, delay_ms):
        self.subtitle_delay_ms = clamp(int(delay_ms), -10_000, 10_000)
        self.settings.setValue("subtitle_delay_ms", self.subtitle_delay_ms)
        self._refresh_meta_label()
        self._update_state(subtitle_delay_ms=self.subtitle_delay_ms)
        self._update_subtitle_display(self.player.position())
        self.show_osd(f"Subtitle delay {format_delay_ms(self.subtitle_delay_ms)}")

    def set_subtitle_size(self, size):
        size = int(size)
        if size not in SUBTITLE_SIZE_OPTIONS:
            return
        self.subtitle_size = size
        self.settings.setValue("subtitle_size", self.subtitle_size)
        self._apply_subtitle_style()
        self._sync_subtitle_ui()
        self._update_state(subtitle_size=self.subtitle_size)
        self.show_osd(f"Subtitle size {self.subtitle_size}px")

    def set_subtitle_color(self, color_name):
        if color_name not in SUBTITLE_COLORS:
            return
        self.subtitle_color_name = color_name
        self.settings.setValue("subtitle_color", self.subtitle_color_name)
        self._apply_subtitle_style()
        self._sync_subtitle_ui()
        self._update_state(subtitle_color=self.subtitle_color_name)
        self.show_osd(f"{self.subtitle_color_name} subtitles")

    def _apply_subtitle_style(self):
        self.subtitle_label.setStyleSheet(
            f"""
            QLabel#subtitleLabel {{
                color: {SUBTITLE_COLORS[self.subtitle_color_name]};
                background: rgba(4, 7, 12, 120);
                border-radius: 16px;
                padding: 8px 14px;
                font-size: {self.subtitle_size}px;
                font-weight: 700;
            }}
            """
        )

    def _sync_subtitle_ui(self):
        loaded = bool(self.subtitle_entries)
        if hasattr(self, "subtitle_toggle_act"):
            blocker = QSignalBlocker(self.subtitle_toggle_act)
            self.subtitle_toggle_act.setChecked(loaded and self.subtitle_enabled)
            self.subtitle_toggle_act.setEnabled(loaded)
            del blocker
        if hasattr(self, "subtitle_size_actions"):
            for size, action in self.subtitle_size_actions.items():
                blocker = QSignalBlocker(action)
                action.setChecked(size == self.subtitle_size)
                del blocker
        if hasattr(self, "subtitle_color_actions"):
            for color_name, action in self.subtitle_color_actions.items():
                blocker = QSignalBlocker(action)
                action.setChecked(color_name == self.subtitle_color_name)
                del blocker

    def _update_subtitle_display(self, position_ms):
        if not self.subtitle_entries or not self.subtitle_enabled:
            self.subtitle_text = ""
            self.subtitle_label.hide()
            return

        effective_position = max(0, position_ms + self.subtitle_delay_ms)
        index = bisect_right(self.subtitle_starts, effective_position) - 1
        if index < 0:
            self.subtitle_text = ""
            self.subtitle_label.hide()
            return

        start_ms, end_ms, text = self.subtitle_entries[index]
        if not (start_ms <= effective_position <= end_ms):
            self.subtitle_text = ""
            self.subtitle_label.hide()
            return

        if text != self.subtitle_text:
            self.subtitle_text = text
            self.subtitle_label.setText(text)
            self._layout_overlay_widgets()
        self.subtitle_label.show()
        self.subtitle_label.raise_()

    def set_view_mode(self, mode):
        mode = str(mode).lower()
        if mode not in VIEW_MODES:
            return
        self.view_mode = mode
        self.settings.setValue("view_mode", self.view_mode)
        self._apply_view_transform()
        self._refresh_meta_label()
        self._update_state(view_mode=self.view_mode)
        self.show_osd(f"{self.view_mode.title()} mode")

    def cycle_view_mode(self, direction=1):
        index = VIEW_MODES.index(self.view_mode)
        self.set_view_mode(VIEW_MODES[(index + direction) % len(VIEW_MODES)])

    def zoom_by(self, factor, absolute=False, show_osd=False):
        if absolute:
            self.user_zoom = clamp(float(factor), 1.0, 8.0)
        else:
            self.user_zoom = clamp(self.user_zoom * float(factor), 1.0, 8.0)
        self._apply_view_transform()
        self._sync_zoom_ui()
        self._update_state(zoom=round(self.user_zoom, 3))
        if show_osd:
            self.show_osd(f"Zoom {self.user_zoom:.2f}x")

    def reset_zoom(self, show_osd=False):
        self.user_zoom = 1.0
        self._apply_view_transform()
        self._sync_zoom_ui()
        self._update_state(zoom=self.user_zoom)
        if show_osd:
            self.show_osd("Zoom reset")

    def _sync_zoom_ui(self):
        percent = int(round(self.user_zoom * 100))
        self.zoom_reset_btn.setText(f"{percent}%")
        self.zoom_reset_btn.setEnabled(self.user_zoom != 1.0)
        self.zoom_out_btn.setEnabled(self.user_zoom > 1.0)
        self.zoom_in_btn.setEnabled(self.user_zoom < 8.0)
        self.view.set_pan_enabled(self.user_zoom > 1.0)

    def _fit_video(self, _size):
        self._apply_view_transform()

    def _apply_view_transform(self):
        size = self.video_item.nativeSize()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0:
            return

        self.video_item.setSize(QSizeF(size))
        self.scene.setSceneRect(self.video_item.boundingRect())
        self.view.resetTransform()
        aspect_mode = {
            "fit": Qt.AspectRatioMode.KeepAspectRatio,
            "fill": Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            "stretch": Qt.AspectRatioMode.IgnoreAspectRatio,
        }[self.view_mode]
        self.view.fitInView(self.video_item, aspect_mode)
        if self.user_zoom != 1.0:
            self.view.scale(self.user_zoom, self.user_zoom)

    def _on_duration(self, duration):
        self.slider.setRange(0, duration)
        self._update_time()
        self._update_state(duration_ms=duration, duration_text=format_ms(duration))
        if self._resume_pending and self.current_file == self._resume_path:
            self._restore_position(duration)

    def _restore_position(self, duration):
        self._resume_pending = False
        resume_ms = self._load_resume_position(self.current_file)
        if self._resume_position_valid(resume_ms, duration):
            self.player.setPosition(resume_ms)
            self.show_osd(f"Resumed at {format_ms(resume_ms)}")
        else:
            self._clear_resume_position(self.current_file)

    def _on_position(self, position):
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self._update_time()
        self._update_subtitle_display(position)
        self._update_state(position_ms=position)

    def _update_time(self):
        position = self.player.position()
        duration = self.player.duration()
        position_text = format_ms(position)
        duration_text = format_ms(duration)
        self.time_label.setText(f"{position_text} / {duration_text}")
        self._update_state(position_text=position_text, duration_text=duration_text)

    def _on_playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        icon = (
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
            if playing
            else self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.play_btn.setIcon(icon)
        self._update_state(playing=playing)
        if playing:
            self._on_pointer_activity()
        else:
            self._save_position()
            self._set_overlay_visible(True)

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._clear_resume_position(self.current_file)
            self.show_osd("Finished")

    def _resume_key(self, path):
        encoded = path.encode("utf-8", errors="surrogatepass").hex()
        return f"resume/{encoded}"

    def _load_resume_position(self, path):
        if not path:
            return None
        value = self.settings.value(self._resume_key(path))
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resume_position_valid(self, position_ms, duration_ms):
        if position_ms is None or duration_ms <= 0:
            return False
        if position_ms < RESUME_MIN_MS:
            return False
        if duration_ms - position_ms <= RESUME_END_CLEARANCE_MS:
            return False
        return True

    def _save_position(self):
        if not self.current_file:
            return
        position = self.player.position()
        duration = self.player.duration()
        if duration <= 0:
            return
        if self._resume_position_valid(position, duration):
            self.settings.setValue(self._resume_key(self.current_file), int(position))
            self.settings.sync()
        else:
            self._clear_resume_position(self.current_file)

    def _clear_resume_position(self, path):
        if path:
            self.settings.remove(self._resume_key(path))
            self.settings.sync()

    def _refresh_meta_label(self):
        if self.current_file:
            title = f"{os.path.basename(self.current_file)} - {APP_DISPLAY_NAME}"
        else:
            title = APP_DISPLAY_NAME
        self.setWindowTitle(title)

        bits = [f"{self.player.playbackRate():g}x", self.view_mode.title()]
        if self._pip_mode:
            bits.append("Mini")
        if self.subtitle_entries:
            if self.subtitle_enabled:
                bits.append(f"Subs {format_delay_ms(self.subtitle_delay_ms)}")
            else:
                bits.append("Subs Off")
        if hasattr(self, "view_mode_actions"):
            for mode, action in self.view_mode_actions.items():
                blocker = QSignalBlocker(action)
                action.setChecked(mode == self.view_mode)
                del blocker

    def set_pip_mode(self, enabled):
        enabled = bool(enabled)
        if enabled == self._pip_mode:
            return

        if enabled:
            self._pip_restore_geometry = self.saveGeometry()
            self._pip_restore_fullscreen = self.isFullScreen()
            if self.isFullScreen():
                self.showNormal()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()
            self.resize(540, 304)
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
            self.show_osd("Mini player on")
        else:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self.show()
            if self._pip_restore_geometry is not None:
                self.restoreGeometry(self._pip_restore_geometry)
            if self._pip_restore_fullscreen:
                self.showFullScreen()
            self.show_osd("Mini player off")

        self._pip_mode = enabled
        if hasattr(self, "pip_act"):
            blocker = QSignalBlocker(self.pip_act)
            self.pip_act.setChecked(enabled)
            del blocker
        self._refresh_meta_label()
        self._sync_window_mode_buttons()
        self._update_state(pip=self._pip_mode)

    def set_fullscreen(self, enabled):
        enabled = bool(enabled)
        if enabled == self.isFullScreen():
            return
        if enabled and self._pip_mode:
            self.set_pip_mode(False)
        if enabled:
            self.showFullScreen()
        else:
            self.showNormal()
        self._sync_window_mode_buttons()
        self._on_pointer_activity()

    def toggle_fullscreen(self):
        self.set_fullscreen(not self.isFullScreen())

    def exit_fullscreen(self):
        if self.isFullScreen():
            self.set_fullscreen(False)

    def _sync_window_mode_buttons(self):
        if hasattr(self, "fullscreen_act"):
            blocker = QSignalBlocker(self.fullscreen_act)
            self.fullscreen_act.setChecked(self.isFullScreen())
            del blocker
        self.menuBar().setVisible(not self.isFullScreen())
        self._update_state(fullscreen=self.isFullScreen())
        if not self.isFullScreen():
            self.unsetCursor()
            self._set_overlay_visible(True)
        self._refresh_meta_label()

    def show_osd(self, text):
        self.osd_label.setText(text)
        self.osd_label.adjustSize()
        self._layout_overlay_widgets()
        self.osd_label.show()
        self.osd_label.raise_()
        self.osd_timer.start(1200)

    def _on_pointer_activity(self):
        self._set_overlay_visible(True)
        if self._should_auto_hide_overlay():
            self.overlay_timer.start(OVERLAY_HIDE_MS)

    def _should_auto_hide_overlay(self):
        return self.isFullScreen() and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _auto_hide_overlay(self):
        if self._should_auto_hide_overlay():
            self._set_overlay_visible(False)

    def _set_overlay_visible(self, visible):
        if self._overlay_visible == visible:
            return
        self._overlay_visible = visible
        self._animate_overlay(self.bottom_panel, self.bottom_effect, self.bottom_anim, visible)
        self._layout_overlay_widgets()
        if visible:
            self.unsetCursor()
        elif self.isFullScreen():
            self.setCursor(Qt.CursorShape.BlankCursor)

    def _animate_overlay(self, panel, effect, animation, visible):
        panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not visible)
        animation.stop()
        animation.setStartValue(effect.opacity())
        animation.setEndValue(1.0 if visible else 0.0)
        animation.start()

    def _on_error(self, _error, message):
        LOG.error("Media error: %s", message)
        self.show_osd("Could not play file")

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter):
            self._on_pointer_activity()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_overlay_widgets()
        self._apply_view_transform()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_window_mode_buttons()

    def _layout_overlay_widgets(self):
        rect = self.central.rect()
        self.view.setGeometry(rect)

        top_height = 0
        timeline_height = self.timeline_shell.sizeHint().height()
        transport_height = self.transport_shell.sizeHint().height()
        bottom_height = max(96, timeline_height + transport_height + 22)
        self.top_panel.setGeometry(0, 0, rect.width(), top_height)
        self.bottom_panel.setGeometry(0, rect.height() - bottom_height, rect.width(), bottom_height)

        subtitle_bottom_margin = bottom_height + 8 if self._overlay_visible else 36
        self.subtitle_label.setGeometry(
            56,
            max(24, rect.height() - subtitle_bottom_margin - 120),
            max(200, rect.width() - 112),
            110,
        )

        if self.osd_label.isVisible():
            self.osd_label.move(
                max(0, (rect.width() - self.osd_label.width()) // 2),
                max(0, (rect.height() - self.osd_label.height()) // 2),
            )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                local_path = url.toLocalFile()
                if local_path.lower().endswith(SUBTITLE_EXTENSIONS):
                    self.load_subtitle_file(local_path)
                else:
                    self.open_file(local_path)
                break

    def closeEvent(self, event):
        LOG.info("Closing %s", APP_NAME)
        self._save_position()
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("volume", self.audio.volume())
        self.settings.setValue("muted", "true" if self.audio.isMuted() else "false")
        self.settings.setValue("view_mode", self.view_mode)
        if getattr(self, "http_server", None):
            self.http_server.shutdown()
            self.http_server.server_close()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("file", nargs="?", help="Video file to open")
    parser.add_argument(
        "--control-host",
        default=None,
        help=f"HTTP control host (default: {DEFAULT_CONTROL_HOST})",
    )
    parser.add_argument(
        "--control-port",
        default=None,
        type=int,
        help=f"HTTP control port (default: {DEFAULT_CONTROL_PORT})",
    )
    parser.add_argument(
        "--no-control-server",
        action="store_true",
        help="Disable the local HTTP control server",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION} ({APP_BUILD})",
    )
    args, qt_args = parser.parse_known_args(sys.argv[1:])

    app = VideoApp([sys.argv[0], *qt_args])
    window = Player(
        control_host=args.control_host,
        control_port=args.control_port,
        control_server_enabled=not args.no_control_server,
    )
    app._window = window

    LOG.info(
        "Starting %s %s (%s) on macOS %s",
        APP_NAME,
        APP_VERSION,
        APP_BUILD,
        APP_MIN_SYSTEM_VERSION,
    )

    if app._pending_file:
        window.open_file(app._pending_file)
    elif args.file:
        window.open_file(args.file)

    window.show()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
