"""Panel's redraw gating — does a real, rendered content change actually
reach the display? Run: python3 -m unittest discover tests"""
from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.app import Panel  # noqa: E402
from panel.client import Task  # noqa: E402
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
    a mutation method now behaves the way the real server would, which
    matters once a Panel method's own internal refresh() reads it back."""

    def __init__(self, tasks):
        self.tasks = list(tasks)

    def fetch(self, view, limit):
        return list(self.tasks)

    def complete(self, task_id):
        self.tasks = [t for t in self.tasks if t.id != task_id]

    def update_checklist(self, task_id, checklist):
        self.tasks = [
            replace(t, checklist=checklist) if t.id == task_id else t for t in self.tasks
        ]

    def set_focused(self, task_id, focused):
        self.tasks = [
            replace(t, focused=focused) if t.id == task_id else t for t in self.tasks
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


def make_panel(tasks) -> Panel:
    config = Config(
        url="http://stub", token="t", view="next", poll_seconds=300,
        check_seconds=5, display="png", touch="none",
        png_path=Path("/tmp/unused-panel-test.png"), touch_device="",
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


class TestStarToggle(unittest.TestCase):
    def test_toggle_star_flips_focused_optimistically(self):
        panel = make_panel([task(1, focused=False)])
        panel.refresh()
        panel.draw(force=True)

        panel._toggle_star("task-1")
        self.assertTrue(panel._find("task-1").focused)

    def test_toggle_star_reverts_on_failure(self):
        class FailingClient(StubClient):
            def set_focused(self, task_id, focused):
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


class TestScreenLock(unittest.TestCase):
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
        self._tap(panel, "toggle_lock")
        self.assertTrue(panel.locked)
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
        self._tap(panel, "toggle_lock")
        panel.handle_gesture(Gesture("swipe", 600, 200, dx=-300))
        panel.handle_gesture(Gesture("tap", 400, 20))
        self.assertEqual(panel.list_page, 0)

    def test_lock_button_unlocks_and_input_resumes(self):
        panel = self._panel()
        self._tap(panel, "toggle_lock")
        self._tap(panel, "toggle_lock")
        self.assertFalse(panel.locked)
        self._tap(panel, "sort_toggle")
        self.assertTrue(panel.sort_newest)

    def test_background_refresh_still_updates_while_locked(self):
        panel = self._panel(3)
        self._tap(panel, "toggle_lock")
        panel.client.tasks = panel.client.tasks[:2]
        panel.refresh()
        panel.draw(partial=True)
        self.assertEqual(len(panel.tasks), 2)
        self.assertTrue(any(b.kind == "toggle_lock" for b in panel.hitboxes))

    def test_detail_view_keeps_the_header_and_can_lock_and_unlock(self):
        panel = self._panel(3)
        self._tap(panel, "open")
        self.assertEqual(panel.mode, "detail")

        self._tap(panel, "toggle_lock")
        self.assertTrue(panel.locked)
        self.assertEqual(panel.mode, "detail")
        self._tap(panel, "back")                  # gated like everything else
        self.assertEqual(panel.mode, "detail")

        self._tap(panel, "toggle_lock")
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


if __name__ == "__main__":
    unittest.main()
