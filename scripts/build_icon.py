"""Render the vector artwork and a multi-resolution Windows shortcut icon."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import struct
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QFontDatabase
from PySide6.QtSvg import QSvgRenderer


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    # Offscreen Windows rendering does not always discover installed fonts.
    for name in ("arial.ttf", "arialbd.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    assets = Path(__file__).resolve().parents[1] / "src/jhr_chiffrage/assets"
    renderer = QSvgRenderer(str(assets / "chiffrage.svg"))
    assert renderer.isValid()
    entries = []
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        canvas = QImage(size, size, QImage.Format_ARGB32)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        renderer.render(painter)
        painter.end()
        if size == 512:
            assert canvas.save(str(assets / "chiffrage.png"))
            continue
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        assert canvas.save(buffer, "PNG")
        entries.append((size, bytes(buffer.data())))
    offset = 6 + 16 * len(entries)
    directory, payload = [], []
    for size, data in entries:
        dimension = 0 if size == 256 else size
        directory.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset))
        payload.append(data)
        offset += len(data)
    (assets / "chiffrage.ico").write_bytes(struct.pack("<HHH", 0, 1, len(entries)) + b"".join(directory + payload))
    print("SVG rendered; PNG and 7-resolution ICO created.")


if __name__ == "__main__":
    main()
