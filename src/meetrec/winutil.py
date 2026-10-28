"""Keep the floating panel out of screen recordings, where the OS lets us."""
from __future__ import annotations

import os
import sys


def exclude_from_capture(widget) -> bool:
    """macOS: window sharing type none. Windows: WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+).
    Linux has no equivalent, so the panel stays visible in the video there.

    `MEETREC_CAPTURE_VISIBLE=1` membalik niatnya: panel TIDAK disembunyikan dari tangkapan
    layar, jadi orang lain di Zoom/Meet ikut melihat panelnya (dipakai launcher mode share).
    """
    if os.environ.get("MEETREC_CAPTURE_VISIBLE", "").strip() not in ("", "0", "false", "False"):
        return False
    try:
        from PySide6.QtGui import QGuiApplication

        # winId() is only a real native handle on the matching Qt platform (not offscreen/minimal/xcb)
        plat = QGuiApplication.platformName()
        if not ((sys.platform == "darwin" and plat == "cocoa") or (sys.platform == "win32" and plat == "windows")):
            return False
        wid = int(widget.winId())
        if sys.platform == "darwin":
            import objc

            objc.objc_object(c_void_p=wid).window().setSharingType_(0)  # NSWindowSharingNone
            return True
        if sys.platform == "win32":
            import ctypes

            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x11))
    except Exception:
        return False
    return False


def set_close_enabled(widget, enabled: bool) -> bool:
    """Grey out / restore the window's native close button (macOS red dot, Windows system menu).
    Used while recording; closeEvent also refuses, so other platforms are covered too."""
    try:
        from PySide6.QtGui import QGuiApplication

        plat = QGuiApplication.platformName()
        if not ((sys.platform == "darwin" and plat == "cocoa") or (sys.platform == "win32" and plat == "windows")):
            return False
        wid = int(widget.winId())
        if sys.platform == "darwin":
            import objc

            win = objc.objc_object(c_void_p=wid).window()
            btn = win.standardWindowButton_(0)  # NSWindowCloseButton
            if btn is None:
                return False
            btn.setEnabled_(bool(enabled))
            return True
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = wid
        menu = user32.GetSystemMenu(hwnd, False)
        SC_CLOSE, MF_BYCOMMAND, MF_GRAYED, MF_ENABLED = 0xF060, 0x0, 0x1, 0x0
        return bool(user32.EnableMenuItem(menu, SC_CLOSE, MF_BYCOMMAND | (MF_ENABLED if enabled else MF_GRAYED)) != -1)
    except Exception:
        return False
