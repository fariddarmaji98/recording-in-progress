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
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSlider, QVBoxLayout, QWidget)

from .fullmedia import build_full_media, is_up_to_date
from .paths import DEFAULT_OUT, list_recordings
from .widgets import BUTTON_STYLE, FG, IconButton

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
""" + BUTTON_STYLE


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
        super().__init__(None, FULL_WINDOW_FLAGS)
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


# --- recordings list (cards) ----------------------------------------------------------------------
CACHE_DIR = Path.home() / ".meetrec" / "thumbs"
CARD_STYLE = """
#recCard { background: #1c2029; border: 1px solid #2c3242; border-radius: 12px; }
#recCard QLabel { background: transparent; }
#recCard:hover { background: #232836; border-color: #3a4258; }
#recName { font-size: 14px; font-weight: 600; }
#recMeta { color: #8f98ad; font-size: 12px; }
#recTitle { font-size: 16px; font-weight: 600; }
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
        dur = float(json.loads((folder / "meta.json").read_text()).get("duration_sec") or 0)
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
    """picture + play badge · name · size | duration | type · [folder] [delete] [final video]. Click = play."""

    clicked = Signal(object)
    folder_clicked = Signal(object)
    delete_clicked = Signal(object)
    final_clicked = Signal(object)

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
        for b in (self.folder_btn, self.delete_btn, self.final_btn):
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        h.addLayout(col, 1)
        self.folder_btn.clicked.connect(lambda: self.folder_clicked.emit(self.folder))
        self.delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.folder))
        self.final_btn.clicked.connect(lambda: self.final_clicked.emit(self.folder))
        self.refresh_info()

    def refresh_info(self) -> dict:
        info = recording_info(self.folder)
        self.name_lbl.setText(info["name"])
        self.meta_lbl.setText(info["text"])
        self.final_btn.setToolTip("Buat ulang video final" if info["final"] else "Buat video final (video + suara jadi satu file)")
        return info

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


class RecordingsWindow(QWidget):
    """Compact scrolling list of finished recordings. Clicking a card opens the player for it (list hides,
    comes back when the player closes). Closing this window announces `closed` (the panel comes back)."""

    closed = Signal()

    def __init__(self, root: Path = DEFAULT_OUT, cache_dir: Path = CACHE_DIR, trash=None, open_folder=None):
        super().__init__(None, FULL_WINDOW_FLAGS)
        self.setWindowTitle("meetrec: rekaman")
        self.setStyleSheet(STYLE + CARD_STYLE)
        self.root = Path(root)
        self.cache_dir = Path(cache_dir)
        self._trash = trash or (lambda path: QFile.moveToTrash(path))
        self._open_folder = open_folder or (lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        self.cards: dict[Path, RecordingCard] = {}
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
        v.addWidget(self.scroll, 1)
        self.empty_lbl = QLabel("Belum ada rekaman yang selesai.", objectName="status")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.empty_lbl)
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
            self.cards_box.insertWidget(self.cards_box.count() - 1, card)
            self.cards[folder] = card
        self.empty_lbl.setVisible(not recs)
        self.title_lbl.setText(f"Rekaman ({len(recs)})" if recs else "Rekaman")
        self._loader = ThumbLoader([(f, f / "video.mp4") for f, _ in recs], self.cache_dir)
        self._loader.ready.connect(self._thumb_ready)
        self._loader.start()

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
        if not self._confirm("Hapus rekaman?",
                             f"{info['name']}\n{info['text']}\n\nSeluruh isi folder (video, mic, suara sistem, video final) "
                             "dipindahkan ke Sampah. Masih bisa dikembalikan dari sana.", "Pindahkan ke Sampah"):
            return
        if self._trash(str(folder)):
            card.setParent(None)
            card.deleteLater()
            del self.cards[Path(folder)]
            self.empty_lbl.setVisible(not self.cards)
            self.title_lbl.setText(f"Rekaman ({len(self.cards)})" if self.cards else "Rekaman")
            self.set_status(f"{info['name']} dipindahkan ke Sampah")
        else:
            self.set_status("Gagal memindahkan ke Sampah (folder tidak diubah).", True)

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
    win = PlayerWindow(DEFAULT_OUT, folder) if folder else RecordingsWindow(DEFAULT_OUT)
    win.show()
    app.exec()
