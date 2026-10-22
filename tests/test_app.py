"""Panel's redraw gating — does a real, rendered content change actually
reach the display? Run: python3 -m unittest discover tests"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.app import Panel  # noqa: E402
from panel.client import Area, Project, Store, Task, apply_fields  # noqa: E402
from panel.config import Config  # noqa: E402
from panel.touch import Gesture, classify  # noqa: E402


def task(n: int, **kw) -> Task:
    defaults = dict(
        id="task-%d" % n, title="Task %d" % n, project=None, area=None,
        contexts=[], focused=False, created_at=None, due_date=None, description=None, checklist=[],
    )
    defaults.update(kw)
    return Task(**defaults)


class StubClient:
    """No network, but persists writes like a real store — a test can still
    reassign `.tasks` directly to simulate "changed on another device", but
    a write behaves the way the real server would (through the same
    apply_fields the panel uses), which matters once a Panel method's own
    internal refresh() reads it back."""

    def __init__(self, tasks, areas=(), projects=()):
        self.tasks = list(tasks)
        self.areas = list(areas)
        self.projects = list(projects)
        self.writes = []

    def fetch(self, limit):
        return Store(tasks=list(self.tasks), areas=self.areas, projects=self.projects)

    def complete(self, task_id):
        self.tasks = [t for t in self.tasks if t.id != task_id]

    def update_task(self, task_id, fields):
        self.writes.append((task_id, fields))
        self.tasks = [
            apply_fields(t, fields, self.projects, self.areas) if t.id == task_id else t
            for t in self.tasks
        ]

    def data_etag(self):
        return ""


class StubDisplay:
    def __init__(self):
        self.shown = []

    def show(self, image):
        self.shown.append(image)

    def show_partial(self, image):
        self.shown.append(image)

    def close(self):
        pass


def make_panel(tasks, rotation: int = 0, state_path=None, view="focus", sort_newest=False,
               **config_kw) -> Panel:
    # Oldest-first, which these tests were written against; the real default
    # (newest-first, or whatever was saved) is Config's and tested there.
    config = Config(
        url="http://stub", token="t", view=view, poll_seconds=300, sort_newest=sort_newest,
        check_seconds=5, display="png", touch="none",
        png_path=Path("/tmp/unused-panel-test.png"), touch_device="",
        rotation=rotation, state_path=state_path, **config_kw,
    )
    panel = Panel(config)
    panel.client = StubClient(tasks)
    panel.display = StubDisplay()
    return panel


class TestListRedrawGating(unittest.TestCase):
    def test_redraws_when_project_changes(self):
        # The failure this guards: refresh() fetches the new project name
        # into self.tasks correctly, but a signature built from only
        # (id, title, focused) can't tell the row changed, so draw() would
        # skip it — stale project shown forever despite a real refresh.
        panel = make_panel([task(1, project="Alpha")])
        panel.refresh()
        panel.draw(force=True)
        self.assertEqual(len(panel.display.shown), 1)

        panel.client.tasks = [task(1, project="Beta")]
        panel.refresh()
        panel.draw()
        self.assertEqual(len(panel.display.shown), 2)

    def test_no_redraw_when_nothing_rendered_changed(self):
        panel = make_panel([task(1, project="Alpha")])
        panel.refresh()
        panel.draw(force=True)
        self.assertEqual(len(panel.display.shown), 1)

        panel.refresh()  # identical data back
        panel.draw()
        self.assertEqual(len(panel.display.shown), 1)


class TestDetailRedrawGating(unittest.TestCase):
    def test_redraws_when_description_changes(self):
        panel = make_panel([task(1, description="first draft")])
        panel.refresh()
        panel.mode, panel.detail_task_id = "detail", "task-1"
        panel.draw(force=True)
        self.assertEqual(len(panel.display.shown), 1)

        panel.client.tasks = [task(1, description="second draft")]
        panel.refresh()
        panel.draw()
        self.assertEqual(len(panel.display.shown), 2)

    def test_redraws_when_project_changes(self):
        panel = make_panel([task(1, project="Alpha")])
        panel.refresh()
        panel.mode, panel.detail_task_id = "detail", "task-1"
        panel.draw(force=True)
        self.assertEqual(len(panel.display.shown), 1)

        panel.client.tasks = [task(1, project="Beta")]
        panel.refresh()
        panel.draw()
        self.assertEqual(len(panel.display.shown), 2)

    def test_redraws_when_a_checklist_item_is_toggled_elsewhere(self):
        from panel.client import ChecklistItem

        panel = make_panel([task(1, checklist=[ChecklistItem("c1", "step one", False)])])
        panel.refresh()
        panel.mode, panel.detail_task_id = "detail", "task-1"
        panel.draw(force=True)
        self.assertEqual(len(panel.display.shown), 1)

        panel.client.tasks = [task(1, checklist=[ChecklistItem("c1", "step one", True)])]
        panel.refresh()
        panel.draw()
        self.assertEqual(len(panel.display.shown), 2)

    def test_no_redraw_when_nothing_rendered_changed(self):
        panel = make_panel([task(1, description="same")])
        panel.refresh()
        panel.mode, panel.detail_task_id = "detail", "task-1"
        panel.draw(force=True)
        self.assertEqual(len(panel.display.shown), 1)

        panel.refresh()
        panel.draw()
        self.assertEqual(len(panel.display.shown), 1)

    def test_falls_back_to_list_when_task_disappears(self):
        panel = make_panel([task(1)])
        panel.refresh()
        panel.mode, panel.detail_task_id = "detail", "task-1"
        panel.draw(force=True)

        panel.client.tasks = []  # completed/deleted from elsewhere
        panel.refresh()
        panel.draw()
        self.assertEqual(panel.mode, "list")


class TestChecklistItemUnfold(unittest.TestCase):
    def test_text_tap_unfolds_and_refolds_without_ticking(self):
        from panel.client import ChecklistItem
        long_title = " ".join(["word%d" % n for n in range(40)])
        checklist = (ChecklistItem(id="i0", title=long_title, completed=False),)
        panel = make_panel([task(1, checklist=checklist)])
        panel.refresh()
        panel.mode, panel.detail_task_id = "detail", "task-1"
        panel.draw(force=True)
        box = next(b for b in panel.hitboxes if b.kind == "expand_item")
        panel.handle_tap((box.x0 + 5, box.y0 + 5))
        self.assertEqual(panel.expanded_items, frozenset({"i0"}))
        self.assertFalse(panel._find("task-1").checklist[0].completed)
        self.assertEqual(len(panel.display.shown), 2)
        box = next(b for b in panel.hitboxes if b.kind == "expand_item")
        panel.handle_tap((box.x0 + 5, box.y0 + 5))
        self.assertEqual(panel.expanded_items, frozenset())


class TestStarToggle(unittest.TestCase):
    def test_toggle_star_flips_focused_optimistically(self):
        panel = make_panel([task(1, focused=False)])
        panel.refresh()
        panel.draw(force=True)

        panel._toggle_star("task-1")
        self.assertTrue(panel._find("task-1").focused)

    def test_toggle_star_reverts_on_failure(self):
        class FailingClient(StubClient):
            def update_task(self, task_id, fields):
                from panel.client import MindwtrError

                raise MindwtrError("boom")

        panel = make_panel([task(1, focused=False)])
        panel.client = FailingClient([task(1, focused=False)])
        panel.refresh()
        panel.draw(force=True)

        panel._toggle_star("task-1")
        self.assertFalse(panel._find("task-1").focused)
        self.assertIsNotNone(panel.stale_since)


class TestListPagination(unittest.TestCase):
    def test_list_page_forward_zone_advances_the_page(self):
        panel = make_panel([task(i) for i in range(25)])
        panel.refresh()
        panel.draw(force=True)

        pagers = [b for b in panel.hitboxes if b.kind == "list_page"]
        forward = max(pagers, key=lambda b: b.x0)  # right half of the row
        panel.handle_tap((forward.x0 + 10, forward.y0 + 10))
        self.assertEqual(panel.list_page, forward.page)

    def test_list_page_backward_zone_goes_back(self):
        panel = make_panel([task(i) for i in range(25)])
        panel.refresh()
        panel.draw(force=True)

        pagers = [b for b in panel.hitboxes if b.kind == "list_page"]
        backward = min(pagers, key=lambda b: b.x0)  # left half of the row
        panel.handle_tap((backward.x0 + 10, backward.y0 + 10))
        self.assertEqual(panel.list_page, backward.page)


class TestSwipePaging(unittest.TestCase):
    def _paged(self):
        panel = make_panel([task(i) for i in range(25)])
        panel.refresh()
        panel.draw(force=True)
        return panel

    def test_swipe_left_goes_to_next_page(self):
        panel = self._paged()
        panel.handle_gesture(Gesture("swipe", 600, 200, dx=-300))
        self.assertEqual(panel.list_page, 1)

    def test_swipe_right_wraps_to_last_page(self):
        panel = self._paged()
        panel.handle_gesture(Gesture("swipe", 200, 200, dx=300))
        self.assertNotEqual(panel.list_page, 0)

    def test_swipe_without_a_pager_does_nothing(self):
        panel = make_panel([task(1)])
        panel.refresh()
        panel.draw(force=True)
        panel.handle_gesture(Gesture("swipe", 600, 200, dx=-300))
        self.assertEqual(panel.list_page, 0)

    def _detail(self, n_items):
        from panel.client import ChecklistItem
        checklist = tuple(ChecklistItem(id="i%d" % i, title="Item %d" % i, completed=False) for i in range(n_items))
        panel = make_panel([task(1, checklist=checklist)])
        panel.refresh()
        panel.mode, panel.detail_task_id = "detail", "task-1"
        panel.draw(force=True)
        return panel

    def test_swipe_right_on_detail_goes_back(self):
        panel = self._detail(2)
        panel.handle_gesture(Gesture("swipe", 200, 200, dx=300))
        self.assertEqual((panel.mode, panel.detail_task_id), ("list", None))

    def test_swipe_right_on_a_later_detail_page_turns_back_a_page_first(self):
        panel = self._detail(20)
        panel.handle_gesture(Gesture("swipe", 600, 200, dx=-300))
        self.assertEqual(panel.checklist_page, 1)
        panel.handle_gesture(Gesture("swipe", 200, 200, dx=300))
        self.assertEqual((panel.mode, panel.checklist_page), ("detail", 0))
        panel.handle_gesture(Gesture("swipe", 200, 200, dx=300))
        self.assertEqual(panel.mode, "list")

    def test_swipe_left_on_detail_without_a_pager_stays(self):
        panel = self._detail(2)
        panel.handle_gesture(Gesture("swipe", 600, 200, dx=-300))
        self.assertEqual(panel.mode, "detail")


class TestClassify(unittest.TestCase):
    def test_small_movement_is_a_tap_at_the_landing_point(self):
        self.assertEqual(classify((100, 100), (110, 105)), Gesture("tap", 100, 100))

    def test_long_horizontal_stroke_is_a_swipe(self):
        self.assertEqual(classify((600, 200), (300, 210)), Gesture("swipe", 600, 200, -300))

    def test_vertical_or_diagonal_drag_is_ignored(self):
        self.assertIsNone(classify((100, 100), (110, 300)))
        self.assertIsNone(classify((100, 100), (300, 300)))

    def test_short_sideways_wobble_is_ignored(self):
        self.assertIsNone(classify((100, 100), (160, 100)))


class TestHeaderControls(unittest.TestCase):
    def _tap(self, panel, kind):
        box = next(b for b in panel.hitboxes if b.kind == kind)
        panel.handle_tap((box.x0 + 5, box.y0 + 5))

    def test_sort_toggle_flips_direction_each_tap(self):
        panel = make_panel([task(1)])
        panel.refresh()
        panel.draw(force=True)
        self.assertFalse(panel.sort_newest)  # oldest-first by default

        self._tap(panel, "sort_toggle")
        self.assertTrue(panel.sort_newest)
        self._tap(panel, "sort_toggle")
        self.assertFalse(panel.sort_newest)

    def test_focus_sticky_toggle_flips_each_tap(self):
        panel = make_panel([task(1)])
        panel.refresh()
        panel.draw(force=True)
        self.assertTrue(panel.focus_sticky)  # on by default

        self._tap(panel, "focus_sticky")
        self.assertFalse(panel.focus_sticky)
        self._tap(panel, "focus_sticky")
        self.assertTrue(panel.focus_sticky)

    def test_project_and_area_view_toggle_independently_and_combine(self):
        panel = make_panel([task(1)])
        panel.refresh()
        panel.draw(force=True)

        self._tap(panel, "toggle_project_view")
        self.assertTrue(panel.group_by_project)
        self.assertFalse(panel.group_by_area)

        self._tap(panel, "toggle_area_view")
        self.assertTrue(panel.group_by_project)
        self.assertTrue(panel.group_by_area)

        self._tap(panel, "toggle_project_view")
        self.assertFalse(panel.group_by_project)
        self.assertTrue(panel.group_by_area)

    def test_due_first_toggle_flips_each_tap(self):
        panel = make_panel([task(1)])
        panel.refresh()
        panel.draw(force=True)
        self.assertFalse(panel.due_first)  # off by default

        self._tap(panel, "toggle_due_first")
        self.assertTrue(panel.due_first)
        self._tap(panel, "toggle_due_first")
        self.assertFalse(panel.due_first)

    def test_toggling_a_view_control_resets_the_page(self):
        panel = make_panel([task(i) for i in range(25)])
        panel.refresh()
        panel.draw(force=True)
        pagers = [b for b in panel.hitboxes if b.kind == "list_page"]
        forward = max(pagers, key=lambda b: b.x0)
        panel.handle_tap((forward.x0 + 10, forward.y0 + 10))
        self.assertNotEqual(panel.list_page, 0)

        self._tap(panel, "toggle_project_view")
        self.assertEqual(panel.list_page, 0)

    def test_home_resets_page_but_leaves_sort_and_grouping_alone(self):
        panel = make_panel([task(i) for i in range(25)])
        panel.refresh()
        panel.draw(force=True)
        self._tap(panel, "sort_toggle")
        self._tap(panel, "toggle_area_view")
        pagers = [b for b in panel.hitboxes if b.kind == "list_page"]
        forward = max(pagers, key=lambda b: b.x0)
        panel.handle_tap((forward.x0 + 10, forward.y0 + 10))
        self.assertNotEqual(panel.list_page, 0)

        self._tap(panel, "home")
        self.assertEqual(panel.list_page, 0)
        self.assertTrue(panel.sort_newest)
        self.assertTrue(panel.group_by_area)


class TestDarkMode(unittest.TestCase):
    def _tap(self, panel, kind):
        box = next(b for b in panel.hitboxes if b.kind == kind)
        panel.handle_tap((box.x0 + 5, box.y0 + 5))

    def test_toggle_flips_and_redraws_inverted_without_losing_the_page(self):
        panel = make_panel([task(i) for i in range(25)])
        panel.refresh()
        panel.draw(force=True)
        forward = max((b for b in panel.hitboxes if b.kind == "list_page"), key=lambda b: b.x0)
        panel.handle_tap((forward.x0 + 10, forward.y0 + 10))
        page = panel.list_page
        before = len(panel.display.shown)

        self._tap(panel, "toggle_dark")
        self.assertTrue(panel.dark)
        self.assertEqual(panel.list_page, page)
        self.assertEqual(len(panel.display.shown), before + 1)
        # Page corner is margin, so it flips from white to black.
        self.assertEqual(panel.display.shown[-1].getpixel((0, 0)), 0)

        self._tap(panel, "toggle_dark")
        self.assertFalse(panel.dark)
        self.assertNotEqual(panel.display.shown[-1].getpixel((0, 0)), 0)

    def test_detail_view_follows_dark_mode(self):
        panel = make_panel([task(1, title="One")])
        panel.refresh()
        panel.draw(force=True)
        self._tap(panel, "toggle_dark")
        box = next(b for b in panel.hitboxes if b.kind == "open")
        panel.handle_tap((box.x0 + 5, box.y0 + 5))
        self.assertEqual(panel.mode, "detail")
        self.assertEqual(panel.display.shown[-1].getpixel((0, 0)), 0)


class TestTouchLock(unittest.TestCase):
    def _panel(self, n=25):
        panel = make_panel([task(i) for i in range(n)])
        panel.refresh()
        panel.draw(force=True)
        return panel

    def _tap(self, panel, kind):
        box = next(b for b in panel.hitboxes if b.kind == kind)
        panel.handle_tap((box.x0 + 5, box.y0 + 5))

    def test_locked_ignores_every_other_target(self):
        panel = self._panel()
        self._tap(panel, "toggle_touch_lock")
        self.assertTrue(panel.touch_locked)
        shown = len(panel.display.shown)

        for kind in ("complete", "open", "star", "list_page", "home", "sort_toggle",
                     "focus_sticky", "toggle_area_view", "toggle_project_view",
                     "toggle_due_first", "toggle_dark"):
            self._tap(panel, kind)
        self.assertEqual(len(panel.tasks), 25)            # nothing completed
        self.assertFalse(any(t.focused for t in panel.tasks))
        self.assertEqual((panel.mode, panel.list_page), ("list", 0))
        self.assertTrue(panel.sort_newest is False and panel.focus_sticky is True)
        self.assertFalse(panel.dark)
        self.assertEqual(len(panel.display.shown), shown)  # and nothing redrawn

    def test_locked_ignores_swipes_and_taps_on_empty_space(self):
        panel = self._panel()
        self._tap(panel, "toggle_touch_lock")
        panel.handle_gesture(Gesture("swipe", 600, 200, dx=-300))
        panel.handle_gesture(Gesture("tap", 400, 20))
        self.assertEqual(panel.list_page, 0)

    def test_lock_button_unlocks_and_input_resumes(self):
        panel = self._panel()
        self._tap(panel, "toggle_touch_lock")
        self._tap(panel, "toggle_touch_lock")
        self.assertFalse(panel.touch_locked)
        self._tap(panel, "sort_toggle")
        self.assertTrue(panel.sort_newest)

    def test_background_refresh_still_updates_while_locked(self):
        panel = self._panel(3)
        self._tap(panel, "toggle_touch_lock")
        panel.client.tasks = panel.client.tasks[:2]
        panel.refresh()
        panel.draw(partial=True)
        self.assertEqual(len(panel.tasks), 2)
        self.assertTrue(any(b.kind == "toggle_touch_lock" for b in panel.hitboxes))

    def test_detail_view_keeps_the_header_and_can_lock_and_unlock(self):
        panel = self._panel(3)
        self._tap(panel, "open")
        self.assertEqual(panel.mode, "detail")

        self._tap(panel, "toggle_touch_lock")
        self.assertTrue(panel.touch_locked)
        self.assertEqual(panel.mode, "detail")
        self._tap(panel, "back")                  # gated like everything else
        self.assertEqual(panel.mode, "detail")

        self._tap(panel, "toggle_touch_lock")
        self._tap(panel, "back")
        self.assertEqual(panel.mode, "list")

    def test_detail_header_toggles_redraw_and_carry_back_to_the_list(self):
        panel = self._panel(3)
        self._tap(panel, "open")
        shown = len(panel.display.shown)
        self._tap(panel, "sort_toggle")
        self.assertTrue(panel.sort_newest)
        self.assertEqual(panel.mode, "detail")
        self.assertEqual(len(panel.display.shown), shown + 1)  # icon state changed
        self._tap(panel, "back")
        self.assertTrue(panel.sort_newest)


class TestPortraitPanel(unittest.TestCase):
    def _panel(self, n=14):
        panel = make_panel([task(i) for i in range(n)], rotation=90)
        panel.refresh()
        panel.draw(force=True)
        return panel

    def _tap(self, panel, kind):
        box = next(b for b in panel.hitboxes if b.kind == kind)
        panel.handle_tap(((box.x0 + box.x1) // 2, (box.y0 + box.y1) // 2))

    def test_draws_a_portrait_canvas_and_the_other_rotations_follow(self):
        for rotation, size in [(0, (800, 480)), (90, (480, 800)), (180, (800, 480)), (270, (480, 800))]:
            panel = make_panel([task(1)], rotation=rotation)
            panel.refresh()
            panel.draw(force=True)
            self.assertEqual(panel.display.shown[-1].size, size)

    def test_taps_work_on_the_logical_canvas(self):
        panel = self._panel()
        self._tap(panel, "toggle_dark")
        self.assertTrue(panel.dark)
        self._tap(panel, "open")
        self.assertEqual(panel.mode, "detail")
        self.assertEqual(panel.display.shown[-1].size, (480, 800))
        self._tap(panel, "back")
        self.assertEqual(panel.mode, "list")

    def test_paging_and_the_lock_gate_work_in_portrait(self):
        panel = self._panel(25)
        forward = max((b for b in panel.hitboxes if b.kind == "list_page"), key=lambda b: b.x0)
        panel.handle_tap((forward.x0 + 5, forward.y0 + 5))
        self.assertEqual(panel.list_page, 1)
        self._tap(panel, "toggle_touch_lock")
        shown = len(panel.display.shown)
        panel.handle_gesture(Gesture("swipe", 300, 400, dx=-200))
        self.assertEqual(len(panel.display.shown), shown)


class RecordingDisplay(StubDisplay):
    """Remembers whether each frame was a full or a partial refresh, and what
    rotation it was last told to draw for."""

    def __init__(self):
        super().__init__()
        self.kinds = []
        self.rotation = None

    def show(self, image):
        super().show(image)
        self.kinds.append("full")

    def show_partial(self, image):
        super().show_partial(image)
        self.kinds.append("partial")

    def set_rotation(self, rotation):
        self.rotation = rotation


class RecordingTouch:
    rotation = None

    def set_rotation(self, rotation):
        self.rotation = rotation


class TestRotateButton(unittest.TestCase):
    def _panel(self, n=25, rotation=0, state_path=None):
        panel = make_panel([task(i) for i in range(n)], rotation=rotation, state_path=state_path)
        panel.display, panel.touch = RecordingDisplay(), RecordingTouch()
        panel.refresh()
        panel.draw(force=True)
        return panel

    def _tap(self, panel, kind):
        box = next(b for b in panel.hitboxes if b.kind == kind)
        panel.handle_tap(((box.x0 + box.x1) // 2, (box.y0 + box.y1) // 2))

    def test_each_tap_flips_between_landscape_and_portrait(self):
        panel = self._panel()
        seen = []
        for _ in range(4):
            self._tap(panel, "toggle_rotate")
            seen.append((panel.rotation, panel.display.shown[-1].size))
        self.assertEqual(seen, [
            (270, (480, 800)), (0, (800, 480)), (270, (480, 800)), (0, (800, 480)),
        ])

    def test_from_a_configured_90_or_180_the_first_tap_returns_to_landscape(self):
        for start in (90, 180):
            panel = self._panel(rotation=start)
            self._tap(panel, "toggle_rotate")
            self.assertEqual(panel.rotation, 0)
            self._tap(panel, "toggle_rotate")
            self.assertEqual(panel.rotation, 270)

    def test_the_display_and_the_touch_chip_are_both_turned_with_it(self):
        panel = self._panel()
        self._tap(panel, "toggle_rotate")
        self.assertEqual((panel.display.rotation, panel.touch.rotation), (270, 270))

    def test_rotating_is_a_full_refresh_not_a_partial(self):
        panel = self._panel()
        panel.display.kinds.clear()
        self._tap(panel, "toggle_rotate")
        self.assertEqual(panel.display.kinds, ["full"])

    def test_page_numbers_restart_because_a_page_holds_a_different_number_of_rows(self):
        panel = self._panel()
        forward = max((b for b in panel.hitboxes if b.kind == "list_page"), key=lambda b: b.x0)
        panel.handle_tap((forward.x0 + 5, forward.y0 + 5))
        self.assertEqual(panel.list_page, 1)
        self._tap(panel, "toggle_rotate")
        self.assertEqual(panel.list_page, 0)

    def test_rotating_inside_a_task_stays_on_that_task(self):
        panel = self._panel(3)
        self._tap(panel, "open")
        self._tap(panel, "toggle_rotate")
        self.assertEqual(panel.mode, "detail")
        self.assertEqual(panel.display.shown[-1].size, (480, 800))

    def test_the_lock_blocks_it_like_everything_else(self):
        panel = self._panel()
        self._tap(panel, "toggle_touch_lock")
        shown = len(panel.display.shown)
        self._tap(panel, "toggle_rotate")
        self.assertEqual((panel.rotation, len(panel.display.shown)), (0, shown))

    def test_the_rotate_box_is_heavy_whenever_the_panel_is_turned(self):
        panel = self._panel()
        box = next(b for b in panel.hitboxes if b.kind == "toggle_rotate")
        before = panel.display.shown[-1].crop((box.x0, 0, box.x1, 64)).tobytes()
        self._tap(panel, "toggle_rotate")   # now portrait: the box moved, so look it up again
        box = next(b for b in panel.hitboxes if b.kind == "toggle_rotate")
        after = panel.display.shown[-1].crop((box.x0, 56, box.x1, 104)).tobytes()
        self.assertNotEqual(before, after)

    def test_the_choice_is_saved_and_a_restart_comes_back_the_same_way_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "local" / "state.json"
            panel = self._panel(state_path=state)
            self._tap(panel, "toggle_rotate")
            self.assertEqual(json.loads(state.read_text()), {"rotation": 270})
            self._tap(panel, "toggle_rotate")
            self.assertEqual(json.loads(state.read_text()), {"rotation": 0})
            self.assertFalse(state.with_suffix(".json.tmp").exists())   # renamed, not left behind

    def test_nothing_is_written_without_a_state_path(self):
        panel = self._panel()
        self._tap(panel, "toggle_rotate")   # must not raise, must not touch the disk
        self.assertEqual(panel.rotation, 270)

    def test_an_unwritable_state_path_does_not_stop_the_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("x")
            panel = self._panel(state_path=blocker / "state.json")   # parent is a file
            self._tap(panel, "toggle_rotate")
            self.assertEqual((panel.rotation, panel.display.shown[-1].size), (270, (480, 800)))


class KindRecordingDisplay(StubDisplay):
    """Remembers whether each frame was a partial, a full refresh, or a full
    refresh while thorough mode was on (see Display.set_thorough)."""

    def __init__(self):
        super().__init__()
        self.kinds = []
        self.thorough = False

    def set_thorough(self, on):
        self.thorough = on

    def show(self, image):
        super().show(image)
        self.kinds.append("thorough" if self.thorough else "full")

    def show_partial(self, image):
        super().show_partial(image)
        self.kinds.append("partial")


class TestScreenLock(unittest.TestCase):
    """The lock screen: hides the list behind a clock, a passcode to get back."""

    def _panel(self, passcode="2468", **kw):
        panel = make_panel([task(i) for i in range(3)], passcode=passcode, **kw)
        panel.display = KindRecordingDisplay()
        panel.refresh()
        panel.draw(force=True)
        return panel

    def _tap(self, panel, kind, key=None):
        box = next(b for b in panel.hitboxes if b.kind == kind and (key is None or b.key == key))
        panel.handle_tap((box.x0 + 5, box.y0 + 5))

    def _type(self, panel, code):
        for digit in code:
            self._tap(panel, "lock_key", digit)

    def _lock(self, panel):
        self._tap(panel, "lock_screen")
        self.assertTrue(panel.screen_locked)

    def test_locking_is_a_thorough_refresh_so_no_ghost_of_the_list_is_left(self):
        panel = self._panel()
        self._lock(panel)
        self.assertEqual(panel.display.kinds[-1], "thorough")
        self.assertEqual({b.kind for b in panel.hitboxes}, {"lock_wake"})

    def test_the_photo_is_always_painted_by_a_thorough_refresh(self):
        panel = self._panel()
        self._lock(panel)
        kinds = panel.display.kinds
        self.assertEqual(kinds[-1], "thorough")                                  # lock
        self._tap(panel, "lock_wake");              self.assertEqual(kinds[-1], "partial")
        self._tap(panel, "lock_key", "2");          self.assertEqual(kinds[-1], "partial")
        self._tap(panel, "lock_key", "delete");     self.assertEqual(kinds[-1], "partial")
        self._tap(panel, "lock_key", "cancel");     self.assertEqual(kinds[-1], "thorough")
        self._tap(panel, "lock_wake")
        n = len(kinds)
        panel.lock_last_input -= 31
        panel.tick()                                # keypad folds back to the clock
        self.assertEqual(kinds[n:], ["thorough"])
        panel.draw(force=True)                      # e.g. the idle cleanup, while locked
        self.assertEqual(kinds[-1], "thorough")
        self._tap(panel, "lock_wake")
        self._type(panel, "2468")                   # unlock: partial, and thorough mode off
        self.assertEqual(kinds[-1], "partial")
        panel.draw(force=True)
        self.assertEqual(kinds[-1], "full")

    def test_starting_up_locked_paints_the_clock_thoroughly(self):
        display = KindRecordingDisplay()
        with mock.patch("panel.app.open_display", return_value=display):
            panel = make_panel([task(1)], passcode="2468", start_locked=True)
        panel.display = display      # make_panel swaps in a plain stub; put ours back
        panel.refresh()
        panel.draw(force=True)
        self.assertEqual(display.kinds, ["thorough"])

    def test_the_right_code_unlocks_back_to_the_same_list(self):
        panel = self._panel()
        self._lock(panel)
        self._tap(panel, "lock_wake")
        self.assertTrue(panel.lock_keypad)
        self._type(panel, "2468")
        self.assertFalse(panel.screen_locked)
        self.assertIn("complete", {b.kind for b in panel.hitboxes})

    def test_a_wrong_code_stays_locked_clears_and_says_so(self):
        panel = self._panel()
        self._lock(panel)
        self._tap(panel, "lock_wake")
        self._type(panel, "1111")
        self.assertTrue(panel.screen_locked)
        self.assertEqual((panel.lock_entry, panel.lock_message), ("", "Wrong code"))

    def test_delete_and_cancel(self):
        panel = self._panel()
        self._lock(panel)
        self._tap(panel, "lock_wake")
        self._type(panel, "249")
        self._tap(panel, "lock_key", "delete")
        self.assertEqual(panel.lock_entry, "24")
        self._type(panel, "68")
        self.assertFalse(panel.screen_locked)

        self._lock(panel)
        self._tap(panel, "lock_wake")
        self._type(panel, "24")
        self._tap(panel, "lock_key", "cancel")
        self.assertEqual((panel.lock_keypad, panel.lock_entry), (False, ""))
        self.assertTrue(panel.screen_locked)

    def test_nothing_else_works_while_locked(self):
        panel = self._panel()
        self._lock(panel)
        panel.handle_gesture(Gesture("swipe", 600, 200, dx=-300))
        self.assertTrue(panel.screen_locked)
        # A stale hitbox from the list (a tap racing the redraw) is ignored.
        panel.hitboxes = [replace(b, kind="complete", task_id="task-0") for b in panel.hitboxes]
        panel.handle_tap((5, 5))
        self.assertEqual(len(panel.tasks), 3)

    def test_too_many_misses_start_a_cooldown_that_ignores_even_the_right_code(self):
        panel = self._panel()
        self._lock(panel)
        self._tap(panel, "lock_wake")
        for _ in range(5):
            self._type(panel, "0000")
        self.assertIn("Try again in 30 s", panel.lock_message)
        self._type(panel, "2468")
        self.assertTrue(panel.screen_locked)
        # Once the cooldown is over, it works again.
        panel.lock_blocked_until = 0.0
        self._type(panel, "2468")
        self.assertFalse(panel.screen_locked)
        self.assertEqual(panel.lock_failures, 0)

    def test_the_keypad_folds_away_after_a_while(self):
        panel = self._panel()
        self._lock(panel)
        self._tap(panel, "lock_wake")
        self._type(panel, "24")
        panel.lock_last_input -= 31
        panel.tick()
        self.assertEqual((panel.lock_keypad, panel.lock_entry), (False, ""))
        self.assertEqual({b.kind for b in panel.hitboxes}, {"lock_wake"})

    def test_a_background_change_does_not_redraw_the_lock_screen(self):
        panel = self._panel()
        self._lock(panel)
        shown = len(panel.display.shown)
        panel.client.tasks = panel.client.tasks[:1]
        panel.refresh()
        panel.draw(partial=True)
        self.assertEqual(len(panel.display.shown), shown)

    def test_without_a_passcode_a_tap_unlocks(self):
        panel = self._panel(passcode="")
        self._lock(panel)
        self._tap(panel, "lock_wake")
        self.assertFalse(panel.screen_locked)

    def test_starting_locked_shows_only_the_clock(self):
        panel = self._panel(start_locked=True)
        self.assertEqual({b.kind for b in panel.hitboxes}, {"lock_wake"})

    def test_the_real_panel_always_starts_locked_whatever_was_saved(self):
        # A reboot while unlocked — or a missing/damaged state file — must
        # not be a way past the lock.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            for content in (None, '{"rotation": 270}', '{"screen_locked": false}', "garbage"):
                if content is not None:
                    state.write_text(content)
                with mock.patch.dict(os.environ, {"PANEL_STATE_PATH": str(state)}):
                    self.assertTrue(Config.from_env().start_locked, content)

    def test_touch_lock_blocks_the_screen_lock_button_too(self):
        panel = self._panel()
        self._tap(panel, "toggle_touch_lock")
        self._tap(panel, "lock_screen")
        self.assertFalse(panel.screen_locked)


def inbox(n, **kw):
    return task(n, status="inbox", created_at=datetime(2026, 10, n), **kw)


class TestViewTabs(unittest.TestCase):
    def _tap(self, panel, kind, key=None):
        box = next(b for b in panel.hitboxes if b.kind == kind and (key is None or b.key == key))
        panel.handle_tap(((box.x0 + box.x1) // 2, (box.y0 + box.y1) // 2))

    def _panel(self):
        tasks = [task(i) for i in range(10)] + [inbox(1), inbox(2), task(20, status="waiting")]
        panel = make_panel(tasks)
        panel.refresh()
        panel.draw(force=True)
        return panel

    def test_a_tab_shows_its_status_and_starts_on_page_one(self):
        panel = self._panel()
        panel.list_page = 1
        self._tap(panel, "view", "inbox")
        self.assertEqual(panel.view, "inbox")
        self.assertEqual(panel.list_page, 0)
        opened = {b.task_id for b in panel.hitboxes if b.kind == "open"}
        self.assertEqual(opened, {"task-1", "task-2"})

    def test_the_active_tab_is_home(self):
        panel = self._panel()
        self._tap(panel, "view", "waiting")
        self.assertEqual({b.key for b in panel.hitboxes if b.kind == "home"}, {"waiting"})

    def test_from_a_task_another_tab_leaves_it_for_that_list(self):
        panel = self._panel()
        self._tap(panel, "open")
        self._tap(panel, "view", "inbox")
        self.assertEqual((panel.mode, panel.view), ("list", "inbox"))

    def test_switching_tab_needs_no_fetch(self):
        panel = self._panel()
        panel.client = None   # any network use would now raise
        self._tap(panel, "view", "someday")
        self.assertEqual(panel.view, "someday")

    def test_an_unknown_configured_view_starts_on_focus(self):
        with mock.patch("sys.stderr"):
            panel = make_panel([task(1)], view="next")
        self.assertEqual(panel.view, "focus")


def tap(panel, kind, key=None):
    """Tap the middle of the first box of `kind` (and `key`, if given)."""
    box = next(b for b in panel.hitboxes if b.kind == kind and (key is None or b.key == key))
    panel.handle_tap(((box.x0 + box.x1) // 2, (box.y0 + box.y1) // 2))


class TestFilingAnyTask(unittest.TestCase):
    """Any task's detail view can change its project or area, not only an
    inbox task being processed."""

    def _editing(self):
        panel = make_panel([])
        panel.client = StubClient([task(1, contexts=["@home"]), task(2)],
                                  [Area("a1", "Digital")], [Project("p1", "sowr", "a1")])
        panel.refresh()
        panel.draw(force=True)
        tap(panel, "open")
        tap(panel, "pick", "edit")
        self.assertEqual((panel.mode, panel.picker), ("picker", "edit"))
        return panel

    def test_a_choice_returns_to_the_edit_screen_and_back_returns_to_the_task(self):
        panel = self._editing()
        task_id = panel.detail_task_id
        tap(panel, "pick", "where")
        tap(panel, "choose_where", "project:p1")
        self.assertEqual(panel.client.writes, [(task_id, {"projectId": "p1", "areaId": None})])
        self.assertEqual((panel.mode, panel.picker), ("picker", "edit"))
        self.assertEqual(panel._find(task_id).project, "sowr")
        self.assertEqual(panel._find(task_id).status, "next")     # filing doesn't move it
        tap(panel, "back")
        self.assertEqual((panel.mode, panel.detail_task_id), ("detail", task_id))

    def test_contexts_and_due_date_from_edit(self):
        panel = self._editing()
        tap(panel, "pick", "contexts")
        tap(panel, "toggle_context", "@home")
        tap(panel, "back")                                         # contexts stays open
        self.assertEqual(panel.picker, "edit")
        tap(panel, "pick", "due")
        tap(panel, "choose_due")                                    # "Today", first
        self.assertEqual(panel.client.writes[-1][1], {"dueDate": date.today().isoformat()})
        self.assertEqual(panel.picker, "edit")

    def test_a_status_from_edit_changes_it_and_goes_back_to_the_task(self):
        panel = self._editing()
        task_id = panel.detail_task_id
        tap(panel, "set_status", "someday")
        self.assertEqual(panel.client.writes[-1], (task_id, {"status": "someday"}))
        self.assertEqual((panel.mode, panel.detail_task_id, panel.editing), ("detail", task_id, False))

    def test_swiping_right_steps_back_one_screen_at_a_time(self):
        panel = self._editing()
        tap(panel, "pick", "where")
        panel.handle_gesture(Gesture("swipe", 200, 300, dx=300))
        self.assertEqual(panel.picker, "edit")
        panel.handle_gesture(Gesture("swipe", 200, 300, dx=300))
        self.assertEqual(panel.mode, "detail")

    def test_swiping_left_on_a_non_inbox_task_skips_nothing(self):
        panel = make_panel([task(1), task(2)])
        panel.refresh()
        panel.draw(force=True)
        tap(panel, "open")
        opened = panel.detail_task_id
        panel.handle_gesture(Gesture("swipe", 300, 300, dx=-300))
        self.assertEqual(panel.detail_task_id, opened)


class TestProcessing(unittest.TestCase):
    AREAS = [Area("a1", "Digital")]
    PROJECTS = [Project("p1", "sowr", "a1")]

    def _tap(self, panel, kind, key=None):
        box = next(b for b in panel.hitboxes if b.kind == kind and (key is None or b.key == key))
        panel.handle_tap(((box.x0 + box.x1) // 2, (box.y0 + box.y1) // 2))

    def _panel(self, n=3, **kw):
        panel = make_panel([], view="inbox", **kw)
        panel.client = StubClient(
            [inbox(i, contexts=["@home"] if i == 1 else []) for i in range(1, n + 1)],
            self.AREAS, self.PROJECTS,
        )
        panel.refresh()
        panel.draw(force=True)
        self._tap(panel, "open")      # oldest first: task-1
        self.assertEqual(panel.detail_task_id, "task-1")
        return panel

    def test_filing_under_a_project_writes_both_ids_and_returns_to_the_task(self):
        panel = self._panel()
        self._tap(panel, "pick", "where")
        self.assertEqual((panel.mode, panel.picker), ("picker", "where"))
        self._tap(panel, "choose_where", "project:p1")
        self.assertEqual(panel.client.writes[-1], ("task-1", {"projectId": "p1", "areaId": None}))
        self.assertEqual(panel.mode, "detail")
        self.assertEqual((panel._find("task-1").project, panel._find("task-1").area), ("sowr", "Digital"))

    def test_an_area_alone_clears_the_project(self):
        panel = self._panel()
        self._tap(panel, "pick", "where")
        self._tap(panel, "choose_where", "area:a1")
        self.assertEqual(panel.client.writes[-1][1], {"projectId": None, "areaId": "a1"})

    def test_contexts_toggle_in_place(self):
        panel = self._panel()
        self._tap(panel, "pick", "contexts")
        self._tap(panel, "toggle_context", "@home")
        self.assertEqual(panel.client.writes[-1][1], {"contexts": []})
        self.assertEqual(panel.mode, "picker")

    def test_a_due_date_is_written_bare_and_clear_writes_null(self):
        panel = self._panel()
        self._tap(panel, "pick", "due")
        self._tap(panel, "choose_due", "2026-10-24")
        self.assertEqual(panel.client.writes[-1][1], {"dueDate": "2026-10-24"})
        self.assertEqual(panel._find("task-1").due_date, datetime(2026, 10, 24))
        self.assertEqual(panel.mode, "detail")
        self._tap(panel, "pick", "due")
        self._tap(panel, "choose_due", "")
        self.assertEqual(panel.client.writes[-1][1], {"dueDate": None})

    def test_a_status_sends_it_on_and_opens_the_next_inbox_task(self):
        panel = self._panel()
        self._tap(panel, "set_status", "next")
        self.assertEqual(panel.client.writes[-1], ("task-1", {"status": "next"}))
        self.assertEqual((panel.mode, panel.detail_task_id), ("detail", "task-2"))
        self._tap(panel, "set_status", "someday")
        self.assertEqual(panel.detail_task_id, "task-3")
        self._tap(panel, "set_status", "waiting")
        self.assertEqual((panel.mode, panel.view), ("list", "inbox"))
        self.assertEqual(panel._inbox_count(), 0)

    def test_completing_from_processing_also_moves_on(self):
        panel = self._panel()
        with mock.patch("panel.app.time.sleep"):
            self._tap(panel, "complete")
        self.assertEqual(panel.detail_task_id, "task-2")

    def test_a_failed_write_restores_the_task_and_the_screen(self):
        panel = self._panel()

        def fail(task_id, fields):
            from panel.client import MindwtrError
            raise MindwtrError("boom")

        panel.client.update_task = fail
        self._tap(panel, "set_status", "next")
        self.assertEqual((panel.mode, panel.detail_task_id), ("detail", "task-1"))
        self.assertEqual(panel._find("task-1").status, "inbox")
        self.assertIsNotNone(panel.stale_since)

    def test_swiping_left_skips_to_the_next_inbox_task_and_wraps(self):
        panel = self._panel()
        for expected in ("task-2", "task-3", "task-1"):
            panel.handle_gesture(Gesture("swipe", 300, 400, dx=-300))
            self.assertEqual((panel.mode, panel.detail_task_id), ("detail", expected))
        self.assertEqual(panel.client.writes, [])          # skipping writes nothing
        self.assertEqual(panel._inbox_count(), 3)

    def test_skipping_is_gated_by_the_touch_lock(self):
        panel = self._panel()
        self._tap(panel, "toggle_touch_lock")
        panel.handle_gesture(Gesture("swipe", 300, 400, dx=-300))
        self.assertEqual(panel.detail_task_id, "task-1")

    def test_a_status_on_the_last_task_comes_back_round_to_a_skipped_one(self):
        panel = self._panel()
        panel.handle_gesture(Gesture("swipe", 300, 400, dx=-300))   # skip task-1
        self._tap(panel, "set_status", "next")                      # task-2
        self._tap(panel, "set_status", "next")                      # task-3, last
        self.assertEqual(panel.detail_task_id, "task-1")

    def test_the_sort_button_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            panel = make_panel([task(1)], state_path=state, sort_newest=True)
            panel.refresh()
            panel.draw(force=True)
            self._tap(panel, "sort_toggle")
            self.assertEqual(json.loads(state.read_text()), {"sort_newest": False})

    def test_swiping_right_leaves_a_picker_but_turns_the_date_pickers_month(self):
        panel = self._panel()
        self._tap(panel, "pick", "where")
        panel.handle_gesture(Gesture("swipe", 200, 300, dx=300))
        self.assertEqual(panel.mode, "detail")
        self._tap(panel, "pick", "due")
        panel.handle_gesture(Gesture("swipe", 200, 300, dx=300))
        self.assertEqual(panel.mode, "picker")
        self.assertIsNotNone(panel.picker_month)

    def test_the_title_row_goes_back_to_the_task(self):
        panel = self._panel()
        self._tap(panel, "pick", "contexts")
        self._tap(panel, "back")
        self.assertEqual((panel.mode, panel.detail_task_id), ("detail", "task-1"))

    def test_touch_lock_gates_the_pickers_too(self):
        panel = self._panel()
        self._tap(panel, "pick", "where")
        self._tap(panel, "toggle_touch_lock")
        self._tap(panel, "choose_where", "project:p1")
        self.assertEqual(panel.client.writes, [])


if __name__ == "__main__":
    unittest.main()
