"""Draws the app icon (red record dot on a dark rounded square) and writes it as an .icns file.

Usage: python make_icon.py <out.icns>   (needs PySide6 and macOS `iconutil`)
"""
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

SIZES = [16, 32, 64, 128, 256, 512, 1024]


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    m = size * 0.06  # macOS icons keep a small margin
    p.setBrush(QColor("#1b2030"))
    p.drawRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), size * 0.22, size * 0.22)
    p.setBrush(QColor("#ff4d4f"))
    r = size * 0.24
    p.drawEllipse(QRectF(size / 2 - r, size / 2 - r, 2 * r, 2 * r))
    p.setBrush(QColor("#38c172"))  # the green of "Me" in the speaker lanes
    for i, h in enumerate((0.10, 0.18, 0.12)):
        w, x = size * 0.035, size * (0.2 + i * 0.045)
        p.drawRoundedRect(QRectF(x, size / 2 - size * h, w, size * h * 2), w / 2, w / 2)
    p.end()
    return img


def main(out: str) -> None:
    QGuiApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "meetrec.iconset")
        os.mkdir(iconset)
        for s in SIZES:
            base = draw(s)
            if s <= 512:
                base.save(os.path.join(iconset, f"icon_{s}x{s}.png"))
            if s >= 32:
                base.save(os.path.join(iconset, f"icon_{s // 2}x{s // 2}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)


if __name__ == "__main__":
    main(sys.argv[1])
