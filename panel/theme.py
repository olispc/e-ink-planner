"""Geometry and type for a 800x480 1-bit panel.

Every number here is chosen against the physical panel, not a browser: the
active area is 163.2 x 97.72 mm, so 1 mm is about 4.9 px. A 62 px row is
roughly 12.6 mm — comfortably past the ~9 mm minimum for a reliable thumb tap.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import ImageFont

WIDTH, HEIGHT = 800, 480

HEADER_H = 64
FOOTER_H = 44
ROW_H = 62
ROWS = (HEIGHT - HEADER_H - FOOTER_H) // ROW_H  # 6

MARGIN = 20
BOX = 34                      # checkbox edge
BOX_X = MARGIN + 4
TEXT_X = BOX_X + BOX + 22
TEXT_RIGHT = WIDTH - MARGIN

BLACK, WHITE = 0, 1           # mode "1" pixel values

# Candidates in preference order: DejaVu ships with Raspberry Pi OS
# (fonts-dejavu-core); the rest are what a Mac has by default.
_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]
_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]


def _font(candidates, size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise RuntimeError(
        "no usable font found. On the Pi: sudo apt install fonts-dejavu-core"
    )


class Fonts:
    """Loaded once — truetype loading is slow enough to matter in a redraw."""

    def __init__(self) -> None:
        self.title = _font(_REGULAR, 25)
        self.meta = _font(_REGULAR, 17)
        self.header = _font(_BOLD, 20)
        self.header_right = _font(_REGULAR, 20)
        self.footer = _font(_REGULAR, 14)
        self.empty = _font(_REGULAR, 28)


_FONTS: Optional[Fonts] = None


def fonts() -> Fonts:
    global _FONTS
    if _FONTS is None:
        _FONTS = Fonts()
    return _FONTS
