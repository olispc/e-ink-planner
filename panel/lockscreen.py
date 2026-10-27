"""The screen lock: a clock that hides the task list, and a keypad to get back.

Two faces, both static frames like everything else on this panel:

- the clock face — time, date and an optional background picture, no hint
  text: what a tap on a wall clock does is learned once. With the clock
  hidden (`clock=False`, a swipe right on the panel) it is just the picture,
  or a blank screen without one: nothing on it changes, so the panel holds
  one image for as long as you're away.
  The whole screen is one tap target ("lock_wake") that brings up...
- the keypad face — a smaller clock, one dot per digit of the code, any
  message ("Wrong code"), and a 3x4 keypad: 1-9, then cancel / 0 / delete.

Background pictures are the one place this app draws something that isn't
1-bit to begin with: a photo is greyscale by nature, so it is dithered
(Floyd–Steinberg, PIL's default for "L" -> "1") rather than thresholded,
which would turn it into a few black blobs. Text never goes through that
path — it is drawn in mode "1" straight onto the picture, as crisp as the rest
of the UI, with nothing behind it: pictures that leave the top light (sky,
in portrait) are the ones that suit the wall.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

from .layout import Hitbox, Rendered, _finish, _text_width
from .theme import (
    BLACK, LANDSCAPE, LOCK_DOT, LOCK_KEY_GAP, LOCK_KEY_H, LOCK_KEY_H_PORTRAIT,
    LOCK_KEY_W, LOCK_KEY_W_PORTRAIT, MARGIN, PORTRAIT, WHITE, fonts,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

# Keypad rows, left to right. "cancel" folds the keypad back to the clock;
# "delete" removes the last digit.
KEYS = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["cancel", "0", "delete"]]

_cache: Dict[Tuple[str, float, Tuple[int, int]], Image.Image] = {}


def pick_background(directory: Optional[Path], today: datetime) -> Optional[Path]:
    """One picture per day, cycling through the directory in name order — so
    the wall changes daily without a redraw ever swapping it mid-glance, and
    adding a picture is just dropping a file in."""
    if directory is None or not directory.is_dir():
        return None
    files = sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        return None
    return files[today.toordinal() % len(files)]


def load_background(path: Optional[Path], size: Tuple[int, int]) -> Optional[Image.Image]:
    """Crop-to-fill `size` and dither to 1-bit. Cached by path, mtime and size:
    the clock redraws every minute, and dithering a photo on a Pi Zero-class
    CPU each time would be wasted work. An unreadable file is skipped, not a
    crash — a bad download in the folder must not take the lock screen down."""
    if path is None:
        return None
    try:
        key = (str(path), path.stat().st_mtime, size)
        if key not in _cache:
            with Image.open(path) as src:
                img = ImageOps.exif_transpose(src).convert("L")
            _cache.clear()   # only today's picture is ever needed
            _cache[key] = ImageOps.fit(img, size).convert("1")
        return _cache[key].copy()
    except (OSError, ValueError):
        return None


def _draw_delete_glyph(d: ImageDraw.ImageDraw, cx: float, cy: float) -> None:
    """The usual backspace key: a tag pointing left with an x in it."""
    w, h = 40, 28
    x0, y0 = cx - w / 2, cy - h / 2
    d.polygon(
        [(x0, cy), (x0 + 12, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0 + 12, y0 + h)],
        outline=BLACK, width=2,
    )
    mx, s = x0 + 12 + (w - 12) / 2, 6
    d.line([(mx - s, cy - s), (mx + s, cy + s)], fill=BLACK, width=2)
    d.line([(mx - s, cy + s), (mx + s, cy - s)], fill=BLACK, width=2)


def _draw_keypad(
    d: ImageDraw.ImageDraw, x0: int, y0: int, kw: int, kh: int, on_picture: bool,
) -> List[Hitbox]:
    f = fonts()
    gap = LOCK_KEY_GAP
    hitboxes = []
    for r, row in enumerate(KEYS):
        for c, key in enumerate(row):
            kx, ky = x0 + c * (kw + gap), y0 + r * (kh + gap)
            cx, cy = kx + kw / 2, ky + kh / 2
            # Outlines, not black fills: a filled key would read as "pressed"
            # or "done", the same reason a focused task's checkbox isn't
            # filled. Over a picture each key is cleared to white — paper, not
            # emphasis — so the photo shows in the gaps between them.
            d.rounded_rectangle(
                (kx, ky, kx + kw, ky + kh), radius=10, outline=BLACK, width=2,
                fill=WHITE if on_picture else None,
            )
            if key == "delete":
                _draw_delete_glyph(d, cx, cy)
            elif key == "cancel":
                d.text((cx, cy), "cancel", font=f.meta, fill=BLACK, anchor="mm")
            else:
                d.text((cx, cy), key, font=f.key, fill=BLACK, anchor="mm")
            # The gaps between keys belong to no key: a tap that lands
            # between two digits is dropped rather than guessed at.
            hitboxes.append(Hitbox("lock_key", int(kx), int(kx + kw), int(ky), int(ky + kh), key=key))
    return hitboxes


def _clock_block(
    d: ImageDraw.ImageDraw, cx: int, top: int, max_w: int, now: datetime, big: bool,
    lines: List[Tuple[Optional[str], object]], on_picture: bool, centre: bool = False,
) -> List[int]:
    """Time, then date, then any further (text, font) lines, centred on cx.
    With `centre`, `top` is where the block's middle goes instead. Returns
    each row's top, for whatever else is drawn into a blank row."""
    f = fonts()
    clock_font = f.clock if big else f.clock_small
    time_text = now.strftime("%H:%M")
    if _text_width(d, time_text, clock_font) > max_w:
        clock_font = f.clock_small
    rows = [(time_text, clock_font), (now.strftime("%A %-d %B"), f.lock_date)] + lines
    heights = []
    for text, font in rows:
        # None reserves a line of that font's height and draws nothing in it;
        # "" is just the row spacing.
        l, t, r, b = d.textbbox((0, 0), "0" if text is None else (text or " "), font=font, anchor="ma")
        heights.append(b - t if text != "" else 0)
    spacing = 18
    total_h = sum(heights) + spacing * (len(rows) - 1)
    if centre:
        top -= total_h // 2
    y, tops = top, []
    for (text, font), h in zip(rows, heights):
        tops.append(y)
        if text:
            # anchor "mt" puts the text's own top at y, so the measured
            # heights above are what actually lands on the panel.
            d.text((cx, y), text, font=font, fill=BLACK, anchor="mt")
        y += h + spacing
    return tops


