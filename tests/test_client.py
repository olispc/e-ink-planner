"""The client's joins and writes, and the views drawn from one fetch.
Run: python3 -m unittest discover tests"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime  # noqa: E402
from unittest import mock  # noqa: E402

from panel.client import ChecklistItem, MindwtrClient, _parse_iso, apply_fields  # noqa: E402
from panel.layout import _relative_age, select_view  # noqa: E402


class FakeClient(MindwtrClient):
    """Substitutes the transport, keeps the joining and view logic under test."""

    def __init__(self, tasks, etag=""):
        self.base = "http://stub"
        self._tasks = tasks
        self._etag = etag

    def _get(self, path, params=None):
        if path == "/v1/projects":
            return {"projects": [
                {"id": "p1", "title": "sowr", "areaId": "a1"},
                {"id": "p2", "title": "Old site", "areaId": "a1", "status": "archived"},
            ]}
        if path == "/v1/areas":
            return {"areas": [{"id": "a1", "name": "Digital"}, {"id": "a2", "name": "Home"}]}
        # No status filter: every open task, as pickTaskList returns them.
        return {"tasks": list(self._tasks), "total": len(self._tasks)}

    def _head(self, path):
        if path == "/v1/data":
            return {"ETag": self._etag} if self._etag else {}
        return {}


def raw(tid, **kw):
    base = {"id": tid, "title": tid.title(), "status": "next"}
    base.update(kw)
    return base


def view(client, name):
    return select_view(client.fetch(50).tasks, name)


class TestFocusView(unittest.TestCase):
    def test_focused_come_first_then_the_rest(self):
        client = FakeClient([raw("a"), raw("b", isFocusedToday=True), raw("c")])
        got = view(client, "focus")
        self.assertEqual([t.id for t in got], ["b", "a", "c"])
        self.assertTrue(got[0].focused)

    def test_a_focused_task_is_not_listed_twice(self):
        # Starred and next both; the panel must not show it twice.
        client = FakeClient([raw("a", isFocusedToday=True), raw("b")])
        self.assertEqual([t.id for t in view(client, "focus")], ["a", "b"])

    def test_nothing_focused_falls_back_to_the_backlog(self):
        # The failure this guards: a blank panel on any morning you forgot to
        # star anything, which is worse than no panel at all.
        client = FakeClient([raw("a"), raw("b")])
        self.assertEqual(len(view(client, "focus")), 2)

    def test_a_starred_task_shows_whatever_its_status(self):
        client = FakeClient([raw("a", status="waiting", isFocusedToday=True), raw("b", status="someday")])
        self.assertEqual([t.id for t in view(client, "focus")], ["a"])

    def test_plain_status_views_split_one_fetch(self):
        client = FakeClient([raw("a"), raw("b", status="inbox"), raw("c", status="waiting")])
        self.assertEqual([t.id for t in view(client, "next")], ["a"])
        self.assertEqual([t.id for t in view(client, "inbox")], ["b"])
        self.assertEqual([t.id for t in view(client, "waiting")], ["c"])


class TestJoins(unittest.TestCase):
    def test_task_in_a_project_inherits_the_project_area(self):
        # GET /tasks reports no areaId for a task inside a project; without the
        # fallback every project task renders as unfiled.
        client = FakeClient([raw("a", projectId="p1")])
        task = client.fetch(50).tasks[0]
        self.assertEqual(task.project, "sowr")
        self.assertEqual(task.area, "Digital")
        self.assertEqual((task.project_id, task.area_id), ("p1", None))

    def test_description_and_checklist_pass_through(self):
        client = FakeClient([raw(
            "a", description="notes",
            checklist=[{"id": "c1", "title": "step one", "isCompleted": True}],
        )])
        task = client.fetch(50).tasks[0]
        self.assertEqual(task.description, "notes")
        self.assertEqual(task.checklist, [ChecklistItem(id="c1", title="step one", completed=True)])

    def test_missing_description_and_checklist_default_safely(self):
        client = FakeClient([raw("a")])
        task = client.fetch(50).tasks[0]
        self.assertIsNone(task.description)
        self.assertEqual(task.checklist, [])

    def test_status_comes_through(self):
        client = FakeClient([raw("a", status="inbox")])
        self.assertEqual(client.fetch(50).tasks[0].status, "inbox")

    def test_archived_projects_are_not_offered(self):
        store = FakeClient([]).fetch(50)
        self.assertEqual([p.id for p in store.projects], ["p1"])
        self.assertEqual([a.name for a in store.areas], ["Digital", "Home"])


class TestApplyFields(unittest.TestCase):
    """The PATCH-to-Task mapping the panel's optimistic redraw relies on."""

    def setUp(self):
        self.store = FakeClient([raw("a", status="inbox", areaId="a2")]).fetch(50)
        self.task = self.store.tasks[0]

    def apply(self, fields):
        return apply_fields(self.task, fields, self.store.projects, self.store.areas)

    def test_filing_under_a_project_renames_and_takes_its_area(self):
        t = self.apply({"projectId": "p1", "areaId": None})
        self.assertEqual((t.project, t.area, t.area_id), ("sowr", "Digital", None))

    def test_filing_under_nothing_clears_both(self):
        t = self.apply({"projectId": None, "areaId": None})
        self.assertEqual((t.project, t.area), (None, None))

    def test_a_bare_date_is_a_due_date_and_null_clears_it(self):
        t = self.apply({"dueDate": "2026-10-24"})
        self.assertEqual(t.due_date, datetime(2026, 10, 24))
        self.assertIsNone(apply_fields(t, {"dueDate": None}, [], []).due_date)

    def test_status_contexts_star_and_checklist(self):
        t = self.apply({
            "status": "next", "contexts": ["@home"], "isFocusedToday": True,
            "checklist": [{"id": "c1", "title": "x", "isCompleted": False}],
        })
        self.assertEqual((t.status, t.contexts, t.focused), ("next", ["@home"], True))
        self.assertEqual(t.checklist, [ChecklistItem("c1", "x", False)])


