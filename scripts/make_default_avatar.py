"""Draw grocery_planner/assets/default_avatar.png (GFP-47's default avatar).

The asset is generated rather than hand-drawn so it is reproducible: the shape
is defined here in code, reviewable in a diff, and re-derivable if the file is
ever lost or needs a different size. Run it after editing:

    .venv\\Scripts\\python.exe scripts/make_default_avatar.py

Design notes, since "a grey blob" has more constraints than it looks:

* **Neutral, and neutral about people.** A default avatar stands in for every
  client a nutritionist has. It is a plain head-and-shoulders silhouette --
  no hair, no features, no skin tone, nothing that reads as a particular
  person, sex or age. Anything more specific would be wrong for most clients.
* **Legible on both themes.** The disc is a mid-tone so it holds its edge
  against the light chart surface and the dark one alike, rather than being
  tuned for whichever theme was open when it was drawn.
* **Transparent outside the disc**, so a caller can round it, badge it, or sit
  it on any background without a rectangle of the wrong grey showing.
* **Drawn oversized (512 px) and scaled down** by the widget: downscaling a
  large render stays crisp on a HiDPI display, where a 56 px source would be
  visibly soft.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap

SIZE = 512
DISC_COLOUR = "#7c828c"      # mid neutral: holds an edge on light and dark
FIGURE_COLOUR = "#f4f5f6"    # near-white, so the silhouette reads at any size

OUTPUT = Path(__file__).resolve().parents[1] / "grocery_planner" / "assets" / "default_avatar.png"


def draw(size: int = SIZE) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)

    painter.setBrush(QColor(DISC_COLOUR))
    painter.drawEllipse(0, 0, size, size)

    # Everything below is clipped to the disc, so the shoulders meet its edge
    # cleanly instead of being cut off by the bounding box.
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(clip)

    painter.setBrush(QColor(FIGURE_COLOUR))
    unit = size / 512.0
    # Head: sits above centre so the shoulders have room without crowding.
    painter.drawEllipse(QRectF(168 * unit, 112 * unit, 176 * unit, 176 * unit))
    # Shoulders: a wide ellipse running off the bottom of the disc, which
    # reads as a torso rather than a second, smaller circle.
    painter.drawEllipse(QRectF(96 * unit, 330 * unit, 320 * unit, 300 * unit))

    painter.end()
    return pixmap


def main() -> int:
    # A QGuiApplication has to exist before QPixmap will paint.
    app = QGuiApplication.instance() or QGuiApplication([])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if not draw().save(str(OUTPUT), "PNG"):
        print(f"failed to write {OUTPUT}", file=sys.stderr)
        return 1
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
