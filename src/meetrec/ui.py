"""Floating recorder panel (PySide6, Windows / Linux / macOS).

idle      : folder · Mulai · Tutup
recording : timer · Gambar · Jeda · Stop         (no Tutup while recording)
drawing   : + pencil/text · colors · thickness · undo · save · discard
            The screen is frozen with a screenshot while drawing (falls back to a transparent overlay
            when the OS refuses the screenshot, e.g. Wayland or missing macOS permission).
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QImage, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

DEFAULT_OUT = Path.home() / "Recordings" / "meetrec"
COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff", "#111111"]
FG = "#e8ebf2"
_W = Qt.WindowType
_A = Qt.WidgetAttribute


# --- icons (drawn, so they stay readable on the dark card on every OS) ---------------------------
def make_icon(name: str, color: str = FG) -> QIcon:
    pm = QPixmap(48, 48)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    P = QPointF
    if name == "play":
        p.setBrush(c)
        p.drawPolygon(QPolygonF([P(15, 9), P(39, 24), P(15, 39)]))
    elif name == "pause":
        p.setBrush(c)
        p.drawRoundedRect(QRectF(13, 10, 8, 28), 2, 2)
        p.drawRoundedRect(QRectF(27, 10, 8, 28), 2, 2)
    elif name == "stop":
        p.setBrush(c)
        p.drawRoundedRect(QRectF(11, 11, 26, 26), 4, 4)
    elif name == "close":
        p.drawLine(12, 12, 36, 36)
        p.drawLine(36, 12, 12, 36)
    elif name == "folder":
        path = QPainterPath(P(6, 14))
        for pt in (P(19, 14), P(24, 19), P(42, 19), P(42, 38), P(6, 38)):
            path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "pencil":
        p.setPen(QPen(c, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(18, 30, 35, 13)
        p.setPen(QPen(c, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(c)
        p.drawPolygon(QPolygonF([P(9, 39), P(12, 28), P(20, 36)]))
    elif name == "text":
        p.drawLine(12, 12, 36, 12)
        p.drawLine(24, 12, 24, 38)
    elif name == "save":
        p.drawLine(24, 8, 24, 28)
        p.drawPolyline([P(15, 20), P(24, 29), P(33, 20)])
        p.drawPolyline([P(10, 32), P(10, 40), P(38, 40), P(38, 32)])
    elif name == "trash":
        p.drawLine(10, 14, 38, 14)
        p.drawPolyline([P(19, 14), P(19, 9), P(29, 9), P(29, 14)])
        p.drawPolyline([P(13, 14), P(15, 40), P(33, 40), P(35, 14)])
    elif name == "undo":
        path = QPainterPath(P(36, 38))
        path.cubicTo(P(38, 22), P(24, 16), P(14, 20))
        p.drawPath(path)
        p.drawPolyline([P(14, 9), P(13, 20), P(24, 22)])
    p.end()
    return QIcon(pm)


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

    def __init__(self, screen, shot: QPixmap | None):
        super().__init__(None, _W.Tool | _W.FramelessWindowHint | _W.WindowStaysOnTopHint)
        self.shot = shot
        self._screen = screen
        self.setAttribute(_A.WA_TranslucentBackground, shot is None)
        self.setAttribute(_A.WA_MacAlwaysShowToolWindow, True)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.items: list[dict] = []
        self.tool, self.color, self.width_px = "pencil", QColor(COLORS[0]), 4
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

    def render_image(self) -> QImage:
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
        else:
            self._start_text(pos)
        self.update()

    def mouseMoveEvent(self, e):
        if self._cur is not None:
            self._cur["pts"].append(e.position())
            self.update()

    def mouseReleaseEvent(self, _):
        self._cur = None

    def keyPressEvent(self, e):
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
        self.update()

    def finish_text(self) -> None:
        if self._edit:
            self._commit_text()


# --- panel ---------------------------------------------------------------------------------------
STYLE = f"""
#card {{ background: #1c2029; border-radius: 14px; border: 1px solid #2c3242; }}
QLabel {{ color: {FG}; font-size: 13px; }}
#path {{ color: #aab2c5; }}
#status {{ color: #8f98ad; font-size: 11px; }}
#timer {{ font-size: 18px; font-weight: 600; }}
QPushButton {{ background: #2a3040; color: {FG}; border: none; border-radius: 8px; padding: 6px 10px; font-size: 13px; }}
QPushButton:hover {{ background: #384058; }}
QPushButton:disabled {{ background: #232836; color: #5d667c; }}
QPushButton:checked {{ background: #4a6cf7; }}
#start {{ background: #2ea043; font-weight: 600; padding: 7px 16px; }}
#start:hover {{ background: #3bb653; }}
#stop {{ background: #d03b3b; }}
#stop:hover {{ background: #e24c4c; }}
QSlider::groove:horizontal {{ height: 4px; background: #3a4258; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {FG}; }}
"""


class Panel(QWidget):
    def __init__(self):
        super().__init__(None, _W.Tool | _W.FramelessWindowHint | _W.WindowStaysOnTopHint)
        self.setAttribute(_A.WA_TranslucentBackground)
        self.setAttribute(_A.WA_MacAlwaysShowToolWindow, True)
        self.setStyleSheet(STYLE)
        self.settings = QSettings("meetrec", "meetrec")
        self.out_dir = Path(self.settings.value("out_dir", str(DEFAULT_OUT)))
        self.state = "idle"  # idle | recording | paused
        self.session = None
        self.overlay: DrawOverlay | None = None
        self._placed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        card = QFrame(objectName="card")
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # folder row
        self.folder_row = QWidget()
        h = QHBoxLayout(self.folder_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.folder_btn = self._btn("folder", tip="Pilih folder penyimpanan")
        self.folder_btn.clicked.connect(self.choose_folder)
        self.path_lbl = QLabel(objectName="path")
        h.addWidget(self.folder_btn)
        h.addWidget(self.path_lbl, 1)
        v.addWidget(self.folder_row)

        # idle row
        self.idle_row = QWidget()
        h = QHBoxLayout(self.idle_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.start_btn = QPushButton(" Mulai", objectName="start")
        self.start_btn.setIcon(make_icon("play"))
        self.start_btn.setIconSize(QSize(18, 18))
        self.start_btn.clicked.connect(self.start)
        self.close_btn = self._btn("close", tip="Tutup aplikasi")
        self.close_btn.clicked.connect(QApplication.quit)
        h.addWidget(self.start_btn, 1)
        h.addWidget(self.close_btn)
        v.addWidget(self.idle_row)

        # recording row
        self.rec_row = QWidget()
        h = QHBoxLayout(self.rec_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.timer_lbl = QLabel("00:00", objectName="timer")
        self.draw_btn = self._btn("pencil", " Gambar")
        self.draw_btn.clicked.connect(self.enter_draw)
        self.pause_btn = self._btn("pause", tip="Jeda")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = self._btn("stop", tip="Stop", name="stop")
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
        self.pencil_btn = self._btn("pencil", tip="Pensil", checkable=True)
        self.text_btn = self._btn("text", tip="Teks", checkable=True)
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
        self.undo_btn = self._btn("undo", tip="Urungkan")
        self.undo_btn.clicked.connect(lambda: self.overlay and self.overlay.undo())
        self.save_btn = self._btn("save", tip="Simpan gambar")
        self.save_btn.clicked.connect(self.save_drawing)
        self.trash_btn = self._btn("trash", tip="Hapus gambar")
        self.trash_btn.clicked.connect(self.discard_drawing)
        b.addWidget(self.size_lbl)
        b.addWidget(self.size_slider, 1)
        b.addWidget(self.undo_btn)
        b.addWidget(self.save_btn)
        b.addWidget(self.trash_btn)
        dv.addLayout(b)
        v.addWidget(self.draw_row)

        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl.fromLocalFile(url)))
        v.addWidget(self.status)

        self.tick = QTimer(self)
        self.tick.setInterval(250)
        self.tick.timeout.connect(self.update_timer)
        self.setFixedWidth(330)
        self.refresh()

    # --- helpers ---------------------------------------------------------------------------------
    def _btn(self, icon: str, text: str = "", tip: str = "", checkable: bool = False, name: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setIcon(make_icon(icon))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        if name:
            b.setObjectName(name)
        return b

    def _fit(self) -> None:
        right = self.x() + self.width() if self._placed else None
        self.adjustSize()
        if right is not None:
            self.move(right - self.width(), self.y())

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
        idle = self.state == "idle"
        drawing = self.overlay is not None
        self.folder_row.setVisible(idle)
        self.idle_row.setVisible(idle)
        self.rec_row.setVisible(not idle)
        self.draw_row.setVisible(drawing)
        self.draw_btn.setEnabled(not drawing and not idle)
        self.path_lbl.setText(self._elide(str(self.out_dir)))
        self.path_lbl.setToolTip(str(self.out_dir))
        self.pause_btn.setIcon(make_icon("play" if self.state == "paused" else "pause"))
        self.pause_btn.setToolTip("Lanjut" if self.state == "paused" else "Jeda")
        self._fit()

    def _elide(self, s: str, n: int = 34) -> str:
        return s if len(s) <= n else "…" + s[-(n - 1):]

    def update_timer(self) -> None:
        if not self.session:
            return
        mm, ss = divmod(int(self.session.active_seconds()), 60)
        self.timer_lbl.setText(("⏸ " if self.state == "paused" else "● ") + f"{mm:02d}:{ss:02d}")

    # --- actions ---------------------------------------------------------------------------------
    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pilih folder penyimpanan", str(self.out_dir))
        if d:
            self.out_dir = Path(d)
            self.settings.setValue("out_dir", d)
            self.refresh()

    def start(self) -> None:
        from .record import Session

        self.set_status("Menyiapkan perekam…")
        QApplication.processEvents()
        try:
            self.session = Session(self.out_dir)
            self.session.start()
        except Exception as e:
            self.session = None
            self.set_status(f"Gagal mulai: {e}", error=True)
            return
        self.state = "recording"
        self.set_status("")
        self.update_timer()
        self.tick.start()
        self.refresh()

    def toggle_pause(self) -> None:
        if not self.session:
            return
        if self.state == "paused":
            self.session.resume()
            self.state = "recording"
        else:
            self.session.pause()
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
            try:
                folder = self.session.stop()
                import json
                meta = json.loads((folder / "meta.json").read_text())
                silent = [n for n, t in meta["tracks"].items() if t["silent"]]
                if silent:
                    warn = f"<br><span style='color:#ffb86b'>Track diam: {', '.join(silent)}. Cek izin mikrofon / audio sistem.</span>"
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
        self.overlay.show()
        self.show()
        self.raise_()
        self.activateWindow()
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
        self._close_overlay()
        self.set_status("Gambar dihapus")

    def _close_overlay(self) -> None:
        if self.overlay:
            self.overlay.close()
            self.overlay.deleteLater()
            self.overlay = None
        self.refresh()
        self.show()
        self.raise_()

    # --- window ----------------------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()

    def closeEvent(self, e):
        if self.state != "idle":
            e.ignore()  # no closing while recording
            return
        super().closeEvent(e)


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("meetrec")
    app.setQuitOnLastWindowClosed(False)
    panel = Panel()
    panel.show()
    panel.place_top_right()
    app.aboutToQuit.connect(lambda: panel.session and panel.session.stop())
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
