"""Layout and hit-testing. Run: python3 -m unittest discover tests"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import replace  # noqa: E402

from panel.client import Area, ChecklistItem, Project, Task  # noqa: E402
from panel.pickers import month_index, render_picker  # noqa: E402
from panel.layout import (  # noqa: E402
    _format_due, _organize_tasks, _relative_age, compute_group_order, hit, render, render_detail,
)
from panel.theme import (  # noqa: E402
    BLACK, COLLAPSED_FOOTER_H, DETAIL_DESC_MIN_LINES_WITH_CHECKLIST, HEADER_H, HEIGHT,
    MARGIN, ROW_H, ROWS,
    STAR_ZONE_W, TEXT_RIGHT, TEXT_X, WHITE, WIDTH,
)


def task(n: int, title: str = None, checklist=None, **kw) -> Task:
    return Task(
        id="task-%d" % n,
        title=title or "Task number %d" % n,
        project=kw.get("project"),
        area=kw.get("area"),
        contexts=kw.get("contexts", []),
        focused=kw.get("focused", False),
        created_at=kw.get("created_at"),
        due_date=kw.get("due_date"),
        description=kw.get("description"),
        checklist=checklist or [],
    )


def item(n: int, completed: bool = False, title: str = None) -> ChecklistItem:
    return ChecklistItem(id="item-%d" % n, title=title or "Item %d" % n, completed=completed)


# The header's own controls (view tabs, focus-sticky, sort direction, area/
# project/due toggles, dark mode, lock) are always present regardless of task content — real,
# static hitboxes, not something tests should mistake for "the header has
# nothing tappable". The active tab is "home" on the list ("back" on a task).
HEADER_CONTROL_KINDS = {
    "home", "view", "focus_sticky", "sort_toggle", "toggle_project_view", "toggle_area_view",
    "toggle_due_first", "toggle_dark", "toggle_touch_lock", "lock_screen", "toggle_rotate",
}


def _non_header_hitboxes(hitboxes):
    return [b for b in hitboxes if b.kind not in HEADER_CONTROL_KINDS]


class TestRender(unittest.TestCase):
    def test_is_one_bit_at_panel_size(self):
        image = render([task(1)], "next").image
        self.assertEqual(image.mode, "1")
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_shows_at_most_one_screenful_when_it_all_fits(self):
        rendered = render([task(i) for i in range(ROWS)], "next")
        # Three hitboxes (checkbox / text / star) per visible row, no pager.
        self.assertEqual(len(_non_header_hitboxes(rendered.hitboxes)), ROWS * 3)

    def test_list_row_shows_relative_age(self):
        now = datetime(2026, 1, 30)
        with_age = render([task(1, created_at=now - timedelta(days=16))], "next", now=now).image
        without = render([task(1)], "next", now=now).image
        self.assertNotEqual(with_age.tobytes(), without.tobytes())

    def test_list_row_shows_due_date(self):
        now = datetime(2026, 1, 30)
        with_due = render([task(1, due_date=now + timedelta(days=2))], "next", now=now).image
        without = render([task(1)], "next", now=now).image
        self.assertNotEqual(with_due.tobytes(), without.tobytes())

    def test_header_controls_are_always_present(self):
        rendered = render([task(i) for i in range(ROWS)], "focus")
        self.assertEqual({b.kind for b in rendered.hitboxes} & HEADER_CONTROL_KINDS, HEADER_CONTROL_KINDS)

    def test_empty_list_renders_without_task_hitboxes(self):
        rendered = render([], "next")
        self.assertEqual(_non_header_hitboxes(rendered.hitboxes), [])

    def test_long_title_is_ellipsized_not_overflowed(self):
        # Failure mode this guards: text running off the panel edge, which is
        # invisible in a PNG viewer that crops but obvious on glass.
        render([task(1, "x" * 400)], "next")

    def test_stale_marker_only_when_offline(self):
        fresh = render([task(1)], "next").image
        stale = render([task(1)], "next", stale_since=datetime(2026, 9, 2, 9, 0)).image
        self.assertNotEqual(fresh.tobytes(), stale.tobytes())

    def test_completing_flash_changes_the_image(self):
        plain = render([task(1)], "next").image
        flashed = render([task(1)], "next", completing="task-1").image
        self.assertNotEqual(plain.tobytes(), flashed.tobytes())


class TestFocusRendering(unittest.TestCase):
    def test_boundary_rule_is_drawn_between_focused_and_backlog(self):
        split = [task(0, focused=True), task(1), task(2)]
        flat = [task(0), task(1), task(2)]
        self.assertNotEqual(
            render(split, "focus").image.tobytes(),
            render(flat, "focus").image.tobytes(),
        )

    def test_header_counts_focused_not_backlog(self):
        one = render([task(0, focused=True)] + [task(i) for i in range(1, 9)], "focus")
        two = render([task(0, focused=True), task(1, focused=True)]
                     + [task(i) for i in range(2, 9)], "focus")
        self.assertNotEqual(one.image.tobytes(), two.image.tobytes())

    def test_boundary_rule_appears_before_a_group_header_too(self):
        # Old bug: the heavy rule only fired when the next entry was another
        # task, so a focused task immediately followed by the first group
        # header of the backlog (exactly this case) drew no boundary at all.
        tasks = [task(0, project="Alpha", focused=True), task(1, project="Beta")]
        rendered = render(tasks, "focus", group_by_project=True)
        y1 = HEADER_H + ROW_H
        # The heavy rule reaches all the way to MARGIN; an ordinary row
        # separator only starts at TEXT_X, so a black pixel this far left
        # is only possible if the full-width boundary line was drawn here.
        self.assertEqual(rendered.image.getpixel((MARGIN + 2, y1 + 1)), BLACK)

    def test_no_boundary_when_focus_sticky_is_off_even_with_grouping(self):
        tasks = [task(0, project="Alpha", focused=True), task(1, project="Beta")]
        rendered = render(tasks, "focus", group_by_project=True, focus_sticky=False)
        y1 = HEADER_H + ROW_H
        self.assertEqual(rendered.image.getpixel((MARGIN + 2, y1 + 1)), WHITE)

    def test_grouped_backlog_row_hides_project_but_leading_focused_row_does_not(self):
        # The leading focused-section row has no group header above it, so
        # its meta line must still show the project; a backlog row sitting
        # under "Alpha" doesn't need to repeat it.
        tasks = [
            task(0, project="Alpha", focused=True, title="Focused one"),
            task(1, project="Alpha", title="Backlog one"),
        ]
        rendered = render(tasks, "focus", group_by_project=True)
        # Render without grouping as a control: the focused row's meta line
        # (containing "Alpha") should render identically whether or not
        # project grouping is toggled on, since it's never in_group.
        ungrouped = render(tasks, "focus", group_by_project=False)
        # Crop just the focused row (row 0) from each and compare — the
        # grouped backlog row's differing "hide project" state lives below
        # this crop and mustn't be the reason for any difference here.
        row0_h = ROW_H
        grouped_row0 = rendered.image.crop((0, HEADER_H, WIDTH, HEADER_H + row0_h))
        plain_row0 = ungrouped.image.crop((0, HEADER_H, WIDTH, HEADER_H + row0_h))
        self.assertEqual(grouped_row0.tobytes(), plain_row0.tobytes())


class TestListHitTesting(unittest.TestCase):
    def setUp(self):
        self.rendered = render([task(i) for i in range(ROWS)], "next")

    def test_checkbox_column_completes(self):
        y = HEADER_H + ROW_H // 2
        box = hit(self.rendered.hitboxes, (10, y))
        self.assertEqual((box.kind, box.task_id), ("complete", "task-0"))

    def test_text_column_opens(self):
        y = HEADER_H + ROW_H // 2
        box = hit(self.rendered.hitboxes, (TEXT_X + 10, y))
        self.assertEqual((box.kind, box.task_id), ("open", "task-0"))

    def test_tap_in_header_gap_or_footer_hits_nothing(self):
        # Between the control cluster and the right-aligned date text —
        # not one of the header's real controls, and not the footer either.
        self.assertIsNone(hit(self.rendered.hitboxes, (650, 10)))
        self.assertIsNone(hit(self.rendered.hitboxes, (400, HEIGHT - 10)))

    def test_row_boundaries_do_not_overlap(self):
        opens = [b for b in self.rendered.hitboxes if b.kind == "open"]
        for earlier, later in zip(opens, opens[1:]):
            self.assertEqual(earlier.y1, later.y0)

    def test_star_column_toggles_focus(self):
        y = HEADER_H + ROW_H // 2
        box = hit(self.rendered.hitboxes, (WIDTH - STAR_ZONE_W // 2, y))
        self.assertEqual((box.kind, box.task_id), ("star", "task-0"))


def _forward_pager(hitboxes, kind):
    """The right-half (next-page) hitbox of a kind that now has two — the
    left half is previous. Identified by position, not list order, so a
    reordering of the append calls in layout.py can't silently break this."""
    candidates = [b for b in hitboxes if b.kind == kind]
    return max(candidates, key=lambda b: b.x0) if candidates else None


