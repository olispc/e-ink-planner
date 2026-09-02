"""Draw the task list, and say where each row landed so taps can be mapped.

Rendering happens in PIL mode "1". That is deliberate: a 1-bit image gets no
antialiasing, which is what the panel can actually show. Drawing in greyscale
and thresholding afterwards looks worse — the glyph edges turn to mud.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from .client import Task
from .theme import (
    BLACK, BOX, BOX_X, FOOTER_H, HEADER_H, HEIGHT, MARGIN, ROW_H, ROWS,
    TEXT_RIGHT, TEXT_X, WHITE, WIDTH, fonts,
)

VIEW_TITLES = {
    "next": "NEXT ACTIONS",
    "inbox": "INBOX",
    "waiting": "WAITING ON",
    "someday": "SOMEDAY",
    "focus": "TODAY",
}

EMPTY_MESSAGES = {
    "focus": "Nothing on today.",
    "next": "Nothing next. Good.",
    "inbox": "Inbox empty.",
}


@dataclass(frozen=True)
class Hitbox:
    task_id: str
    y0: int
    y1: int

    def contains(self, y: int) -> bool:
        return self.y0 <= y < self.y1


@dataclass(frozen=True)
class Rendered:
    image: Image.Image
    hitboxes: List[Hitbox]


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_width(draw, text[:mid] + ellipsis, font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ellipsis


def _meta_line(task: Task) -> str:
    """Project / area first, then contexts. Skipped entirely when empty."""
    bits = []
    if task.project:
        bits.append(task.project)
    elif task.area:
        bits.append(task.area)
    bits.extend(task.contexts)
    return "  ·  ".join(bits)


def render(
    tasks: Sequence[Task],
    view: str,
    total: Optional[int] = None,
    now: Optional[datetime] = None,
    stale_since: Optional[datetime] = None,
) -> Rendered:
    now = now or datetime.now()
    f = fonts()
    img = Image.new("1", (WIDTH, HEIGHT), WHITE)
    d = ImageDraw.Draw(img)

    visible = list(tasks)[:ROWS]
    total = len(tasks) if total is None else total

    # --- header -----------------------------------------------------------
    d.text((MARGIN, 20), VIEW_TITLES.get(view, view.upper()), font=f.header, fill=BLACK)
    right = now.strftime("%a %-d %b")
    # In the focus view the useful number is what you chose for today, not the
    # size of the backlog underneath it.
    headline = sum(1 for t in visible if t.focused) if view == "focus" else total
    if headline:
        right = "%d  ·  %s" % (headline, right)
    d.text(
        (TEXT_RIGHT - _text_width(d, right, f.header_right), 21),
        right, font=f.header_right, fill=BLACK,
    )
    d.line([(MARGIN, HEADER_H - 8), (TEXT_RIGHT, HEADER_H - 8)], fill=BLACK, width=2)

    # --- rows -------------------------------------------------------------
    hitboxes: List[Hitbox] = []
    if not visible:
        message = EMPTY_MESSAGES.get(view, "Nothing here.")
        d.text(
            ((WIDTH - _text_width(d, message, f.empty)) // 2, HEIGHT // 2 - 24),
            message, font=f.empty, fill=BLACK,
        )
    for i, task in enumerate(visible):
        y0 = HEADER_H + i * ROW_H
        hitboxes.append(Hitbox(task.id, y0, y0 + ROW_H))

        box_y = y0 + (ROW_H - BOX) // 2
        # Emphasis is a heavier border, never a fill: a filled checkbox reads as
        # "done" in every interface anyone has ever used, and this one is the
        # tap target for completing the task.
        d.rectangle(
            [BOX_X, box_y, BOX_X + BOX, box_y + BOX],
            outline=BLACK, width=4 if task.focused else 2,
        )

        meta = _meta_line(task)
        title_y = y0 + (12 if meta else 20)
        d.text(
            (TEXT_X, title_y),
            _ellipsize(d, task.title, f.title, TEXT_RIGHT - TEXT_X),
            font=f.title, fill=BLACK,
        )
        if meta:
            d.text(
                (TEXT_X, title_y + 30),
                _ellipsize(d, meta, f.meta, TEXT_RIGHT - TEXT_X),
                font=f.meta, fill=BLACK,
            )
        if i < len(visible) - 1:
            # A heavier full-width rule marks where today's chosen work ends
            # and the backlog filling the remaining rows begins.
            boundary = task.focused and not visible[i + 1].focused
            d.line(
                [(MARGIN if boundary else TEXT_X, y0 + ROW_H), (TEXT_RIGHT, y0 + ROW_H)],
                fill=BLACK, width=2 if boundary else 1,
            )

    # --- footer -----------------------------------------------------------
    fy = HEIGHT - FOOTER_H
    d.line([(MARGIN, fy), (TEXT_RIGHT, fy)], fill=BLACK)
    left = "updated %s" % now.strftime("%H:%M")
    overflow = total - len(visible)
    if overflow > 0:
        left += "   ·   %d more" % overflow
    d.text((MARGIN, fy + 14), left, font=f.footer, fill=BLACK)

    if stale_since is not None:
        # The image persists with the power off, so a dead network would
        # otherwise show confidently wrong data forever. Say so on the glass.
        warn = "OFFLINE since %s" % stale_since.strftime("%H:%M")
        d.text(
            (TEXT_RIGHT - _text_width(d, warn, f.footer), fy + 14),
            warn, font=f.footer, fill=BLACK,
        )

    return Rendered(image=img, hitboxes=hitboxes)


def hit(hitboxes: Sequence[Hitbox], point: Tuple[int, int]) -> Optional[str]:
    _, y = point
    for box in hitboxes:
        if box.contains(y):
            return box.task_id
    return None
