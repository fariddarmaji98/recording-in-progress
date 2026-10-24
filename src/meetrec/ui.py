"""Floating recorder panel (PySide6, Windows / Linux / macOS).

idle      : folder · Mulai · Tutup
recording : timer · Gambar · Jeda · Stop         (no Tutup while recording)
drawing   : + pencil/text · colors · thickness · undo · save · discard
            The screen is frozen with a screenshot while drawing (falls back to a transparent overlay
            when the OS refuses the screenshot, e.g. Wayland or missing macOS permission).
"""
from __future__ import annotations

import json
import secrets
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from collections import deque

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QImage, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget)

from .meetbridge import DEFAULT_PORT, MeetBridge
from .paths import DEFAULT_OUT
from .widgets import BUTTON_STYLE, FG, IconButton, make_icon
from .screen import ScreenRecorder, screen_permission
from .settings import AUDIO_RATES, VIDEO_FPS, SettingsDialog  # noqa: F401  (re-exported: ui.SettingsDialog)
from .upload import UploadManager
from .windows import WindowInfo, list_windows
from .winutil import exclude_from_capture, set_close_enabled

COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff", "#111111"]
_W = Qt.WindowType
_A = Qt.WidgetAttribute
# The main panel is a normal window with the OS title bar but ONLY close + minimize (no maximize), kept on top
# while you work in another app. The OS draws those controls; there is no custom close button.
PANEL_FLAGS = (_W.Window | _W.CustomizeWindowHint | _W.WindowTitleHint | _W.WindowCloseButtonHint
               | _W.WindowMinimizeButtonHint | _W.WindowStaysOnTopHint)


def _screen_capture_allowed() -> bool:
    if sys.platform == "darwin":
        try:
            from Quartz import CGPreflightScreenCaptureAccess
            return bool(CGPreflightScreenCaptureAccess())
        except Exception:
            return True
    return True


# --- drawing overlay -----------------------------------------------------------------------------
class _TextEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(e)