def _backward_pager(hitboxes, kind):
    candidates = [b for b in hitboxes if b.kind == kind]
    return min(candidates, key=lambda b: b.x0) if candidates else None


class TestListPagination(unittest.TestCase):
    def test_no_pager_when_it_all_fits(self):
        rendered = render([task(i) for i in range(ROWS)], "next")
        self.assertEqual([b for b in rendered.hitboxes if b.kind == "list_page"], [])

    def test_pager_has_independent_forward_and_back_zones(self):
        tasks = [task(i) for i in range(25)]
        rendered = render(tasks, "next")
        pagers = [b for b in rendered.hitboxes if b.kind == "list_page"]
        self.assertEqual(len(pagers), 2)
        fwd, back = _forward_pager(rendered.hitboxes, "list_page"), _backward_pager(rendered.hitboxes, "list_page")
        self.assertNotEqual(fwd.page, back.page)
        self.assertLess(back.x1, fwd.x0 + 1)  # back zone sits left of forward zone

    def test_pager_appears_and_reaches_every_task(self):
        tasks = [task(i) for i in range(25)]
        rendered = render(tasks, "next")
        seen = {b.task_id for b in rendered.hitboxes if b.kind == "open"}
        next_page = _forward_pager(rendered.hitboxes, "list_page").page
        guard = 0
        while next_page != 0 and guard < 20:
            r = render(tasks, "next", page=next_page)
            seen |= {b.task_id for b in r.hitboxes if b.kind == "open"}
            fwd = _forward_pager(r.hitboxes, "list_page")
            next_page = fwd.page if fwd else 0
            guard += 1
        self.assertEqual(seen, {"task-%d" % i for i in range(25)})

    def test_backward_pager_also_reaches_every_task(self):
        tasks = [task(i) for i in range(25)]
        rendered = render(tasks, "next")
        seen = {b.task_id for b in rendered.hitboxes if b.kind == "open"}
        prev_page = _backward_pager(rendered.hitboxes, "list_page").page
        guard = 0
        while prev_page != 0 and guard < 20:
            r = render(tasks, "next", page=prev_page)
            seen |= {b.task_id for b in r.hitboxes if b.kind == "open"}
            back = _backward_pager(r.hitboxes, "list_page")
            prev_page = back.page if back else 0
            guard += 1
        self.assertEqual(seen, {"task-%d" % i for i in range(25)})

    def test_boundary_rule_survives_a_page_split(self):
        # The focused/backlog split falls right between page 0's last row
        # and page 1's first — render() must look at the global list to
        # find it, not just the tasks visible on the current page.
        tasks = [task(i, focused=True) for i in range(5)] + [task(i) for i in range(5, 10)]
        plain = render(tasks, "focus").image
        no_boundary = render([task(i, focused=True) for i in range(10)], "focus").image
        self.assertNotEqual(plain.tobytes(), no_boundary.tobytes())


def _dt(n: int) -> datetime:
    return datetime(2026, 1, 1 + n)


