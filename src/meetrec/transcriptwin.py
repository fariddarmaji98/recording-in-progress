"""Jendela progres "Buat transkrip": langkah demi langkah, seperti log pemasangan.

Bukan dialog modal dan tanpa induk: player tetap terbuka dan bisa diputar selagi
transkrip berjalan. Menutup jendela hanya berhenti menonton — pekerjaannya tetap
jalan di thread daemon dan hasilnya muncul saat player memuat ulang.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout)

from .job import Job

STYLE = """
QDialog { background: #14171e; }
QLabel { color: #e8ebf2; font-size: 13px; }
QLabel#phase { color: #9aa6bf; }
QProgressBar { background: #1c2029; border: 1px solid #2c3242; border-radius: 7px;
               height: 16px; text-align: center; color: #e8ebf2; font-size: 11px; }
QProgressBar::chunk { background: #4a6cf7; border-radius: 6px; }
QPlainTextEdit { background: #0f1218; color: #cfd5e3; border: 1px solid #2c3242; border-radius: 8px;
                 padding: 8px; selection-background-color: #2a3350; }
QPushButton { background: #2a3040; border: none; border-radius: 8px; padding: 6px 14px; color: #e8ebf2; }
QPushButton:hover { background: #384058; }
"""


class TranskripWindow(QDialog):
    done = Signal(str)           # "" = selesai; selain itu pesan galat
    _line = Signal(str, object)  # dari thread job -> UI (pct: int | None)

    def __init__(self, folder: Path, language: str | None = "id"):
        super().__init__(None)
        self.folder = Path(folder)
        self.language = language
        self._closed = False
        self.setWindowTitle(f"Buat transkrip — {self.folder.name}")
        self.setStyleSheet(STYLE)
        self.resize(680, 470)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        lay.addWidget(QLabel(f"<b>{self.folder.name}</b> — langkah yang sedang berjalan"))

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFormat("%p%")
        lay.addWidget(self.bar)

        self.phase = QLabel("menyiapkan…", objectName="phase")
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
        self.job = Job(self._work)
        self.job.done.connect(self._finished)
        self.job.start()

    def _work(self) -> None:
        from .transcript import build_transcript

        build_transcript(self.folder, self.language, on_event=self._emit)

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
