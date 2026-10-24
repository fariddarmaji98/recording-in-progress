"""Jendela "Buat transkrip": setelan di dalam, lalu langkah demi langkah.

Tombol "Buat transkrip" di player hanya MEMBUKA jendela ini — belum mengirim
apa pun. Unggahan baru mulai setelah setelan dipilih dan **Mulai** ditekan.

Bukan dialog modal dan tanpa induk: player tetap terbuka dan bisa diputar selagi
transkrip berjalan. Menutup jendela hanya berhenti menonton — pekerjaannya tetap
jalan di thread daemon dan hasilnya muncul saat player memuat ulang.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
                               QPushButton, QVBoxLayout)

from .job import Job

FALLBACK_LANGS = [("Indonesia", "id"), ("English", "en"), ("Otomatis", None)]

STYLE = """
QDialog { background: #14171e; }
QLabel { color: #e8ebf2; font-size: 13px; }
QLabel#phase { color: #9aa6bf; }
QLabel#field { color: #9aa6bf; font-size: 12px; }
QProgressBar { background: #1c2029; border: 1px solid #2c3242; border-radius: 7px;
               height: 16px; text-align: center; color: #e8ebf2; font-size: 11px; }
QProgressBar::chunk { background: #4a6cf7; border-radius: 6px; }
QPlainTextEdit { background: #0f1218; color: #cfd5e3; border: 1px solid #2c3242; border-radius: 8px;
                 padding: 8px; selection-background-color: #2a3350; }
QComboBox { background: #1c2029; color: #e8ebf2; border: 1px solid #2c3242; border-radius: 7px; padding: 4px 8px; }
QComboBox:disabled { color: #6f7890; }
QComboBox QAbstractItemView { background: #1c2029; color: #e8ebf2; selection-background-color: #2a3350; }
QPushButton { background: #2a3040; border: none; border-radius: 8px; padding: 6px 14px; color: #e8ebf2; }
QPushButton:hover { background: #384058; }
QPushButton#go { background: #4a6cf7; font-weight: 600; }
QPushButton#go:hover { background: #5c7cff; }
QPushButton:disabled { background: #232835; color: #6f7890; }
"""


def _local_engine() -> Callable:
    """faster-whisper lokal (paket opsional). Word-level: digabung jadi giliran."""
    from . import asr

    def local(path, language="id", model="small", on_event=None):
        try:
            return asr.transcribe(path, language, model)
        except ImportError:
            raise RuntimeError("mesin lokal butuh paket faster-whisper — pakai 'Server transkrip', "
                               "atau pasang: pip install -e .[transcribe]") from None

    local.segment_level = False
    return local


class TranskripWindow(QDialog):
    done = Signal(str)           # "" = selesai; selain itu pesan galat
    _line = Signal(str, object)  # dari thread job -> UI (pct: int | None)

    def __init__(self, folder: Path):
        super().__init__(None)
        self.folder = Path(folder)
        self._closed = False
        self.job: Job | None = None
        self.setWindowTitle(f"Buat transkrip — {self.folder.name}")
        self.setStyleSheet(STYLE)
        self.resize(680, 520)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        lay.addWidget(QLabel(f"<b>{self.folder.name}</b> — pilih setelan, lalu tekan Mulai"))

        self.lang = QComboBox()
        self.engine = QComboBox()
        self.phase = QLabel("belum mulai — belum ada yang diunggah", objectName="phase")
        self._fill_choices()

        form = QHBoxLayout()
        form.setSpacing(8)
        for label, box in (("Bahasa", self.lang), ("Mesin", self.engine)):
            cell = QVBoxLayout()
            cell.setSpacing(3)
            cell.addWidget(QLabel(label, objectName="field"))
            cell.addWidget(box)
            form.addLayout(cell, 1)
        self.go = QPushButton("Mulai", objectName="go")
        self.go.setMinimumWidth(110)
        self.go.clicked.connect(self.start)
        tail = QVBoxLayout()
        tail.setSpacing(3)
        tail.addWidget(QLabel(" ", objectName="field"))
        tail.addWidget(self.go)
        form.addLayout(tail)
        lay.addLayout(form)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("%p%")
        lay.addWidget(self.bar)

        lay.addWidget(self.phase)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFont(QFont("Consolas", 9))
        self.log.setMaximumBlockCount(4000)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.log, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = QPushButton("Tutup")
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.close_btn)
        lay.addLayout(row)

        self._line.connect(self._append)

    # --- setelan -----------------------------------------------------------------------------------
    def _fill_choices(self) -> None:
        """Bahasa + mesin dari server transkrip; kalau mati, pakai daftar bawaan."""
        from .remoteasr import server_options

        opts = server_options() or {}
        langs = [(str(x.get("label") or x["id"]), x["id"]) for x in opts.get("languages") or [] if "id" in x] \
            or FALLBACK_LANGS
        if not any(code is None for _, code in langs):
            langs.append(("Otomatis", None))
        for label, code in langs:
            self.lang.addItem(label, code)

        model = opts.get("model")
        self.engine.addItem(f"Server transkrip — {model}" if model else "Server transkrip (bawaan)", "server")
        self.engine.setItemData(0, "Unggah ke repo transkrip; modelnya dipilih di setelan aplikasi itu",
                                Qt.ItemDataRole.ToolTipRole)
        self.engine.addItem("faster-whisper lokal (small)", "local")
        self.engine.setItemData(1, "Jalan di mesin ini; butuh paket faster-whisper", Qt.ItemDataRole.ToolTipRole)
        if not opts:
            self.phase.setText("backend transkrip tidak menjawab — 'server' tetap dicoba saat Mulai")

    # --- jalan -------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.job and self.job.isRunning():
            return
        lang, engine = self.lang.currentData(), self.engine.currentData()
        for w in (self.lang, self.engine, self.go):
            w.setEnabled(False)
        self.go.setText("Berjalan…")
        self.log.clear()
        self.bar.setValue(0)
        self.bar.setStyleSheet("")
        self.phase.setText("mulai…")
        self.log.appendPlainText(f"setelan: bahasa={self.lang.currentText()}  mesin={self.engine.currentText()}")
        self.job = Job(lambda: self._work(lang, engine))
        self.job.done.connect(self._finished)
        self.job.start()

    def _work(self, lang: str | None, engine: str) -> None:
        from .remoteasr import pick_transcriber
        from .transcript import build_transcript

        transcribe = pick_transcriber() if engine == "server" else _local_engine()
        build_transcript(self.folder, lang, transcribe=transcribe, on_event=self._emit)

    def _emit(self, ev: dict) -> None:
        """Dipanggil dari thread job; antre lewat signal supaya UI disentuh dari thread Qt."""
        self._line.emit(str(ev.get("text", "")), ev.get("pct"))

    def _append(self, text: str, pct) -> None:
        if self._closed or not text:
            return
        self.log.appendPlainText(text)
        self.phase.setText(text)
        if isinstance(pct, int):
            self.bar.setValue(max(0, min(100, pct)))

    def _finished(self, err: str) -> None:
        if self._closed:
            return
        for w in (self.lang, self.engine, self.go):
            w.setEnabled(True)
        self.go.setText("Mulai ulang" if err else "Mulai")
        if err:
            self.log.appendPlainText(f"\nGAGAL: {err}")
            self.phase.setText("gagal — pesan lengkap ada di baris terakhir log")
            self.bar.setStyleSheet("QProgressBar::chunk { background: #d03b3b; }")
        else:
            self.bar.setValue(100)
            self.phase.setText("selesai")
        self.done.emit(err)

    def closeEvent(self, e) -> None:
        self._closed = True  # job tetap jalan; hasilnya dibaca player saat dibuka
        super().closeEvent(e)
