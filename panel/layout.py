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
    BLACK, BOX, BOX_X, DETAIL_BOX, DETAIL_BOX_Y_OFFSET, DETAIL_ROW_H,
    FOOTER_H, HEADER_H, HEIGHT, MARGIN, ROW_H, ROWS, TEXT_RIGHT, TEXT_X,
    WHITE, WIDTH, fonts,
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
    """One tappable rectangle. `kind` says what a tap on it does:

    - "complete"  — mark task_id done (list view, x < TEXT_X)
    - "open"      — enter detail view for task_id (list view, x >= TEXT_X)
    - "back"      — return to list view (detail view header)
    - "toggle"    — flip item_id's checked state on task_id (detail view)
    - "expand"    — un-stack the done-items summary row (detail view)
    - "collapse"  — re-stack done items back into the summary row
    - "page"      — jump to checklist page `page` (detail view, wraps around)
    """
    kind: str
    x0: int
    x1: int
    y0: int
    y1: int
    task_id: Optional[str] = None
    item_id: Optional[str] = None
    page: Optional[int] = None

    def contains(self, point: Tuple[int, int]) -> bool:
        x, y = point
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1


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


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> List[str]:
    """Greedy word-wrap to at most `max_lines`; overflow ellipsizes the last
    line rather than growing a line count the layout has no room for."""
    words = text.split()
    lines: List[str] = []
    i = 0
    while i < len(words) and len(lines) < max_lines:
        line = words[i]
        i += 1
        while i < len(words) and _text_width(draw, line + " " + words[i], font) <= max_width:
            line += " " + words[i]
            i += 1
        lines.append(line)
    if i < len(words) and lines:
        lines[-1] = _ellipsize(draw, lines[-1] + " " + " ".join(words[i:]), font, max_width)
    return lines


def _draw_check(d: ImageDraw.ImageDraw, x0: int, y0: int, size: int) -> None:
    """Short-then-long tick inside a `size`-px box — confirmation, not a
    fill, so it doesn't collide with the focused/unfocused border rule."""
    d.line(
        [
            (x0 + size * 0.18, y0 + size * 0.52),
            (x0 + size * 0.40, y0 + size * 0.75),
            (x0 + size * 0.84, y0 + size * 0.22),
        ],
        fill=BLACK, width=3, joint="curve",
    )


def _draw_disclosure(d: ImageDraw.ImageDraw, x0: int, y0: int, size: int, direction: str) -> None:
    """Small triangle in the checkbox column, marking a row that stands in
    for several others rather than being an item of its own: right-pointing
    for "tap to reveal" (expand a stack, or page to more), down-pointing for
    "tap to hide" (collapse back)."""
    if direction == "down":
        points = [
            (x0 + size * 0.15, y0 + size * 0.30),
            (x0 + size * 0.85, y0 + size * 0.30),
            (x0 + size * 0.5, y0 + size * 0.80),
        ]
    else:
        points = [
            (x0 + size * 0.22, y0 + size * 0.15),
            (x0 + size * 0.22, y0 + size * 0.85),
            (x0 + size * 0.78, y0 + size * 0.5),
        ]
    d.polygon(points, fill=BLACK)


def _strike_line(d: ImageDraw.ImageDraw, x: int, y: int, text: str, font, width: int = 2) -> None:
    """Strikethrough centered on the text's actual rendered bounds, not a
    fixed offset from its drawing origin — that drifts with font metrics
    (ascent/descent vary by size) and ends up visibly off-center."""
    bbox = d.textbbox((x, y), text, font=font)
    mid_y = (bbox[1] + bbox[3]) // 2
    d.line([(bbox[0], mid_y), (bbox[2], mid_y)], fill=BLACK, width=width)


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
    completing: Optional[str] = None,
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
        y1 = y0 + ROW_H
        # Split per row, not per task: the checkbox column completes, the
        # text opens detail — see the Hitbox docstring for why TEXT_X is
        # reused as the boundary everywhere.
        hitboxes.append(Hitbox("complete", 0, TEXT_X, y0, y1, task_id=task.id))
        hitboxes.append(Hitbox("open", TEXT_X, WIDTH, y0, y1, task_id=task.id))

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
        title_text = _ellipsize(d, task.title, f.title, TEXT_RIGHT - TEXT_X)
        d.text((TEXT_X, title_y), title_text, font=f.title, fill=BLACK)
        if task.id == completing:
            # Held for one beat before the row is dropped and the list
            # reflows — two static frames, not motion, so a tap visibly
            # registers before its row disappears from under the finger.
            _draw_check(d, BOX_X, box_y, BOX)
            _strike_line(d, TEXT_X, title_y, title_text, f.title, width=3)
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


