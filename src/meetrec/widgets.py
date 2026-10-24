"""Small shared widgets: drawn icons and THE icon-only button used by every window of the app."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QPushButton, QStackedWidget, QVBoxLayout, QHBoxLayout, QWidget

FG = "#e8ebf2"

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
    elif name == "film":
        p.drawRoundedRect(QRectF(8, 12, 32, 24), 3, 3)
        p.drawLine(18, 12, 18, 36)
        p.drawLine(30, 12, 30, 36)
    elif name == "list":
        p.drawRoundedRect(QRectF(8, 9, 32, 30), 4, 4)
        for y in (19, 26, 33):
            p.drawLine(15, y, 33 if y != 33 else 27, y)
    elif name == "screen":
        p.drawRoundedRect(QRectF(7, 10, 34, 22), 3, 3)
        p.drawLine(24, 32, 24, 38)
        p.drawLine(15, 38, 33, 38)
    elif name == "window":
        p.drawRoundedRect(QRectF(7, 14, 26, 22), 3, 3)
        p.drawLine(7, 21, 33, 21)
        p.drawRoundedRect(QRectF(17, 8, 24, 18), 3, 3)
    elif name == "gear":
        import math
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(8):  # teeth
            a = k * math.pi / 4
            p.drawLine(int(24 + 11 * math.cos(a)), int(24 + 11 * math.sin(a)), int(24 + 17 * math.cos(a)), int(24 + 17 * math.sin(a)))
        p.drawEllipse(QPointF(24, 24), 11, 11)
        p.drawEllipse(QPointF(24, 24), 4.5, 4.5)
    elif name == "final":  # film strip with a plus: "generate the final video"
        p.drawRoundedRect(QRectF(6, 14, 26, 22), 3, 3)
        p.drawLine(15, 14, 15, 36)
        p.drawLine(24, 14, 24, 36)
        p.drawLine(34, 12, 34, 24)
        p.drawLine(28, 18, 40, 18)
    elif name == "check":
        p.drawPolyline([QPointF(10, 25), QPointF(20, 35), QPointF(38, 14)])
    elif name == "upload":  # arrow up out of a tray
        p.drawLine(24, 31, 24, 10)
        p.drawPolyline([P(14, 20), P(24, 10), P(34, 20)])
        p.drawPolyline([P(10, 32), P(10, 39), P(38, 39), P(38, 32)])
    elif name == "download":  # arrow down into a tray
        p.drawLine(24, 9, 24, 30)
        p.drawPolyline([P(14, 20), P(24, 30), P(34, 20)])
        p.drawPolyline([P(10, 32), P(10, 39), P(38, 39), P(38, 32)])
    elif name == "cloud":
        path = QPainterPath(P(14, 36))
        path.cubicTo(P(4, 36), P(4, 22), P(15, 22))
        path.cubicTo(P(16, 10), P(36, 10), P(36, 24))
        path.cubicTo(P(46, 24), P(46, 36), P(36, 36))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "plus":
        p.drawLine(24, 9, 24, 39)
        p.drawLine(9, 24, 39, 24)
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



class IconButton(QPushButton):
    """THE icon-only button of the panel: always an exact SIZE x SIZE square, icon centred.
    tone: "default" | "primary" (green) | "danger" (red). Every icon button in the UI is one of these."""

    SIZE = 36
    ICON = 20

    def __init__(self, icon: str, tip: str = "", tone: str = "default", checkable: bool = False):
        super().__init__()
        self.setIcon(make_icon(icon))
        self.setIconSize(QSize(self.ICON, self.ICON))
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setProperty("square", True)
        self.setProperty("tone", tone)

BUTTON_STYLE = f"""
QPushButton[square="true"] {{ padding: 0; }}
QPushButton[square="true"]:disabled {{ background: #232836; }}
QPushButton[tone="primary"] {{ background: #2ea043; }}
QPushButton[tone="primary"]:hover {{ background: #3bb653; }}
QPushButton[tone="primary"]:disabled {{ background: #1f5a2c; }}
QPushButton[tone="danger"] {{ background: #d03b3b; }}
QPushButton[tone="danger"]:hover {{ background: #e24c4c; }}
"""


class Section(QFrame):
    """A titled, bordered block (the look of the panel's sections). Put widgets/layouts into `.body`."""

    def __init__(self, title: str = ""):
        super().__init__()
        self.setObjectName("section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)
        self.title = QLabel(title, objectName="sectionTitle")
        self.title.setVisible(bool(title))
        outer.addWidget(self.title)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)


class SidebarPages(QWidget):
    """Menu on the left, page on the right. add_page(title, widget); the first page is shown first."""

    SIDEBAR_W = 150

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.menu = QListWidget(objectName="sidebar")
        self.menu.setFixedWidth(self.SIDEBAR_W)
        self.menu.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pages = QStackedWidget()
        row.addWidget(self.menu)
        row.addWidget(self.pages, 1)
        self.menu.currentRowChanged.connect(self.pages.setCurrentIndex)

    def add_page(self, title: str, page: QWidget) -> None:
        self.menu.addItem(title)
        self.pages.addWidget(page)
        if self.menu.count() == 1:
            self.menu.setCurrentRow(0)

    def titles(self) -> list[str]:
        return [self.menu.item(i).text() for i in range(self.menu.count())]

    def set_page(self, index: int) -> None:
        self.menu.setCurrentRow(index)


SECTION_STYLE = """
#section { border: 1px solid #2c3242; border-radius: 10px; background: transparent; }
#sectionTitle { font-size: 14px; font-weight: 600; }
#sidebar { background: #171b24; border: none; border-radius: 10px; padding: 6px; outline: none; }
#sidebar::item { padding: 9px 12px; border-radius: 8px; color: #cfd5e3; }
#sidebar::item:selected { background: #4a6cf7; color: #ffffff; }
#sidebar::item:hover:!selected { background: #232836; }
"""


class ProgressIconButton(IconButton):
    """An IconButton for something that takes time (upload). set_state(state, progress):
    idle    -> `idle_icon`
    running -> pause icon inside a progress ring (click = pause)
    paused  -> play icon inside a paused ring (click = resume)
    done    -> check icon, green
    error   -> `idle_icon`, red (the tooltip carries the reason)
    The ring is drawn over the normal button, so the button keeps its exact square size."""

    RING = "#4a6cf7"
    RING_PAUSED = "#e0a03a"
    TRACK = "#3a4258"

    def __init__(self, idle_icon: str, tip: str = ""):
        super().__init__(idle_icon, tip)
        self.idle_icon, self.idle_tip = idle_icon, tip
        self.state, self.progress = "idle", 0.0

    def set_state(self, state: str, progress: float = 0.0, tip: str = "") -> None:
        self.state, self.progress = state, max(0.0, min(1.0, progress))
        icon = {"running": "pause", "paused": "play", "done": "check"}.get(state, self.idle_icon)
        self.setIcon(make_icon(icon))
        tone = {"done": "primary", "error": "danger"}.get(state, "default")
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setToolTip(tip or self.idle_tip)
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.state not in ("running", "paused"):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3.5, 3.5, -3.5, -3.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(self.TRACK), 3))
        p.drawEllipse(r)
        p.setPen(QPen(QColor(self.RING if self.state == "running" else self.RING_PAUSED), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, 90 * 16, -int(self.progress * 360 * 16))  # starts at 12 o'clock, clockwise
        p.end()