class TestOrganizeTasks(unittest.TestCase):
    def test_focus_sticky_wins_over_sort_direction(self):
        # Oldest-first sort, but the focused task (created most recently of
        # the two) still leads — starring is a stronger signal than age.
        tasks = [task(0, created_at=_dt(1)), task(1, focused=True, created_at=_dt(3))]
        entries, allow_boundary = _organize_tasks(tasks, False, True, False, False)
        self.assertEqual([e[1].id for e in entries], ["task-1", "task-0"])
        self.assertTrue(allow_boundary)

    def test_focus_sticky_off_lets_sort_interleave_focused_and_not(self):
        tasks = [task(0, focused=True, created_at=_dt(1)), task(1, created_at=_dt(3))]
        entries, allow_boundary = _organize_tasks(tasks, True, False, False, False)
        self.assertEqual([e[1].id for e in entries], ["task-1", "task-0"])  # pure date order
        self.assertFalse(allow_boundary)

    def test_newest_first_sorts_by_created_date_descending(self):
        tasks = [task(0, created_at=_dt(1)), task(1, created_at=_dt(3)), task(2, created_at=_dt(2))]
        entries, _ = _organize_tasks(tasks, True, False, False, False)
        self.assertEqual([e[1].id for e in entries], ["task-1", "task-2", "task-0"])

    def test_oldest_first_sorts_ascending(self):
        tasks = [task(0, created_at=_dt(1)), task(1, created_at=_dt(3)), task(2, created_at=_dt(2))]
        entries, _ = _organize_tasks(tasks, False, False, False, False)
        self.assertEqual([e[1].id for e in entries], ["task-0", "task-2", "task-1"])

    def test_missing_created_at_sorts_to_the_oldest_end(self):
        tasks = [task(0, created_at=_dt(1)), task(1, created_at=None)]
        newest_first, _ = _organize_tasks(tasks, True, False, False, False)
        self.assertEqual(newest_first[-1][1].id, "task-1")

    def test_project_grouping_buckets_with_a_fallback_for_unfiled_tasks(self):
        tasks = [task(0, project="Alpha"), task(1, project=None), task(2, project="Alpha")]
        entries, allow_boundary = _organize_tasks(tasks, False, True, False, True)
        kinds_and_labels = [(e[0], e[1] if e[0] == "header" else e[1].id) for e in entries]
        self.assertEqual(
            kinds_and_labels,
            [("header", "Alpha"), ("task", "task-0"), ("task", "task-2"),
             ("header", "No project"), ("task", "task-1")],
        )
        self.assertTrue(allow_boundary)  # reflects focus_sticky, not grouping

    def test_project_header_carries_the_project_icon(self):
        entries, _ = _organize_tasks([task(0, project="Alpha")], False, True, False, True)
        self.assertEqual(entries[0], ("header", "Alpha", "project"))

    def test_newest_first_orders_project_headers_by_each_projects_newest_task(self):
        # Alpha's tasks are both older than Beta's — newest-first should put
        # Beta's header first, using each project's own newest task as its
        # ordering key, not whichever task happened to be fetched first.
        tasks = [
            task(0, project="Alpha", created_at=_dt(1)),
            task(1, project="Alpha", created_at=_dt(2)),
            task(2, project="Beta", created_at=_dt(3)),
            task(3, project="Beta", created_at=_dt(4)),
        ]
        entries, _ = _organize_tasks(tasks, True, False, False, True)
        headers = [e[1] for e in entries if e[0] == "header"]
        self.assertEqual(headers, ["Beta", "Alpha"])

    def test_oldest_first_orders_project_headers_by_each_projects_oldest_task(self):
        # Same tasks, opposite toggle: oldest-first orders by each project's
        # own oldest task, which flips Alpha back in front of Beta.
        tasks = [
            task(0, project="Alpha", created_at=_dt(1)),
            task(1, project="Alpha", created_at=_dt(2)),
            task(2, project="Beta", created_at=_dt(3)),
            task(3, project="Beta", created_at=_dt(4)),
        ]
        entries, _ = _organize_tasks(tasks, False, False, False, True)
        headers = [e[1] for e in entries if e[0] == "header"]
        self.assertEqual(headers, ["Alpha", "Beta"])

    def test_no_project_sorts_last_regardless_of_sort_direction(self):
        # "No project" isn't a real project with a meaningful recency — its
        # task here is the newest of the three, which would otherwise put it
        # first under newest-first and split up the real groups around it.
        tasks = [
            task(0, project="Alpha", created_at=_dt(1)),
            task(1, project=None, created_at=_dt(5)),
            task(2, project="Beta", created_at=_dt(3)),
        ]
        newest, _ = _organize_tasks(tasks, True, False, False, True)
        oldest, _ = _organize_tasks(tasks, False, False, False, True)
        self.assertEqual([e[1] for e in newest if e[0] == "header"], ["Beta", "Alpha", "No project"])
        self.assertEqual([e[1] for e in oldest if e[0] == "header"], ["Alpha", "Beta", "No project"])

    def test_no_area_sorts_last_regardless_of_sort_direction(self):
        tasks = [
            task(0, area="Digital", created_at=_dt(1)),
            task(1, area=None, created_at=_dt(5)),
            task(2, area="Music", created_at=_dt(3)),
        ]
        newest, _ = _organize_tasks(tasks, True, False, True, False)
        headers = [e[1] for e in newest if e[0] == "header"]
        self.assertEqual(headers, ["Music", "Digital", "No area"])

    def test_frozen_group_order_survives_completing_a_groups_oldest_task(self):
        # Oldest-first: Alpha leads only because of task 0. Completing it
        # would, unfrozen, make Beta (created day 2) the oldest group.
        tasks = [
            task(0, project="Alpha", created_at=_dt(1)),
            task(1, project="Beta", created_at=_dt(2)),
            task(2, project="Alpha", created_at=_dt(3)),
        ]
        frozen = compute_group_order(tasks, False, True, False, True)
        remaining = tasks[1:]
        live, _ = _organize_tasks(remaining, False, True, False, True)
        held, _ = _organize_tasks(remaining, False, True, False, True, group_order=frozen)
        self.assertEqual([e[1] for e in live if e[0] == "header"], ["Beta", "Alpha"])
        self.assertEqual([e[1] for e in held if e[0] == "header"], ["Alpha", "Beta"])

    def test_groups_new_since_the_snapshot_follow_known_ones(self):
        frozen = compute_group_order([task(0, project="Beta", created_at=_dt(5))], False, True, False, True)
        tasks = [task(0, project="Beta", created_at=_dt(5)), task(1, project="Alpha", created_at=_dt(1))]
        entries, _ = _organize_tasks(tasks, False, True, False, True, group_order=frozen)
        self.assertEqual([e[1] for e in entries if e[0] == "header"], ["Beta", "Alpha"])

    def test_area_grouping_alone_ignores_project(self):
        tasks = [task(0, area="Digital", project="Alpha"), task(1, area="Digital", project="Beta")]
        entries, _ = _organize_tasks(tasks, False, True, True, False)
        # One area header, then both tasks directly underneath — no project
        # sub-headers since group_by_project is off.
        self.assertEqual([e[0] for e in entries], ["header", "task", "task"])
        self.assertEqual(entries[0][2], "area")

    def test_area_and_project_combine_into_one_header_line_not_nested(self):
        tasks = [task(0, area="Digital", project="Alpha"), task(1, area="Digital", project="Beta")]
        entries, _ = _organize_tasks(tasks, False, True, True, True)
        headers = [e for e in entries if e[0] == "header"]
        self.assertEqual([h[1] for h in headers], ["Digital / Alpha", "Digital / Beta"])
        self.assertTrue(all(h[2] == "both" for h in headers))

    def test_area_without_a_project_does_not_say_no_project(self):
        entries, _ = _organize_tasks(
            [task(0, area="Digital", project=None)], False, True, True, True
        )
        header = next(e for e in entries if e[0] == "header")
        self.assertEqual(header[1], "Digital")

    def test_focus_sticky_pulls_focused_tasks_out_of_grouping_into_their_own_section(self):
        # Two focused tasks in different projects still lead together, flat,
        # with no header of their own — grouping only takes effect once the
        # backlog starts underneath them.
        tasks = [
            task(0, project="Alpha", focused=True, created_at=_dt(1)),
            task(1, project="Alpha", created_at=_dt(2)),
            task(2, project="Beta", focused=True, created_at=_dt(3)),
            task(3, project="Beta", created_at=_dt(4)),
        ]
        entries, allow_boundary = _organize_tasks(tasks, False, True, False, True)
        self.assertEqual(
            entries,
            [
                ("task", tasks[0], False),
                ("task", tasks[2], False),
                ("header", "Alpha", "project"),
                ("task", tasks[1], True),
                ("header", "Beta", "project"),
                ("task", tasks[3], True),
            ],
        )
        self.assertTrue(allow_boundary)

    def test_focus_sticky_off_lets_grouping_bucket_everything_including_focused(self):
        # With the sticky section off, a focused task is just a task —
        # grouping buckets it by project like any other, no leading section.
        tasks = [task(0, project="Alpha", focused=True), task(1, project="Beta")]
        entries, allow_boundary = _organize_tasks(tasks, False, False, False, True)
        self.assertEqual([e[0] for e in entries], ["header", "task", "header", "task"])
        self.assertTrue(all(e[2] for e in entries if e[0] == "task"))
        self.assertFalse(allow_boundary)

    def test_no_focused_tasks_means_no_leading_section_even_with_sticky_on(self):
        tasks = [task(0, project="Alpha"), task(1, project="Beta")]
        entries, _ = _organize_tasks(tasks, False, True, False, True)
        self.assertEqual([e[0] for e in entries], ["header", "task", "header", "task"])

    def test_due_first_pulls_due_dated_backlog_tasks_to_the_top_soonest_first(self):
        tasks = [
            task(0, created_at=_dt(1)),
            task(1, created_at=_dt(2), due_date=_dt(10)),
            task(2, created_at=_dt(3), due_date=_dt(5)),
        ]
        entries, _ = _organize_tasks(tasks, False, False, False, False, due_first=True)
        # Soonest due date leads regardless of created_at/sort_newest; the
        # one task with no due date falls back to plain sort_newest order.
        self.assertEqual([e[1].id for e in entries], ["task-2", "task-1", "task-0"])

    def test_due_first_never_reorders_the_focused_leading_section(self):
        # "Non-starred list" means exactly that: a focused task with no due
        # date still leads, ahead of a backlog task that has one.
        tasks = [
            task(0, focused=True, created_at=_dt(1)),
            task(1, created_at=_dt(2), due_date=_dt(3)),
        ]
        entries, _ = _organize_tasks(tasks, False, True, False, False, due_first=True)
        self.assertEqual([e[1].id for e in entries], ["task-0", "task-1"])

    def test_due_first_applies_within_each_group_not_across_groups(self):
        tasks = [
            task(0, project="Alpha", created_at=_dt(1)),
            task(1, project="Alpha", created_at=_dt(2), due_date=_dt(9)),
            task(2, project="Beta", created_at=_dt(3)),
        ]
        entries, _ = _organize_tasks(tasks, False, False, False, True, due_first=True)
        # Group header order is untouched by due_first (still sort_newest,
        # oldest-first here); within Alpha, the due-dated task leads.
        self.assertEqual(
            [(e[0], e[1] if e[0] == "header" else e[1].id) for e in entries],
            [("header", "Alpha"), ("task", "task-1"), ("task", "task-0"), ("header", "Beta"), ("task", "task-2")],
        )

    def test_due_first_off_ignores_due_dates_entirely(self):
        tasks = [
            task(0, created_at=_dt(1)),
            task(1, created_at=_dt(2), due_date=_dt(3)),
        ]
        entries, _ = _organize_tasks(tasks, False, False, False, False, due_first=False)
        self.assertEqual([e[1].id for e in entries], ["task-0", "task-1"])