def render_detail(
    task: Task, completing: bool = False, expanded: bool = False, page: int = 0
) -> Rendered:
    """One task, full title, and its checklist if it has one.

    Unchecked items always render individually, at the top. Done items
    collapse into a single "N done — tap to show" row unless `expanded` —
    a glance at the wall needs the open items, not eleven already-finished
    ones each taking a row. Expanding replaces that row with a "hide" row
    (tap to re-stack) followed by the done items individually, so a
    specific one can be unticked. Checklist items get one full-width tap
    zone each rather than the checkbox/text split list rows use — a leaf
    item has only one action, so there is nothing for a text-tap to "open".

    Whatever doesn't fit `page`'s worth of rows is reachable by paging, not
    silently dropped — the last row on a page becomes a "page X/Y" control
    (wrapping around) whenever there's more than one page.
    """
    f = fonts()
    img = Image.new("1", (WIDTH, HEIGHT), WHITE)
    d = ImageDraw.Draw(img)
    hitboxes: List[Hitbox] = []

    # --- header: "back" replaces the view title/count in this mode --------
    d.text((MARGIN, 20), "< BACK", font=f.header, fill=BLACK)
    d.line([(MARGIN, HEADER_H - 8), (TEXT_RIGHT, HEADER_H - 8)], fill=BLACK, width=2)
    hitboxes.append(Hitbox("back", 0, WIDTH, 0, HEADER_H))

    # --- title + the task's own checkbox -----------------------------------
    y = HEADER_H + 14
    box_y = y
    d.rectangle(
        [BOX_X, box_y, BOX_X + BOX, box_y + BOX],
        outline=BLACK, width=4 if task.focused else 2,
    )
    title_lines = _wrap(d, task.title, f.title, TEXT_RIGHT - TEXT_X, max_lines=2)
    line_y = y
    drawn_lines = []
    for line in title_lines:
        d.text((TEXT_X, line_y), line, font=f.title, fill=BLACK)
        drawn_lines.append((line, line_y))
        line_y += 30
    if completing:
        # Same held-beat confirmation as the list view's row — see render().
        _draw_check(d, BOX_X, box_y, BOX)
        for line, ly in drawn_lines:
            _strike_line(d, TEXT_X, ly, line, f.title, width=3)
    title_row_bottom = max(line_y, box_y + BOX + 8)
    # Same rule as everywhere else: only the checkbox column acts. The title
    # text has nothing further to open, so a tap there is a no-op rather
    # than a second, easy-to-hit-by-accident way to complete the task.
    hitboxes.append(Hitbox("complete", 0, TEXT_X, HEADER_H, title_row_bottom, task_id=task.id))

    # --- meta + a one-line description snippet -----------------------------
    y = title_row_bottom + 4
    meta = _meta_line(task)
    if meta:
        d.text((TEXT_X, y), _ellipsize(d, meta, f.meta, TEXT_RIGHT - TEXT_X), font=f.meta, fill=BLACK)
        y += 24
    if task.description:
        snippet = task.description.strip().splitlines()[0] if task.description.strip() else ""
        if snippet:
            d.text(
                (TEXT_X, y),
                _ellipsize(d, snippet, f.footer, TEXT_RIGHT - TEXT_X),
                font=f.footer, fill=BLACK,
            )
            y += 20

    y += 6
    d.line([(MARGIN, y), (TEXT_RIGHT, y)], fill=BLACK, width=1)
    y += 10

    # --- checklist: unchecked individually, done items stacked -------------
    fy = HEIGHT - FOOTER_H
    unchecked = [item for item in task.checklist if not item.completed]
    done = [item for item in task.checklist if item.completed]
    entries: List[Tuple[str, object]] = [("item", item) for item in unchecked]
    if done:
        if expanded:
            entries.append(("collapse", len(done)))
            entries.extend(("item", item) for item in done)
        else:
            entries.append(("stack", done))

    max_rows = max(0, (fy - y) // DETAIL_ROW_H)
    # A page whose entries don't fit reserves its last row for the pager —
    # so if paging is needed at all, each page holds one fewer real row.
    needs_paging = len(entries) > max_rows
    page_capacity = max(1, max_rows - 1) if needs_paging else max_rows
    total_pages = max(1, -(-len(entries) // page_capacity)) if page_capacity else 1
    page = max(0, min(page, total_pages - 1))
    start = page * page_capacity
    visible = entries[start:start + page_capacity]

    item_text_x = BOX_X + DETAIL_BOX + 16
    for i, (kind, payload) in enumerate(visible):
        iy0 = y + i * DETAIL_ROW_H
        iy1 = iy0 + DETAIL_ROW_H
        box_y2 = iy0 + DETAIL_BOX_Y_OFFSET
        text_y = iy0 + (DETAIL_ROW_H - 20) // 2

        if kind == "stack":
            hitboxes.append(Hitbox("expand", 0, WIDTH, iy0, iy1, task_id=task.id))
            _draw_disclosure(d, BOX_X, box_y2, DETAIL_BOX, direction="right")
            d.text(
                (item_text_x, text_y), "%d done — tap to show" % len(payload),
                font=f.meta, fill=BLACK,
            )
        elif kind == "collapse":
            hitboxes.append(Hitbox("collapse", 0, WIDTH, iy0, iy1, task_id=task.id))
            _draw_disclosure(d, BOX_X, box_y2, DETAIL_BOX, direction="down")
            d.text(
                (item_text_x, text_y), "hide %d done" % payload,
                font=f.meta, fill=BLACK,
            )
        else:
            item = payload
            hitboxes.append(
                Hitbox("toggle", 0, WIDTH, iy0, iy1, task_id=task.id, item_id=item.id)
            )
            d.rectangle(
                [BOX_X, box_y2, BOX_X + DETAIL_BOX, box_y2 + DETAIL_BOX], outline=BLACK, width=2
            )
            text = _ellipsize(d, item.title, f.meta, TEXT_RIGHT - item_text_x)
            d.text((item_text_x, text_y), text, font=f.meta, fill=BLACK)
            if item.completed:
                # Tick, not a fill — a fill is reserved for "tap here to
                # complete", and this item already is. Strikethrough too,
                # centered on the actual glyph bounds (see _strike_line).
                _draw_check(d, BOX_X, box_y2, DETAIL_BOX)
                _strike_line(d, item_text_x, text_y, text, f.meta)
        if i < len(visible) - 1 or needs_paging:
            d.line([(item_text_x, iy1), (TEXT_RIGHT, iy1)], fill=BLACK, width=1)

    if needs_paging:
        pi = len(visible)
        iy0 = y + pi * DETAIL_ROW_H
        iy1 = iy0 + DETAIL_ROW_H
        box_y2 = iy0 + DETAIL_BOX_Y_OFFSET
        text_y = iy0 + (DETAIL_ROW_H - 20) // 2
        next_page = (page + 1) % total_pages
        hitboxes.append(Hitbox("page", 0, WIDTH, iy0, iy1, task_id=task.id, page=next_page))
        _draw_disclosure(d, BOX_X, box_y2, DETAIL_BOX, direction="right")
        d.text(
            (item_text_x, text_y), "page %d/%d — tap for more" % (page + 1, total_pages),
            font=f.meta, fill=BLACK,
        )

    # --- footer: checklist progress, not a poll timestamp -------------------
    fy_line = HEIGHT - FOOTER_H
    d.line([(MARGIN, fy_line), (TEXT_RIGHT, fy_line)], fill=BLACK)
    if task.checklist:
        d.text(
            (MARGIN, fy_line + 14), "%d of %d done" % (len(done), len(task.checklist)),
            font=f.footer, fill=BLACK,
        )

    return Rendered(image=img, hitboxes=hitboxes)


def hit(hitboxes: Sequence[Hitbox], point: Tuple[int, int]) -> Optional[Hitbox]:
    for box in hitboxes:
        if box.contains(point):
            return box
    return None