def _draw_dots(d: ImageDraw.ImageDraw, cx: int, y: int, entered: int, length: int) -> None:
    """One circle per digit of the code, filled as each is typed — drawn, not
    set in type, so the bundled font needing those glyphs isn't a question."""
    step = LOCK_DOT + 14
    x = cx - (length * step - 14) / 2
    for i in range(length):
        box = (x + i * step, y, x + i * step + LOCK_DOT, y + LOCK_DOT)
        if i < entered:
            d.ellipse(box, fill=BLACK)
        else:
            d.ellipse(box, outline=BLACK, width=2)


def render_lock(
    now: Optional[datetime] = None,
    portrait: bool = False,
    keypad: bool = False,
    entered: int = 0,
    code_length: int = 0,
    message: Optional[str] = None,
    background: Optional[Image.Image] = None,
    dark: bool = False,
    clock: bool = True,
) -> Rendered:
    """`background` is an already-dithered canvas-sized picture (see
    load_background), or None for a plain clock. `code_length` is 0 when no
    passcode is set, which makes the clock face's tap an unlock, not a keypad.

    Dark mode inverts a plain clock like any other screen, but not a picture
    — that would turn a photo into its negative."""
    g = PORTRAIT if portrait else LANDSCAPE
    now = now or datetime.now()
    f = fonts()
    img = background.copy() if background is not None else Image.new("1", (g.width, g.height), WHITE)
    d = ImageDraw.Draw(img)
    on_picture = background is not None

    if not keypad and not clock:
        return _finish(img, [Hitbox("lock_wake", 0, g.width, 0, g.height)], dark and not on_picture)

    if not keypad:
        # Over a picture in portrait the clock goes up top, phone-style, so it
        # doesn't sit on the middle of the photo where the subject usually is.
        # Landscape's canvas is too short for that to leave much picture free.
        centre_y = 170 if portrait and on_picture else g.height // 2
        _clock_block(
            d, g.width // 2, centre_y, g.width - 2 * MARGIN,
            now, big=True, lines=[], on_picture=on_picture,
            centre=True,
        )
        return _finish(img, [Hitbox("lock_wake", 0, g.width, 0, g.height)], dark and not on_picture)

    if portrait:
        kw, kh = LOCK_KEY_W_PORTRAIT, LOCK_KEY_H_PORTRAIT
        pad_w, pad_h = 3 * kw + 2 * LOCK_KEY_GAP, 4 * kh + 3 * LOCK_KEY_GAP
        pad_x, pad_y = (g.width - pad_w) // 2, g.height - pad_h - 40
        info_cx, info_top, info_w = g.width // 2, 40, g.width - 2 * MARGIN
    else:
        kw, kh = LOCK_KEY_W, LOCK_KEY_H
        pad_w, pad_h = 3 * kw + 2 * LOCK_KEY_GAP, 4 * kh + 3 * LOCK_KEY_GAP
        pad_x, pad_y = g.width // 2 + (g.width // 2 - pad_w) // 2, (g.height - pad_h) // 2
        info_cx, info_top, info_w = g.width // 4, 90, g.width // 2 - 2 * MARGIN

    # Room under the date for the dots row and a message line, reserved even
    # when empty so the clock doesn't jump as a message comes and goes.
    tops = _clock_block(
        d, info_cx, info_top, info_w, now, big=False,
        lines=[("", f.footer), (None, f.lock_date), (message or None, f.meta)],
        on_picture=on_picture,
    )
    # The dots go in the blank date-sized row reserved above.
    _draw_dots(d, info_cx, tops[3] + 2, entered, code_length)
    hitboxes = _draw_keypad(d, pad_x, pad_y, kw, kh, on_picture)
    return _finish(img, hitboxes, dark and not on_picture)

