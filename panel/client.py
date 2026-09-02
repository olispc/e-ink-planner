"""Client for the self-hosted mindwtr-cloud REST API.

Shapes here were read off apps/cloud/src/server.ts rather than guessed:
  GET  /v1/tasks?status=next   -> {"tasks": [...], "total", "limit", "offset"}
  GET  /v1/projects            -> {"projects": [...]}
  GET  /v1/areas               -> {"areas": [...]}
  POST /v1/tasks/<id>/complete
Query params the list route understands: status, query, all=1, deleted=1,
isFocusedToday.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

TIMEOUT = 10


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    project: Optional[str]
    area: Optional[str]
    contexts: List[str]
    focused: bool


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

    def complete(self, task_id: str) -> None:
        try:
            r = self.session.post(
                "%s/v1/tasks/%s/complete" % (self.base, task_id), timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            raise MindwtrError("complete failed: %s" % exc) from exc
        if not r.ok:
            raise MindwtrError("complete %s: HTTP %s" % (task_id, r.status_code))

    def fetch(self, view: str, limit: int) -> List[Task]:
        """One panel's worth of tasks, with project and area names joined in.

        `focus` is not a status — it is the isFocusedToday flag — and it is also
        the one compound view: what you chose for today, then next actions
        filling whatever rows are left. A wall panel that is blank whenever you
        forgot to star anything is worse than no panel, and this way the focus
        flag earns its place without being load-bearing.
        """
        if view == "focus":
            focused = self._list({"isFocusedToday": "true", "limit": limit})
            seen = {t.id for t in focused}
            rest = self._list({"status": "next", "limit": limit})
            return focused + [t for t in rest if t.id not in seen]
        return self._list({"status": view, "limit": limit})

    def _list(self, params: Dict[str, Any]) -> List[Task]:
        raw = self._get("/v1/tasks", params).get("tasks", [])
        projects = {p["id"]: p for p in self._get("/v1/projects").get("projects", [])}
        areas = {a["id"]: label(a) for a in self._get("/v1/areas").get("areas", [])}

        tasks = []
        for t in raw:
            proj = projects.get(t.get("projectId"))
            project = label(proj) if proj else None
            # A task inside a project inherits that project's area and reports
            # no areaId of its own, so fall back to the project's rather than
            # rendering it as unfiled.
            area = areas.get(t.get("areaId")) or (
                areas.get(proj.get("areaId")) if proj else None
            )
            tasks.append(
                Task(
                    id=t["id"],
                    title=t.get("title", "(untitled)"),
                    project=project,
                    area=area,
                    contexts=list(t.get("contexts") or []),
                    focused=bool(t.get("isFocusedToday")),
                )
            )
        return tasks


def label(entity: Dict[str, Any]) -> str:
    """Areas are keyed 'name', projects 'title'. The API is not consistent."""
    return entity.get("title") or entity.get("name") or "?"
