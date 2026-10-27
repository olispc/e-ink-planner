"""The pickers an inbox task's processing rows open: where to file it (a
project or an area), its contexts, and its due date — and "edit", the screen
that gives any other task those same rows (opened from its meta line).

Typing on a wall panel is miserable, so each one is a choice among what
Mindwtr already has — nothing here creates an area, project or context. The
header is the same bar as on every other screen; under it, a title row is the
way back to the task. Swiping right does the same from a picker's first page,
except on the date picker, where swiping turns the month instead.

Single choices (where, due date) go back as soon as they're made — to the
Edit screen if that's where the picker was opened, else to the task; contexts
are several, so ticking one stays put.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from .client import Area, Project, Task
from .layout import (
    Hitbox, Rendered, _draw_check, _draw_detail_pager, _draw_disclosure, _draw_folder_icon,
    _draw_header, _draw_layers_icon, _draw_processing, _ellipsize, _finish, _pack_pages,
    _text_width,
)
from .theme import (
    BLACK, BOX_X, COLLAPSED_FOOTER_H, DETAIL_BOX, DETAIL_BOX_Y_OFFSET, DETAIL_ROW_H,
    GROUP_ICON_SIZE, LANDSCAPE, MARGIN, PORTRAIT, TEXT_X, WHITE, Geometry, fonts,
)

PICKER_TITLES = {"edit": "Edit", "where": "Project or area", "contexts": "Contexts", "due": "Due date"}

# A project row sits under its area's, indented by this much, so the list
# reads as the tree it is without needing a second level of header.
PROJECT_INDENT = 28


def month_index(year: int, month: int) -> int:
    """A month as one integer, so it can ride a Hitbox's `page` and step by 1."""
    return year * 12 + month - 1


def render_picker(
    kind: str, task: Task, projects: Sequence[Project] = (), areas: Sequence[Area] = (),
    contexts: Sequence[str] = (), page: int = 0, month: Optional[int] = None,
    now: Optional[datetime] = None, dark: bool = False, touch_locked: bool = False,
    sort_newest: bool = False, focus_sticky: bool = True, group_by_area: bool = False,
    group_by_project: bool = False, due_first: bool = False, portrait: bool = False,
    rotation: int = 0, view: str = "inbox", inbox_count: int = 0,
) -> Rendered:
    """`kind` is "edit", "where", "contexts" or "due". `page` pages the
    where and contexts lists; `month` (a month_index) is the month the date
    picker shows — the task's due month, or this one, when None."""
    g = PORTRAIT if portrait else LANDSCAPE
    now = now or datetime.now()
    img = Image.new("1", (g.width, g.height), WHITE)
    d = ImageDraw.Draw(img)

    # The active tab is a plain "view" link here: "back" belongs to the title
    # row, which goes back to the task rather than out to the list.
    hitboxes: List[Hitbox] = _draw_header(
        d, g, now, view, "view", inbox_count=inbox_count,
        sort_newest=sort_newest, focus_sticky=focus_sticky,
        group_by_area=group_by_area, group_by_project=group_by_project,
        due_first=due_first, dark=dark, touch_locked=touch_locked, rotation=rotation,
    )
    y = _draw_title_row(d, g, kind, task, hitboxes)
    fy = g.height - COLLAPSED_FOOTER_H
    if kind == "edit":
        hitboxes.extend(_draw_processing(d, g, task, y + 6, now))
    elif kind == "due":
        hitboxes.extend(_draw_due(d, g, task, y, fy, month, now))
    else:
        rows = _where_rows(task, projects, areas) if kind == "where" else _context_rows(task, contexts)
        hitboxes.extend(_draw_rows(d, g, task, kind, rows, y, fy, page))
    d.line([(MARGIN, fy), (g.text_right, fy)], fill=BLACK)
    return _finish(img, hitboxes, dark)


