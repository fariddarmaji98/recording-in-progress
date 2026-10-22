"""Open application windows: list them (for the "record an application" picker) and follow one while recording.

Rectangles are in the same coordinates mss uses for the screen (macOS: points, Windows/Linux: pixels).
Window recording crops the screen to the window's rectangle, so the window must stay visible (not covered).

macOS: Quartz (needs Screen Recording permission for window titles). Windows: ctypes (Win32).
Linux: X11 through python-xlib; on Wayland no windows are listed.
Every function returns an empty result instead of raising, so the picker degrades to "whole screen only".
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

MIN_W, MIN_H = 160, 100


@dataclass
class WindowInfo:
    id: int
    app: str
    title: str
    left: int
    top: int
    width: int
    height: int

    @property
    def label(self) -> str:
        title = (self.title or "").strip()
        return f"{self.app}  ·  {title}" if title else self.app

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)


def list_windows() -> list[WindowInfo]:
    try:
        if sys.platform == "darwin":
            wins = _mac_list()
        elif sys.platform == "win32":
            wins = _win_list()
        else:
            wins = _x11_list()
    except Exception:
        return []
    return [w for w in wins if w.width >= MIN_W and w.height >= MIN_H]


def window_rect(win_id: int) -> tuple[int, int, int, int] | None:
    """Current (left, top, width, height) of one window, or None when it is gone."""
    try:
        if sys.platform == "darwin":
            return _mac_rect(win_id)
        if sys.platform == "win32":
            return _win_rect(win_id)
        return _x11_rect(win_id)
    except Exception:
        return None


# --- macOS -------------------------------------------------------------------------------------------
def _mac_bounds(b) -> tuple[int, int, int, int]:
    return int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"])


def _mac_list() -> list[WindowInfo]:
    from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListExcludeDesktopElements,
                        kCGWindowListOptionOnScreenOnly)

    out = []
    for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                                        kCGNullWindowID) or []:
        if w.get("kCGWindowLayer", 0) != 0 or w.get("kCGWindowOwnerPID") == os.getpid():
            continue  # menu bar, dock, overlays... and our own windows
        app = str(w.get("kCGWindowOwnerName") or "")
        if not app or not w.get("kCGWindowBounds"):
            continue
        x, y, wd, ht = _mac_bounds(w["kCGWindowBounds"])
        out.append(WindowInfo(int(w["kCGWindowNumber"]), app, str(w.get("kCGWindowName") or ""), x, y, wd, ht))
    return out


def _mac_rect(win_id: int):
    from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionIncludingWindow

    info = CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow, win_id) or []
    return _mac_bounds(info[0]["kCGWindowBounds"]) if info else None


# --- Windows -----------------------------------------------------------------------------------------
def _win_frame(hwnd):
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    # extended frame bounds = what you see, without the invisible resize border
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _win_app(hwnd) -> str:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(len(buf))
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.splitext(os.path.basename(buf.value))[0]
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    return ""


def _win_list() -> list[WindowInfo]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[WindowInfo] = []
    me = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        cloaked = ctypes.c_int(0)  # windows on other virtual desktops / hidden UWP shells
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if cloaked.value or pid.value == me:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        x, y, w, h = _win_frame(hwnd)
        found.append(WindowInfo(int(hwnd), _win_app(hwnd), buf.value, x, y, w, h))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _win_rect(win_id: int):
    import ctypes

    if not ctypes.windll.user32.IsWindow(win_id) or ctypes.windll.user32.IsIconic(win_id):
        return None
    return _win_frame(win_id)


# --- Linux / X11 -------------------------------------------------------------------------------------
def _x11_windows():
    from Xlib import X, display

    d = display.Display()
    root = d.screen().root
    prop = root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    return d, (prop.value if prop else [])


def _x11_info(d, wid) -> WindowInfo | None:
    win = d.create_resource_object("window", wid)
    name = win.get_full_property(d.intern_atom("_NET_WM_NAME"), 0) or win.get_full_property(d.intern_atom("WM_NAME"), 0)
    title = name.value.decode("utf-8", "replace") if name and isinstance(name.value, bytes) else str(name.value) if name else ""
    cls = win.get_wm_class()
    geo = win.get_geometry()
    pos = win.translate_coords(d.screen().root, 0, 0)  # window origin on the screen
    return WindowInfo(int(wid), (cls[1] if cls else ""), title, -pos.x, -pos.y, geo.width, geo.height)


def _x11_list() -> list[WindowInfo]:
    d, ids = _x11_windows()
    out = []
    for wid in ids:
        info = _x11_info(d, wid)
        if info and info.app != "meetrec":
            out.append(info)
    return out


def _x11_rect(win_id: int):
    from Xlib import display

    d = display.Display()
    info = _x11_info(d, win_id)
    return info.rect if info else None