class TestRelativeAgeAndDueFormatting(unittest.TestCase):
    def test_relative_age_buckets(self):
        now = datetime(2026, 1, 30)
        self.assertEqual(_relative_age(now, now), "added today")
        self.assertEqual(_relative_age(now - timedelta(days=1), now), "added yesterday")
        self.assertEqual(_relative_age(now - timedelta(days=5), now), "added 5d ago")
        self.assertEqual(_relative_age(now - timedelta(days=14), now), "added 2w ago")
        self.assertEqual(_relative_age(now - timedelta(days=365), now), "added 12mo ago")
        self.assertEqual(_relative_age(now - timedelta(days=365 * 6), now), "added 6y ago")

    def test_format_due_buckets(self):
        now = datetime(2026, 1, 30)
        self.assertEqual(_format_due(now - timedelta(days=10), now), "overdue 10d")
        self.assertEqual(_format_due(now - timedelta(days=1), now), "overdue 1d")
        self.assertEqual(_format_due(now, now), "due today")
        self.assertEqual(_format_due(now + timedelta(days=1), now), "due tomorrow")
        self.assertEqual(_format_due(now + timedelta(days=4), now), "due in 4d")
        far = now + timedelta(days=30)
        self.assertEqual(_format_due(far, now), "due %s" % far.strftime("%-d %b"))


