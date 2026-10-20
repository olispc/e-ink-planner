"""Draw the task list, and say where each row landed so taps can be mapped.

Rendering happens in PIL mode "1". That is deliberate: a 1-bit image gets no
antialiasing, which is what the panel can actually show. Drawing in greyscale
and thresholding afterwards looks worse — the glyph edges turn to mud.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageOps

from .client import Task
from .rotation import is_portrait
from .theme import (
    BLACK, BOX, BOX_X, COLLAPSED_FOOTER_H, DETAIL_BOX, DETAIL_BOX_Y_OFFSET,
    DETAIL_DESC_LINE_H, DETAIL_DESC_MIN_LINES_WITH_CHECKLIST, DETAIL_ITEM_ARROW_W,
    DETAIL_ITEM_LINE_H, DETAIL_ROW_H,
    DETAIL_STAR_ZONE_W, FOCUS_STAR_SIZE, FOOTER_H,
    GROUP_HEADER_H, GROUP_ICON_SIZE, GROUP_TOGGLE_BOX, MARGIN, PAGER_BOX, PAGER_H, ROW_H, ROWS, SORT_ARROW_SIZE, STAR_SIZE,
    LANDSCAPE, PORTRAIT, PORTRAIT_HEADER_ROW_H, STAR_ZONE_W, TEXT_X, WHITE, Geometry, fonts,
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
    - "toggle"    — flip item_id's checked state on task_id (detail view,
                    checkbox column only)
    - "expand_item" — show item_id's full wrapped title, or fold it back to
                    one line; `page` is the checklist page it lands on after
    - "expand"    — un-stack the done-items summary row (detail view)
    - "collapse"  — re-stack done items back into the summary row
    - "page"      — jump to checklist page `page` (detail view, wraps around)
    - "star"      — flip task_id's isFocusedToday (list row or detail title)
    - "list_page" — jump to list page `page` (list view, wraps around)
    - "home"      — list page back to 0, sort/grouping toggles untouched
    - "focus_sticky"      — flip whether starred-today tasks pin to the top
    - "sort_toggle"       — flip sort_newest direction
    - "toggle_area_view"  — flip area grouping
    - "toggle_project_view" — flip project grouping
    - "toggle_due_first"  — flip whether due-dated backlog tasks lead
    - "toggle_dark"       — flip dark mode (whole screen inverted)
    - "toggle_touch_lock" — flip the touch lock; the only thing a tap does
                            while it's on
    - "lock_screen"       — hide the tasks behind the lock screen
    - "toggle_rotate"     — flip between landscape and portrait (full refresh)

    And on the lock screen (panel/lockscreen.py), the only boxes it has:

    - "lock_wake"         — the whole clock face: bring up the keypad
    - "lock_key"          — one keypad key; `key` is "0"-"9", "cancel" or
                            "delete"
    """
    kind: str
    x0: int
    x1: int
    y0: int
    y1: int
    task_id: Optional[str] = None
    item_id: Optional[str] = None
    page: Optional[int] = None
    key: Optional[str] = None

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


