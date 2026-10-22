"""Keep the floating panel out of screen recordings, where the OS lets us."""
from __future__ import annotations

import sys


def exclude_from_capture(widget) -> bool:
    """macOS: window sharing type none. Windows: WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+).
    Linux has no equivalent, so the panel stays visible in the video there."""
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
