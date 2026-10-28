"""Video player for a finished recording (PySide6 / Qt Multimedia, Windows / Linux / macOS).

It plays full.mp4: the screen video with three audio tracks (Mix, Mic = Me, System = Remote), so you can
listen to only yourself or only the other side. If full.mp4 is missing or older than its sources, it is
generated first in the background. video.mp4, mic.wav and system.wav are never changed or deleted.
"""
from __future__ import annotations

import sys
from pathlib import Path

import json

from PySide6.QtCore import QFile, QPointF, QRectF, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF, QShortcut)
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSlider, QStackedWidget, QTabBar, QVBoxLayout, QWidget)

from .job import Job
from . import cloud
from .fullmedia import build_full_media, is_up_to_date, timeline_start
from .paths import DEFAULT_OUT, list_recordings
from .merge import fmt_time
from .speakerdata import display_name, load_speakers, load_transcript, set_label
from .upload import ACTIVE, Upload, UploadManager, list_cloud_recordings
from .widgets import BUTTON_STYLE, FG, IconButton, ProgressIconButton, make_icon

# Other pages: the OS's own close + minimize + maximize (no custom buttons).
FULL_WINDOW_FLAGS = (Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
                     | Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMinimizeButtonHint
                     | Qt.WindowType.WindowMaximizeButtonHint)

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
#whoTitle { font-weight: 600; }
#now { color: #cfd5e3; }
QListWidget { background: #1c2029; border: 1px solid #2c3242; border-radius: 8px; padding: 4px; outline: none; }
QListWidget::item { padding: 3px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #2a3350; }
""" + BUTTON_STYLE


def _clock(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class _Builder(QThread):
    """Builds full.mp4 off the UI thread."""

    done = Signal(str, str)  # error text ("" = ok), path
    progress = Signal(int)   # persentase, supaya layar tidak tampak macet

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder

    def run(self):
        try:
            self.done.emit("", str(build_full_media(self.folder, on_progress=self._pct)))
        except Exception as e:
            self.done.emit(str(e), "")

    def _pct(self, frac: float) -> None:
        self.progress.emit(int(frac * 100))


# --- who speaks when -------------------------------------------------------------------------------
ME_COLOR = "#38c172"
OVERLAP_COLOR = "#ff8a3d"
# none of these is the green of "Me" or the orange of overlaps
SPEAKER_COLORS = ["#4a6cf7", "#d65db1", "#3fb8af", "#9b7bf0", "#4fa8e8", "#e6c84a"]
LANGUAGES = [("Indonesia", "id"), ("English", "en"), ("Otomatis", None)]  # dipakai CLI lama


def speaker_order(segments: list[dict]) -> list[str]:
    """Me first, then the others, most talkative first."""
    tot: dict[str, float] = {}
    for s in segments:
        tot[s["speaker"]] = tot.get(s["speaker"], 0.0) + (s["end"] - s["start"])
    return sorted(tot, key=lambda k: (k != "Me", -tot[k], k))


def speaker_colors(order: list[str]) -> dict[str, str]:
    others = [k for k in order if k != "Me"]
    return {"Me": ME_COLOR, **{k: SPEAKER_COLORS[i % len(SPEAKER_COLORS)] for i, k in enumerate(others)}}


class SpeakerLanes(QWidget):
    """One lane per speaker, a bar wherever that person talks, a line for the playhead. Click or drag = seek.
    Times given to it are player seconds (timeline seconds minus the player's start)."""

    seek = Signal(int)       # player position in milliseconds
    rename = Signal(str)     # speaker id whose name was double-clicked

    LABEL_W, LANE_H, GAP, PAD = 120, 18, 4, 6

    def __init__(self):
        super().__init__()
        self.lanes: list[str] = []
        self.names: dict[str, str] = {}
        self.colors: dict[str, str] = {}
        self.segs: dict[str, list[tuple[float, float]]] = {}
        self.overlaps: list[tuple[float, float]] = []
        self.dur = 0.0
        self.pos = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFixedHeight(self.PAD * 2)

    # --- data ------------------------------------------------------------------------------------
    def set_data(self, doc: dict, t_start: float) -> None:
        segments = doc.get("segments", [])
        self.lanes = speaker_order(segments)
        self.colors = speaker_colors(self.lanes)
        self.names = {k: display_name(doc, k) for k in self.lanes}
        self.segs = {k: [(s["start"] - t_start, s["end"] - t_start) for s in segments if s["speaker"] == k] for k in self.lanes}
        self.overlaps = [(o["start"] - t_start, o["end"] - t_start) for o in doc.get("overlaps", [])]
        self.setFixedHeight(self.PAD * 2 + self._rows() * (self.LANE_H + self.GAP) - self.GAP)
        self.update()

    def clear(self) -> None:
        self.lanes, self.segs, self.overlaps = [], {}, []
        self.setFixedHeight(self.PAD * 2)
        self.update()

    def set_duration(self, sec: float) -> None:
        self.dur = max(0.0, sec)
        self.update()

    def set_position(self, sec: float) -> None:
        self.pos = sec
        self.update()

    def _rows(self) -> int:
        return len(self.lanes) + (1 if self.overlaps else 0)

    def active_at(self, t: float) -> list[str]:
        return [k for k in self.lanes if any(a <= t < b for a, b in self.segs[k])]

    # --- geometry ----------------------------------------------------------------------------------
    def _bar_x(self, t: float) -> float:
        w = self.width() - self.LABEL_W - self.PAD
        return self.LABEL_W + (min(max(t, 0.0), self.dur) / self.dur * w if self.dur > 0 else 0.0)

    def time_at(self, x: float) -> float:
        w = self.width() - self.LABEL_W - self.PAD
        return min(max((x - self.LABEL_W) / w, 0.0), 1.0) * self.dur if w > 0 else 0.0

    def lane_at(self, y: float) -> str | None:
        row = int((y - self.PAD) // (self.LANE_H + self.GAP))
        return self.lanes[row] if 0 <= row < len(self.lanes) else None

    # --- painting ----------------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetrics(self.font())
        rows = [(k, self.names[k], self.segs[k], QColor(self.colors[k])) for k in self.lanes]
        if self.overlaps:
            rows.append((None, "Tumpang tindih", self.overlaps, QColor(OVERLAP_COLOR)))
        for i, (_k, name, segs, color) in enumerate(rows):
            y = self.PAD + i * (self.LANE_H + self.GAP)
            p.setPen(QColor("#cfd5e3"))
            p.drawText(QRectF(0, y, self.LABEL_W - 8, self.LANE_H), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, self.LABEL_W - 8))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1c2029"))
            p.drawRoundedRect(QRectF(self.LABEL_W, y, self.width() - self.LABEL_W - self.PAD, self.LANE_H), 4, 4)
            p.setBrush(color)
            for a, b in segs:
                x0, x1 = self._bar_x(a), self._bar_x(b)
                if x1 > x0 or b > a:
                    p.drawRoundedRect(QRectF(x0, y + 2, max(2.0, x1 - x0), self.LANE_H - 4), 3, 3)
        if self.dur > 0 and rows:
            x = self._bar_x(self.pos)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.end()

    # --- mouse -------------------------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().x() >= self.LABEL_W:
            self.seek.emit(int(self.time_at(e.position().x()) * 1000))

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.MouseButton.LeftButton and e.position().x() >= self.LABEL_W:
            self.seek.emit(int(self.time_at(e.position().x()) * 1000))

    def mouseDoubleClickEvent(self, e) -> None:
        k = self.lane_at(e.position().y())
        if e.position().x() < self.LABEL_W and k:
            self.rename.emit(k)


class PlayerWindow(QWidget):
    closed = Signal()  # emitted once, when the window is closed (the panel then comes back)

    def __init__(self, root: Path = DEFAULT_OUT, folder: Path | None = None):
        super().__init__(None, FULL_WINDOW_FLAGS)
        self.setWindowTitle("meetrec: putar rekaman")
        self.setStyleSheet(STYLE)
        self.resize(980, 720)
        self.root = Path(root)
        self.folder: Path | None = None
        self._builder: _Builder | None = None
        self._seeking = False
        self._job: Job | None = None
        self._tw = None  # jendela progres transkrip (hidup sendiri, player tetap terbuka)
        self._ok_text = ""
        self._closed = False
        self.t_start = 0.0        # recording-timeline second that is second 0 of the player
        self.speakers: dict | None = None
        self.turns: list[dict] = []
        self._turn_starts: list[float] = []
        self._turn_row = -1

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        # Cadangan saat full.mp4 gagal dibuat: video diputar tanpa suara, suaranya
        # dari mix.wav di player kedua. `self._fallback` menandai mode ini.
        self.aux = QMediaPlayer(self)
        self.aux_out = QAudioOutput(self)
        self.aux.setAudioOutput(self.aux_out)
        self._fallback = False
        self.video = QVideoWidget()
        self.video.setMinimumSize(640, 360)
        self.player.setVideoOutput(self.video)

        v = QVBoxLayout(self)
        self.picker_row = QWidget()  # only for a player opened without a given recording
        top = QHBoxLayout(self.picker_row)
        top.setContentsMargins(0, 0, 0, 0)
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
        v.addWidget(self.picker_row)
        v.addWidget(self.video, 1)

        self.who_box = QWidget()  # who speaks when (and, optionally, what they said)
        wv = QVBoxLayout(self.who_box)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(QLabel("Pembicara", objectName="whoTitle"))
        self.now_lbl = QLabel("", objectName="now")
        head.addWidget(self.now_lbl, 1)
        self.analyze_btn = QPushButton("Analisis pembicara")
        self.analyze_btn.setToolTip("Cari siapa bicara kapan (termasuk saat bicara barengan). Berjalan di komputer ini; file asli tidak diubah.")
        self.analyze_btn.clicked.connect(self.analyze)
        self.count = QComboBox()
        self.count.addItem("Remote: otomatis")
        for n in range(1, 9):
            self.count.addItem(f"Remote: {n} orang")
        self.count.setToolTip("Jumlah orang di sisi lawan bicara (bukan kamu). Otomatis kadang salah hitung: kalau tahu jumlahnya, pilih sendiri lalu Analisis ulang.")
        self.text_btn = QPushButton("Buat transkrip")
        self.text_btn.setToolTip("Buka jendela transkrip: pilih bahasa dan mesin di sana, lalu tekan Mulai.")
        self.text_btn.clicked.connect(self.transcribe)
        self.export_btn = QPushButton("Ekspor…")
        self.export_btn.setToolTip("Simpan transkrip ke berkas: .md, .txt, atau .srt")
        self.export_btn.clicked.connect(self.export)
        head.addWidget(self.count)
        head.addWidget(self.analyze_btn)
        head.addWidget(self.text_btn)
        head.addWidget(self.export_btn)
        wv.addLayout(head)
        self.lanes = SpeakerLanes()
        self.lanes.setToolTip("Klik = loncat ke menit itu. Klik dua kali nama = beri nama pembicara.")
        self.lanes.seek.connect(self._seek)
        self.lanes.rename.connect(self.rename_speaker)
        wv.addWidget(self.lanes)
        self.lines = QListWidget()
        self.lines.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep Space / arrows for the player
        self.lines.setFixedHeight(150)
        self.lines.itemClicked.connect(self._line_clicked)
        wv.addWidget(self.lines)
        v.addWidget(self.who_box)
        self._show_who()

        ctl = QHBoxLayout()
        self.play_btn = QPushButton("▶  Putar")
        self.play_btn.setFixedWidth(100)
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderMoved.connect(self._seek)
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
        self.speed.currentIndexChanged.connect(self._speed_changed)
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
        QShortcut(Qt.Key.Key_Left, self, activated=lambda: self._seek(max(0, self.player.position() - 5000)))
        QShortcut(Qt.Key.Key_Right, self, activated=lambda: self._seek(self.player.position() + 5000))

        self.refresh_list()
        if folder:  # opened from the recordings list: the list already chose, so no picker here
            self.picker_row.hide()
            self.setWindowTitle(f"meetrec: {Path(folder).name}")
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
        self._who_buttons()

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
        self.t_start = timeline_start(self.folder)
        self.load_who()
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
        self._builder.progress.connect(
            lambda pct: self.set_status(f"Membuat full media… {pct}% (file asli tidak diubah)"))
        self._builder.start()

    def _built(self, err: str, path: str) -> None:
        if self._builder:
            self._builder.wait(3000)
        if err:
            self._fallback_play(err)
            return
        self._load(Path(path))

    def _fallback_play(self, err: str) -> None:
        """full.mp4 gagal dibuat: rekaman tetap bisa ditonton — video tanpa suara + mix.wav."""
        if not self.folder or not (self.folder / "video.mp4").exists():
            self._enable(False, True)
            self.set_status(f"Gagal membuat full media: {err}", True)
            return
        from .fullmedia import build_mix_wav

        try:
            mix = build_mix_wav(self.folder)  # audio saja: detik-an, bukan menit
        except Exception as e:
            mix = None
            err = f"{err} · audio juga gagal dicampur: {e}"
        self._enable(True)
        self._fallback = True
        self.audio.setMuted(True)  # video asli tidak punya audio; suara dari mix.wav
        self.player.setSource(QUrl.fromLocalFile(str(self.folder / "video.mp4")))
        self.aux.setSource(QUrl.fromLocalFile(str(mix)) if mix else QUrl())
        self.tracks.blockSignals(True)
        self.tracks.clear()
        self.tracks.addItem("Mix (video + audio terpisah)")
        self.tracks.setEnabled(False)
        self.tracks.blockSignals(False)
        self.tracks.setToolTip("Mode cadangan: pilih track hanya tersedia di full.mp4")
        self.set_status(f"{self.folder.name}: full.mp4 gagal dibuat ({err}). "
                        "Memutar video + audio terpisah — coba 'Generate full media' lagi.", True)

    def _exit_fallback(self) -> None:
        if not self._fallback:
            return
        self._fallback = False
        self.aux.stop()
        self.aux.setSource(QUrl())
        self.audio.setMuted(False)
        self.tracks.setEnabled(True)
        self.tracks.setToolTip("Track audio")

    def _load(self, path: Path) -> None:
        self._exit_fallback()
        self._enable(True)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.set_status(f"{self.folder.name}: {path.name}")

    # --- transport ---------------------------------------------------------------------------------
    def _seek(self, ms: int) -> None:
        self.player.setPosition(ms)
        if self._fallback:
            self.aux.setPosition(ms)

    def _speed_changed(self, i: int) -> None:
        self.player.setPlaybackRate(SPEEDS[i][1])
        self.aux.setPlaybackRate(SPEEDS[i][1])

    def toggle(self) -> None:
        if self.player.source().isEmpty():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.aux.pause()
        else:
            self.player.play()
            if self._fallback:
                self.aux.setPosition(self.player.position())
                self.aux.play()

    def _state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸  Jeda" if playing else "▶  Putar")

    def _duration(self, ms: int) -> None:
        self.slider.setRange(0, ms)
        self.lanes.set_duration(ms / 1000)
        self.time_lbl.setText(f"{_clock(self.player.position())} / {_clock(ms)}")

    def _position(self, ms: int) -> None:
        if not self._seeking:
            self.slider.setValue(ms)
        self.time_lbl.setText(f"{_clock(ms)} / {_clock(self.player.duration())}")
        self._follow(ms / 1000)
        if self._fallback and self.aux.playbackState() == QMediaPlayer.PlaybackState.PlayingState \
                and abs(self.aux.position() - ms) > 400:  # dua player bisa melenceng; tarik kembali
            self.aux.setPosition(ms)

    def _released(self) -> None:
        self._seeking = False
        self._seek(self.slider.value())

    # --- who speaks when ---------------------------------------------------------------------------
    def load_who(self) -> None:
        """Read speakers.json / transcript.json of the open recording (if the analysis was done before)."""
        self.speakers = load_speakers(self.folder) if self.folder else None
        self.turns = (load_transcript(self.folder) or []) if self.folder else []
        self._turn_starts = [t["start"] - self.t_start for t in self.turns]
        self._turn_row = -1
        if self.speakers:
            self.lanes.set_data(self.speakers, self.t_start)
            self.lanes.set_duration(self.player.duration() / 1000)
        else:
            self.lanes.clear()
        self._fill_lines()
        self._show_who()
        self._follow(self.player.position() / 1000)

    def _show_who(self) -> None:
        has_audio = bool(self.folder and any((self.folder / n).exists() for n in ("mic.wav", "system.wav")))
        self.who_box.setVisible(has_audio)
        self.lanes.setVisible(bool(self.speakers and self.speakers["segments"]))
        self.lines.setVisible(bool(self.turns))
        self.analyze_btn.setText("Analisis ulang" if self.speakers else "Analisis pembicara")
        self.text_btn.setText("Buat ulang transkrip" if self.turns else "Buat transkrip")
        self.export_btn.setEnabled(bool(self.turns))
        if not self.speakers:
            self.now_lbl.setText("")
        self._who_buttons()

    def _who_buttons(self) -> None:
        busy = bool(self._job and self._job.isRunning())
        has_audio = bool(self.folder and any((self.folder / n).exists() for n in ("mic.wav", "system.wav")))
        for w in (self.analyze_btn, self.text_btn, self.count):
            w.setEnabled(has_audio and not busy)

    def _name(self, speaker: str) -> str:
        return "Tidak jelas" if speaker == "?" else display_name(self.speakers or {}, speaker)  # "?" = word outside every speaker segment

    def _fill_lines(self) -> None:
        self.lines.clear()
        for t in self.turns:
            item = QListWidgetItem(f"{fmt_time(t['start'] - self.t_start)}: {t['text']}")
            item.setForeground(QColor("#cfd5e3"))
            item.setData(Qt.ItemDataRole.UserRole, t["start"] - self.t_start)
            self.lines.addItem(item)

    def _follow(self, sec: float) -> None:
        """Playback moved: say who is talking now and mark the matching transcript line."""
        if self.speakers:
            names = [self._name(k) for k in self.lanes.active_at(sec)]
            text = "" if not self.speakers["segments"] else ("Sedang bicara: " + " + ".join(names) + (" (tumpang tindih)" if len(names) > 1 else "")
                                                             if names else "Sedang bicara: (hening)")
            if text != self.now_lbl.text():
                self.now_lbl.setText(text)
        self.lanes.set_position(sec)
        if self._turn_starts:
            from bisect import bisect_right

            row = bisect_right(self._turn_starts, sec) - 1
            if row != self._turn_row and row >= 0:
                self._turn_row = row
                self.lines.setCurrentRow(row)
                self.lines.scrollToItem(self.lines.item(row), QListWidget.ScrollHint.PositionAtCenter)

    def _line_clicked(self, item: QListWidgetItem) -> None:
        self._seek(max(0, int(item.data(Qt.ItemDataRole.UserRole) * 1000)))

    def _ask_name(self, speaker: str, current: str) -> str | None:
        text, ok = QInputDialog.getText(self, "Nama pembicara", f"Nama untuk \"{speaker}\" (kosong = nama asli):", text=current)
        return text if ok else None

    def rename_speaker(self, speaker: str) -> None:
        if not self.folder or not self.speakers:
            return
        name = self._ask_name(speaker, self._name(speaker))
        if name is None:
            return
        set_label(self.folder, speaker, name)
        self.load_who()

    def export(self) -> None:
        """Simpan transkrip ke berkas pilihan user; format ikut ekstensi."""
        if not self.folder:
            return
        from .transcript import export_transcript

        dest, _ = QFileDialog.getSaveFileName(self, "Ekspor transkrip",
                                              str(self.folder / "transcript.md"),
                                              "Markdown (*.md);;Teks (*.txt);;Subtitle SRT (*.srt)")
        if not dest:
            return
        try:
            path = export_transcript(self.folder, Path(dest))
        except Exception as e:
            self.set_status(f"Gagal ekspor: {e}", True)
            return
        self.set_status(f"Transkrip diekspor ke {path}")

    def _run_job(self, fn, busy_text: str, ok_text: str) -> None:
        if not self.folder or (self._job and self._job.isRunning()):
            return
        self.set_status(busy_text)
        self._ok_text = ok_text
        def guarded():
            try:
                return fn()
            except ImportError as e:  # the transcript needs the optional faster-whisper package
                raise RuntimeError(f'paket belum terpasang: {e}. Pasang dengan: pip install -e ".[transcribe]"') from e

        self._job = Job(guarded)
        self._job.done.connect(self._job_done)
        self._who_buttons_busy()
        self._job.start()

    def _who_buttons_busy(self) -> None:
        for w in (self.analyze_btn, self.text_btn, self.count):
            w.setEnabled(False)

    def _job_done(self, err: str) -> None:
        if self._closed:
            return
        self._who_buttons()
        if err:
            self.set_status(f"Gagal: {err}", True)
            return
        self.load_who()
        self.set_status(self._ok_text)

    def analyze(self) -> None:
        from .timeline import build_timeline

        folder, n = self.folder, self.count.currentIndex() or -1  # first item = automatic
        self._run_job(lambda: build_timeline(folder, n), "Menganalisis pembicara… (rekaman panjang bisa beberapa menit)",
                      "Analisis pembicara selesai. Klik dua kali nama di kiri untuk memberi nama.")

    def transcribe(self) -> None:
        """Buka jendela progres tersendiri — player tetap terbuka dan bisa diputar."""
        from .transcriptwin import TranskripWindow

        if not self.folder or (self._tw and self._tw.job and self._tw.job.isRunning()):
            return
        self._tw = TranskripWindow(self.folder)
        self._tw.done.connect(self._transcript_done)
        self._who_buttons_busy()
        self._tw.show()
        self._tw.raise_()

    def _transcript_done(self, err: str) -> None:
        if self._closed:
            return
        self._who_buttons()
        if err:
            self.set_status(f"Gagal: {err}", True)
            return
        self.load_who()
        self.set_status("Transkrip selesai. Klik satu baris untuk loncat ke menit itu.")

    # --- audio tracks ------------------------------------------------------------------------------
    def _fill_tracks(self) -> None:
        if self._fallback:  # video.mp4 tidak punya audio: combo tetap satu baris
            return
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
        self._closed = True  # a running analysis keeps writing its files; nobody waits for it
        self.player.stop()
        self.aux.stop()
        if self._builder:
            self._builder.wait(5000)
        super().closeEvent(e)
        if not getattr(self, "_announced", False):
            self._announced = True
            self.closed.emit()


# --- recordings list (cards) ----------------------------------------------------------------------
CACHE_DIR = Path.home() / ".meetrec" / "thumbs"
CARD_STYLE = """
#recCard { background: #1c2029; border: 1px solid #2c3242; border-radius: 12px; }
#recCard QLabel { background: transparent; }
#recCard:hover { background: #232836; border-color: #3a4258; }
#recName { font-size: 14px; font-weight: 600; }
#recMeta { color: #8f98ad; font-size: 12px; }
#recTitle { font-size: 16px; font-weight: 600; }
#cloudTile { background: #0f1218; border-radius: 8px; }
QTabBar::tab { background: #1c2029; color: #8f98ad; padding: 7px 0; border: none; border-radius: 8px; min-width: 100px; margin-right: 6px; }
QTabBar::tab:selected { background: #4a6cf7; color: #ffffff; }
QTabBar::tab:hover:!selected { background: #232836; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #3a4258; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def fmt_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{round(n / 1024 ** 2)} MB"
    return f"{max(1, round(n / 1024))} KB"


def fmt_duration(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def recording_info(folder: Path) -> dict:
    """What a card shows. Size/type are those of the file you would play: the final video when it is up to date."""
    folder = Path(folder)
    final = is_up_to_date(folder)
    main = folder / ("full.mp4" if final else "video.mp4")
    try:
        dur = float(json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("duration_sec") or 0)
    except (OSError, ValueError):
        dur = 0.0
    size = main.stat().st_size if main.exists() else 0
    return {"name": folder.name, "size": size, "duration": dur, "kind": main.suffix.lstrip(".") or "mp4", "final": final,
            "text": " | ".join([fmt_size(size), fmt_duration(dur), main.suffix.lstrip(".") or "mp4"] + (["final"] if final else []))}


class ThumbLoader(QThread):
    """Decodes one frame per recording (about 10% in) off the UI thread; cached so the next opening is instant."""

    ready = Signal(str, QImage)

    def __init__(self, items: list[tuple[Path, Path]], cache_dir: Path = CACHE_DIR, width: int = 336):
        super().__init__()
        self.items, self.cache_dir, self.width = items, Path(cache_dir), width
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def cache_path(self, folder: Path, video: Path) -> Path:
        st = video.stat()
        return self.cache_dir / f"{folder.name}-{st.st_size}-{int(st.st_mtime)}.jpg"

    def run(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for folder, video in self.items:
            if self._stop:
                return
            try:
                cp = self.cache_path(folder, video)
                img = QImage(str(cp)) if cp.exists() else self._decode(video)
                if img.isNull():
                    continue
                if not cp.exists():
                    img.save(str(cp), "JPG", 85)
                self.ready.emit(str(folder), img)
            except Exception:
                continue  # a card without a picture is fine

    def _decode(self, video: Path) -> QImage:
        import av

        c = av.open(str(video))
        try:
            s = c.streams.video[0]
            dur = float(s.duration * s.time_base) if s.duration else (c.duration or 0) / 1e6
            t = max(0.5, min(dur * 0.1, 20.0))
            c.seek(int(t / s.time_base), stream=s, backward=True)
            frame = None
            for f in c.decode(s):
                frame = f
                if f.time is None or f.time >= t - 0.5:
                    break
            if frame is None:
                return QImage()
            w = self.width
            h = max(2, int(w * frame.height / frame.width) // 2 * 2)
            arr = frame.reformat(width=w, height=h, format="rgb24").to_ndarray()
            return QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        finally:
            c.close()


class Thumb(QWidget):
    """Video picture with a play badge in the middle (picture fills the box, corners rounded)."""

    W, H = 168, 104

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.W, self.H)
        self.pix: QPixmap | None = None

    def set_image(self, img: QImage) -> None:
        self.pix = QPixmap.fromImage(img)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, self.W, self.H), 10, 10)
        p.setClipPath(clip)
        p.fillRect(self.rect(), QColor("#11141a"))
        if self.pix and not self.pix.isNull():
            scaled = self.pix.scaled(self.W, self.H, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.W - scaled.width()) // 2, (self.H - scaled.height()) // 2, scaled)
        c = QPointF(self.W / 2, self.H / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 185))
        p.drawEllipse(c, 21, 21)
        p.setBrush(QColor("#2b2f3a"))
        p.drawPolygon(QPolygonF([QPointF(c.x() - 6, c.y() - 10), QPointF(c.x() + 11, c.y()), QPointF(c.x() - 6, c.y() + 10)]))


class RecordingCard(QFrame):
    """picture + play badge · name · size | duration | type · [folder] [delete] [final video] [upload]. Click = play."""

    clicked = Signal(object)
    folder_clicked = Signal(object)
    delete_clicked = Signal(object)
    final_clicked = Signal(object)
    upload_clicked = Signal(object)

    def __init__(self, folder: Path):
        super().__init__(objectName="recCard")
        self.folder = Path(folder)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 10, 12, 10)
        h.setSpacing(12)
        self.thumb = Thumb()
        h.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.name_lbl = QLabel(objectName="recName")
        self.meta_lbl = QLabel(objectName="recMeta")
        col.addWidget(self.name_lbl)
        col.addWidget(self.meta_lbl)
        col.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.folder_btn = IconButton("folder", "Buka folder rekaman")
        self.delete_btn = IconButton("trash", "Hapus rekaman (dipindah ke Sampah)")
        self.final_btn = IconButton("final", "Buat video final (video + suara jadi satu file)")
        self.upload_btn = ProgressIconButton("upload", "Upload ke cloud (tujuan di Pengaturan > Cloud)")
        for b in (self.folder_btn, self.delete_btn, self.final_btn, self.upload_btn):
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        h.addLayout(col, 1)
        self.folder_btn.clicked.connect(lambda: self.folder_clicked.emit(self.folder))
        self.delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.folder))
        self.final_btn.clicked.connect(lambda: self.final_clicked.emit(self.folder))
        self.upload_btn.clicked.connect(lambda: self.upload_clicked.emit(self.folder))
        self.refresh_info()

    def refresh_info(self) -> dict:
        info = recording_info(self.folder)
        self.name_lbl.setText(info["name"])
        self.meta_lbl.setText(info["text"])
        self.final_btn.setToolTip("Buat ulang video final" if info["final"] else "Buat video final (video + suara jadi satu file)")
        return info

    def show_upload(self, up: Upload | None) -> None:
        """Button look for the upload of this recording: idle, ring with progress (click = pause), paused, done, error."""
        b = self.upload_btn
        if up is None or up.state == "cancelled":
            return b.set_state("idle")
        pct = int(up.progress * 100)
        if up.state == "running":
            b.set_state("running", up.progress, f"Mengunggah {pct}% — klik untuk jeda")
        elif up.state == "paused":
            b.set_state("paused", up.progress, f"Dijeda di {pct}% — klik untuk lanjut")
        elif up.state == "done":
            b.set_state("done", 1.0, f"Terunggah ke {up.remote}. Klik untuk unggah ulang")
        else:
            b.set_state("error", 0.0, f"Upload gagal: {up.message}. Klik untuk coba lagi")

    def set_busy(self, busy: bool) -> None:
        self.final_btn.setEnabled(not busy)
        self.delete_btn.setEnabled(not busy)
        if busy:
            self.final_btn.setToolTip("Membuat video final…")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.folder)
        else:
            super().mousePressEvent(e)


class CloudCard(QFrame):
    """A recording folder found in the cloud: tile · name · size | files | date · [download]."""

    download_clicked = Signal(str)

    def __init__(self, info: dict, local: bool):
        super().__init__(objectName="recCard")
        self.name = info["name"]
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 10, 12, 10)
        h.setSpacing(12)
        tile = QLabel(objectName="cloudTile")
        tile.setFixedSize(Thumb.W, Thumb.H)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(make_icon("cloud", "#5d667c").pixmap(48, 48))
        h.addWidget(tile)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.name_lbl = QLabel(self.name, objectName="recName")
        self.meta_lbl = QLabel(objectName="recMeta")
        self.meta_lbl.setWordWrap(True)
        self.info = info
        col.addWidget(self.name_lbl)
        col.addWidget(self.meta_lbl)
        col.addStretch(1)
        row = QHBoxLayout()
        self.download_btn = ProgressIconButton("download", "Unduh ke komputer ini (masuk ke daftar Local)")
        row.addWidget(self.download_btn)
        row.addStretch(1)
        col.addLayout(row)
        h.addLayout(col, 1)
        self.download_btn.clicked.connect(lambda: self.download_clicked.emit(self.name))
        self.set_local(local)

    def set_local(self, local: bool) -> None:
        i = self.info
        self._local = local
        text = " | ".join([fmt_size(i["size"]), f"{i['files']} file"] + ([i["modified"][:10]] if i["modified"] else []))
        self.meta_lbl.setText(text + ("  ·  sudah ada di komputer" if local else ""))
        b = self.download_btn
        b.setEnabled(not local)  # already here: nothing to download, so the button must not look clickable
        if local:
            b.set_state("done", 1.0, "Sudah ada di komputer ini")

    def show_download(self, dl: Upload | None) -> None:
        b = self.download_btn
        if self._local:
            return b.set_state("done", 1.0, "Sudah ada di komputer ini")
        if dl is None or dl.state == "cancelled":
            return b.set_state("idle")
        pct = int(dl.progress * 100)
        if dl.state == "running":
            b.set_state("running", dl.progress, f"Mengunduh {pct}% — klik untuk jeda")
        elif dl.state == "paused":
            b.set_state("paused", dl.progress, f"Dijeda di {pct}% — klik untuk lanjut")
        elif dl.state == "done":
            b.set_state("done", 1.0, f"Terunduh ke {dl.folder}. Klik untuk unduh ulang (berkas yang sama dilewati)")
        else:
            b.set_state("error", 0.0, f"Unduh gagal: {dl.message}. Klik untuk coba lagi")


class RecordingsWindow(QWidget):
    """Compact scrolling list of finished recordings. Clicking a card opens the player for it (list hides,
    comes back when the player closes). Closing this window announces `closed` (the panel comes back)."""

    closed = Signal()

    def __init__(self, root: Path = DEFAULT_OUT, cache_dir: Path = CACHE_DIR, trash=None, open_folder=None,
                 uploads: UploadManager | None = None):
        super().__init__(None, FULL_WINDOW_FLAGS)
        self.setWindowTitle("meetrec: rekaman")
        self.setStyleSheet(STYLE + CARD_STYLE)
        self.root = Path(root)
        self.cache_dir = Path(cache_dir)
        self._trash = trash or (lambda path: QFile.moveToTrash(path))
        self._open_folder = open_folder or (lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        self.cards: dict[Path, RecordingCard] = {}
        self.uploads = uploads  # None: no upload buttons (nothing to upload to)
        if uploads is not None:
            uploads.changed.connect(self._upload_changed)
            uploads.download_changed.connect(self._download_changed)
        self.cloud_cards: dict[str, CloudCard] = {}
        self._cloud_key = None        # (account, folder) the cloud list was loaded for
        self._cloud_stale = True      # something may have changed in the cloud since
        self._cloud_job: Job | None = None
        self._builders: dict[Path, _Builder] = {}
        self._loader: ThumbLoader | None = None
        self._player: PlayerWindow | None = None
        self._announced = False

        screen = QGuiApplication.primaryScreen()
        max_h = int(screen.availableGeometry().height() * 0.8) if screen else 560
        self.resize(460, min(560, max_h))  # narrow and not full height: the list scrolls
        self.setMinimumWidth(420)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 10)
        v.setSpacing(8)
        self.title_lbl = QLabel("Rekaman", objectName="recTitle")
        v.addWidget(self.title_lbl)
        self.tabs = QTabBar()  # Local = what is on this computer (upload), Cloud = what is in the cloud (download)
        self.tabs.addTab("Local")
        self.tabs.addTab("Cloud")
        self.tabs.setExpanding(True)
        self.tabs.setDrawBase(False)
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.setVisible(uploads is not None)  # no cloud without an upload manager
        v.addWidget(self.tabs)
        self.stack = QStackedWidget()
        local = QWidget()
        lv = QVBoxLayout(local)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        inner = QWidget()
        self.cards_box = QVBoxLayout(inner)
        self.cards_box.setContentsMargins(0, 0, 4, 0)
        self.cards_box.setSpacing(10)
        self.cards_box.addStretch(1)
        self.scroll.setWidget(inner)
        lv.addWidget(self.scroll, 1)
        self.empty_lbl = QLabel("Belum ada rekaman yang selesai.", objectName="status")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv.addWidget(self.empty_lbl)
        self.stack.addWidget(local)

        cloud_page = QWidget()
        cv = QVBoxLayout(cloud_page)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(8)
        head = QHBoxLayout()
        self.cloud_target_lbl = QLabel("", objectName="status")
        self.cloud_refresh = QPushButton("Muat ulang")
        self.cloud_refresh.clicked.connect(lambda: self.refresh_cloud(force=True))
        head.addWidget(self.cloud_target_lbl, 1)
        head.addWidget(self.cloud_refresh)
        cv.addLayout(head)
        self.cloud_scroll = QScrollArea()
        self.cloud_scroll.setWidgetResizable(True)
        self.cloud_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cinner = QWidget()
        self.cloud_box = QVBoxLayout(cinner)
        self.cloud_box.setContentsMargins(0, 0, 4, 0)
        self.cloud_box.setSpacing(10)
        self.cloud_box.addStretch(1)
        self.cloud_scroll.setWidget(cinner)
        cv.addWidget(self.cloud_scroll, 1)
        self.cloud_msg = QLabel("", objectName="status")
        self.cloud_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cloud_msg.setWordWrap(True)
        cv.addWidget(self.cloud_msg)
        self.stack.addWidget(cloud_page)
        v.addWidget(self.stack, 1)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.reload()

    # --- content -----------------------------------------------------------------------------------
    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(f'<span style="color:#ff6b6b">{text}</span>' if error else text)

    def reload(self) -> None:
        if self._loader:
            self._loader.stop()
            self._loader.wait(2000)
        for c in self.cards.values():
            c.setParent(None)
            c.deleteLater()
        self.cards.clear()
        recs = list_recordings(self.root)
        for folder, _label in recs:
            card = RecordingCard(folder)
            card.clicked.connect(self.open_recording)
            card.folder_clicked.connect(self.reveal)
            card.delete_clicked.connect(self.delete_recording)
            card.final_clicked.connect(self.make_final)
            card.upload_clicked.connect(self.upload_recording)
            card.upload_btn.setVisible(self.uploads is not None)
            if self.uploads is not None:
                card.show_upload(self.uploads.get(folder.name))
            self.cards_box.insertWidget(self.cards_box.count() - 1, card)
            self.cards[folder] = card
        self.empty_lbl.setVisible(not recs)
        self._update_title()
        self._loader = ThumbLoader([(f, f / "video.mp4") for f, _ in recs], self.cache_dir)
        self._loader.ready.connect(self._thumb_ready)
        self._loader.start()

    def _update_title(self) -> None:
        if self.tabs.currentIndex() == 1:
            n = len(self.cloud_cards)
            self.title_lbl.setText(f"Rekaman di cloud ({n})" if n else "Rekaman di cloud")
        else:
            n = len(self.cards)
            self.title_lbl.setText(f"Rekaman ({n})" if n else "Rekaman")

    # --- cloud tab ---------------------------------------------------------------------------------
    def _tab_changed(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self._update_title()
        if i == 1:
            self.refresh_cloud(force=False)

    def _clear_cloud(self) -> None:
        for c in self.cloud_cards.values():
            c.setParent(None)
            c.deleteLater()
        self.cloud_cards = {}

    def refresh_cloud(self, force: bool = True) -> None:
        """List the recordings in the chosen cloud destination (in the background). Not repeated when nothing changed."""
        if self.uploads is None or (self._cloud_job and self._cloud_job.isRunning()):
            return
        target = self.uploads.target()
        key = (target.account, target.folder) if target else None
        if not force and key == self._cloud_key and not self._cloud_stale:
            return
        self._clear_cloud()
        self._update_title()
        if target is None:
            self._cloud_key = None
            self.cloud_target_lbl.setText("")
            self.cloud_msg.setText("Belum ada tujuan cloud. Buka ⚙ Pengaturan > Cloud, pilih satu akun (tombol centang).")
            return
        exe = cloud.find_rclone()
        if exe is None:
            self.cloud_msg.setText("rclone belum terpasang. Pasang di ⚙ Pengaturan > Cloud > Setup rclone.")
            return
        self._cloud_key, self._cloud_stale = key, False
        self.cloud_target_lbl.setText(f"{target.account}:{target.folder or '(level atas)'}")
        self.cloud_msg.setText("Memuat daftar dari cloud…")
        self.cloud_refresh.setEnabled(False)
        self._cloud_job = Job(lambda: list_cloud_recordings(exe, target))
        self._cloud_job.done.connect(lambda err, k=key: self._cloud_listed(err, k))
        self._cloud_job.start()

    def _cloud_listed(self, err: str, key) -> None:
        self.cloud_refresh.setEnabled(True)
        if key != self._cloud_key:
            return  # the destination changed while this was loading
        if err:
            self._cloud_stale = True
            self.cloud_msg.setText(f'<span style="color:#ff6b6b">Gagal memuat dari cloud: {err}</span>')
            return
        items = self._cloud_job.result or []
        for info in items:
            card = CloudCard(info, local=(self.root / info["name"]).exists())
            card.download_clicked.connect(self.download_recording)
            card.show_download(self.uploads.get_download(info["name"]))
            self.cloud_box.insertWidget(self.cloud_box.count() - 1, card)
            self.cloud_cards[info["name"]] = card
        self.cloud_msg.setText("" if items else "Belum ada rekaman di tujuan ini. Unggah dari tab Local.")
        self._update_title()

    def download_recording(self, name: str) -> None:
        """First click: fetch this recording from the cloud into the recordings folder. While it runs: pause / resume."""
        if self.uploads is None:
            return
        dl = self.uploads.get_download(name)
        if dl is not None and dl.state in ACTIVE:
            self.uploads.toggle_pause_download(name)
            return
        err = self.uploads.start_download(name, self.root)
        if err:
            self.set_status(err, True)
            self._info(err)
        else:
            self.set_status(f"Mengunduh {name} ke {self.root / name}")

    def _download_changed(self, name: str) -> None:
        card = self.cloud_cards.get(name)
        dl = self.uploads.get_download(name) if self.uploads else None
        if card is not None:
            card.show_download(dl)
        if dl is not None and dl.state == "done":
            if card is not None:
                card.set_local(True)
            self.set_status(f"{name} terunduh ke {dl.folder}")
            self.reload()  # the Local tab now has it
        elif dl is not None and dl.state == "error":
            self.set_status(f"Unduh {name} gagal: {dl.message}", True)

    def _thumb_ready(self, folder: str, img: QImage) -> None:
        card = self.cards.get(Path(folder))
        if card:
            card.thumb.set_image(img)

    # --- actions -----------------------------------------------------------------------------------
    def reveal(self, folder: Path) -> None:
        self._open_folder(folder)

    def open_recording(self, folder: Path) -> None:
        if self._player is not None:
            return
        self._player = PlayerWindow(self.root, Path(folder))
        self._player.closed.connect(self._player_closed)
        self.hide()  # the list is a sub page of the panel, the player a sub page of the list
        self._player.show()
        self._player.raise_()
        self._player.activateWindow()

    def _player_closed(self) -> None:
        self._player = None
        for c in self.cards.values():
            c.refresh_info()  # the player may have made the final video
        self.show()
        self.raise_()

    def _confirm(self, title: str, text: str, ok: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.NoButton, self)
        yes = box.addButton(ok, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Batal", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def delete_recording(self, folder: Path) -> None:
        card = self.cards.get(Path(folder))
        if not card or Path(folder) in self._builders:
            return
        info = recording_info(folder)
        up = self.uploads.get(Path(folder).name) if self.uploads else None
        uploading = up is not None and up.state in ACTIVE
        if not self._confirm("Hapus rekaman?",
                             f"{info['name']}\n{info['text']}\n\nSeluruh isi folder (video, mic, suara sistem, video final) "
                             "dipindahkan ke Sampah. Masih bisa dikembalikan dari sana."
                             + ("\n\nRekaman ini sedang diunggah; upload dibatalkan." if uploading else ""), "Pindahkan ke Sampah"):
            return
        if uploading:
            self.uploads.cancel(Path(folder).name)
        if self._trash(str(folder)):
            card.setParent(None)
            card.deleteLater()
            del self.cards[Path(folder)]
            self.empty_lbl.setVisible(not self.cards)
            self._update_title()
            self.set_status(f"{info['name']} dipindahkan ke Sampah")
        else:
            self.set_status("Gagal memindahkan ke Sampah (folder tidak diubah).", True)

    def _info(self, text: str) -> None:
        QMessageBox.information(self, "Upload", text)

    def upload_recording(self, folder: Path) -> None:
        """First click: upload this recording folder to the chosen cloud account. While it runs: pause / resume."""
        if self.uploads is None:
            return
        name = Path(folder).name
        up = self.uploads.get(name)
        if up is not None and up.state in ACTIVE:
            self.uploads.toggle_pause(name)
            return
        err = self.uploads.start(Path(folder))
        if err:
            self.set_status(err, True)
            self._info(err)
        else:
            self.set_status(f"Mengunggah {name} ke {self.uploads.target().remote(name)}")

    def _upload_changed(self, name: str) -> None:
        card = next((c for f, c in self.cards.items() if f.name == name), None)
        if card is not None and self.uploads is not None:
            card.show_upload(self.uploads.get(name))
            up = self.uploads.get(name)
            if up is not None and up.state == "error":
                self.set_status(f"Upload {name} gagal: {up.message}", True)
            elif up is not None and up.state == "done":
                self.set_status(f"{name} terunggah ke {up.remote}")
                self._cloud_stale = True  # the Cloud tab lists it next time it is opened

    def make_final(self, folder: Path) -> None:
        folder = Path(folder)
        card = self.cards.get(folder)
        if not card or folder in self._builders:
            return
        card.set_busy(True)
        self.set_status(f"Membuat video final {folder.name}… (file asli tidak diubah)")
        b = _Builder(folder)
        b.done.connect(lambda err, path, f=folder: self._final_done(f, err, path))
        self._builders[folder] = b
        b.start()

    def _final_done(self, folder: Path, err: str, path: str) -> None:
        b = self._builders.pop(folder, None)
        if b:
            b.wait(3000)
        card = self.cards.get(folder)
        if card:
            card.set_busy(False)
            card.refresh_info()
        self.set_status(f"Gagal membuat video final: {err}" if err else f"{folder.name}: video final siap ({Path(path).name})", bool(err))

    def closeEvent(self, e) -> None:
        if self.uploads is not None:
            try:
                self.uploads.changed.disconnect(self._upload_changed)  # uploads go on; this window just stops listening
                self.uploads.download_changed.disconnect(self._download_changed)
            except (RuntimeError, TypeError):
                pass
        if self._loader:
            self._loader.stop()
            self._loader.wait(2000)
        for b in list(self._builders.values()):
            b.wait(15000)
        super().closeEvent(e)
        if not self._announced:
            self._announced = True
            self.closed.emit()


def run(folder: Path | None = None) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    from PySide6.QtCore import QSettings

    win = PlayerWindow(DEFAULT_OUT, folder) if folder else RecordingsWindow(DEFAULT_OUT, uploads=UploadManager(QSettings("meetrec", "meetrec")))
    win.show()
    app.exec()
