"""Client for the self-hosted mindwtr-cloud REST API.

Shapes here were read off apps/cloud/src/server.ts and packages/core/src/
task-sync-schema.ts rather than guessed:
  GET   /v1/tasks              -> {"tasks": [...], "total", "limit", "offset"}
  GET   /v1/projects           -> {"projects": [...]}
  GET   /v1/areas              -> {"areas": [...]}
  POST  /v1/tasks/<id>/complete
  PATCH /v1/tasks/<id>         -> {"task": {...}}
  HEAD  /v1/data               -> ETag header, no body
`/v1/data` is the whole-store sync endpoint the native apps use; its ETag is
built from the store file's stat (inode/size/mtime/ctime — server-data-cache.ts)
so it changes on any write at all, from any device. A HEAD there costs one
filesystem stat(), no JSON parsing — cheap enough to poll every few seconds
to notice a change fast, reserving the 3-request fetch() below for when the
ETag actually moves.
Query params the list route understands: status, query, all=1, deleted=1,
isFocusedToday, limit (capped at 1000 — LIST_PAGE_MAX_LIMIT in
shared-api-write-limits.ts). With no status it returns every open task — all
but done and archived (pickTaskList, server-validation.ts) — which is what
fetch() asks for: one request covers every view the panel can switch to, so a
tab change never waits on the network. List and single-task responses already
carry every field the task has, including `description` and `checklist` — no
separate detail endpoint is needed to get them.

Statuses are inbox, next, waiting, someday, reference, done, archived
(TASK_STATUS_VALUES, task-status.ts). status, projectId, areaId, dueDate and
contexts are all plain PATCH fields (cloudWrite 'create-patch' in
task-sync-schema.fixture.json), so processing an inbox task is a series of
PATCHes. Mindwtr's own processor writes a due date as a bare "YYYY-MM-DD"
when no time is set (joinDateTime, date-draft.ts), and setting a project
clears areaId — a task in a project takes the project's area
(process-inbox-workflow.ts).

`checklist` is `{id, title, isCompleted}[]`, and the schema's cloudWrite for
it is 'create-patch': the whole array replaces on PATCH, there is no
per-item toggle route. Ticking one item means read-modify-write the array.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

TIMEOUT = 10


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Mindwtr's timestamps have a variable-length fractional-seconds field
    (observed 5 digits, e.g. "...06.23589Z") — Python's fromisoformat only
    accepts exactly 3 or 6 digits before 3.11, so pad/truncate rather than
    let sorting silently drop tasks whose timestamp has an unusual count.

    A timestamp with a zone (createdAt is UTC, "...Z") comes back as naive
    *local* time, the same frame as datetime.now() and as a bare
    "YYYY-MM-DD" due date. Kept in UTC, its date was the wrong day for the
    first hour after midnight in BST: a task added at 00:30 is 23:30Z the day
    before, and read "added yesterday" (seen on the panel, 2026-10-21)."""
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    if "." in v:
        head, rest = v.split(".", 1)
        for sign in ("+", "-"):
            if sign in rest:
                frac, tz = rest.split(sign, 1)
                v = "%s.%s%s%s" % (head, (frac + "000000")[:6], sign, tz)
                break
        else:
            v = "%s.%s" % (head, (rest + "000000")[:6])
    try:
        parsed = datetime.fromisoformat(v)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    title: str
    completed: bool


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    project: Optional[str]
    area: Optional[str]
    contexts: List[str]
    focused: bool
    created_at: Optional[datetime]
    due_date: Optional[datetime]
    description: Optional[str]
    checklist: List[ChecklistItem]
    # `project`/`area` above are display names joined in by rejoin(); these
    # are what the task itself stores, which processing writes and re-joins.
    # `area_id` is the task's own — empty for a task inside a project.
    status: str = "next"
    project_id: Optional[str] = None
    area_id: Optional[str] = None


@dataclass(frozen=True)
class Area:
    id: str
    name: str


@dataclass(frozen=True)
class Project:
    id: str
    title: str
    area_id: Optional[str]


@dataclass(frozen=True)
class Store:
    """One fetch: every open task, plus the areas and projects needed to name
    them and to offer them when processing the inbox."""
    tasks: List[Task]
    areas: List[Area]
    projects: List[Project]


class MindwtrError(RuntimeError):
    pass


class MindwtrClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + token

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            r = self.session.get(self.base + path, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise MindwtrError("cannot reach %s (%s)" % (self.base, exc)) from exc
        if r.status_code == 401:
            raise MindwtrError("401 unauthorized — check MINDWTR_TOKEN")
        if not r.ok:
            raise MindwtrError("HTTP %s on %s: %s" % (r.status_code, path, r.text[:120]))
        return r.json()

    def _head(self, path: str) -> Dict[str, str]:
        try:
            r = self.session.head(self.base + path, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise MindwtrError("cannot reach %s (%s)" % (self.base, exc)) from exc
        if r.status_code == 404:
            return {}
        if not r.ok:
            raise MindwtrError("HTTP %s on %s" % (r.status_code, path))
        return r.headers

    def data_etag(self) -> str:
        """Empty string when the store has never been written to — still a
        valid, comparable value, just one that never matches a real ETag."""
        return self._head("/v1/data").get("ETag", "")

    def complete(self, task_id: str) -> None:
        try:
            r = self.session.post(
                "%s/v1/tasks/%s/complete" % (self.base, task_id), timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            raise MindwtrError("complete failed: %s" % exc) from exc
        if not r.ok:
            raise MindwtrError("complete %s: HTTP %s" % (task_id, r.status_code))

    def update_task(self, task_id: str, fields: Dict[str, Any]) -> None:
        """PATCH any of the task's own fields. null clears one (dueDate,
        projectId, areaId)."""
        try:
            r = self.session.patch(
                "%s/v1/tasks/%s" % (self.base, task_id), json=fields, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            raise MindwtrError("update failed: %s" % exc) from exc
        if not r.ok:
            raise MindwtrError("update %s: HTTP %s %s" % (task_id, r.status_code, r.text[:120]))

    def fetch(self, limit: int) -> Store:
        """Every open task, named. Which of them a view shows is decided on
        the panel (layout.select_view), not by the query."""
        body = self._get("/v1/tasks", {"limit": limit})
        raw = body.get("tasks", [])
        if body.get("total", len(raw)) > len(raw):
            print("fetched %d of %s open tasks" % (len(raw), body.get("total")), file=sys.stderr)
        projects = [
            Project(id=p["id"], title=label(p), area_id=p.get("areaId") or None)
            for p in self._get("/v1/projects").get("projects", [])
            # An archived project can't take new tasks and shouldn't be
            # offered; its tasks are archived with it, so none need naming.
            if p.get("status") != "archived" and not p.get("deletedAt")
        ]
        areas = [
            Area(id=a["id"], name=label(a))
            for a in self._get("/v1/areas").get("areas", [])
            if not a.get("deletedAt")
        ]
        tasks = [
            rejoin(
                Task(
                    id=t["id"],
                    title=t.get("title", "(untitled)"),
                    project=None,
                    area=None,
                    contexts=list(t.get("contexts") or []),
                    focused=bool(t.get("isFocusedToday")),
                    created_at=_parse_iso(t.get("createdAt")),
                    # dueDate is sometimes a bare "YYYY-MM-DD" (no time
                    # component) rather than a full timestamp — _parse_iso
                    # already handles that via fromisoformat's own support
                    # for date-only strings, same helper as createdAt.
                    due_date=_parse_iso(t.get("dueDate")),
                    description=t.get("description") or None,
                    checklist=[
                        ChecklistItem(
                            id=c["id"], title=c["title"], completed=bool(c.get("isCompleted"))
                        )
                        for c in (t.get("checklist") or [])
                    ],
                    status=t.get("status") or "inbox",
                    project_id=t.get("projectId") or None,
                    area_id=t.get("areaId") or None,
                ),
                projects, areas,
            )
            for t in raw
        ]
        return Store(tasks=tasks, areas=areas, projects=projects)


def checklist_field(checklist: List[ChecklistItem]) -> List[Dict[str, Any]]:
    """A checklist as the PATCH body wants it. Whole-array replace — the
    schema has no per-item toggle route, so ticking one item rewrites them all."""
    return [{"id": i.id, "title": i.title, "isCompleted": i.completed} for i in checklist]


def apply_fields(task: Task, fields: Dict[str, Any], projects: List[Project], areas: List[Area]) -> Task:
    """What a PATCH of `fields` does to the task, applied locally — the one
    mapping from the API's field names onto Task, used for the panel's
    optimistic redraw (and by the stand-in clients the tests and the web
    tester use, so they store exactly what the panel expects back)."""
    changes: Dict[str, Any] = {}
    if "status" in fields:
        changes["status"] = fields["status"]
    if "isFocusedToday" in fields:
        changes["focused"] = bool(fields["isFocusedToday"])
    if "contexts" in fields:
        changes["contexts"] = list(fields["contexts"])
    if "projectId" in fields:
        changes["project_id"] = fields["projectId"] or None
    if "areaId" in fields:
        changes["area_id"] = fields["areaId"] or None
    if "dueDate" in fields:
        changes["due_date"] = _parse_iso(fields["dueDate"])
    if "checklist" in fields:
        changes["checklist"] = [
            ChecklistItem(id=c["id"], title=c["title"], completed=bool(c.get("isCompleted")))
            for c in fields["checklist"]
        ]
    return rejoin(replace(task, **changes), projects, areas)


def rejoin(task: Task, projects: List[Project], areas: List[Area]) -> Task:
    """Fill in the project and area names from the task's own ids. The one
    home for the join, so a task the panel has just re-filed reads exactly as
    it will once the server's copy comes back."""
    proj = next((p for p in projects if p.id == task.project_id), None)
    names = {a.id: a.name for a in areas}
    # A task inside a project inherits that project's area and reports no
    # areaId of its own, so fall back to the project's rather than rendering
    # it as unfiled.
    area = names.get(task.area_id) or (names.get(proj.area_id) if proj else None)
    return replace(task, project=proj.title if proj else None, area=area)


def label(entity: Dict[str, Any]) -> str:
    """Areas are keyed 'name', projects 'title'. The API is not consistent."""
    return entity.get("title") or entity.get("name") or "?"
