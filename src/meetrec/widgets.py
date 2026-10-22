"""Small shared widgets: drawn icons and THE icon-only button used by every window of the app."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QPushButton

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
QPushButton[tone="primary"] {{ background: #2ea043; }}
QPushButton[tone="primary"]:hover {{ background: #3bb653; }}
QPushButton[tone="primary"]:disabled {{ background: #1f5a2c; }}
QPushButton[tone="danger"] {{ background: #d03b3b; }}
QPushButton[tone="danger"]:hover {{ background: #e24c4c; }}
"""
