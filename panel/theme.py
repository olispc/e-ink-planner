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

# TEXT_X is the tap-zone boundary everywhere in the app, not just list-row
# geometry: x < TEXT_X always means "the checkbox column" (complete a task,
# toggle a checklist item, or — in the header — go back), x >= TEXT_X always
# means "the text" (open a task's detail, or nothing further for a leaf
# checklist item). One boundary, reused, keeps the tap model legible.

# Checklist rows in the detail view get their own, smaller geometry: no
# meta sub-line under each item, so more of them fit per screen than a task
# row needs.
DETAIL_ROW_H = 46
DETAIL_BOX = 24
DETAIL_BOX_Y_OFFSET = (DETAIL_ROW_H - DETAIL_BOX) // 2

# A star to toggle isFocusedToday from the panel itself, not just the phone.
# Reserved as its own column at the right edge — a third tap zone per row,
# distinct from complete (left) and open (middle) — rather than overloading
# either with a gesture (tap-vs-long-press is exactly the kind of fragile
# distinction this app has avoided so far).
STAR_SIZE = 26
STAR_ZONE_W = 60
LIST_TEXT_RIGHT = TEXT_RIGHT - STAR_ZONE_W  # title/meta ellipsis width in render()

# Detail view reserves the same kind of column in its title block instead —
# the title can wrap to 2 lines, so this trims _wrap()'s width rather than
# competing for a fixed corner.
DETAIL_STAR_ZONE_W = 56

# List-view header controls: home (tap the view title), a focus-sticky
# toggle, sort direction (newest/oldest by created date), and area/project
# grouping. All fit in the existing single header row — no extra height
# taken from the 6 body rows, which are already scarce.
FOCUS_STAR_SIZE = 20           # header-scale — smaller than a row's own star
SORT_ARROW_SIZE = 20           # one button, shows whichever direction is on
GROUP_TOGGLE_BOX = 28          # small icon boxes, heavier border when on
GROUP_ICON_SIZE = 16           # the folder/layers icon drawn inside those
SYNC_ICON_SIZE = 12            # deliberately smaller than the 20px controls —
                               # "updated HH:MM" is status, not a 5th toggle

# A group divider (area or project name) is half a task row — organizational
# text, not a tappable item, so it doesn't need a task row's full height.
GROUP_HEADER_H = ROW_H // 2

# The footer collapses to a bare rule when there's nothing it needs to say —
# "updated HH:MM" lives in the header instead (see render()) — but keeps its
# full height for the OFFLINE warning, which is the one thing this app
# promises never to make hard to notice.
COLLAPSED_FOOTER_H = 10

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