def _wrap_paragraphs(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    """Word-wrap the full text with no line cap, preserving the author's own
    line breaks as paragraph boundaries — description text is free-form
    notes, not one run-on sentence, so a blank line in it should stay a
    blank line rather than getting swallowed into the wrap. Unlike _wrap(),
    this never truncates; the caller decides how much of the result fits."""
    lines: List[str] = []
    for raw_line in text.strip("\n").splitlines():
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = current + " " + word
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
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
    """Small triangle marking a row that stands in for several others
    rather than being an item of its own: right for "tap to reveal" (expand
    a stack) or "next page", left for "previous page", down for "tap to
    hide" (collapse back). Up folds a wrapped checklist item back to one line."""
    if direction == "up":
        points = [
            (x0 + size * 0.15, y0 + size * 0.70),
            (x0 + size * 0.85, y0 + size * 0.70),
            (x0 + size * 0.5, y0 + size * 0.20),
        ]
    elif direction == "down":
        points = [
            (x0 + size * 0.15, y0 + size * 0.30),
            (x0 + size * 0.85, y0 + size * 0.30),
            (x0 + size * 0.5, y0 + size * 0.80),
        ]
    elif direction == "left":
        points = [
            (x0 + size * 0.78, y0 + size * 0.15),
            (x0 + size * 0.78, y0 + size * 0.85),
            (x0 + size * 0.22, y0 + size * 0.5),
        ]
    else:
        points = [
            (x0 + size * 0.22, y0 + size * 0.15),
            (x0 + size * 0.22, y0 + size * 0.85),
            (x0 + size * 0.78, y0 + size * 0.5),
        ]
    d.polygon(points, fill=BLACK)


def _pack_pages(heights: Sequence[int], room: int) -> Tuple[List[int], int, bool]:
    """Bin-pack checklist rows of varying height into pages, in order.
    Returns each row's page, the page count, and whether a pager is needed.
    With all rows the same height this is the old fixed rows-per-page split."""
    needs_paging = sum(heights) > room
    capacity = room - PAGER_H if needs_paging else room
    page_of: List[int] = []
    page, used = 0, 0
    for h in heights:
        if used and used + h > capacity:
            page, used = page + 1, 0
        page_of.append(page)
        used += h
    return page_of, (page + 1 if heights else 1), needs_paging


def _draw_detail_pager(
    d: ImageDraw.ImageDraw, g: Geometry, y0: int, y1: int, page: int, total_pages: int, task_id: str
) -> List[Hitbox]:
    """The detail view's bidirectional "page X/Y" row — split left/right
    into independent previous/next zones, each wrapping at its own end.
    Shared by checklist pagination and description pagination: same scale
    (DETAIL_BOX/DETAIL_ROW_H), same visual language, just different content
    above it, so a task never needs to learn two different pager controls."""
    f = fonts()
    box_y = y0 + (y1 - y0 - DETAIL_BOX) // 2
    text_y = y0 + (y1 - y0 - 20) // 2
    mid = g.width // 2
    prev_page = (page - 1) % total_pages
    next_page = (page + 1) % total_pages
    hitboxes = [
        Hitbox("page", 0, mid, y0, y1, task_id=task_id, page=prev_page),
        Hitbox("page", mid, g.width, y0, y1, task_id=task_id, page=next_page),
    ]
    _draw_disclosure(d, BOX_X, box_y, DETAIL_BOX, direction="left")
    _draw_disclosure(d, g.text_right - DETAIL_BOX, box_y, DETAIL_BOX, direction="right")
    label = "page %d/%d" % (page + 1, total_pages)
    d.text(((g.width - _text_width(d, label, f.meta)) // 2, text_y), label, font=f.meta, fill=BLACK)
    return hitboxes


def _draw_star(d: ImageDraw.ImageDraw, cx: int, cy: int, size: int, filled: bool) -> None:
    """Five-point star, filled when starred. Unlike the checkbox, a filled
    star doesn't collide with "no fills mean done" — different shape,
    different and unambiguous meaning everywhere this convention appears."""
    r_outer, r_inner = size / 2, size / 2 * 0.42
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    if filled:
        d.polygon(points, fill=BLACK)
    else:
        d.polygon(points, outline=BLACK, width=2)


def _draw_arrow(d: ImageDraw.ImageDraw, cx: int, cy: int, size: int, up: bool) -> None:
    """Sort direction, one button: shows whichever direction is currently
    active (newest-first = up, oldest-first = down). Filled, since there's
    no "other" arrow alongside it to contrast against."""
    half = size / 2
    if up:
        points = [(cx, cy - half), (cx - half, cy + half), (cx + half, cy + half)]
    else:
        points = [(cx, cy + half), (cx - half, cy - half), (cx + half, cy - half)]
    d.polygon(points, fill=BLACK)


def _draw_folder_icon(d: ImageDraw.ImageDraw, x0: int, y0: int, size: int) -> None:
    """Mindwtr's own project icon (lucide-react's `Folder`, confirmed
    against apps/desktop/src/components/views/projects/ProjectsSidebar.tsx)
    — a recognizable link to the app this panel is a window onto, not an
    invented glyph."""
    w, h = size, size * 0.8
    tab_w, tab_h = w * 0.5, h * 0.28
    d.rectangle([x0, y0 + tab_h, x0 + w, y0 + h], outline=BLACK, width=2)
    d.rectangle([x0, y0, x0 + tab_w, y0 + tab_h + 1], outline=BLACK, width=2)


def _draw_layers_icon(d: ImageDraw.ImageDraw, x0: int, y0: int, size: int) -> None:
    """Areas have no single fixed icon in Mindwtr — each one carries a
    user-chosen emoji there (AreaManagerModal), which a 1-bit DejaVu render
    can't reproduce anyway. This custom glyph — three stacked flattened
    diamonds, distinct shapes with real gaps rather than overlapping
    rectangles that blur together at this size — echoes the `Layers` icon
    Mindwtr's own area filter already uses, without claiming to be any
    specific area's actual icon."""
    w, h = size, size * 0.34
    gap = size * 0.3
    for i in range(3):
        cy = y0 + h / 2 + i * gap
        d.polygon(
            [
                (x0 + w / 2, cy - h / 2), (x0 + w, cy),
                (x0 + w / 2, cy + h / 2), (x0, cy),
            ],
            outline=BLACK, width=2,
        )


def _draw_calendar_icon(d: ImageDraw.ImageDraw, x0: float, y0: float, size: float) -> None:
    """A minimal calendar glyph — bordered body, a header band, two binding
    rings — for the due-date-first header toggle."""
    top = y0 + size * 0.18
    d.rectangle([x0, top, x0 + size, y0 + size], outline=BLACK, width=2)
    band_h = size * 0.22
    d.line([(x0, top + band_h), (x0 + size, top + band_h)], fill=BLACK, width=2)
    for rx in (x0 + size * 0.25, x0 + size * 0.75):
        d.line([(rx, y0), (rx, top + band_h * 0.5)], fill=BLACK, width=2)


def _draw_contrast_icon(d: ImageDraw.ImageDraw, x0: float, y0: float, size: float) -> None:
    """The standard light/dark glyph — a circle with its right half filled —
    for the dark-mode header toggle. Same shape whichever mode is active; the
    box's border weight says whether it's on."""
    box = [x0, y0, x0 + size, y0 + size]
    d.ellipse(box, outline=BLACK, width=2)
    d.pieslice(box, start=-90, end=90, fill=BLACK)


def _draw_lock_icon(d: ImageDraw.ImageDraw, x0: float, y0: float, size: float) -> None:
    """Closed padlock for the screen-lock button: solid body, shackle dropped
    into it on both sides. One state only — once it's on, the lock screen
    covers the header, so there is no "on" look to draw."""
    body_top = y0 + size * 0.45
    d.rectangle([x0 + size * 0.08, body_top, x0 + size * 0.92, y0 + size], fill=BLACK)
    left, right = x0 + size * 0.28, x0 + size * 0.72
    arc_top, arc_bottom = y0, y0 + size * 0.7
    d.arc([left, arc_top, right, arc_bottom], start=180, end=360, fill=BLACK, width=2)
    mid_y = (arc_top + arc_bottom) / 2
    d.line([(left, mid_y), (left, body_top)], fill=BLACK, width=2)
    d.line([(right, mid_y), (right, body_top)], fill=BLACK, width=2)


def _draw_pointer_icon(d: ImageDraw.ImageDraw, x0: float, y0: float, size: float, off: bool) -> None:
    """The pointing hand a cursor turns into over something clickable, for the
    touch-lock toggle: index finger up, fist below, thumb out to the left.
    With touch locked it is struck through corner to corner — the glyph
    itself says "touch is off", not only the heavier border around it."""
    u = size
    outline = [
        (0.30, 0.50), (0.30, 0.10), (0.40, 0.00), (0.50, 0.10), (0.50, 0.42),
        (0.92, 0.50), (0.98, 0.60), (0.98, 0.82), (0.84, 1.00), (0.40, 1.00),
        (0.06, 0.66), (0.10, 0.56), (0.18, 0.56), (0.30, 0.68),
    ]
    d.polygon([(x0 + px * u, y0 + py * u) for px, py in outline], outline=BLACK, width=2)
    if off:
        d.line([(x0 - 2, y0 - 2), (x0 + u + 2, y0 + u + 2)], fill=BLACK, width=2)


def _draw_rotate_icon(d: ImageDraw.ImageDraw, x0: float, y0: float, size: float) -> None:
    """A circular arrow — an almost-full circle with a head at its open end —
    for the rotate button. One glyph for every orientation; the box's border
    weight says whether the panel is in landscape, away from its portrait
    default."""
    cx, cy, r = x0 + size / 2, y0 + size / 2, size / 2
    end = 315  # degrees, clockwise from 3 o'clock; the gap is the arrow's mouth
    d.arc([x0, y0, x0 + size, y0 + size], start=25, end=end, fill=BLACK, width=2)
    th = math.radians(end)
    ex, ey = cx + r * math.cos(th), cy + r * math.sin(th)
    tx, ty = -math.sin(th), math.cos(th)     # direction of travel at the end
    nx, ny = math.cos(th), math.sin(th)      # outward from the circle
    d.polygon(
        [(ex + tx * 5, ey + ty * 5), (ex - tx * 1 + nx * 4.5, ey - ty * 1 + ny * 4.5),
         (ex - tx * 1 - nx * 4.5, ey - ty * 1 - ny * 4.5)],
        fill=BLACK,
    )


def _draw_toggle_box(d: ImageDraw.ImageDraw, x0: int, y0: int, size: int, icon: str, on: bool) -> None:
    """Small icon box for a header toggle (project/area grouping, due-date-
    first, dark mode, touch lock, screen lock, rotate) — same heavier-border-means-on
    language as a focused task's checkbox, so a new control doesn't need a new
    visual vocabulary."""
    d.rectangle([x0, y0, x0 + size, y0 + size], outline=BLACK, width=3 if on else 1)
    icon_size = GROUP_ICON_SIZE
    ix = x0 + (size - icon_size) / 2
    iy = y0 + (size - icon_size * 0.8) / 2
    if icon == "project":
        _draw_folder_icon(d, ix, iy, icon_size)
    elif icon == "due":
        _draw_calendar_icon(d, ix, iy, icon_size)
    elif icon == "dark":
        _draw_contrast_icon(d, ix, y0 + (size - icon_size) / 2, icon_size)
    elif icon == "touch":
        _draw_pointer_icon(d, ix, y0 + (size - icon_size) / 2, icon_size, on)
    elif icon == "lock":
        _draw_lock_icon(d, ix, y0 + (size - icon_size) / 2 + 1, icon_size)
    elif icon == "rotate":
        _draw_rotate_icon(d, ix, y0 + (size - icon_size) / 2, icon_size)
    else:
        _draw_layers_icon(d, ix, iy, icon_size)


def _strike_line(d: ImageDraw.ImageDraw, x: int, y: int, text: str, font, width: int = 2) -> None:
    """Strikethrough centered on the text's actual rendered bounds, not a
    fixed offset from its drawing origin — that drifts with font metrics
    (ascent/descent vary by size) and ends up visibly off-center."""
    bbox = d.textbbox((x, y), text, font=font)
    mid_y = (bbox[1] + bbox[3]) // 2
    d.line([(bbox[0], mid_y), (bbox[2], mid_y)], fill=BLACK, width=width)


def _meta_line(task: Task, hide_project: bool = False, hide_area: bool = False) -> str:
    """Project / area first, then contexts. Skipped entirely when empty.

    hide_project/hide_area let a grouped list view drop whichever of these
    the group header already says — repeating "E-ink Planner" as both a
    project header and every row's own meta line underneath it is noise,
    not information. Hiding project still falls through to area if the
    task has one and it isn't itself hidden — that's a real, different
    fact the header row hasn't already stated.
    """
    bits = []
    if task.project and not hide_project:
        bits.append(task.project)
    elif task.area and not hide_area:
        bits.append(task.area)
    bits.extend(task.contexts)
    return "  ·  ".join(bits)


def _relative_age(created_at: datetime, now: datetime) -> str:
    """"added N ago", compact enough to share a row with project/area/
    contexts on either the list or detail view's meta line. Calendar-day
    difference, not a raw timedelta — a task added at 11pm
    yesterday should read "added yesterday" at 1am today, not "added 0d
    ago" just because fewer than 24 raw hours have passed."""
    days = (now.date() - created_at.date()).days
    if days <= 0:
        return "added today"
    if days == 1:
        return "added yesterday"
    if days < 7:
        return "added %dd ago" % days
    weeks = days // 7
    if weeks < 8:
        return "added %dw ago" % weeks
    months = days // 30
    if months < 24:
        return "added %dmo ago" % months
    return "added %dy ago" % (days // 365)


def _format_due(due: datetime, now: datetime) -> str:
    """"due ..." / "overdue ...", same calendar-day-difference basis as
    _relative_age. Anything a week or more out just shows the date, matching
    the header's own short date style, rather than "due in 19d"."""
    days = (due.date() - now.date()).days
    if days < 0:
        return "overdue %dd" % -days
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    if days < 7:
        return "due in %dd" % days
    return "due %s" % due.strftime("%-d %b")


def _append_age_due(meta: str, task: Task, now: datetime) -> str:
    """Appends "added N ago" / "due ..." to an existing meta string, in the
    same "  ·  "-joined run as project/area/contexts rather than a row of
    their own — shared by the list view's row meta and the detail view's,
    so a task reads the same age/due info wherever it's shown."""
    extra = []
    if task.created_at:
        extra.append(_relative_age(task.created_at, now))
    if task.due_date:
        extra.append(_format_due(task.due_date, now))
    if not extra:
        return meta
    return "  ·  ".join([meta] + extra) if meta else "  ·  ".join(extra)


def _sort_by_date(items: Sequence[Task], sort_newest: bool) -> List[Task]:
    key = lambda t: t.created_at or datetime.min  # noqa: E731
    return sorted(items, key=key, reverse=sort_newest)


def _sort_backlog(items: Sequence[Task], sort_newest: bool, due_first: bool) -> List[Task]:
    """The backlog's own ordering — plain sort_newest, or (when due_first is
    on) due-dated tasks first, soonest due date leading, then everything
    else in the usual sort_newest order. Never applied to the leading
    focused/starred section (see _organize_tasks) — due_first is scoped to
    "the non-starred list," matching what pushing a due date to the top
    should and shouldn't touch. Urgency isn't a newest/oldest-created
    concept, so the due-dated subset ignores sort_newest entirely and always
    goes soonest-first.
    """
    if not due_first:
        return _sort_by_date(items, sort_newest)
    has_due = [t for t in items if t.due_date is not None]
    no_due = [t for t in items if t.due_date is None]
    has_due.sort(key=lambda t: t.due_date)
    return has_due + _sort_by_date(no_due, sort_newest)


def _group_date_key(tasks: Sequence[Task], sort_newest: bool) -> datetime:
    """A group's own representative date for ordering headers by
    sort_newest: its most-recently-created task when sorting newest-first,
    its least-recently-created one when sorting oldest-first — the same
    task that would end up at the top of that group's own task list."""
    dates = [t.created_at or datetime.min for t in tasks]
    return max(dates) if sort_newest else min(dates)


def _grouped_entries(
    items: Sequence[Task],
    sort_newest: bool,
    group_by_area: bool,
    group_by_project: bool,
    due_first: bool,
    group_order: Optional[dict] = None,
    record: Optional[dict] = None,
) -> List[tuple]:
    """Bucket by area and/or project. With both on, this emits one combined
    "Area / Project" header per pair rather than nesting two header levels —
    a page rarely holds more than one project under a given area heading
    anyway. Each task entry is flagged `in_group=True` so render() knows a
    header above it already states whichever of project/area it's grouped
    by, and can skip repeating that in the row's own meta line.

    Groups are ordered by sort_newest too, via _group_date_key — not dict
    insertion order, which was really just "whichever task happened to sit
    first in the raw fetch" and had nothing to do with the sort toggle, so
    flipping it visibly reordered tasks within a group but left the groups
    themselves seemingly frozen. The fallback ("No area" / "No project")
    group is the exception: it always sorts last regardless of direction,
    since it's an absence of the thing being grouped by, not a real bucket
    whose recency is meaningful — left in the running, it could otherwise
    land in the middle of the page and split up real groups around it.

    due_first reorders tasks within each group (via _sort_backlog) but never
    the groups themselves — group header order stays sort_newest-only.

    Group order is an aggregate of its members' dates, so completing or
    starring a group's oldest task would shuffle the groups under the user's
    finger. `group_order` (a frozen {(level, parent, name): position}, from
    `compute_group_order`) pins them instead: known groups keep their frozen
    position, groups that appeared since follow in fresh order, and the
    fallback group stays last. Tasks *within* a group need no freezing —
    removing one leaves the rest in the same relative order. `record`, if
    given, is filled with the order this call produced.
    """
    def bucket(source: Sequence[Task], key_fn, fallback: str, level: str, parent: str = "") -> List[tuple]:
        buckets: "dict[str, list]" = {}
        for t in source:
            key = key_fn(t) or fallback
            buckets.setdefault(key, []).append(t)
        unfiled = buckets.pop(fallback, None)
        named = sorted(
            buckets.items(),
            key=lambda kv: _group_date_key(kv[1], sort_newest),
            reverse=sort_newest,
        )
        if group_order is not None:
            # Stable sort: groups not in the snapshot keep their fresh
            # relative order, after every known one.
            named.sort(key=lambda kv: group_order.get((level, parent, kv[0]), len(group_order)))
        if record is not None:
            for i, (name, _) in enumerate(named):
                record[(level, parent, name)] = i
        if unfiled is not None:
            named.append((fallback, unfiled))
        return named

    entries: List[tuple] = []
    if group_by_area and group_by_project:
        for area_name, area_tasks in bucket(items, lambda t: t.area, "No area", "area"):
            for proj_name, proj_tasks in bucket(area_tasks, lambda t: t.project, "No project", "project", area_name):
                label = area_name if proj_name == "No project" else "%s / %s" % (area_name, proj_name)
                entries.append(("header", label, "both"))
                entries.extend(("task", t, True) for t in _sort_backlog(proj_tasks, sort_newest, due_first))
    elif group_by_area:
        for area_name, area_tasks in bucket(items, lambda t: t.area, "No area", "area"):
            entries.append(("header", area_name, "area"))
            entries.extend(("task", t, True) for t in _sort_backlog(area_tasks, sort_newest, due_first))
    else:  # project grouping only
        for proj_name, proj_tasks in bucket(items, lambda t: t.project, "No project", "project"):
            entries.append(("header", proj_name, "project"))
            entries.extend(("task", t, True) for t in _sort_backlog(proj_tasks, sort_newest, due_first))
    return entries


def _organize_tasks(
    tasks: Sequence[Task],
    sort_newest: bool,
    focus_sticky: bool,
    group_by_area: bool,
    group_by_project: bool,
    due_first: bool = False,
    group_order: Optional[dict] = None,
    record: Optional[dict] = None,
) -> Tuple[List[tuple], bool]:
    """Turns the fetched task list into a stream of ("task", Task, in_group)
    and ("header", label, icon) entries, per the current toggles.
    Returns (entries, allow_boundary) — the heavy focused/backlog rule only
    means something when focus_sticky guarantees a clean partition; with it
    off, a focused task could land anywhere in date order, and drawing the
    rule at that arbitrary point would look like a real boundary that isn't.

    Focus-sticky always wins, even over grouping: whenever it's on, focused-
    today tasks form their own flat leading section — sorted by sort_newest,
    never bucketed by area/project even when grouping is also on, since
    starring is a stronger signal than either. Sort direction and grouping
    apply only to the backlog beneath the heavy rule; a page with nothing
    starred today has no leading section and no rule, same as before. Each
    task entry's `in_group` flag is False for this leading section (nothing
    states its project/area for it) even when the backlog beneath it is
    grouped and flags its own entries True.

    due_first never touches this leading section either — it's scoped to
    "the non-starred list" by construction, since the focused section always
    sorts via plain _sort_by_date while everything else goes through
    _sort_backlog.
    """
    ordered = list(tasks)
    grouping = group_by_area or group_by_project

    if not focus_sticky:
        if not grouping:
            return [("task", t, False) for t in _sort_backlog(ordered, sort_newest, due_first)], False
        return _grouped_entries(ordered, sort_newest, group_by_area, group_by_project, due_first, group_order, record), False

    focused = [t for t in ordered if t.focused]
    rest = [t for t in ordered if not t.focused]
    entries: List[tuple] = [("task", t, False) for t in _sort_by_date(focused, sort_newest)]
    if grouping:
        entries.extend(_grouped_entries(rest, sort_newest, group_by_area, group_by_project, due_first, group_order, record))
    else:
        entries.extend(("task", t, False) for t in _sort_backlog(rest, sort_newest, due_first))
    return entries, True


def compute_group_order(
    tasks: Sequence[Task],
    sort_newest: bool,
    focus_sticky: bool,
    group_by_area: bool,
    group_by_project: bool,
) -> dict:
    """Snapshot of the current group order, to hand back to render() as
    `group_order` so later completions/stars don't reshuffle the groups."""
    record: dict = {}
    _organize_tasks(tasks, sort_newest, focus_sticky, group_by_area, group_by_project,
                    record=record)
    return record


def _entry_height(entry: tuple) -> int:
    return ROW_H if entry[0] == "task" else GROUP_HEADER_H


def _paginate(entries: List[tuple], available_h: int, pager_h: int) -> Tuple[List[List[tuple]], bool]:
    """Greedy bin-pack by actual height, not a fixed row count — a page mixes
    task rows and half-height group dividers. Every page reserves `pager_h`
    uniformly once paging is needed at all, matching how the fixed-row
    version reserved one row on every page rather than only where needed."""
    total_h = sum(_entry_height(e) for e in entries)
    if total_h <= available_h:
        return [entries], False
    capacity = available_h - pager_h
    pages: List[List[tuple]] = []
    current: List[tuple] = []
    current_h = 0
    for entry in entries:
        h = _entry_height(entry)
        if current and current_h + h > capacity:
            pages.append(current)
            current, current_h = [], 0
        current.append(entry)
        current_h += h
    if current:
        pages.append(current)
    return pages or [[]], True


def _finish(img: Image.Image, hitboxes: List[Hitbox], dark: bool) -> Rendered:
    """Dark mode is the finished light render inverted, not a second set of
    colours threaded through every draw call: the layout, hitboxes and glyphs
    stay identical, so the two modes can't drift apart. Inverting 1-bit pixels
    is lossless (no antialiasing to mangle). Via "L" because this module's
    WHITE is 1, not 255, which PIL's own invert on mode "1" gets wrong."""
    if dark:
        img = ImageOps.invert(img.convert("L")).convert("1", dither=Image.Dither.NONE)
    return Rendered(image=img, hitboxes=hitboxes)


def _draw_header(
    d: ImageDraw.ImageDraw,
    g: Geometry,
    now: datetime,
    title_text: str,
    title_kind: str,
    not_fetched: int = 0,
    sort_newest: bool = False,
    focus_sticky: bool = True,
    group_by_area: bool = False,
    group_by_project: bool = False,
    due_first: bool = False,
    dark: bool = False,
    touch_locked: bool = False,
    rotation: int = 0,
) -> List[Hitbox]:
    """The control bar, shared by the list and the detail view so the two can't
    drift apart (and so the locks and dark toggle are reachable from either).
    `title_kind` is what a tap on the title does: "home" in the list, "back"
    in the detail view. The cluster and status text are the same in both —
    the view toggles don't change what a detail view shows, but they're the
    state the list will have when you go back, so they stay live.

    Landscape is one row: title, then the nine controls at fixed positions,
    then status and date. Portrait (480px wide) can't fit that, so it is two:
    title / status / date on top and the nine controls spread across the full
    width beneath, one equal slot each — larger tap targets than landscape's.
    The controls are the same nine in the same order either way."""
    f = fonts()
    d.text((MARGIN, 20), title_text, font=f.header, fill=BLACK)
    title_w = _text_width(d, title_text, f.header)

    # Short on purpose ("Sat 17", "upd 10:16"): the header's width is the scarce
    # thing, and the weekday + day is what a wall planner is glanced at for.
    right = now.strftime("%a %-d")
    date_x = g.text_right - _text_width(d, right, f.header_right)
    d.text((date_x, 21), right, font=f.header_right, fill=BLACK)

    # Routine status — "updated HH:MM" plus how much wasn't even fetched —
    # sits right-aligned next to the date, grouped with it as read-only status
    # text rather than with the controls: those are the only things on this
    # header a tap actually changes, and footer-sized text keeps this from
    # reading as another control.
    updated_text = "upd %s" % now.strftime("%H:%M")
    if not_fetched > 0:
        updated_text += "  ·  %d more" % not_fetched
    updated_text_x = date_x - (16 if g.portrait else 30) - _text_width(d, updated_text, f.footer)
    d.text((updated_text_x, 24), updated_text, font=f.footer, fill=BLACK)

    # Control positions as (centre x, half hit-width), in order: focus-sticky,
    # sort, area, project, due, dark, rotate, touch, lock. Landscape's are fixed absolute
    # positions rather than relative to the (variable-width) title or date, so
    # neither can ever collide with the cluster; portrait's are equal slots.
    if g.portrait:
        # Hit slots run to 10px from each edge, not MARGIN: nine slots inside
        # the margins would be 48px, under the 50 a thumb wants, while the
        # 28px boxes drawn at their centres still land inside the margins.
        edge = MARGIN // 2
        slot = (g.width - 2 * edge) / 9
        controls = [(int(edge + slot * (i + 0.5)), int(slot // 2)) for i in range(9)]
        ctrl_y = PORTRAIT_HEADER_ROW_H + (g.header_h - 8 - PORTRAIT_HEADER_ROW_H) // 2
        hit_y0, title_y1 = PORTRAIT_HEADER_ROW_H, PORTRAIT_HEADER_ROW_H
        home_x1 = min(MARGIN + title_w + 16, updated_text_x - 8)
    else:
        step = GROUP_TOGGLE_BOX + 8
        centres = [212, 246] + [276 + step * i + GROUP_TOGGLE_BOX // 2 for i in range(7)]
        controls = [(c, 16 if i < 2 else GROUP_TOGGLE_BOX // 2 + 4) for i, c in enumerate(centres)]
        ctrl_y = 30
        hit_y0, title_y1 = 0, g.header_h
        # The cluster's left edge is fixed, so the longest title ("NEXT
        # ACTIONS") would otherwise reach under the first control — clamp
        # the title's tap zone to end there.
        home_x1 = min(MARGIN + title_w + 16, controls[0][0] - 16)
    # Tapping the title is "home": back to page 1. Sort/group/sticky toggles
    # are deliberately untouched by it — the controls are the only way to
    # change them, so a stray tap here can't reset a view you set up on purpose.
    hitboxes: List[Hitbox] = [Hitbox(title_kind, 0, home_x1, 0, title_y1)]

    (star_c, star_hw), (arrow_c, arrow_hw), *boxes = controls
    _draw_star(d, star_c, ctrl_y, FOCUS_STAR_SIZE, filled=focus_sticky)
    # One button, not two: with focus_sticky handling "important first"
    # separately, sort has nothing a third "unsorted" state would mean —
    # newest-first draws up, oldest-first draws down, tap flips it.
    _draw_arrow(d, arrow_c, ctrl_y, SORT_ARROW_SIZE, up=sort_newest)
    hitboxes.append(Hitbox("focus_sticky", star_c - star_hw, star_c + star_hw, hit_y0, g.header_h))
    hitboxes.append(Hitbox("sort_toggle", arrow_c - arrow_hw, arrow_c + arrow_hw, hit_y0, g.header_h))

    # View controls (area / project / due) first, then display (dark /
    # rotate), then input (touch lock / screen lock): the latter change how the
    # panel looks and whether it listens, not what the list contains. The two
    # locks sit together at the far right, screen lock outermost. Rotate's
    # border is heavy in landscape — away from the portrait default — like any
    # other "on"; the screen lock's never is, since the lock screen hides the
    # header.
    box_y = ctrl_y - GROUP_TOGGLE_BOX // 2
    for (cx, hw), (icon, on, kind) in zip(boxes, [
        ("area", group_by_area, "toggle_area_view"),
        ("project", group_by_project, "toggle_project_view"),
        ("due", due_first, "toggle_due_first"),
        ("dark", dark, "toggle_dark"),
        ("rotate", not is_portrait(rotation), "toggle_rotate"),
        ("touch", touch_locked, "toggle_touch_lock"),
        ("lock", False, "lock_screen"),
    ]):
        _draw_toggle_box(d, cx - GROUP_TOGGLE_BOX // 2, box_y, GROUP_TOGGLE_BOX, icon, on)
        hitboxes.append(Hitbox(kind, cx - hw, cx + hw, hit_y0, g.header_h))

    d.line([(MARGIN, g.header_h - 8), (g.text_right, g.header_h - 8)], fill=BLACK, width=2)
    return hitboxes


def render(
    tasks: Sequence[Task],
    view: str,
    total: Optional[int] = None,
    now: Optional[datetime] = None,
    stale_since: Optional[datetime] = None,
    completing: Optional[str] = None,
    page: int = 0,
    sort_newest: bool = False,
    focus_sticky: bool = True,
    group_by_area: bool = False,
    group_by_project: bool = False,
    due_first: bool = False,
    group_order: Optional[dict] = None,
    dark: bool = False,
    touch_locked: bool = False,
    portrait: bool = False,
    rotation: int = 0,
) -> Rendered:
    g = PORTRAIT if portrait else LANDSCAPE
    now = now or datetime.now()
    f = fonts()
    img = Image.new("1", (g.width, g.height), WHITE)
    d = ImageDraw.Draw(img)

    all_tasks = list(tasks)
    total = len(all_tasks) if total is None else total

    entries, allow_boundary = _organize_tasks(
        all_tasks, sort_newest, focus_sticky, group_by_area, group_by_project, due_first,
        group_order=group_order,
    )
    # The footer only has something to say when offline — otherwise it's
    # dead space "updated HH:MM" (now in the header, see below) used to
    # spend on every single render. Collapsing it to a bare rule when
    # online gives that back to pagination/grouping headroom.
    footer_h = FOOTER_H if stale_since is not None else COLLAPSED_FOOTER_H
    available_h = g.height - g.header_h - footer_h
    pages, needs_paging = _paginate(entries, available_h, PAGER_H)
    total_pages = len(pages)
    page = max(0, min(page, total_pages - 1))
    visible = pages[page]

    # --- header: shared with the detail view (see _draw_header) -------------
    hitboxes = _draw_header(
        d, g, now, VIEW_TITLES.get(view, view.upper()), "home",
        not_fetched=total - len(all_tasks),
        sort_newest=sort_newest, focus_sticky=focus_sticky,
        group_by_area=group_by_area, group_by_project=group_by_project,
        due_first=due_first, dark=dark, touch_locked=touch_locked, rotation=rotation,
    )

    # --- rows -----------------------------------------------------------
    if not visible:
        message = EMPTY_MESSAGES.get(view, "Nothing here.")
        d.text(
            ((g.width - _text_width(d, message, f.empty)) // 2, g.height // 2 - 24),
            message, font=f.empty, fill=BLACK,
        )
    star_x0 = g.width - STAR_ZONE_W
    y = g.header_h
    # Global offset of `visible`'s first entry within the full `entries`
    # list — needed so the boundary rule below can look one entry past the
    # end of THIS page, into the next one, rather than only within what's
    # currently on screen (see test_boundary_rule_survives_a_page_split).
    page_start = sum(len(p) for p in pages[:page])
    for i, entry in enumerate(visible):
        kind = entry[0]
        if kind == "header":
            _, label, icon = entry
            h = GROUP_HEADER_H
            icon_size = GROUP_ICON_SIZE * 0.8
            icon_y = y + (h - icon_size) / 2
            if icon in ("area", "both"):
                _draw_layers_icon(d, MARGIN, icon_y, icon_size)
            if icon == "project":
                _draw_folder_icon(d, MARGIN, icon_y, icon_size)
            elif icon == "both":
                _draw_folder_icon(d, MARGIN + icon_size + 6, icon_y, icon_size)
            text_x = MARGIN + (icon_size * 2 + 12 if icon == "both" else icon_size + 8)
            d.text((text_x, y + 6), label, font=f.meta, fill=BLACK)
            d.line([(MARGIN, y + h), (g.text_right, y + h)], fill=BLACK, width=1)
            y += h
            continue

        task, in_group = entry[1], entry[2]
        y0, y1 = y, y + ROW_H
        # Split per row, not per task: checkbox completes, text opens
        # detail, the star column (far right) toggles today's focus — see
        # the Hitbox docstring for why TEXT_X is reused as a boundary
        # everywhere else in the app; the star gets its own column because
        # there's no gesture left on the other two zones to overload safely.
        hitboxes.append(Hitbox("complete", 0, TEXT_X, y0, y1, task_id=task.id))
        hitboxes.append(Hitbox("open", TEXT_X, star_x0, y0, y1, task_id=task.id))
        hitboxes.append(Hitbox("star", star_x0, g.width, y0, y1, task_id=task.id))

        box_y = y0 + (ROW_H - BOX) // 2
        # Emphasis is a heavier border, never a fill: a filled checkbox reads as
        # "done" in every interface anyone has ever used, and this one is the
        # tap target for completing the task.
        d.rectangle(
            [BOX_X, box_y, BOX_X + BOX, box_y + BOX],
            outline=BLACK, width=4 if task.focused else 2,
        )
        _draw_star(d, (star_x0 + g.width) // 2, y0 + ROW_H // 2, STAR_SIZE, filled=task.focused)

        # Only a task actually sitting under a group header (in_group) has
        # its project/area already stated above it — the leading focused
        # section is never in_group, even when the backlog beneath it is
        # grouped, so its own rows keep showing project/area.
        meta = _append_age_due(
            _meta_line(
                task, hide_project=group_by_project and in_group, hide_area=group_by_area and in_group
            ),
            task, now,
        )
        title_y = y0 + (12 if meta else 20)
        title_text_row = _ellipsize(d, task.title, f.title, g.list_text_right - TEXT_X)
        d.text((TEXT_X, title_y), title_text_row, font=f.title, fill=BLACK)
        if task.id == completing:
            # Held for one beat before the row is dropped and the list
            # reflows — two static frames, not motion, so a tap visibly
            # registers before its row disappears from under the finger.
            _draw_check(d, BOX_X, box_y, BOX)
            _strike_line(d, TEXT_X, title_y, title_text_row, f.title, width=3)
        if meta:
            d.text(
                (TEXT_X, title_y + 30),
                _ellipsize(d, meta, f.meta, g.list_text_right - TEXT_X),
                font=f.meta, fill=BLACK,
            )
        if i < len(visible) - 1 or needs_paging:
            # A heavier full-width rule marks where today's chosen work ends
            # and the backlog filling the remaining rows begins. The next
            # entry ends the leading focused section either by being a
            # non-focused task (ungrouped backlog) or a header (grouped
            # backlog starts right there) — either way, this is the edge.
            global_idx = page_start + i
            next_entry = entries[global_idx + 1] if global_idx + 1 < len(entries) else None
            next_still_focused = (
                next_entry is not None and next_entry[0] == "task" and next_entry[1].focused
            )
            boundary = (
                allow_boundary and next_entry is not None
                and task.focused and not next_still_focused
            )
            d.line(
                [(MARGIN if boundary else TEXT_X, y1), (g.text_right, y1)],
                fill=BLACK, width=2 if boundary else 1,
            )
        y += ROW_H

    if needs_paging:
        y0, y1 = y, y + PAGER_H
        mid = g.width // 2
        prev_page = (page - 1) % total_pages
        next_page = (page + 1) % total_pages
        # Split the row itself rather than adding a second row — screen
        # space here is already scarce. Shorter than a task row (PAGER_H),
        # which is what lets 6 tasks fit above it; swiping is the other
        # way to turn a page.
        hitboxes.append(Hitbox("list_page", 0, mid, y0, y1, page=prev_page))
        hitboxes.append(Hitbox("list_page", mid, g.width, y0, y1, page=next_page))
        box_y = y0 + (PAGER_H - PAGER_BOX) // 2
        _draw_disclosure(d, BOX_X, box_y, PAGER_BOX, direction="left")
        _draw_disclosure(d, g.width - MARGIN - PAGER_BOX, box_y, PAGER_BOX, direction="right")
        label = "page %d/%d" % (page + 1, total_pages)
        d.text(
            ((g.width - _text_width(d, label, f.meta)) // 2, y0 + (PAGER_H - 20) // 2),
            label, font=f.meta, fill=BLACK,
        )

    # --- footer -------------------------------------------------------------
    # "updated HH:MM" now lives in the header (see above) — the footer has
    # nothing routine left to say, so it collapses to a bare rule and gives
    # that space back (footer_h computed above). It only expands back to
    # full height for the one thing this app promises never to make easy to
    # miss: a dead network showing a stale list with no explanation.
    fy = g.height - footer_h
    d.line([(MARGIN, fy), (g.text_right, fy)], fill=BLACK)
    if stale_since is not None:
        warn = "OFFLINE since %s" % stale_since.strftime("%H:%M")
        d.text((MARGIN, fy + 14), warn, font=f.footer, fill=BLACK)

    return _finish(img, hitboxes, dark)


def render_detail(
    task: Task, completing: bool = False, expanded: bool = False, page: int = 0,
    now: Optional[datetime] = None, dark: bool = False, touch_locked: bool = False,
    sort_newest: bool = False, focus_sticky: bool = True, group_by_area: bool = False,
    group_by_project: bool = False, due_first: bool = False, portrait: bool = False,
    rotation: int = 0, expanded_items: frozenset = frozenset(),
) -> Rendered:
    """One task, full title, and its checklist if it has one.

    Unchecked items always render individually, at the top. Done items
    collapse into a single "N done — tap to show" row unless `expanded` —
    a glance at the wall needs the open items, not eleven already-finished
    ones each taking a row. Expanding replaces that row with a "hide" row
    (tap to re-stack) followed by the done items individually, so a
    specific one can be unticked. Checklist items split at TEXT_X like list
    rows: the checkbox column toggles, and the text — only when the item is
    too long for one line, marked by a small triangle at its end — unfolds
    it to its full wrapped title (`expanded_items`), or folds it back.

    Whatever doesn't fit `page`'s worth of rows is reachable by paging, not
    silently dropped — the last row on a page becomes a "page X/Y" control
    (wrapping around) whenever there's more than one page.
    """
    g = PORTRAIT if portrait else LANDSCAPE
    now = now or datetime.now()
    f = fonts()
    img = Image.new("1", (g.width, g.height), WHITE)
    d = ImageDraw.Draw(img)

    # --- header: the same bar as the list, "< BACK" in place of the title ---
    hitboxes: List[Hitbox] = _draw_header(
        d, g, now, "< BACK", "back",
        sort_newest=sort_newest, focus_sticky=focus_sticky,
        group_by_area=group_by_area, group_by_project=group_by_project,
        due_first=due_first, dark=dark, touch_locked=touch_locked, rotation=rotation,
    )

    # --- title + the task's own checkbox and star ---------------------------
    y = g.header_h + 10
    box_y = y
    d.rectangle(
        [BOX_X, box_y, BOX_X + BOX, box_y + BOX],
        outline=BLACK, width=4 if task.focused else 2,
    )
    star_cx = g.text_right - DETAIL_STAR_ZONE_W // 2
    _draw_star(d, star_cx, box_y + BOX // 2, STAR_SIZE, filled=task.focused)
    hitboxes.append(
        Hitbox("star", g.text_right - DETAIL_STAR_ZONE_W, g.text_right, y, y + BOX, task_id=task.id)
    )
    title_lines = _wrap(
        d, task.title, f.title, g.text_right - TEXT_X - DETAIL_STAR_ZONE_W, max_lines=2
    )
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
    title_row_bottom = max(line_y, box_y + BOX + 6)
    # Same rule as everywhere else: only the checkbox column acts. The title
    # text has nothing further to open, so a tap there is a no-op rather
    # than a second, easy-to-hit-by-accident way to complete the task.
    hitboxes.append(Hitbox("complete", 0, TEXT_X, g.header_h, title_row_bottom, task_id=task.id))

    # --- meta + description --------------------------------------------------
    # The footer is just a rule here (the list's collapsed footer, same reason:
    # every pixel it doesn't spend is a checklist row). Checklist progress,
    # which used to live in it, rides the meta line's right edge instead.
    fy = g.height - COLLAPSED_FOOTER_H
    y = title_row_bottom + 4
    meta = _append_age_due(_meta_line(task), task, now)
    done_count = sum(1 for item in task.checklist if item.completed)
    progress = "%d of %d done" % (done_count, len(task.checklist)) if task.checklist else ""
    if meta or progress:
        meta_right = g.text_right
        if progress:
            meta_right -= _text_width(d, progress, f.footer)
            d.text((meta_right, y + 2), progress, font=f.footer, fill=BLACK)
            meta_right -= 16
        d.text((TEXT_X, y), _ellipsize(d, meta, f.meta, meta_right - TEXT_X), font=f.meta, fill=BLACK)
        y += 22
    if task.description:
        max_width = g.text_right - TEXT_X
        all_lines = _wrap_paragraphs(d, task.description.strip(), f.footer, max_width)
        if task.checklist:
            # Competing with a checklist below — a fixed, generous
            # allocation rather than the full page, so the checklist always
            # keeps a real minimum too instead of being crowded to zero.
            visible_desc = all_lines[:DETAIL_DESC_MIN_LINES_WITH_CHECKLIST]
            if len(all_lines) > len(visible_desc) and visible_desc:
                visible_desc[-1] = _ellipsize(d, visible_desc[-1] + " …", f.footer, max_width)
            for line in visible_desc:
                d.text((TEXT_X, y), line, font=f.footer, fill=BLACK)
                y += DETAIL_DESC_LINE_H
        else:
            # Nothing else needs this space — description gets all of it,
            # paginated (same control as the checklist) if even that isn't
            # enough to show the whole thing at once.
            reserved_below = 16  # the rule + gap drawn right after this block
            available = max(DETAIL_DESC_LINE_H, fy - y - reserved_below)
            total_h = len(all_lines) * DETAIL_DESC_LINE_H
            if total_h <= available:
                desc_page, desc_total_pages, visible_desc = 0, 1, all_lines
            else:
                capacity = max(DETAIL_DESC_LINE_H, available - PAGER_H)
                lines_per_page = max(1, capacity // DETAIL_DESC_LINE_H)
                desc_total_pages = max(1, -(-len(all_lines) // lines_per_page))
                desc_page = max(0, min(page, desc_total_pages - 1))
                start = desc_page * lines_per_page
                visible_desc = all_lines[start:start + lines_per_page]
            for line in visible_desc:
                d.text((TEXT_X, y), line, font=f.footer, fill=BLACK)
                y += DETAIL_DESC_LINE_H
            if desc_total_pages > 1:
                hitboxes.extend(_draw_detail_pager(d, g, y, y + PAGER_H, desc_page, desc_total_pages, task.id))
                y += PAGER_H

    y += 6
    d.line([(MARGIN, y), (g.text_right, y)], fill=BLACK, width=1)
    y += 8

    # --- checklist: unchecked individually, done items stacked -------------
    unchecked = [item for item in task.checklist if not item.completed]
    done = [item for item in task.checklist if item.completed]
    entries: List[Tuple[str, object]] = [("item", item) for item in unchecked]
    if done:
        if expanded:
            entries.append(("collapse", len(done)))
            entries.extend(("item", item) for item in done)
        else:
            entries.append(("stack", done))

    # Each item is measured both ways up front: one line, or wrapped in full.
    # Rows then pack into pages by height, so an unfolded item can push the
    # rest onto the next page instead of off the bottom.
    item_text_x = BOX_X + DETAIL_BOX + 16
    full_w = g.text_right - item_text_x
    wrap_w = full_w - DETAIL_ITEM_ARROW_W
    # A single item never grows past one page, pager included.
    max_lines = max(1, 1 + (fy - y - PAGER_H - DETAIL_ROW_H) // DETAIL_ITEM_LINE_H)
    wrapped = {}
    for kind, payload in entries:
        if kind == "item" and _text_width(d, payload.title, f.meta) > full_w:
            wrapped[payload.id] = _wrap(d, payload.title, f.meta, wrap_w, max_lines)

    def heights(flip: Optional[str] = None) -> List[int]:
        out = []
        for kind, payload in entries:
            open_ = kind == "item" and payload.id in wrapped and (
                (payload.id in expanded_items) != (payload.id == flip)
            )
            n = len(wrapped[payload.id]) if open_ else 1
            out.append(DETAIL_ROW_H + (n - 1) * DETAIL_ITEM_LINE_H)
        return out

    row_h = heights()
    page_of, total_pages, needs_paging = _pack_pages(row_h, fy - y)
    page = max(0, min(page, total_pages - 1))

    iy0 = y
    drawn = 0
    for i, (kind, payload) in enumerate(entries):
        if page_of[i] != page:
            continue
        if drawn:
            d.line([(item_text_x, iy0), (g.text_right, iy0)], fill=BLACK, width=1)
        drawn += 1
        iy1 = iy0 + row_h[i]
        box_y2 = iy0 + DETAIL_BOX_Y_OFFSET
        text_y = iy0 + (DETAIL_ROW_H - 20) // 2

        if kind == "stack":
            hitboxes.append(Hitbox("expand", 0, g.width, iy0, iy1, task_id=task.id))
            _draw_disclosure(d, BOX_X, box_y2, DETAIL_BOX, direction="right")
            d.text(
                (item_text_x, text_y), "%d done — tap to show" % len(payload),
                font=f.meta, fill=BLACK,
            )
        elif kind == "collapse":
            hitboxes.append(Hitbox("collapse", 0, g.width, iy0, iy1, task_id=task.id))
            _draw_disclosure(d, BOX_X, box_y2, DETAIL_BOX, direction="down")
            d.text(
                (item_text_x, text_y), "hide %d done" % payload,
                font=f.meta, fill=BLACK,
            )
        else:
            item = payload
            hitboxes.append(
                Hitbox("toggle", 0, TEXT_X, iy0, iy1, task_id=task.id, item_id=item.id)
            )
            d.rectangle(
                [BOX_X, box_y2, BOX_X + DETAIL_BOX, box_y2 + DETAIL_BOX], outline=BLACK, width=2
            )
            if item.id in wrapped:
                is_open = item.id in expanded_items
                lines = wrapped[item.id] if is_open else [_ellipsize(d, item.title, f.meta, wrap_w)]
                # Where the item will be once flipped, so the panel can turn
                # to that page rather than lose it off the bottom of this one.
                flipped_page = _pack_pages(heights(flip=item.id), fy - y)[0][i]
                hitboxes.append(Hitbox(
                    "expand_item", TEXT_X, g.width, iy0, iy1,
                    task_id=task.id, item_id=item.id, page=flipped_page,
                ))
                arrow = DETAIL_ITEM_ARROW_W - 8
                _draw_disclosure(
                    d, g.text_right - arrow, text_y + (20 - arrow) // 2 + 1, arrow,
                    direction="up" if is_open else "down",
                )
            else:
                lines = [item.title]
            for n, line in enumerate(lines):
                ly = text_y + n * DETAIL_ITEM_LINE_H
                d.text((item_text_x, ly), line, font=f.meta, fill=BLACK)
                if item.completed:
                    # Strikethrough, centered on the actual glyph bounds
                    # (see _strike_line), on every wrapped line.
                    _strike_line(d, item_text_x, ly, line, f.meta)
            if item.completed:
                # Tick, not a fill — a fill is reserved for "tap here to
                # complete", and this item already is.
                _draw_check(d, BOX_X, box_y2, DETAIL_BOX)
        iy0 = iy1

    if needs_paging:
        d.line([(item_text_x, iy0), (g.text_right, iy0)], fill=BLACK, width=1)
        hitboxes.extend(_draw_detail_pager(d, g, iy0, iy0 + PAGER_H, page, total_pages, task.id))

    # --- footer: a bare rule (progress is on the meta line) -----------------
    d.line([(MARGIN, fy), (g.text_right, fy)], fill=BLACK)

    return _finish(img, hitboxes, dark)


def hit(hitboxes: Sequence[Hitbox], point: Tuple[int, int]) -> Optional[Hitbox]:
    for box in hitboxes:
        if box.contains(point):
            return box
    return None