class TestDetailRendering(unittest.TestCase):
    def test_is_one_bit_at_panel_size(self):
        image = render_detail(task(1)).image
        self.assertEqual(image.mode, "1")
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_long_title_wraps_rather_than_overflows(self):
        render_detail(task(1, "x " * 60))

    def test_created_at_shows_relative_age_on_the_meta_row(self):
        now = _dt(30)
        with_age = render_detail(task(1, created_at=_dt(20)), now=now).image
        without = render_detail(task(1), now=now).image
        self.assertNotEqual(with_age.tobytes(), without.tobytes())

    def test_due_date_shows_on_the_meta_row(self):
        now = datetime(2026, 1, 30)
        with_due = render_detail(task(1, due_date=now + timedelta(days=5)), now=now).image
        without = render_detail(task(1), now=now).image
        self.assertNotEqual(with_due.tobytes(), without.tobytes())

    def test_unchecked_items_render_before_done_ones(self):
        # Order sent in: done, done, unchecked. The render should put the
        # unchecked item's toggle hitbox first, matching "unchecked at top".
        checklist = [item(0, completed=True), item(1, completed=True), item(2, completed=False)]
        rendered = render_detail(task(1, checklist=checklist))
        toggles = [b for b in rendered.hitboxes if b.kind == "toggle"]
        self.assertEqual(toggles[0].item_id, "item-2")

    def test_checklist_overflow_is_capped_not_overflowed(self):
        checklist = [item(i) for i in range(50)]
        rendered = render_detail(task(1, checklist=checklist))
        toggles = [b for b in rendered.hitboxes if b.kind == "toggle"]
        self.assertLess(len(toggles), 50)

    def test_no_checklist_renders_without_toggle_hitboxes(self):
        rendered = render_detail(task(1))
        self.assertEqual([b for b in rendered.hitboxes if b.kind == "toggle"], [])

    def test_six_checklist_items_fit_without_a_pager(self):
        # The footer is a bare rule and progress rides the stacked row, so a
        # six-item checklist (a normal shopping list) needs no paging at all.
        rendered = render_detail(task(1, checklist=[item(n) for n in range(6)]))
        self.assertEqual(len([b for b in rendered.hitboxes if b.kind == "toggle"]), 6)
        self.assertEqual([b for b in rendered.hitboxes if b.kind == "page"], [])

    def test_paged_checklist_still_shows_six_rows_above_a_thin_pager(self):
        rendered = render_detail(task(1, checklist=[item(n) for n in range(12)]))
        self.assertEqual(len([b for b in rendered.hitboxes if b.kind == "toggle"]), 6)
        pagers = [b for b in rendered.hitboxes if b.kind == "page"]
        self.assertEqual(len(pagers), 2)
        self.assertLessEqual(pagers[0].y1, HEIGHT - COLLAPSED_FOOTER_H)

    def test_active_tab_goes_back(self):
        rendered = render_detail(task(1))
        box = hit(rendered.hitboxes, (20, 20))
        self.assertEqual(box.kind, "back")

    def test_header_keeps_the_list_controls(self):
        # The whole bar stays: same control hitboxes as the list's, so lock,
        # dark and the view toggles are reachable without leaving the task.
        detail = {b.kind for b in render_detail(task(1)).hitboxes if b.y0 == 0}
        listing = {b.kind for b in render([task(1)], "focus").hitboxes if b.y0 == 0}
        self.assertEqual(detail, (listing - {"home"}) | {"back"})

    def test_title_checkbox_completes_the_task(self):
        rendered = render_detail(task(1))
        box = hit(rendered.hitboxes, (10, HEADER_H + 20))
        self.assertEqual((box.kind, box.task_id), ("complete", "task-1"))

    def test_title_star_toggles_focus(self):
        rendered = render_detail(task(1))
        box = hit(rendered.hitboxes, (TEXT_RIGHT - 10, HEADER_H + 20))
        self.assertEqual((box.kind, box.task_id), ("star", "task-1"))

    def test_portrait_detail_title_wraps_further_than_landscape(self):
        # A long title on the tall canvas gets more lines before the "…";
        # landscape keeps its two, so its checklist keeps its rows.
        long_title = " ".join(["word%d" % n for n in range(30)])
        def complete_box_h(**kw):
            r = render_detail(task(1, title=long_title), **kw)
            box = next(b for b in r.hitboxes if b.kind == "complete")
            return box.y1 - box.y0
        self.assertGreater(complete_box_h(portrait=True), complete_box_h() + 60)

    def test_checklist_item_toggle_is_only_the_checkbox_column(self):
        # Same TEXT_X split as list rows: the text is left free for unfolding
        # a long item, so a tap there must never tick it by accident.
        checklist = [item(0)]
        rendered = render_detail(task(1, checklist=checklist))
        toggle = next(b for b in rendered.hitboxes if b.kind == "toggle")
        self.assertEqual((toggle.x0, toggle.x1), (0, TEXT_X))

    def test_short_item_text_has_no_expand_zone(self):
        rendered = render_detail(task(1, checklist=[item(0)]))
        self.assertFalse([b for b in rendered.hitboxes if b.kind == "expand_item"])

    def test_long_item_unfolds_to_a_taller_row(self):
        long_title = " ".join(["word%d" % n for n in range(40)])
        checklist = [item(0, title=long_title), item(1)]
        folded = render_detail(task(1, checklist=checklist))
        box = next(b for b in folded.hitboxes if b.kind == "expand_item")
        self.assertEqual((box.item_id, box.x0, box.page), ("item-0", TEXT_X, 0))
        unfolded = render_detail(task(1, checklist=checklist), expanded_items=frozenset({"item-0"}))
        def row_h(r):
            return next(b for b in r.hitboxes if b.kind == "toggle" and b.item_id == "item-0")
        self.assertGreater(row_h(unfolded).y1 - row_h(unfolded).y0, row_h(folded).y1 - row_h(folded).y0)
        # The next item moves down by exactly the extra height.
        def y_of(r, item_id):
            return next(b.y0 for b in r.hitboxes if b.kind == "toggle" and b.item_id == item_id)
        self.assertGreater(y_of(unfolded, "item-1"), y_of(folded, "item-1"))

    def test_unfolding_an_item_pushed_off_the_page_points_at_its_new_page(self):
        long_title = " ".join(["word%d" % n for n in range(40)])
        # Five short items then a long one: six fit one page folded, but the
        # long one unfolded no longer fits under the other five.
        checklist = [item(n) for n in range(5)] + [item(5, title=long_title)]
        folded = render_detail(task(1, checklist=checklist))
        self.assertFalse([b for b in folded.hitboxes if b.kind == "page"])
        box = next(b for b in folded.hitboxes if b.kind == "expand_item")
        self.assertEqual(box.page, 1)
        unfolded = render_detail(
            task(1, checklist=checklist), expanded_items=frozenset({"item-5"}), page=box.page,
        )
        self.assertTrue(any(b.kind == "toggle" and b.item_id == "item-5" for b in unfolded.hitboxes))
        self.assertTrue([b for b in unfolded.hitboxes if b.kind == "page"])

    def test_completing_flash_changes_the_image(self):
        plain = render_detail(task(1)).image
        flashed = render_detail(task(1), completing=True).image
        self.assertNotEqual(plain.tobytes(), flashed.tobytes())

    def test_done_items_stack_into_one_row_by_default(self):
        checklist = [item(0, completed=True), item(1, completed=True), item(2, completed=False)]
        rendered = render_detail(task(1, checklist=checklist))
        toggles = [b for b in rendered.hitboxes if b.kind == "toggle"]
        expands = [b for b in rendered.hitboxes if b.kind == "expand"]
        # Only the unchecked item gets its own row; the two done ones fold
        # into a single "tap to show" row instead of two more toggle rows.
        self.assertEqual([b.item_id for b in toggles], ["item-2"])
        self.assertEqual(len(expands), 1)

    def test_expanded_reveals_every_item_individually(self):
        checklist = [item(0, completed=True), item(1, completed=True), item(2, completed=False)]
        rendered = render_detail(task(1, checklist=checklist), expanded=True)
        toggles = {b.item_id for b in rendered.hitboxes if b.kind == "toggle"}
        self.assertEqual(toggles, {"item-0", "item-1", "item-2"})
        self.assertEqual([b for b in rendered.hitboxes if b.kind == "expand"], [])

    def test_expanded_shows_a_collapse_control(self):
        checklist = [item(0, completed=True), item(1, completed=False)]
        rendered = render_detail(task(1, checklist=checklist), expanded=True)
        collapses = [b for b in rendered.hitboxes if b.kind == "collapse"]
        self.assertEqual(len(collapses), 1)
        self.assertEqual(collapses[0].task_id, "task-1")

    def test_no_done_items_means_no_stack_row_even_when_collapsed(self):
        checklist = [item(0, completed=False), item(1, completed=False)]
        rendered = render_detail(task(1, checklist=checklist), expanded=False)
        self.assertEqual([b for b in rendered.hitboxes if b.kind == "expand"], [])
        self.assertEqual(len([b for b in rendered.hitboxes if b.kind == "toggle"]), 2)

    def test_footer_more_count_accounts_for_stacked_items(self):
        # 1 unchecked + 40 done: the done ones fold into one row, so nothing
        # should be reported as hidden beyond that.
        checklist = [item(0, completed=False)] + [item(i, completed=True) for i in range(1, 41)]
        rendered = render_detail(task(1, checklist=checklist))
        toggles = [b for b in rendered.hitboxes if b.kind == "toggle"]
        expands = [b for b in rendered.hitboxes if b.kind == "expand"]
        self.assertEqual(len(toggles), 1)
        self.assertEqual(len(expands), 1)

    def test_completed_item_checkmark_changes_the_image(self):
        unchecked_checklist = [item(0, completed=False)]
        done_checklist = [item(0, completed=True)]
        plain = render_detail(task(1, checklist=unchecked_checklist)).image
        ticked = render_detail(task(1, checklist=done_checklist), expanded=True).image
        self.assertNotEqual(plain.tobytes(), ticked.tobytes())

    def test_pager_has_independent_forward_and_back_zones(self):
        checklist = [item(i, completed=False) for i in range(30)]
        rendered = render_detail(task(1, checklist=checklist))
        pages = [b for b in rendered.hitboxes if b.kind == "page"]
        self.assertEqual(len(pages), 2)
        fwd = _forward_pager(rendered.hitboxes, "page")
        back = _backward_pager(rendered.hitboxes, "page")
        self.assertNotEqual(fwd.page, back.page)
        self.assertLess(back.x1, fwd.x0 + 1)

    def test_many_unchecked_items_paginate_rather_than_vanish(self):
        checklist = [item(i, completed=False) for i in range(30)]
        rendered = render_detail(task(1, checklist=checklist))
        # Every item must be reachable across some page, not just page 0 —
        # walk the forward pager's target until back at 0, collecting ids.
        seen = {b.item_id for b in rendered.hitboxes if b.kind == "toggle"}
        next_page = _forward_pager(rendered.hitboxes, "page").page
        guard = 0
        while next_page != 0 and guard < 20:
            r = render_detail(task(1, checklist=checklist), page=next_page)
            seen |= {b.item_id for b in r.hitboxes if b.kind == "toggle"}
            fwd = _forward_pager(r.hitboxes, "page")
            next_page = fwd.page if fwd else 0
            guard += 1
        self.assertEqual(seen, {"item-%d" % i for i in range(30)})

    def test_backward_pager_also_reaches_every_item(self):
        checklist = [item(i, completed=False) for i in range(30)]
        rendered = render_detail(task(1, checklist=checklist))
        seen = {b.item_id for b in rendered.hitboxes if b.kind == "toggle"}
        prev_page = _backward_pager(rendered.hitboxes, "page").page
        guard = 0
        while prev_page != 0 and guard < 20:
            r = render_detail(task(1, checklist=checklist), page=prev_page)
            seen |= {b.item_id for b in r.hitboxes if b.kind == "toggle"}
            back = _backward_pager(r.hitboxes, "page")
            prev_page = back.page if back else 0
            guard += 1
        self.assertEqual(seen, {"item-%d" % i for i in range(30)})

    def test_page_wraps_around_from_the_last_page(self):
        checklist = [item(i, completed=False) for i in range(30)]
        first = render_detail(task(1, checklist=checklist))
        first_page_control = _forward_pager(first.hitboxes, "page")
        last = render_detail(task(1, checklist=checklist), page=first_page_control.page)
        # However many pages there are, walking "next" from page 0 and
        # landing back on 0 confirms wraparound rather than running off past
        # the end into an empty page.
        pages_visited = {0, first_page_control.page}
        p = _forward_pager(last.hitboxes, "page")
        while p is not None and p.page not in pages_visited:
            pages_visited.add(p.page)
            r = render_detail(task(1, checklist=checklist), page=p.page)
            p = _forward_pager(r.hitboxes, "page")
        self.assertIn(0, pages_visited)

    def test_out_of_range_page_clamps_instead_of_rendering_empty(self):
        checklist = [item(i, completed=False) for i in range(30)]
        rendered = render_detail(task(1, checklist=checklist), page=999)
        toggles = [b for b in rendered.hitboxes if b.kind == "toggle"]
        self.assertGreater(len(toggles), 0)

    def test_short_description_needs_no_pager(self):
        rendered = render_detail(task(1, description="A short note."))
        self.assertEqual([b for b in rendered.hitboxes if b.kind == "page"], [])

    def test_long_description_with_no_checklist_paginates(self):
        long_desc = "word " * 400
        rendered = render_detail(task(1, description=long_desc))
        pages = [b for b in rendered.hitboxes if b.kind == "page"]
        self.assertEqual(len(pages), 2)

    def test_long_description_pagination_reaches_every_line(self):
        # Each page's worth of text should differ — walking through every
        # page and confirming no two renders are pixel-identical is a cheap
        # proxy for "the content actually advances," without needing to
        # extract rendered text back out of a 1-bit image.
        long_desc = "\n".join("paragraph number %d with some words in it" % i for i in range(40))
        first = render_detail(task(1, description=long_desc))
        fwd = _forward_pager(first.hitboxes, "page")
        self.assertIsNotNone(fwd)
        seen_images = {first.image.tobytes()}
        page = fwd.page
        guard = 0
        while page != 0 and guard < 20:
            r = render_detail(task(1, description=long_desc), page=page)
            seen_images.add(r.image.tobytes())
            fwd = _forward_pager(r.hitboxes, "page")
            page = fwd.page if fwd else 0
            guard += 1
        self.assertGreater(len(seen_images), 1)

    def test_description_blank_lines_are_preserved_as_paragraph_breaks(self):
        two_paragraphs = "first paragraph"
        one_line = render_detail(task(1, description=two_paragraphs)).image
        with_blank = render_detail(task(1, description="first paragraph\n\nsecond")).image
        self.assertNotEqual(one_line.tobytes(), with_blank.tobytes())

    def test_long_description_competing_with_checklist_still_shows_checklist(self):
        # The bug this guards: a long description eating the whole page
        # would leave zero room for the checklist below it. With both
        # present, the checklist must still get a real minimum.
        long_desc = "word " * 400
        checklist = [item(i, completed=False) for i in range(5)]
        rendered = render_detail(task(1, description=long_desc, checklist=checklist))
        toggles = [b for b in rendered.hitboxes if b.kind == "toggle"]
        self.assertGreater(len(toggles), 0)

    def test_description_competing_with_checklist_has_no_pager_of_its_own(self):
        # Only one pager fits on screen — when checklist is present,
        # description is capped rather than independently paginated (the
        # checklist's own pager, if any, is what "page" hitboxes belong to).
        long_desc = "word " * 400
        checklist = [item(i, completed=False) for i in range(2)]
        rendered = render_detail(task(1, description=long_desc, checklist=checklist))
        # With only 2 short items, the checklist itself shouldn't need
        # paging either, so no "page" hitboxes should appear at all.
        self.assertEqual([b for b in rendered.hitboxes if b.kind == "page"], [])

    def test_description_height_is_capped_when_checklist_present(self):
        # A 20-line description and a description trimmed to exactly the
        # guaranteed cap should push the checklist down by the same amount —
        # proving content past the cap doesn't claim any extra height.
        long_desc = "\n".join("line %d" % i for i in range(20))
        with_long = render_detail(task(1, description=long_desc, checklist=[item(0)]))
        capped_desc = "\n".join(
            "line %d" % i for i in range(DETAIL_DESC_MIN_LINES_WITH_CHECKLIST)
        )
        with_capped = render_detail(task(1, description=capped_desc, checklist=[item(0)]))
        long_toggle = next(b for b in with_long.hitboxes if b.kind == "toggle")
        capped_toggle = next(b for b in with_capped.hitboxes if b.kind == "toggle")
        self.assertEqual(long_toggle.y0, capped_toggle.y0)


