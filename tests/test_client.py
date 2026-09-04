"""The compound focus view. Run: python3 -m unittest discover tests"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.client import ChecklistItem, MindwtrClient  # noqa: E402


class FakeClient(MindwtrClient):
    """Substitutes the transport, keeps the joining and view logic under test."""

    def __init__(self, tasks, etag=""):
        self.base = "http://stub"
        self._tasks = tasks
        self._etag = etag

    def _get(self, path, params=None):
        if path == "/v1/projects":
            return {"projects": [{"id": "p1", "title": "sowr", "areaId": "a1"}]}
        if path == "/v1/areas":
            return {"areas": [{"id": "a1", "name": "Digital"}]}
        params = params or {}
        if params.get("isFocusedToday") == "true":
            return {"tasks": [t for t in self._tasks if t.get("isFocusedToday")]}
        status = params.get("status")
        return {"tasks": [t for t in self._tasks if t.get("status") == status]}

    def _head(self, path):
        if path == "/v1/data":
            return {"ETag": self._etag} if self._etag else {}
        return {}


def raw(tid, **kw):
    base = {"id": tid, "title": tid.title(), "status": "next"}
    base.update(kw)
    return base


class TestFocusView(unittest.TestCase):
    def test_focused_come_first_then_the_rest(self):
        client = FakeClient([raw("a"), raw("b", isFocusedToday=True), raw("c")])
        got = client.fetch("focus", 50)
        self.assertEqual([t.id for t in got], ["b", "a", "c"])
        self.assertTrue(got[0].focused)

    def test_a_focused_task_is_not_listed_twice(self):
        # It comes back from both queries; the panel must not show it twice.
        client = FakeClient([raw("a", isFocusedToday=True), raw("b")])
        self.assertEqual([t.id for t in client.fetch("focus", 50)], ["a", "b"])

    def test_nothing_focused_falls_back_to_the_backlog(self):
        # The failure this guards: a blank panel on any morning you forgot to
        # star anything, which is worse than no panel at all.
        client = FakeClient([raw("a"), raw("b")])
        self.assertEqual(len(client.fetch("focus", 50)), 2)

    def test_plain_status_view_is_unaffected(self):
        client = FakeClient([raw("a"), raw("b", status="inbox")])
        self.assertEqual([t.id for t in client.fetch("next", 50)], ["a"])


class TestJoins(unittest.TestCase):
    def test_task_in_a_project_inherits_the_project_area(self):
        # GET /tasks reports no areaId for a task inside a project; without the
        # fallback every project task renders as unfiled.
        client = FakeClient([raw("a", projectId="p1")])
        task = client.fetch("next", 50)[0]
        self.assertEqual(task.project, "sowr")
        self.assertEqual(task.area, "Digital")

    def test_description_and_checklist_pass_through(self):
        client = FakeClient([raw(
            "a", description="notes",
            checklist=[{"id": "c1", "title": "step one", "isCompleted": True}],
        )])
        task = client.fetch("next", 50)[0]
        self.assertEqual(task.description, "notes")
        self.assertEqual(task.checklist, [ChecklistItem(id="c1", title="step one", completed=True)])

    def test_missing_description_and_checklist_default_safely(self):
        client = FakeClient([raw("a")])
        task = client.fetch("next", 50)[0]
        self.assertIsNone(task.description)
        self.assertEqual(task.checklist, [])


class TestDataEtag(unittest.TestCase):
    def test_etag_reflects_the_store_file(self):
        client = FakeClient([], etag='W/"abc"')
        self.assertEqual(client.data_etag(), 'W/"abc"')

    def test_no_store_yet_is_a_stable_empty_value(self):
        client = FakeClient([])
        self.assertEqual(client.data_etag(), "")


if __name__ == "__main__":
    unittest.main()
