"""Turning the UI onto a panel that is mounted sideways or upside down.

The UI is always drawn upright on a *logical* canvas — 800x480 for rotation 0
and 180, 480x800 for 90 and 270 — and only the two edges of the app know about
rotation: the display turns the finished image onto the panel's native 800x480,
and the touch reader turns raw touch points back the other way. Layout, hitboxes
and `Panel` never see it, so a tap lands on the same rectangle it was drawn in.

`rotation` is how far the panel itself is turned clockwise from its native
landscape orientation, as you look at it mounted — so the image on it is turned
the opposite way: at 270 the UI is turned 90 degrees clockwise on the panel
(its top at the panel's right edge), at 90 anticlockwise. TODO(oli): confirm on
the real panel that this matches how it was mounted (corner taps settle it).

Both directions live here, side by side, because they must agree exactly: the
test round-trips a marked pixel through the image transform and the point
transform for every rotation.
"""
from __future__ import annotations

from typing import Tuple

from PIL import Image

from .theme import HEIGHT, WIDTH

ROTATIONS = (0, 90, 180, 270)

# What the header's rotate button toggles to from landscape: the UI turned 90
# degrees clockwise (rotation 270), the panel mounted turned anticlockwise to
# read it upright. The other portrait (90) and upside-down (180) stay reachable
# through PANEL_ROTATION, but the button never goes there.
PORTRAIT_TURN = 270

_TRANSPOSE = {
    90: Image.Transpose.ROTATE_90,    # turning the panel cw = turning the image ccw
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


def is_portrait(rotation: int) -> bool:
    return rotation in (90, 270)


def to_panel_image(image: Image.Image, rotation: int) -> Image.Image:
    """The logical image as the panel's native 800x480 frame wants it.
    Lossless on 1-bit — no resampling, just moving pixels."""
    return image if rotation == 0 else image.transpose(_TRANSPOSE[rotation])


def to_logical_point(x: int, y: int, rotation: int) -> Tuple[int, int]:
    """A point in the panel's native 800x480 frame (what the touch chip
    reports) as the logical canvas coordinate that was drawn under it."""
    if rotation == 90:
        return HEIGHT - 1 - y, x
    if rotation == 180:
        return WIDTH - 1 - x, HEIGHT - 1 - y
    if rotation == 270:
        return y, WIDTH - 1 - x
    return x, y