class TestLocalTime(unittest.TestCase):
    """createdAt is UTC; the panel's day boundaries are local."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/London"
        time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_a_utc_timestamp_comes_back_as_local_wall_time(self):
        # 23:30Z on the 21st is 00:30 BST on the 22nd.
        self.assertEqual(_parse_iso("2026-10-21T23:30:00.000Z"), datetime(2026, 10, 22, 0, 30))

    def test_just_after_midnight_is_added_today(self):
        # What the panel said "added yesterday" for, 2026-10-21.
        created = _parse_iso("2026-10-21T23:30:00.000Z")
        self.assertEqual(_relative_age(created, datetime(2026, 10, 22, 0, 45)), "added today")

    def test_a_bare_date_stays_that_date(self):
        self.assertEqual(_parse_iso("2026-10-24"), datetime(2026, 10, 24))

    def test_bare_and_zoned_due_dates_sort_together(self):
        # Both naive now; mixing aware and naive would raise in the sort.
        dates = [_parse_iso("2026-10-24"), _parse_iso("2026-10-23T09:00:00Z")]
        self.assertEqual(sorted(dates)[0].day, 23)


class TestUpdateTask(unittest.TestCase):
    def test_patches_exactly_the_fields_given(self):
        client = MindwtrClient("http://stub", "t")
        with mock.patch.object(client.session, "patch") as patch:
            patch.return_value.ok = True
            client.update_task("a", {"dueDate": None, "status": "next"})
        url, = patch.call_args.args
        self.assertEqual(url, "http://stub/v1/tasks/a")
        self.assertEqual(patch.call_args.kwargs["json"], {"dueDate": None, "status": "next"})


class TestDataEtag(unittest.TestCase):
    def test_etag_reflects_the_store_file(self):
        client = FakeClient([], etag='W/"abc"')
        self.assertEqual(client.data_etag(), 'W/"abc"')

    def test_no_store_yet_is_a_stable_empty_value(self):
        client = FakeClient([])
        self.assertEqual(client.data_etag(), "")


if __name__ == "__main__":
    unittest.main()