class TestHeaderCluster(unittest.TestCase):
    def _header_boxes(self, view):
        r = render([task(1)], view)
        return [b for b in r.hitboxes if b.y1 <= HEADER_H]

    def test_header_hitboxes_never_overlap_for_any_view(self):
        from panel.layout import VIEW_TABS
        for view in VIEW_TABS:
            boxes = sorted(self._header_boxes(view), key=lambda b: b.x0)
            for a, b in zip(boxes, boxes[1:]):
                self.assertLessEqual(a.x1, b.x0, "%s: %s overlaps %s" % (view, a.kind, b.kind))

    def test_dark_and_lock_controls_are_present_in_the_list_header(self):
        kinds = {b.kind for b in self._header_boxes("next")}
        self.assertIn("toggle_dark", kinds)
        self.assertIn("toggle_touch_lock", kinds)

    def test_header_controls_stay_clear_of_the_updated_status(self):
        # Rightmost control must end before the "updated" text, in
        # the worst case (the widest date string). Pixels in that gap are blank.
        r = render([task(1)], "next", now=datetime(2026, 12, 28, 9, 0))
        rightmost = next(b for b in r.hitboxes if b.kind == "lock_screen")
        gap = r.image.crop((rightmost.x1, 10, rightmost.x1 + 12, 54))
        self.assertTrue(all(p == WHITE or p == 255 for p in gap.getdata()))

    def test_dark_render_is_the_exact_inverse_with_identical_hitboxes(self):
        tasks = [task(i) for i in range(3)]
        light = render(tasks, "next", now=datetime(2026, 10, 15, 9, 0))
        dark = render(tasks, "next", now=datetime(2026, 10, 15, 9, 0), dark=True)
        self.assertEqual(light.hitboxes, dark.hitboxes)
        self.assertEqual(dark.image.mode, "1")
        # Everywhere except the dark button itself, whose border is heavier
        # when on — that difference is the point, not an inversion error.
        btn = next(h for h in light.hitboxes if h.kind == "toggle_dark")
        lo, do = light.image.convert("L"), dark.image.convert("L")
        for i, (a, b) in enumerate(zip(lo.getdata(), do.getdata())):
            x, y = i % WIDTH, i // WIDTH
            if btn.x0 <= x < btn.x1 and y < HEADER_H:
                continue
            self.assertEqual(a, 255 - b, "pixel (%d, %d)" % (x, y))

    def test_detail_view_dark_is_inverted_too(self):
        light = render_detail(task(1))
        dark = render_detail(task(1), dark=True)
        self.assertEqual(light.hitboxes, dark.hitboxes)
        self.assertEqual(dark.image.getpixel((0, 0)), 0)

    def test_locked_changes_only_the_lock_glyph_and_nothing_hittable(self):
        a = render([task(1)], "next", now=datetime(2026, 10, 15, 9, 0))
        b = render([task(1)], "next", now=datetime(2026, 10, 15, 9, 0), touch_locked=True)
        self.assertEqual(a.hitboxes, b.hitboxes)
        lock = next(h for h in a.hitboxes if h.kind == "toggle_touch_lock")
        diff = [i for i, (p, q) in enumerate(zip(a.image.getdata(), b.image.getdata())) if p != q]
        self.assertTrue(diff)
        for i in diff:
            x, y = i % WIDTH, i // WIDTH
            self.assertTrue(lock.x0 <= x < lock.x1 and y < HEADER_H)