class DrawOverlay(QWidget):
    """Full-screen canvas over a frozen screenshot (or transparent when no screenshot)."""

    escape = Signal()  # Esc pressed: leave drawing mode

    def __init__(self, screen, shot: QPixmap | None):
        super().__init__(None, _W.Tool | _W.FramelessWindowHint | _W.WindowStaysOnTopHint)
        self.shot = shot
        self._screen = screen
        self.setAttribute(_A.WA_TranslucentBackground, shot is None)
        self.setAttribute(_A.WA_MacAlwaysShowToolWindow, True)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.items: list[dict] = []
        self.tool, self.color, self.width_px = "pencil", QColor(COLORS[0]), 4
        self.version = 0  # bumped on every change, so the panel knows when to re-render the video frame
        self._cur: dict | None = None
        self._edit: _TextEdit | None = None
        self._edit_pos = QPointF()

    # painting
    def _paint_items(self, p: QPainter) -> None:
        for it in self.items:
            if it["k"] == "stroke":
                pen = QPen(it["color"], it["w"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                pts = it["pts"]
                if len(pts) == 1:
                    p.drawPoint(pts[0])
                else:
                    path = QPainterPath(pts[0])
                    for pt in pts[1:]:
                        path.lineTo(pt)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
            else:
                f = QFont()
                f.setPixelSize(it["size"])
                f.setBold(True)
                p.setFont(f)
                p.setPen(it["color"])
                p.drawText(it["pos"], it["text"])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.shot is not None:
            p.drawPixmap(self.rect(), self.shot)
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, 2))  # near-invisible but still catches the mouse
        self._paint_items(p)

    def render_image(self, dpr: float | None = None) -> QImage:
        if dpr is None:
            dpr = self.shot.devicePixelRatio() if self.shot is not None else self._screen.devicePixelRatio()
        img = QImage(int(self.width() * dpr), int(self.height() * dpr), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(dpr, dpr)
        if self.shot is not None:
            p.drawPixmap(QRectF(0, 0, self.width(), self.height()), self.shot, QRectF(self.shot.rect()))
        self._paint_items(p)
        p.end()
        return img

    # input
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        if self._edit:
            self._commit_text()
        if self.tool == "pencil":
            self._cur = {"k": "stroke", "color": QColor(self.color), "w": self.width_px, "pts": [pos]}
            self.items.append(self._cur)
            self.version += 1
        else:
            self._start_text(pos)
        self.update()

    def mouseMoveEvent(self, e):
        if self._cur is not None:
            self._cur["pts"].append(e.position())
            self.version += 1
            self.update()

    def mouseReleaseEvent(self, _):
        self._cur = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            if self._edit:
                self._cancel_text()
            else:
                self.escape.emit()
            return
        if e.key() == Qt.Key.Key_Z and e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.undo()

    def _text_size(self) -> int:
        return 14 + self.width_px * 2

    def _start_text(self, pos: QPointF) -> None:
        ed = _TextEdit(self)
        size = self._text_size()
        ed.setStyleSheet(f"background: rgba(0,0,0,110); color: {self.color.name()}; border: 1px dashed {self.color.name()};"
                         f"font-size: {size}px; font-weight: bold; padding: 0 4px;")
        ed.move(int(pos.x()), int(pos.y()))
        ed.resize(280, size + 12)
        ed.returnPressed.connect(self._commit_text)
        ed.cancelled.connect(self._cancel_text)
        ed.show()
        ed.setFocus()
        self._edit, self._edit_pos = ed, pos

    def _commit_text(self) -> None:
        ed, self._edit = self._edit, None
        if ed is None:
            return
        text = ed.text().strip()
        ed.deleteLater()
        if text:
            size = self._text_size()
            self.items.append({"k": "text", "color": QColor(self.color), "size": size, "text": text,
                               "pos": QPointF(self._edit_pos.x() + 4, self._edit_pos.y() + size)})
            self.version += 1
        self.update()

    def _cancel_text(self) -> None:
        ed, self._edit = self._edit, None
        if ed:
            ed.deleteLater()

    def undo(self) -> None:
        if self._edit:
            self._cancel_text()
        elif self.items:
            self.items.pop()
            self.version += 1
        self.update()

    def finish_text(self) -> None:
        if self._edit:
            self._commit_text()


class PathLabel(QLabel):
    """The full storage path (wraps after "/" when long). Clicking it opens the folder chooser."""

    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Klik untuk ganti folder penyimpanan")

    def setPath(self, path: str) -> None:
        self.setText(path.replace("/", "/\u200b").replace("\\", "\\\u200b"))  # zero-width break points

    def path(self) -> str:
        return self.text().replace("\u200b", "")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        else:
            super().mousePressEvent(e)


class LevelLine(QWidget):
    """Thin live waveform: green = microphone, blue = system sound. Flat when nothing is recorded."""

    N = 150

    def __init__(self):
        super().__init__()
        self.setFixedHeight(26)
        self.setToolTip("Level suara langsung: hijau = mikrofon, biru = suara sistem")
        self.mic = deque([0.0] * self.N, maxlen=self.N)
        self.sys = deque([0.0] * self.N, maxlen=self.N)

    def push(self, mic: float, system: float) -> None:
        self.mic.append(mic)
        self.sys.append(system)
        self.update()

    def reset(self) -> None:
        self.mic.extend([0.0] * self.N)
        self.sys.extend([0.0] * self.N)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2
        p.setPen(QPen(QColor("#3a4258"), 1.5))
        p.drawLine(0, int(mid), w, int(mid))
        step = w / self.N
        for series, color in ((self.sys, "#0a84ff"), (self.mic, "#34c759")):
            p.setPen(QPen(QColor(color), max(1.5, step * 0.6), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i, v in enumerate(series):
                if v <= 0.002:
                    continue
                a = min(1.0, (v ** 0.5) * 1.6) * (mid - 2)  # sqrt: quiet speech is still visible
                x = int(i * step + step / 2)
                p.drawLine(x, int(mid - a), x, int(mid + a))


class _Starter(QThread):
    """Opens the devices off the UI thread (CoreAudio can take seconds), then reports ok or an error."""

    done = Signal(str)  # "" = ok, else the error text

    def __init__(self, session, window: WindowInfo | None = None, video: bool = True, fps: int = 8):
        super().__init__()
        self.session = session
        self.window = window
        self.want_video = video
        self.fps = fps
        self.video: ScreenRecorder | None = None
        self.video_note = ""

    def run(self):
        t0 = time.monotonic()
        self.session.t0 = t0  # one clock origin for audio and video
        # Video is best effort: without it the audio recording still goes on. It starts at the same
        # time as the audio devices (not after them), so both are ready together.
        v = None
        self.session.dir.mkdir(parents=True, exist_ok=True)  # the video thread writes here before audio starts
        try:
            if not self.want_video:
                pass  # turned off in Pengaturan: audio only
            elif not screen_permission(request=True):
                self.video_note = ("Izin Screen Recording belum diberikan, jadi layar tidak direkam (audio tetap jalan). "
                                   "Beri izin lalu buka ulang aplikasi.")
            else:
                v = ScreenRecorder(self.session.dir / "video.mp4", t0, fps=self.fps, window=self.window)
                v.start()
        except Exception as e:
            self.video_note = f"Video gagal: {e}"
            v = None
        try:
            self.session.start()
        except Exception as e:
            if v:
                v.stop()
            self.done.emit(str(e))
            return
        if v:
            v.ready.wait(20)
            if v.error or not v.ready.is_set():
                self.video_note = f"Video gagal: {v.error or 'encoder tidak siap'}"
                v.stop()
            else:
                self.video = v
        self.done.emit("")


# --- panel ---------------------------------------------------------------------------------------
STYLE = f"""
#panel {{ background: #1c2029; }}
#card {{ background: transparent; border: none; }}
QLabel {{ color: {FG}; font-size: 13px; }}
#path {{ color: #aab2c5; }}
#status {{ color: #8f98ad; font-size: 11px; }}
#timer {{ font-size: 18px; font-weight: 600; }}
QPushButton {{ background: #2a3040; color: {FG}; border: none; border-radius: 8px; padding: 6px 10px; font-size: 13px; }}
QPushButton:hover {{ background: #384058; }}
QPushButton:disabled {{ background: #232836; color: #5d667c; }}
QPushButton:checked {{ background: #4a6cf7; }}
#section {{ border: 1px solid #2c3242; border-radius: 10px; background: transparent; }}
QCheckBox {{ color: {FG}; }}
QComboBox {{ background: #2a3040; border: none; border-radius: 8px; padding: 5px 10px; color: {FG}; }}
#startwide {{ background: #2ea043; font-weight: 600; padding: 7px 14px; }}
#startwide:hover {{ background: #3bb653; }}
QListWidget {{ background: #232836; border: none; border-radius: 8px; color: {FG}; padding: 4px; }}
QListWidget::item {{ padding: 5px 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: #4a6cf7; }}
QSlider::groove:horizontal {{ height: 4px; background: #3a4258; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {FG}; }}
"""


class Panel(QWidget):
    def __init__(self):
        super().__init__(None, PANEL_FLAGS)
        self.setObjectName("panel")
        self.setWindowTitle("meetrec")
        self.setStyleSheet(STYLE + BUTTON_STYLE)
        self.settings = QSettings("meetrec", "meetrec")
        self.uploads = UploadManager(self.settings)  # uploads outlive the windows that start them
        self.out_dir = Path(self.settings.value("out_dir", str(DEFAULT_OUT)))
        self.state = "idle"  # idle | starting | recording | paused
        self._choosing = False  # the 'what to record' picker is open
        self._target: WindowInfo | None = None
        self._player = None  # the recordings list / player window while it is open
        self.session = None
        self.overlay: DrawOverlay | None = None
        self._placed = False
        self._top_left = self.pos()
        self._shown_errors: list[str] = []
        token = self.settings.value("bridge_token", "", type=str)
        if not token:
            token = secrets.token_urlsafe(12)
            self.settings.setValue("bridge_token", token)
        self.bridge = MeetBridge(token, self.settings.value("bridge_port", DEFAULT_PORT, type=int))
        self.video: ScreenRecorder | None = None
        self._ov_timer: QTimer | None = None
        self._ov_version = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="card")
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # first screen: two sections, stacked. Top: where recordings are stored (full path). Bottom: actions.
        self.idle_row = QWidget()
        self.folder_row = self.idle_row  # older name for the same container
        iv = QVBoxLayout(self.idle_row)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(8)

        self.storage_section = QFrame(objectName="section")
        h = QHBoxLayout(self.storage_section)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(8)
        self.folder_btn = IconButton("folder", "Pilih folder penyimpanan")
        self.folder_btn.clicked.connect(self.choose_folder)
        self.path_lbl = PathLabel()
        self.path_lbl.clicked.connect(self.choose_folder)
        h.addWidget(self.folder_btn)
        h.addWidget(self.path_lbl, 1)
        iv.addWidget(self.storage_section)

        self.actions_section = QFrame(objectName="section")
        h = QHBoxLayout(self.actions_section)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(8)
        self.start_btn = IconButton("play", "Mulai rekam", tone="primary")
        self.start_btn.clicked.connect(self.show_picker)
        self.play_rec_btn = IconButton("list", "Daftar rekaman / putar")
        self.play_rec_btn.clicked.connect(self.open_player)
        self.settings_btn = IconButton("gear", "Pengaturan")
        self.settings_btn.clicked.connect(self.open_settings)
        h.addStretch(1)
        for btn in (self.start_btn, self.play_rec_btn, self.settings_btn):
            h.addWidget(btn)
        iv.addWidget(self.actions_section)
        v.addWidget(self.idle_row)

        # after play: what to record, the whole screen or one application
        self.pick_row = QWidget()
        pv = QVBoxLayout(self.pick_row)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(8)
        # No button here: the window's own close (top corner) goes BACK to the first screen.
        pv.addWidget(QLabel("Rekam apa?"))
        choices = QHBoxLayout()
        self.screen_btn = self._btn("screen", " Layar penuh", tip="Rekam seluruh layar utama")
        self.screen_btn.clicked.connect(lambda: self._begin(None))
        self.app_btn = self._btn("window", " Aplikasi", tip="Rekam satu aplikasi / jendela, mis. Google Meet atau Zoom")
        self.app_btn.clicked.connect(self.choose_app)
        choices.addWidget(self.screen_btn, 1)
        choices.addWidget(self.app_btn, 1)
        pv.addLayout(choices)
        self.win_box = QWidget()
        wv = QVBoxLayout(self.win_box)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(6)
        self.win_list = QListWidget()
        self.win_list.setFixedHeight(150)
        self.win_list.itemSelectionChanged.connect(lambda: self.win_start.setEnabled(bool(self.win_list.selectedItems())))
        self.win_list.itemDoubleClicked.connect(lambda _i: self._begin_selected())
        wv.addWidget(self.win_list)
        self.win_note = QLabel("", objectName="status")
        self.win_note.setWordWrap(True)
        wv.addWidget(self.win_note)
        wrow = QHBoxLayout()
        self.win_refresh = QPushButton("Segarkan")
        self.win_refresh.clicked.connect(self._fill_windows)
        self.win_start = QPushButton(" Mulai rekam aplikasi ini", objectName="startwide")
        self.win_start.setIcon(make_icon("play"))
        self.win_start.setIconSize(QSize(16, 16))
        self.win_start.setEnabled(False)
        self.win_start.clicked.connect(self._begin_selected)
        wrow.addWidget(self.win_refresh)
        wrow.addWidget(self.win_start, 1)
        wv.addLayout(wrow)
        pv.addWidget(self.win_box)
        v.addWidget(self.pick_row)

        # recording row
        self.rec_row = QWidget()
        h = QHBoxLayout(self.rec_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.timer_lbl = QLabel("00:00", objectName="timer")
        self.draw_btn = self._btn("pencil", " Gambar")
        self.draw_btn.clicked.connect(self.enter_draw)
        self.pause_btn = IconButton("pause", "Jeda")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = IconButton("stop", "Stop", tone="danger")
        self.stop_btn.clicked.connect(self.stop)
        h.addWidget(self.timer_lbl, 1)
        h.addWidget(self.draw_btn)
        h.addWidget(self.pause_btn)
        h.addWidget(self.stop_btn)
        v.addWidget(self.rec_row)

        # draw toolbar
        self.draw_row = QWidget()
        dv = QVBoxLayout(self.draw_row)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(6)
        a = QHBoxLayout()
        self.pencil_btn = IconButton("pencil", "Pensil", checkable=True)
        self.text_btn = IconButton("text", "Teks", checkable=True)
        self.pencil_btn.setChecked(True)
        self.pencil_btn.clicked.connect(lambda: self.set_tool("pencil"))
        self.text_btn.clicked.connect(lambda: self.set_tool("text"))
        a.addWidget(self.pencil_btn)
        a.addWidget(self.text_btn)
        a.addSpacing(6)
        self.swatches: list[QPushButton] = []
        for col in COLORS:
            s = QPushButton()
            s.setFixedSize(22, 22)
            s.setCheckable(True)
            s.setStyleSheet(f"QPushButton {{ background: {col}; border-radius: 11px; border: 2px solid #1c2029; padding: 0; }}"
                            f"QPushButton:checked {{ border: 2px solid #ffffff; }}")
            s.clicked.connect(lambda _=False, c=col: self.set_color(c))
            self.swatches.append(s)
            a.addWidget(s)
        self.swatches[0].setChecked(True)
        a.addStretch(1)
        dv.addLayout(a)
        b = QHBoxLayout()
        self.size_lbl = QLabel("Tebal")
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(1, 24)
        self.size_slider.setValue(4)
        self.size_slider.valueChanged.connect(self.set_width)
        self.undo_btn = IconButton("undo", "Urungkan")
        self.undo_btn.clicked.connect(lambda: self.overlay and self.overlay.undo())
        self.save_btn = IconButton("save", "Simpan gambar")
        self.save_btn.clicked.connect(self.save_drawing)
        self.trash_btn = IconButton("trash", "Hapus gambar")
        self.trash_btn.clicked.connect(self.discard_drawing)
        b.addWidget(self.size_lbl)
        b.addWidget(self.size_slider, 1)
        b.addWidget(self.undo_btn)
        b.addWidget(self.save_btn)
        b.addWidget(self.trash_btn)
        dv.addLayout(b)
        v.addWidget(self.draw_row)

        self.level = LevelLine()
        v.addWidget(self.level)

        self.meet_lbl = QLabel("", objectName="status")
        self.meet_lbl.setWordWrap(True)
        self.meet_lbl.hide()
        v.addWidget(self.meet_lbl)

        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl.fromLocalFile(url)))
        self.status.hide()  # shown only when there is something to say, so no blank strip under the buttons
        v.addWidget(self.status)

        for w in self.findChildren(QPushButton) + self.findChildren(QSlider):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep keyboard focus on the canvas (Esc, Ctrl+Z)
        self._drag_off = None

        self.tick = QTimer(self)
        self.tick.setInterval(250)
        self.tick.timeout.connect(self.update_timer)
        self.level_timer = QTimer(self)
        self.level_timer.setInterval(80)
        self.level_timer.timeout.connect(self._push_levels)
        self.setFixedWidth(370)
        self.refresh()
        if self.settings.value("bridge_enabled", True, type=bool):
            self.bridge.start()

    # --- helpers ---------------------------------------------------------------------------------
    def _btn(self, icon: str, text: str = "", tip: str = "", checkable: bool = False, name: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setIcon(make_icon(icon))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFixedHeight(IconButton.SIZE)  # same height as the square buttons beside it
        if name:
            b.setObjectName(name)
        return b

    def _fit(self) -> None:
        """Height follows the content in BOTH directions (a top-level window only grows by itself, which left it
        tall after the picker or the application list was closed). Width stays fixed."""
        right = self.x() + self.width() if self._placed else None
        lay = self.layout()
        lay.invalidate()
        lay.activate()  # visibility changes are applied lazily; measure the layout as it is NOW
        h = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else self.sizeHint().height()
        self.setFixedHeight(max(h, self.minimumSizeHint().height()))  # fixed = not draggable to another size
        if right is not None:
            self.move(right - self.width(), self.y())
        if not getattr(self, "_refit_pending", False):  # once more when the event loop has settled the layout
            self._refit_pending = True
            QTimer.singleShot(0, self._refit)

    def _refit(self) -> None:
        self._refit_pending = False
        if self.overlay is None or self.isWindow():  # not while the panel is a child of the drawing overlay
            self._fit()
            self._sync_close()

    def _sync_close(self) -> None:
        """Native close button greyed out while recording. Applied AFTER resizing: changing the size hints makes
        the OS rebuild the window buttons and would undo it."""
        if self.isWindow():
            set_close_enabled(self, self.state == "idle")

    def place_center(self) -> None:
        """Exact middle of the whole screen the panel is on (used when the app starts)."""
        g = (self.screen() or QGuiApplication.primaryScreen()).geometry()
        self.adjustSize()
        fg = self.frameGeometry()  # includes the title bar
        self.move(g.center().x() - fg.width() // 2, g.center().y() - fg.height() // 2)
        self._placed = True

    def show_initial(self) -> None:
        """First appearance at launch: shown in the center of the screen."""
        self.show()
        self.place_center()

    def place_top_right(self) -> None:
        g = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        self.adjustSize()
        self.move(g.right() - self.width() - 12, g.top() + 12)
        self._placed = True

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(f'<span style="color:#ff6b6b">{text}</span>' if error else text)
        self.status.setVisible(bool(text))
        self._fit()

    def refresh(self) -> None:
        idle = self.state in ("idle", "starting")
        drawing = self.overlay is not None
        choosing = self._choosing and self.state == "idle"
        want = [(self.idle_row, idle and not choosing), (self.pick_row, choosing), (self.rec_row, not idle),
                (self.draw_row, drawing), (self.level, self.state in ("recording", "paused"))]
        # Hide first, then show. Showing a section while the old one is still visible makes the window grow to fit
        # both, and a top-level window never shrinks by itself afterwards (it stayed tall after closing the picker).
        for w, visible in want:
            if not visible:
                w.setVisible(False)
        for w, visible in want:
            if visible:
                w.setVisible(True)
        self.draw_btn.setEnabled(not drawing and not idle)
        self.path_lbl.setPath(str(self.out_dir))
        self.pause_btn.setIcon(make_icon("play" if self.state == "paused" else "pause"))
        self.pause_btn.setToolTip("Lanjut" if self.state == "paused" else "Jeda")
        self._fit()
        self._sync_close()

    def _elide(self, s: str, n: int = 34) -> str:
        return s if len(s) <= n else "…" + s[-(n - 1):]

    @staticmethod
    def _clip(s: str, n: int) -> str:
        """Keep the beginning (the application name), cut the tail of a long window title."""
        return s if len(s) <= n else s[:n - 1] + "…"

    def _push_levels(self) -> None:
        if not self.session or self.state != "recording":
            self.level.push(0.0, 0.0)
            return
        try:
            self.level.push(*self.session.levels())
        except Exception:
            self.level.push(0.0, 0.0)

    def update_timer(self) -> None:
        if not self.session:
            return
        errs = self.session.errors() if hasattr(self.session, 'errors') else []
        if errs and errs != self._shown_errors:
            self._shown_errors = errs
            self.set_status('Masalah rekam: ' + '; '.join(errs), error=True)
        mm, ss = divmod(int(self.session.active_seconds()), 60)
        self.timer_lbl.setText(("⏸ " if self.state == "paused" else "● ") + f"{mm:02d}:{ss:02d}")
        names = self.bridge.names() if self.bridge.recording else []
        self.meet_lbl.setText("Ekstensi Meet terhubung: " + ", ".join(names) if names else "")
        if self.meet_lbl.isVisibleTo(self) != bool(names):
            self.meet_lbl.setVisible(bool(names))
            self._fit()  # height follows the content (it is locked, so it must be refitted when a line appears)

    # --- actions ---------------------------------------------------------------------------------
    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pilih folder penyimpanan", str(self.out_dir))
        if d:
            self.out_dir = Path(d)
            self.settings.setValue("out_dir", d)
            self.refresh()

    # --- what to record ------------------------------------------------------------------------------
    def show_picker(self) -> None:
        if self.state != "idle":
            return
        self._choosing = True
        self.win_box.hide()
        self.set_status("")
        self.refresh()

    def cancel_pick(self) -> None:
        self._choosing = False
        self.refresh()

    def choose_app(self) -> None:
        self.win_box.show()
        self._fill_windows()
        self._fit()

    def _fill_windows(self) -> None:
        self.win_list.clear()
        wins = list_windows()
        meeting = ("meet", "zoom", "teams", "webex", "slack", "discord")
        wins.sort(key=lambda w: (not any(k in f"{w.app} {w.title}".lower() for k in meeting), w.app.lower()))
        for w in wins:
            item = QListWidgetItem(self._clip(w.label, 46))
            item.setData(Qt.ItemDataRole.UserRole, w)
            item.setToolTip(w.label)
            self.win_list.addItem(item)
        self.win_start.setEnabled(False)
        self.win_note.setText("" if wins else "Tidak ada jendela yang bisa dipilih. Di Linux Wayland daftar jendela tidak tersedia; pakai Layar penuh.")
        self.win_note.setVisible(not wins)
        self._fit()

    def _begin_selected(self) -> None:
        items = self.win_list.selectedItems()
        if items:
            self._begin(items[0].data(Qt.ItemDataRole.UserRole))

    def _begin(self, window: WindowInfo | None) -> None:
        self._choosing = False
        self.start(window)

    def start(self, window: WindowInfo | None = None) -> None:
        from .record import Session

        if self.state != "idle":
            return
        self._choosing = False
        self._target = window
        self.session = Session(self.out_dir, rate=self.settings.value("audio_rate", 16000, type=int))
        self.state = "starting"
        self.start_btn.setEnabled(False)
        self.start_btn.setToolTip("Menyiapkan…")
        self.refresh()
        self.set_status("Membuka mikrofon, audio sistem, dan layar…")
        self._starter = _Starter(self.session, window, video=self.settings.value("record_video", True, type=bool),
                                 fps=self.settings.value("video_fps", 8, type=int))
        self._starter.done.connect(self._started)
        self._starter.start()

    def _started(self, err: str) -> None:
        self._starter.wait(3000)  # run() has already returned; let the thread finish before it is dropped
        self.start_btn.setEnabled(True)
        self.start_btn.setToolTip("Mulai rekam")
        if err:
            self.session = None
            self.state = "idle"
            self.refresh()
            self.set_status(f"Gagal mulai: {err}", error=True)
            return
        self.state = "recording"
        self._shown_errors = []
        self.video = self._starter.video
        note = self._starter.video_note
        where = f"Merekam jendela: {self._target.app}" if self._target and self.video else ""
        self.set_status(f"<span style='color:#ffb86b'>{note}</span>" if note else where)
        self.update_timer()
        self.tick.start()
        self.level.reset()
        self.level_timer.start()
        self.bridge.begin(self.session.t0_wall)
        self.refresh()

    def toggle_pause(self) -> None:
        if not self.session:
            return
        if self.state == "paused":
            self.session.resume()
            if self.video:
                self.video.resume()
            self.state = "recording"
        else:
            self.session.pause()
            if self.video:
                self.video.pause()
            self.state = "paused"
        self.update_timer()
        self.refresh()

    def stop(self) -> None:
        if self.overlay:
            self._close_overlay()
        folder = None
        warn = ""
        if self.session:
            self.tick.stop()
            self.level_timer.stop()
            self.level.reset()
            self.meet_lbl.hide()
            meet = self.bridge.end() if self.bridge.recording else None
            try:
                if meet and meet["events"]:
                    (self.session.dir / "meet_events.json").write_text(json.dumps(meet, ensure_ascii=False, indent=1), encoding="utf-8")
                    self.session.extra_meta["meet"] = {"meeting": meet["meeting"], "events": len(meet["events"]),
                                                       "names": sorted({i["name"] for i in meet["intervals"] if not i["self"]})}
                if self.video:
                    self.video.stop()
                    self.session.extra_meta["video"] = self.video.info()
                    if self.video.error:
                        warn += f"<br><span style='color:#ffb86b'>Video bermasalah: {self.video.error}</span>"
                    self.video = None
                folder = self.session.stop()
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                silent = [n for n, t in meta["tracks"].items() if t["silent"]]
                if silent:
                    warn += f"<br><span style='color:#ffb86b'>Track diam: {', '.join(silent)}. Cek izin mikrofon / audio sistem.</span>"
            except Exception as e:
                self.set_status(f"Gagal menyimpan: {e}", error=True)
        self.session = None
        self.state = "idle"
        self.timer_lbl.setText("00:00")
        self.refresh()
        if folder:
            self.set_status(f'Tersimpan: <a href="{folder}" style="color:#7fb0ff">{self._elide(str(folder), 40)}</a>{warn}')

    # --- drawing ---------------------------------------------------------------------------------
    def enter_draw(self) -> None:
        if self.overlay or not self.session:
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        self._top_left = self.pos()  # global position of the panel, restored when drawing ends
        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(220, lambda: self._open_overlay(screen))

    def _open_overlay(self, screen) -> None:
        shot = None
        note = ""
        if _screen_capture_allowed():
            pm = screen.grabWindow(0)
            if not pm.isNull():
                shot = pm
        if shot is None:
            note = "Layar tidak bisa dibekukan di sistem ini; mencoret langsung di atas layar."
        self.overlay = DrawOverlay(screen, shot)
        self.overlay.tool = "text" if self.text_btn.isChecked() else "pencil"
        self.overlay.width_px = self.size_slider.value()
        for s, col in zip(self.swatches, COLORS):
            if s.isChecked():
                self.overlay.color = QColor(col)
        ov = self.overlay
        ov.escape.connect(self.discard_drawing)
        if shot is not None and self.video:
            # Frozen screen: encode overlay renders (screenshot + strokes) so the panel is not in the video.
            self._ov_version = -1
            self._ov_timer = QTimer(self)
            self._ov_timer.setInterval(150)
            self._ov_timer.timeout.connect(self._push_overlay)
            self._ov_timer.start()
            QTimer.singleShot(0, self._push_overlay)
        # The panel lives INSIDE the overlay while drawing. As a separate window it got covered the
        # moment the overlay was clicked (the OS raises the clicked window).
        local = self._top_left - screen.geometry().topLeft()
        self.setParent(ov)
        self.move(local)
        ov.show()
        self.show()
        self.raise_()
        ov.activateWindow()
        ov.setFocus()
        self.set_status(note)
        self.refresh()

    def set_tool(self, tool: str) -> None:
        self.pencil_btn.setChecked(tool == "pencil")
        self.text_btn.setChecked(tool == "text")
        if self.overlay:
            self.overlay.finish_text()
            self.overlay.tool = tool

    def set_color(self, col: str) -> None:
        for s, c in zip(self.swatches, COLORS):
            s.setChecked(c == col)
        if self.overlay:
            self.overlay.color = QColor(col)

    def set_width(self, w: int) -> None:
        if self.overlay:
            self.overlay.width_px = w

    def save_drawing(self) -> None:
        if not (self.overlay and self.session):
            return
        self.overlay.finish_text()
        img = self.overlay.render_image()
        d = self.session.dir / "drawings"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"drawing_{datetime.now():%H-%M-%S}.png"
        ok = img.save(str(path))
        self._close_overlay()
        self.set_status(f"Gambar disimpan: drawings/{path.name}" if ok else "Gagal menyimpan gambar", error=not ok)

    def discard_drawing(self) -> None:
        had = bool(self.overlay and self.overlay.items)
        self._close_overlay()
        self.set_status("Gambar dihapus" if had else "Mode gambar ditutup")

    def _push_overlay(self) -> None:
        ov = self.overlay
        if not ov or not self.video or ov.shot is None or ov.version == self._ov_version:
            return
        img = ov.render_image(1.0)
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, :img.width()].copy()
        self.video.set_override(arr)
        self._ov_version = ov.version

    def _close_overlay(self) -> None:
        ov, self.overlay = self.overlay, None
        if self._ov_timer:
            self._ov_timer.stop()
            self._ov_timer = None
        if self.video:
            self.video.set_override(None)
        if ov:
            gpos = ov.mapToGlobal(self.pos()) if self.parent() is ov else self._top_left
            self.hide()
            # take the panel out of the overlay BEFORE closing it, or it is deleted with its parent
            self.setParent(None, PANEL_FLAGS)
            self.move(gpos)
            ov.close()
            ov.deleteLater()
        self.refresh()
        self.show()
        self.raise_()
        exclude_from_capture(self)  # the native window was recreated

    def open_settings(self) -> None:
        if self.state != "idle":
            return
        dlg = SettingsDialog(self.settings, self, token=self.bridge.token, port=self.bridge.port, uploads=self.uploads)
        if dlg.exec():
            dlg.store(self.settings)
            if self.settings.value("bridge_enabled", True, type=bool):
                self.bridge.start()
            else:
                self.bridge.stop()
            self.set_status("Pengaturan disimpan; berlaku di rekaman berikutnya.")

    def open_player(self) -> None:
        from .player import RecordingsWindow

        if self._player is not None and self._player.isVisible():
            self._player.raise_()
            self._player.activateWindow()
            return
        self._player = RecordingsWindow(self.out_dir, uploads=self.uploads)
        self._player.closed.connect(self._player_closed)
        # The player is a sub page. This panel is always on top, so it would cover the player:
        # hide it while the player is open and bring it back when the player closes.
        self.hide()
        self._player.show()
        self._player.raise_()
        self._player.activateWindow()

    def _player_closed(self) -> None:
        self._player = None
        self.show()
        self.raise_()
        exclude_from_capture(self)

    def shutdown(self) -> None:
        """Stop and save whatever is going on, also when quit/kill arrives while the devices are still opening."""
        st = getattr(self, "_starter", None)
        if st is not None:
            st.wait(25000)  # never drop a QThread that is still running
            if self.state == "starting":  # its done-signal has not been delivered yet: adopt the result
                self.video = st.video
                self.state = "recording"
        if self.overlay:
            self._close_overlay()
        if self.state != "idle":
            self.stop()
        self.uploads.cancel_everything()
        self.bridge.stop()

    # --- window ----------------------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.isWindow():
            if self.windowHandle():
                self.windowHandle().startSystemMove()
        else:
            self._drag_off = e.position()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and not self.isWindow() and e.buttons() & Qt.MouseButton.LeftButton:
            top_left = (self.mapToParent(e.position()) - self._drag_off).toPoint()
            par = self.parentWidget()
            x = max(0, min(top_left.x(), par.width() - self.width()))
            y = max(0, min(top_left.y(), par.height() - self.height()))
            self.move(x, y)

    def mouseReleaseEvent(self, _):
        self._drag_off = None

    def confirm_quit_with_uploads(self, n: int) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, "Keluar?", f"Sedang mengunggah/mengunduh {n} rekaman. Kalau aplikasi ditutup, prosesnya dibatalkan dan "
                          "progress-nya hilang.", QMessageBox.StandardButton.NoButton, self)
        yes = box.addButton("Tutup aplikasi", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Lanjutkan", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def closeEvent(self, e):
        if self.state != "idle":  # recording, paused or still starting
            e.ignore()  # no closing while recording
            self.set_status("Hentikan rekaman dulu (tombol Stop) sebelum menutup aplikasi.", error=True)
            return
        if self._choosing:  # a sub page: close = back to the page before it, never quit
            e.ignore()
            self.cancel_pick()
            return
        active = self.uploads.busy()
        if active and not self.confirm_quit_with_uploads(len(active)):
            e.ignore()
            return
        super().closeEvent(e)
        QApplication.quit()  # only on the main page does close end the app


def _prewarm() -> None:
    """Load the capture libraries and query the devices before the first click on Mulai."""
    try:
        import warnings

        import av  # noqa: F401
        import mss  # noqa: F401
        import soundcard as sc
        import soxr  # noqa: F401

        from .screen import pick_encoder
        pick_encoder()
        if sys.platform == "darwin":
            import catap  # noqa: F401
        from .windows import list_windows

        list_windows()  # loads Quartz now, so the application picker opens instantly
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sc.default_microphone()
            sc.default_speaker()
    except Exception:
        pass


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("meetrec")
    app.setQuitOnLastWindowClosed(False)
    threading.Thread(target=_prewarm, daemon=True).start()
    panel = Panel()
    panel.show_initial()
    exclude_from_capture(panel)
    def finish_recording():
        panel.shutdown()

    app.aboutToQuit.connect(finish_recording)

    # Ctrl-C / kill: stop and save the recording instead of dying with half-written files.
    def graceful(*_):
        panel.shutdown()
        app.quit()

    signal.signal(signal.SIGINT, graceful)
    signal.signal(signal.SIGTERM, graceful)
    keepalive = QTimer()  # lets the Python-level signal handlers run while Qt owns the loop
    keepalive.timeout.connect(lambda: None)
    keepalive.start(200)
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
