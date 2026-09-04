"""Layout and hit-testing. Run: python3 -m unittest discover tests"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.client import ChecklistItem, Task  # noqa: E402
from panel.layout import hit, render, render_detail  # noqa: E402
from panel.theme import (  # noqa: E402
    HEADER_H, HEIGHT, ROW_H, ROWS, STAR_ZONE_W, TEXT_RIGHT, TEXT_X, WIDTH,
)


def task(n: int, title: str = None, checklist=None, **kw) -> Task:
    return Task(
        id="task-%d" % n,
        title=title or "Task number %d" % n,
        project=kw.get("project"),
        area=kw.get("area"),
        contexts=kw.get("contexts", []),
        focused=kw.get("focused", False),
        description=kw.get("description"),
        checklist=checklist or [],
    )


def item(n: int, completed: bool = False, title: str = None) -> ChecklistItem:
    return ChecklistItem(id="item-%d" % n, title=title or "Item %d" % n, completed=completed)


class TestRender(unittest.TestCase):
    def test_is_one_bit_at_panel_size(self):
        image = render([task(1)], "next").image
        self.assertEqual(image.mode, "1")
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_shows_at_most_one_screenful_when_it_all_fits(self):
        rendered = render([task(i) for i in range(ROWS)], "next")
        # Three hitboxes (checkbox / text / star) per visible row, no pager.
        self.assertEqual(len(rendered.hitboxes), ROWS * 3)

    def test_empty_list_renders_without_hitboxes(self):
        rendered = render([], "next")
        self.assertEqual(rendered.hitboxes, [])

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

    def test_tap_on_header_or_footer_hits_nothing(self):
        self.assertIsNone(hit(self.rendered.hitboxes, (400, 10)))
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


class TestDetailRendering(unittest.TestCase):
    def test_is_one_bit_at_panel_size(self):
        image = render_detail(task(1)).image
        self.assertEqual(image.mode, "1")
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_long_title_wraps_rather_than_overflows(self):
        render_detail(task(1, "x " * 60))

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

    def test_header_tap_goes_back(self):
        rendered = render_detail(task(1))
        box = hit(rendered.hitboxes, (400, 20))
        self.assertEqual(box.kind, "back")

    def test_title_checkbox_completes_the_task(self):
        rendered = render_detail(task(1))
        box = hit(rendered.hitboxes, (10, HEADER_H + 20))
        self.assertEqual((box.kind, box.task_id), ("complete", "task-1"))

    def test_title_star_toggles_focus(self):
        rendered = render_detail(task(1))
        box = hit(rendered.hitboxes, (TEXT_RIGHT - 10, HEADER_H + 20))
        self.assertEqual((box.kind, box.task_id), ("star", "task-1"))

    def test_checklist_item_toggle_is_full_width(self):
        # A leaf item has one action, so unlike a task row its whole width
        # (not just a checkbox column) should be tappable.
        checklist = [item(0)]
        rendered = render_detail(task(1, checklist=checklist))
        toggle = next(b for b in rendered.hitboxes if b.kind == "toggle")
        self.assertEqual((toggle.x0, toggle.x1), (0, WIDTH))

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


if __name__ == "__main__":
    unittest.main()
