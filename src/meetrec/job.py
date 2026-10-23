"""Run one slow function on a plain daemon thread and report back on the Qt thread.

A daemon thread (not QThread) so closing a window or quitting the app never waits for it.
"""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, Signal


class Job(QObject):
    done = Signal(str)        # error text; "" = ok. The return value of fn is in .result
    progress = Signal(float)  # 0..1, only when report=True and fn calls the function it was given

    def __init__(self, fn: Callable, *, report: bool = False):
        super().__init__()
        self.fn = fn
        self.result = None
        self._report = report
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="meetrec-job")
        self._thread.start()

    def isRunning(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        try:
            self.result = self.fn(self.progress.emit) if self._report else self.fn()
        except Exception as e:
            self.done.emit(str(e) or e.__class__.__name__)
        else:
            self.done.emit("")
