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


def task(n: int, **kw) -> Task:
    defaults = dict(
        id="task-%d" % n, title="Task %d" % n, project=None, area=None,
        contexts=[], focused=False, description=None, checklist=[],
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


if __name__ == "__main__":
    unittest.main()
