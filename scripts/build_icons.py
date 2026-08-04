"""Rasterise packaging/icon.svg into the icons the platforms need (GFP-158).

Run after editing the SVG:

    python scripts/build_icons.py

The outputs are CHECKED IN. A build machine should never need a rasteriser to
produce an application icon -- the macOS and Windows release jobs run
PyInstaller and nothing else, and adding an image toolchain to them would be a
new way for a release to fail for a reason unrelated to the code.

Qt does the rendering, which is why this needs no new dependency: PySide6 is
already a build requirement for the GUI, its SVG renderer handles the flat
shapes in icon.svg, and QImageWriter supports both ``ico`` and ``icns``
natively. That was worth checking rather than assuming -- the obvious
alternative, Pillow, cannot write icns at all.
"""
from __future__ import annotations

import pathlib
import sys

#: Sizes baked into the .ico. 16 and 32 are the ones that actually get used
#: (taskbar, title bar, Explorer list view); the larger entries exist so
#: Windows never has to upscale.
ICO_SIZES = (16, 32, 48, 64, 128, 256)

#: macOS asks for the big ones. 1024 is what Retina uses in Finder previews.
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)

#: Rendered separately for the README and for the in-app window icon, where a
#: plain PNG is simpler than pulling a frame out of an .ico at run time.
PNG_SIZES = (32, 64, 128, 256, 512)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "packaging" / "icon.svg"
OUT = ROOT / "packaging" / "icons"


def render(renderer, size: int):
    """One square frame of the SVG at ``size``, transparent behind it."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    # Smooth transforms matter most at 16px, which is where an unantialiased
    # edge on the fat cap turns into a jagged white line.
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    return image


def main() -> int:
    from PySide6.QtGui import QImageWriter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    if not SOURCE.exists():
        print(f"missing {SOURCE}", file=sys.stderr)
        return 1

    # A QGuiApplication is required before any QImage work; constructing it is
    # cheap and this script is never on a hot path.
    app = QApplication([])
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        print(f"{SOURCE} is not valid SVG", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for size in PNG_SIZES:
        target = OUT / f"icon-{size}.png"
        render(renderer, size).save(str(target), "PNG")
        written.append(target.name)

    # Multi-frame containers. QImageWriter takes one image, so the largest
    # frame is written and the platform downscales -- which is acceptable
    # because the SVG is drawn to stay legible when it does.
    for name, fmt, size in (
        ("icon.ico", "ICO", max(ICO_SIZES)),
        ("icon.icns", "ICNS", max(ICNS_SIZES)),
    ):
        target = OUT / name
        writer = QImageWriter(str(target), fmt.lower().encode())
        if not writer.write(render(renderer, size)):
            print(f"could not write {name}: {writer.errorString()}", file=sys.stderr)
            return 1
        written.append(target.name)

    print("wrote:", ", ".join(written))
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