class TestPortrait(unittest.TestCase):
    """480x800: the same UI on a taller canvas, with a two-row header."""

    CONTROLS = {"focus_sticky", "sort_toggle", "toggle_area_view", "toggle_project_view",
                "toggle_due_first", "toggle_dark", "toggle_touch_lock", "lock_screen", "toggle_rotate"}

    def tasks(self, n=14):
        return [task(i, title="Task %d" % i) for i in range(n)]

    def test_canvas_is_480_by_800_one_bit(self):
        for rendered in (render(self.tasks(), "next", portrait=True),
                         render_detail(task(1), portrait=True)):
            self.assertEqual(rendered.image.size, (480, 800))
            self.assertEqual(rendered.image.mode, "1")

    def test_list_shows_more_rows_than_landscape(self):
        def rows(portrait):
            r = render(self.tasks(), "next", portrait=portrait)
            return len([b for b in r.hitboxes if b.kind == "open"])
        self.assertEqual(rows(False), 6)
        self.assertGreaterEqual(rows(True), 10)

    def test_the_header_has_the_same_eight_controls_as_landscape(self):
        for rendered in (render(self.tasks(), "next", portrait=True),
                         render_detail(task(1), portrait=True)):
            kinds = {b.kind for b in rendered.hitboxes if b.y0 < 112}
            self.assertEqual(kinds & self.CONTROLS, self.CONTROLS)

    def test_controls_are_big_equal_slots_that_do_not_overlap_the_title_row(self):
        rendered = render(self.tasks(), "focus", portrait=True)
        controls = sorted((b for b in rendered.hitboxes if b.kind in self.CONTROLS), key=lambda b: b.x0)
        self.assertEqual(len(controls), 9)
        for a, b in zip(controls, controls[1:]):
            self.assertLessEqual(a.x1, b.x0)           # no overlap
        for b in controls:
            self.assertGreaterEqual(b.x1 - b.x0, 50)   # a real thumb target
            self.assertGreaterEqual(b.y0, 56)          # below the title row
        title = next(b for b in rendered.hitboxes if b.kind == "home")
        self.assertLessEqual(title.y1, 56)

    def test_every_hitbox_stays_on_the_canvas(self):
        for rendered in (render(self.tasks(), "next", portrait=True),
                         render(self.tasks(), "next", portrait=True, group_by_project=True),
                         render_detail(task(1, checklist=[item(n) for n in range(30)]), portrait=True)):
            for b in rendered.hitboxes:
                self.assertTrue(0 <= b.x0 < b.x1 <= 480 and 0 <= b.y0 < b.y1 <= 800, b)

    def test_tabs_do_not_reach_the_status_text(self):
        # The last tab's tap zone must stop before the "updated" text rather
        # than run under it.
        rendered = render(self.tasks(), "focus", portrait=True)
        tabs = [b for b in rendered.hitboxes if b.kind in ("home", "view")]
        self.assertEqual(len(tabs), 4)
        self.assertLess(max(b.x1 for b in tabs), 300)
        for b in tabs:
            self.assertGreaterEqual(b.x1 - b.x0, 50)   # a real thumb target

    def test_detail_checklist_fits_more_items(self):
        r = render_detail(task(1, checklist=[item(n) for n in range(30)]), portrait=True)
        self.assertGreater(len([b for b in r.hitboxes if b.kind == "toggle"]), 6)

    def test_taps_land_on_the_rows_that_were_drawn(self):
        rendered = render(self.tasks(), "next", portrait=True)
        box = next(b for b in rendered.hitboxes if b.kind == "open" and b.task_id == "task-3")
        found = hit(rendered.hitboxes, ((box.x0 + box.x1) // 2, (box.y0 + box.y1) // 2))
        self.assertEqual((found.kind, found.task_id), ("open", "task-3"))

    def test_dark_portrait_is_the_inverted_light_render(self):
        light = render(self.tasks(), "next", portrait=True).image
        dark = render(self.tasks(), "next", portrait=True, dark=True).image
        self.assertEqual(dark.getpixel((0, 0)), 0)
        self.assertNotEqual(light.getpixel((0, 0)), 0)


class TestViewTabs(unittest.TestCase):
    def test_active_tab_is_home_and_the_rest_switch_view(self):
        tabs = [b for b in render([task(1)], "inbox").hitboxes if b.kind in ("home", "view")]
        self.assertEqual([(b.kind, b.key) for b in sorted(tabs, key=lambda b: b.x0)], [
            ("view", "focus"), ("home", "inbox"), ("view", "waiting"), ("view", "someday"),
        ])

    def test_landscape_tabs_end_before_the_control_cluster(self):
        boxes = render([task(1)], "focus").hitboxes
        last_tab = max(b.x1 for b in boxes if b.kind in ("home", "view"))
        first_control = min(b.x0 for b in boxes if b.kind == "focus_sticky")
        self.assertLessEqual(last_tab, first_control)

    def test_inbox_count_badge_draws(self):
        without = render([task(1)], "focus").image
        with_count = render([task(1)], "focus", inbox_count=3).image
        self.assertNotEqual(without.tobytes(), with_count.tobytes())


def inbox_task(n=1, **kw):
    return replace(task(n, **kw), status="inbox")


class TestProcessingView(unittest.TestCase):
    def kinds(self, rendered, kind):
        return [b for b in rendered.hitboxes if b.kind == kind]

    def test_an_inbox_task_gets_the_processing_rows_instead_of_its_checklist(self):
        r = render_detail(inbox_task(checklist=[item(1)]), view="inbox")
        self.assertEqual([b.key for b in self.kinds(r, "pick")], ["where", "contexts", "due"])
        self.assertEqual([b.key for b in self.kinds(r, "set_status")], ["next", "waiting", "someday"])
        self.assertEqual(self.kinds(r, "toggle"), [])
        self.assertEqual(len(self.kinds(r, "complete")), 1)   # done stays the checkbox

    def test_other_tasks_get_only_the_edit_line(self):
        r = render_detail(task(1, checklist=[item(n) for n in range(8)]))
        self.assertEqual([b.key for b in self.kinds(r, "pick")], ["edit"])
        self.assertEqual(self.kinds(r, "set_status"), [])

    def test_the_project_line_is_a_full_tap_target_clear_of_its_neighbours(self):
        for t in (task(1, checklist=[item(n) for n in range(8)]),
                  task(2, title="word " * 30, description="line\n" * 10, project="P")):
            for portrait in (False, True):
                r = render_detail(t, portrait=portrait)
                where = self.kinds(r, "pick")[0]
                self.assertGreaterEqual(where.y1 - where.y0, 44)
                for other in r.hitboxes:
                    if other is not where and other.kind in ("star", "toggle", "expand_item", "page"):
                        self.assertTrue(other.y1 <= where.y0 or other.y0 >= where.y1, (other, where))

    def test_checkbox_and_star_are_centred_on_the_title_however_long(self):
        from panel.theme import BOX_X, DETAIL_STAR_ZONE_W
        # No descenders, so the text's dark extent is cap top to baseline.
        for title in ("Short", "Some words that wrap " * 2, "Some words " * 12):
            for portrait in (False, True):
                r = render_detail(task(1, title=title), portrait=portrait)
                complete = self.kinds(r, "complete")[0]
                star = self.kinds(r, "star")[0]
                self.assertEqual((complete.y0, complete.y1), (star.y0, star.y1))
                px, w = r.image.load(), r.image.size[0]
                rows = range(complete.y0, complete.y1 - 2)   # above the closing rule
                box = [y for y in rows if px[BOX_X, y] == BLACK]
                text = [y for y in rows
                        if any(px[x, y] == BLACK for x in range(TEXT_X, w - MARGIN - DETAIL_STAR_ZONE_W))]
                box_mid, text_mid = (box[0] + box[-1]) / 2, (text[0] + text[-1]) / 2
                self.assertLessEqual(abs(box_mid - text_mid), 2, (title, portrait, box_mid, text_mid))

    def test_the_field_icons_share_one_centre(self):
        # The @ is a font glyph among drawn icons; it once sat 1.5px off them.
        from PIL import Image, ImageDraw
        from panel.layout import _draw_field_icon
        for key, t in (("where", task(1, project="P")), ("where", task(1, area="A")),
                       ("contexts", task(1)), ("due", task(1))):
            for cy in (30, 31):
                img = Image.new("1", (60, 60), WHITE)
                _draw_field_icon(ImageDraw.Draw(img), key, t, 20, cy)
                x0, y0, x1, y1 = Image.eval(img.convert("L"), lambda v: 255 - v).getbbox()
                cx, mid = (x0 + x1 - 1) / 2, (y0 + y1 - 1) / 2
                self.assertEqual(cx, 28, (key, t.area))
                self.assertLessEqual(abs(mid - cy), 0.5, (key, t.area, mid))

    def test_unset_fields_take_no_room_but_edit_stays_reachable(self):
        full = task(1, project="P", contexts=["@a"], created_at=datetime(2026, 9, 1),
                    due_date=datetime(2026, 10, 24))
        bare = task(2)
        tall = self.kinds(render_detail(full), "pick")[0]
        short = self.kinds(render_detail(bare), "pick")[0]
        self.assertGreater(tall.y1 - tall.y0, short.y1 - short.y0)
        self.assertGreaterEqual(short.y1 - short.y0, 44)            # still a real target
        # The Edit screen keeps its placeholders: a bare task's rows still say something.
        self.assertNotEqual(render_picker("edit", bare).image.tobytes(),
                            render_picker("edit", replace(bare, project="P")).image.tobytes())

    def test_a_busy_info_block_keeps_its_tap_zone_clear_of_the_checklist(self):
        busy = task(1, project="Home Server", contexts=["@computer", "@home", "@errands"],
                    created_at=datetime(2026, 9, 1), due_date=datetime(2026, 10, 24),
                    checklist=[item(n) for n in range(8)])
        for portrait in (False, True):
            r = render_detail(busy, portrait=portrait)
            where = self.kinds(r, "pick")[0]
            for other in r.hitboxes:
                if other.kind in ("star", "toggle", "expand_item", "expand", "page"):
                    self.assertTrue(other.y1 <= where.y0 or other.y0 >= where.y1, (other, where))

    def test_a_task_filed_nowhere_still_says_so(self):
        filed = render_detail(task(1, project="Home Server")).image
        unfiled = render_detail(task(1)).image
        self.assertNotEqual(filed.tobytes(), unfiled.tobytes())

    def test_fits_above_the_footer_with_a_long_title_and_description(self):
        t = inbox_task(title="word " * 40, description="line\n" * 20)
        for portrait in (False, True):
            r = render_detail(t, portrait=portrait, view="inbox")
            bottom = (800 if portrait else HEIGHT) - COLLAPSED_FOOTER_H
            self.assertLessEqual(max(b.y1 for b in self.kinds(r, "set_status")), bottom)


class TestPickers(unittest.TestCase):
    AREAS = [Area("a1", "Digital"), Area("a2", "Home")]
    PROJECTS = [Project("p1", "sowr", "a1"), Project("p2", "Loose", None)]

    def boxes(self, rendered, kind):
        return [b for b in rendered.hitboxes if b.kind == kind]

    def test_where_lists_areas_with_their_projects_then_loose_projects(self):
        r = render_picker("where", inbox_task(), self.PROJECTS, self.AREAS)
        self.assertEqual([b.key for b in self.boxes(r, "choose_where")],
                         ["none", "area:a1", "project:p1", "area:a2", "project:p2"])

    def test_where_pages_when_there_are_many_projects(self):
        projects = [Project("p%d" % i, "Project %d" % i, "a1") for i in range(30)]
        first = render_picker("where", inbox_task(), projects, self.AREAS)
        second = render_picker("where", inbox_task(), projects, self.AREAS, page=1)
        self.assertEqual(len(self.boxes(first, "picker_page")), 2)
        keys = lambda r: {b.key for b in self.boxes(r, "choose_where")}  # noqa: E731
        self.assertFalse(keys(first) & keys(second))

    def test_contexts_offers_those_in_use_and_the_tasks_own(self):
        r = render_picker("contexts", inbox_task(contexts=["@phone"]), contexts=["@home", "@computer"])
        self.assertEqual([b.key for b in self.boxes(r, "toggle_context")],
                         ["@computer", "@home", "@phone"])

    def test_due_grid_is_the_whole_month_plus_quick_dates(self):
        now = datetime(2026, 10, 21)   # a Wednesday
        r = render_picker("due", inbox_task(), now=now)
        keys = [b.key for b in self.boxes(r, "choose_due")]
        self.assertEqual(keys[:4], ["2026-10-21", "2026-10-22", "2026-10-26", ""])
        self.assertEqual(keys[4:], ["2026-10-%02d" % d for d in range(1, 32)])
        self.assertEqual(sorted(b.page for b in self.boxes(r, "month")),
                         [month_index(2026, 9), month_index(2026, 11)])

    def test_due_opens_on_the_due_month(self):
        r = render_picker("due", inbox_task(due_date=datetime(2027, 2, 3)), now=datetime(2026, 10, 21))
        self.assertIn("2027-02-28", [b.key for b in self.boxes(r, "choose_due")])

    def test_edit_gives_any_task_the_processing_rows_with_its_status_boxed(self):
        r = render_picker("edit", task(1))
        self.assertEqual([b.key for b in self.boxes(r, "pick")], ["where", "contexts", "due"])
        self.assertEqual([b.key for b in self.boxes(r, "set_status")], ["next", "waiting", "someday"])
        as_waiting = render_picker("edit", replace(task(1), status="waiting")).image
        self.assertNotEqual(r.image.tobytes(), as_waiting.tobytes())

    def test_every_picker_stays_on_the_canvas_and_has_a_way_back(self):
        t = inbox_task(contexts=["@home"])
        for portrait in (False, True):
            w, h = (480, 800) if portrait else (WIDTH, HEIGHT)
            for kind in ("edit", "where", "contexts", "due"):
                r = render_picker(kind, t, self.PROJECTS, self.AREAS, ["@a"], portrait=portrait)
                self.assertEqual(len(self.boxes(r, "back")), 1)
                for b in r.hitboxes:
                    self.assertTrue(0 <= b.x0 < b.x1 <= w and 0 <= b.y0 < b.y1 <= h - COLLAPSED_FOOTER_H, b)


if __name__ == "__main__":
    unittest.main()
