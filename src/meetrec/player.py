"""Video player for a finished recording (PySide6 / Qt Multimedia, Windows / Linux / macOS).

It plays full.mp4: the screen video with three audio tracks (Mix, Mic = Me, System = Remote), so you can
listen to only yourself or only the other side. If full.mp4 is missing or older than its sources, it is
generated first in the background. video.mp4, mic.wav and system.wav are never changed or deleted.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from .fullmedia import build_full_media, is_up_to_date
from .paths import DEFAULT_OUT, list_recordings

TRACK_FALLBACK = ["Mix (semua suara)", "Mic (Me)", "System (Remote)"]
SPEEDS = [("0.5×", 0.5), ("0.75×", 0.75), ("1×", 1.0), ("1.25×", 1.25), ("1.5×", 1.5), ("2×", 2.0)]
STYLE = """
QWidget { background: #14171e; color: #e8ebf2; font-size: 13px; }
QPushButton { background: #2a3040; border: none; border-radius: 8px; padding: 6px 12px; }
QPushButton:hover { background: #384058; }
QPushButton:disabled { background: #1c2029; color: #5d667c; }
QComboBox { background: #2a3040; border: none; border-radius: 8px; padding: 5px 10px; }
QSlider::groove:horizontal { height: 5px; background: #3a4258; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #4a6cf7; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #e8ebf2; }
#status { color: #8f98ad; font-size: 12px; }
"""


def _clock(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class _Builder(QThread):
    """Builds full.mp4 off the UI thread."""

    done = Signal(str, str)  # error text ("" = ok), path

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder

    def run(self):
        try:
            self.done.emit("", str(build_full_media(self.folder)))
        except Exception as e:
            self.done.emit(str(e), "")


class PlayerWindow(QWidget):
    closed = Signal()  # emitted once, when the window is closed (the panel then comes back)

    def __init__(self, root: Path = DEFAULT_OUT, folder: Path | None = None):
        super().__init__()
        self.setWindowTitle("meetrec: putar rekaman")
        self.setStyleSheet(STYLE)
        self.resize(980, 660)
        self.root = Path(root)
        self.folder: Path | None = None
        self._builder: _Builder | None = None
        self._seeking = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setMinimumSize(640, 360)
        self.player.setVideoOutput(self.video)

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.recordings = QComboBox()
        self.recordings.setMinimumWidth(320)
        self.recordings.activated.connect(lambda i: self.open_folder(self.recordings.itemData(i)))
        browse = QPushButton("Buka folder…")
        browse.clicked.connect(self.browse)
        self.gen_btn = QPushButton("Generate full media")
        self.gen_btn.setToolTip("Buat ulang full.mp4 dari video + mic + sistem (file asli tidak dihapus)")
        self.gen_btn.clicked.connect(self.generate)
        top.addWidget(QLabel("Rekaman:"))
        top.addWidget(self.recordings, 1)
        top.addWidget(browse)
        top.addWidget(self.gen_btn)
        v.addLayout(top)
        v.addWidget(self.video, 1)

        ctl = QHBoxLayout()
        self.play_btn = QPushButton("▶  Putar")
        self.play_btn.setFixedWidth(100)
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.slider.sliderReleased.connect(self._released)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setMinimumWidth(110)
        self.tracks = QComboBox()
        self.tracks.setToolTip("Track audio")
        self.tracks.currentIndexChanged.connect(self._track_changed)
        self.speed = QComboBox()
        for label, _ in SPEEDS:
            self.speed.addItem(label)
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(lambda i: self.player.setPlaybackRate(SPEEDS[i][1]))
        ctl.addWidget(self.play_btn)
        ctl.addWidget(self.slider, 1)
        ctl.addWidget(self.time_lbl)
        ctl.addWidget(QLabel("Suara:"))
        ctl.addWidget(self.tracks)
        ctl.addWidget(self.speed)
        v.addLayout(ctl)

        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.player.durationChanged.connect(self._duration)
        self.player.positionChanged.connect(self._position)
        self.player.playbackStateChanged.connect(self._state)
        self.player.tracksChanged.connect(self._fill_tracks)
        self.player.errorOccurred.connect(lambda _e, msg: self.set_status(f"Gagal memutar: {msg}", True))
        QShortcut(Qt.Key.Key_Space, self, activated=self.toggle)
        QShortcut(Qt.Key.Key_Left, self, activated=lambda: self.player.setPosition(max(0, self.player.position() - 5000)))
        QShortcut(Qt.Key.Key_Right, self, activated=lambda: self.player.setPosition(self.player.position() + 5000))

        self.refresh_list()
        target = folder or (self.recordings.itemData(0) if self.recordings.count() else None)
        if target:
            self.open_folder(Path(target))
        else:
            self.set_status("Belum ada rekaman yang selesai. Klik 'Buka folder…' untuk memilih satu.")
            self._enable(False)

    # --- helpers ---------------------------------------------------------------------------------
    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(f'<span style="color:#ff6b6b">{text}</span>' if error else text)

    def _enable(self, controls: bool, generate: bool | None = None) -> None:
        for w in (self.play_btn, self.slider, self.tracks, self.speed):
            w.setEnabled(controls)
        self.gen_btn.setEnabled(controls if generate is None else generate)

    def refresh_list(self) -> None:
        self.recordings.clear()
        for path, label in list_recordings(self.root):
            self.recordings.addItem(label, path)

    # --- open / generate ---------------------------------------------------------------------------
    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pilih folder rekaman", str(self.root))
        if d:
            self.open_folder(Path(d))

    def open_folder(self, folder: Path) -> None:
        self.player.stop()
        self.folder = Path(folder)
        for i in range(self.recordings.count()):
            if self.recordings.itemData(i) == self.folder:
                self.recordings.setCurrentIndex(i)
        if not (self.folder / "video.mp4").exists():
            self.set_status(f"{self.folder.name}: tidak ada video.mp4", True)
            self._enable(False)
            return
        if is_up_to_date(self.folder):
            self._load(self.folder / "full.mp4")
        else:
            self.generate()

    def generate(self) -> None:
        if not self.folder or (self._builder and self._builder.isRunning()):
            return
        self.player.stop()
        self.player.setSource(QUrl())  # release full.mp4 before it is rewritten
        self._enable(False)
        self.set_status("Membuat full media… (file asli tidak diubah)")
        self._builder = _Builder(self.folder)
        self._builder.done.connect(self._built)
        self._builder.start()

    def _built(self, err: str, path: str) -> None:
        if self._builder:
            self._builder.wait(3000)
        if err:
            self._enable(False, True)
            self.set_status(f"Gagal membuat full media: {err}", True)
            return
        self._load(Path(path))

    def _load(self, path: Path) -> None:
        self._enable(True)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.set_status(f"{self.folder.name}: {path.name}")

    # --- transport ---------------------------------------------------------------------------------
    def toggle(self) -> None:
        if self.player.source().isEmpty():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸  Jeda" if playing else "▶  Putar")

    def _duration(self, ms: int) -> None:
        self.slider.setRange(0, ms)
        self.time_lbl.setText(f"{_clock(self.player.position())} / {_clock(ms)}")

    def _position(self, ms: int) -> None:
        if not self._seeking:
            self.slider.setValue(ms)
        self.time_lbl.setText(f"{_clock(ms)} / {_clock(self.player.duration())}")

    def _released(self) -> None:
        self._seeking = False
        self.player.setPosition(self.slider.value())

    # --- audio tracks ------------------------------------------------------------------------------
    def _fill_tracks(self) -> None:
        metas = self.player.audioTracks()
        self.tracks.blockSignals(True)
        self.tracks.clear()
        for i, m in enumerate(metas):
            title = ""
            try:
                title = str(m.stringValue(QMediaMetaData.Key.Title) or "")
            except Exception:
                pass
            if not title and len(metas) == len(TRACK_FALLBACK):
                title = TRACK_FALLBACK[i]  # full.mp4 always stores them in this order
            self.tracks.addItem(title or f"Track {i + 1}")
        active = max(0, self.player.activeAudioTrack())
        self.tracks.setCurrentIndex(min(active, max(0, self.tracks.count() - 1)))
        self.tracks.blockSignals(False)

    def _track_changed(self, i: int) -> None:
        if i >= 0:
            self.player.setActiveAudioTrack(i)

    def closeEvent(self, e) -> None:
        self.player.stop()
        if self._builder:
            self._builder.wait(5000)
        super().closeEvent(e)
        if not getattr(self, "_announced", False):
            self._announced = True
            self.closed.emit()


def run(folder: Path | None = None) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = PlayerWindow(DEFAULT_OUT, folder)
    win.show()
    app.exec()