def _draw_title_row(d: ImageDraw.ImageDraw, g: Geometry, kind: str, task: Task,
                    hitboxes: List[Hitbox]) -> int:
    """"‹ Due date  Task title…" — a tap anywhere on it goes back to the task."""
    f = fonts()
    y0, y1 = g.header_h, g.header_h + DETAIL_ROW_H
    _draw_disclosure(d, BOX_X, y0 + DETAIL_BOX_Y_OFFSET, DETAIL_BOX, "left")
    label = PICKER_TITLES[kind]
    d.text((TEXT_X, y0 + (DETAIL_ROW_H - 24) // 2), label, font=f.header, fill=BLACK)
    title_x = TEXT_X + _text_width(d, label, f.header) + 16
    if title_x < g.text_right - 40:
        d.text((title_x, y0 + (DETAIL_ROW_H - 20) // 2),
               _ellipsize(d, task.title, f.meta, g.text_right - title_x), font=f.meta, fill=BLACK)
    hitboxes.append(Hitbox("back", 0, g.width, y0, y1, task_id=task.id))
    d.line([(MARGIN, y1), (g.text_right, y1)], fill=BLACK, width=2)
    return y1 + 4


# A row: (key, label, selected, indent, icon) — icon "area", "project" or None.
Row = Tuple[str, str, bool, int, Optional[str]]


def _where_rows(task: Task, projects: Sequence[Project], areas: Sequence[Area]) -> List[Row]:
    """Each area with its projects under it, then projects with no area. One
    choice: a project (which brings its area — Mindwtr clears the task's own
    areaId when a project is set), an area alone, or neither."""
    if task.project_id:
        current = "project:" + task.project_id
    elif task.area_id:
        current = "area:" + task.area_id
    else:
        current = "none"
    rows: List[Row] = [("none", "No project or area", current == "none", 0, None)]
    area_ids = {a.id for a in areas}
    for a in areas:
        rows.append(("area:" + a.id, a.name, current == "area:" + a.id, 0, "area"))
        for p in projects:
            if p.area_id == a.id:
                rows.append(("project:" + p.id, p.title, current == "project:" + p.id,
                             PROJECT_INDENT, "project"))
    for p in projects:
        if p.area_id not in area_ids:
            rows.append(("project:" + p.id, p.title, current == "project:" + p.id, 0, "project"))
    return rows


def _context_rows(task: Task, contexts: Sequence[str]) -> List[Row]:
    """Every context already in use on an open task (the cloud API has no
    list of its own), plus the task's own; several can be ticked."""
    names = sorted(set(contexts) | set(task.contexts), key=str.lower)
    return [(c, c, c in task.contexts, 0, None) for c in names]


def _draw_rows(d: ImageDraw.ImageDraw, g: Geometry, task: Task, kind: str, rows: List[Row],
               y: int, fy: int, page: int) -> List[Hitbox]:
    """A paged column of choices, a radio for one-of (where) and a checkbox
    for several (contexts). Each row is one tap target across its width:
    there is nothing else to do with a choice's text."""
    f = fonts()
    hitboxes: List[Hitbox] = []
    if not rows:
        message = "No contexts in use yet."
        d.text(((g.width - _text_width(d, message, f.meta)) // 2, y + 40), message, font=f.meta, fill=BLACK)
        return hitboxes
    tap_kind = "choose_where" if kind == "where" else "toggle_context"
    page_of, total_pages, needs_paging = _pack_pages([DETAIL_ROW_H] * len(rows), fy - y)
    page = max(0, min(page, total_pages - 1))
    for i, (key, label, selected, indent, icon) in enumerate(rows):
        if page_of[i] != page:
            continue
        y0, y1 = y, y + DETAIL_ROW_H
        bx, by = BOX_X + indent, y0 + DETAIL_BOX_Y_OFFSET
        if kind == "where":
            d.ellipse([bx, by, bx + DETAIL_BOX, by + DETAIL_BOX], outline=BLACK, width=2)
            if selected:
                inset = DETAIL_BOX * 0.28
                d.ellipse([bx + inset, by + inset, bx + DETAIL_BOX - inset, by + DETAIL_BOX - inset],
                          fill=BLACK)
        else:
            d.rectangle([bx, by, bx + DETAIL_BOX, by + DETAIL_BOX], outline=BLACK, width=2)
            if selected:
                _draw_check(d, bx, by, DETAIL_BOX)
        text_x = TEXT_X + indent
        if icon is not None:
            icon_y = y0 + (DETAIL_ROW_H - GROUP_ICON_SIZE) // 2
            draw_icon = _draw_layers_icon if icon == "area" else _draw_folder_icon
            draw_icon(d, text_x, icon_y + (0 if icon == "area" else 2), GROUP_ICON_SIZE)
            text_x += GROUP_ICON_SIZE + 10
        font = f.header if icon == "area" else f.meta
        d.text((text_x, y0 + (DETAIL_ROW_H - 22) // 2),
               _ellipsize(d, label, font, g.text_right - text_x), font=font, fill=BLACK)
        hitboxes.append(Hitbox(tap_kind, 0, g.width, y0, y1, task_id=task.id, key=key))
        d.line([(TEXT_X + indent, y1), (g.text_right, y1)], fill=BLACK, width=1)
        y = y1
    if needs_paging:
        hitboxes.extend(_draw_detail_pager(d, g, fy - 4 - 34, fy - 4, page, total_pages, task.id,
                                           kind="picker_page"))
    return hitboxes


def next_monday(today: date) -> date:
    """Mindwtr's "next week" quick date: the coming Monday, a full week on
    when today is one (getQuickDate in packages/core/src/date.ts)."""
    return today + timedelta(days=(7 - today.weekday()) or 7)


def _draw_due(d: ImageDraw.ImageDraw, g: Geometry, task: Task, y: int, fy: int,
              month: Optional[int], now: datetime) -> List[Hitbox]:
    """Quick dates, then a month grid (weeks start Monday). The month turns
    with the ‹ › row or a swipe, like any other pager. The chosen day has
    the heavy border every other "on" state in this app has; today a thin one."""
    f = fonts()
    hitboxes: List[Hitbox] = []
    today = now.date()
    selected = task.due_date.date() if task.due_date else None
    if month is None:
        month = month_index((selected or today).year, (selected or today).month)
    year, mon = divmod(month, 12)
    mon += 1

    # --- quick dates -------------------------------------------------------
    quick_h = 50 if g.portrait else 40
    quick = [("Today", today), ("Tomorrow", today + timedelta(days=1)),
             ("Next week", next_monday(today)), ("Clear", None)]
    seg_w = (g.text_right - MARGIN) / len(quick)
    for i, (label, day) in enumerate(quick):
        x0, x1 = int(MARGIN + seg_w * i), int(MARGIN + seg_w * (i + 1))
        on = day is not None and day == selected
        d.rectangle([x0 + 3, y + 2, x1 - 3, y + quick_h - 2], outline=BLACK, width=3 if on else 1)
        d.text(((x0 + x1 - _text_width(d, label, f.meta)) // 2, y + (quick_h - 22) // 2),
               label, font=f.meta, fill=BLACK)
        hitboxes.append(Hitbox("choose_due", x0, x1, y, y + quick_h, task_id=task.id,
                               key=day.isoformat() if day else ""))
    y += quick_h + 4

    # --- month row: ‹ October 2026 › ---------------------------------------
    month_h = 44 if g.portrait else 36
    mid = g.width // 2
    hitboxes.append(Hitbox("month", 0, mid, y, y + month_h, page=month - 1))
    hitboxes.append(Hitbox("month", mid, g.width, y, y + month_h, page=month + 1))
    arrow = 20
    _draw_disclosure(d, BOX_X, y + (month_h - arrow) // 2, arrow, "left")
    _draw_disclosure(d, g.text_right - arrow, y + (month_h - arrow) // 2, arrow, "right")
    label = date(year, mon, 1).strftime("%B %Y")
    d.text(((g.width - _text_width(d, label, f.header)) // 2, y + (month_h - 24) // 2),
           label, font=f.header, fill=BLACK)
    y += month_h

    # --- weekday initials and the grid ---------------------------------------
    cell_w = (g.text_right - MARGIN) / 7
    for i, name in enumerate("MTWTFSS"):
        cx = MARGIN + cell_w * (i + 0.5)
        d.text((cx - _text_width(d, name, f.footer) / 2, y), name, font=f.footer, fill=BLACK)
    y += 22
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, mon)
    # Six rows' worth of height whatever the month, so the grid never jumps
    # between a four- and a six-week month.
    cell_h = min(64, (fy - 4 - y) // 6)
    for row, week in enumerate(weeks):
        cy0 = y + row * cell_h
        for col, day in enumerate(week):
            if day.month != mon:
                continue
            x0 = int(MARGIN + cell_w * col)
            x1 = int(MARGIN + cell_w * (col + 1))
            if day == selected or day == today:
                d.rectangle([x0 + 3, cy0 + 3, x1 - 3, cy0 + cell_h - 3], outline=BLACK,
                            width=3 if day == selected else 1)
            text = str(day.day)
            d.text(((x0 + x1 - _text_width(d, text, f.header_right)) // 2,
                    cy0 + (cell_h - 24) // 2), text, font=f.header_right, fill=BLACK)
            hitboxes.append(Hitbox("choose_due", x0, x1, cy0, cy0 + cell_h, task_id=task.id,
                                   key=day.isoformat()))
    return hitboxes
