"""The loop: poll, draw, wait for a tap, tick off, repeat.

Redraw discipline is the design constraint. A full refresh is ~1.5s; the
vendored driver also does flicker-free partial refresh (confirmed against
Waveshare's epd7in5_V2 source and demo — see display.py), but repeated
partial updates build up visible ghosting, so it isn't used unconditionally.
The split: a poll tick always does a full refresh, because it's not
latency-sensitive and content can change anywhere on screen. A tap is
latency-sensitive — that's the finger waiting — so navigation and optimistic
updates go through partial refresh, capped by EPaperDisplay itself.
"""
from __future__ import annotations

import signal
import sys
import time
from dataclasses import replace
from datetime import datetime
from typing import List, Optional

from .client import MindwtrClient, MindwtrError, Task
from .config import Config
from .display import open_display
from .layout import hit, render, render_detail
from .touch import open_touch

# One screenful, plus enough to keep the "N more" counter honest without
# pulling the whole list every five minutes.
FETCH_LIMIT = 50

# How long the checkmark+strikethrough confirmation holds before the row
# disappears and the list reflows. Two static frames, not motion — see the
# module docstring — so this is a deliberate pause, not a frame duration.
CONFIRM_SECONDS = 0.45


class Panel:
    def __init__(self, config: Config):
        self.config = config
        self.client = MindwtrClient(config.url, config.token)
        self.display = open_display(config)
        self.touch = open_touch(config)
        self.tasks: List[Task] = []
        self.stale_since: Optional[datetime] = None
        self.mode: str = "list"  # "list" | "detail"
        self.detail_task_id: Optional[str] = None
        # Done checklist items stack into one summary row by default; a tap
        # on it un-stacks them for this detail view. Resets on the next
        # "open" — see handle_tap — so reopening a task starts stacked again.
        self.checklist_expanded: bool = False
        self.checklist_page: int = 0
        # Which page of the list view is showing — clamped, not reset, by
        # render() itself when the list shrinks, same as checklist_page.
        self.list_page: int = 0
        # None = default fetch order (focused-first, heavy rule intact);
        # True/False = sorted by created date, newest/oldest first. Tapping
        # the already-active sort arrow clears back to None — the only way
        # back to the default order, since "home" deliberately leaves these
        # toggles alone (see handle_tap's "home" case).
        self.sort_mode: Optional[bool] = None
        self.group_by_area: bool = False
        self.group_by_project: bool = False
        self._last_signature: Optional[tuple] = None
        self._last_etag: Optional[str] = None

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self.tasks = self.client.fetch(self.config.view, FETCH_LIMIT)
            self.stale_since = None
        except MindwtrError as exc:
            # Keep showing the last known list rather than blanking: stale but
            # labelled beats empty and unexplained.
            if self.stale_since is None:
                self.stale_since = datetime.now()
            print("refresh failed: %s" % exc, file=sys.stderr)

    def _find(self, task_id: Optional[str]) -> Optional[Task]:
        if task_id is None:
            return None
        return next((t for t in self.tasks if t.id == task_id), None)

    def _signature(self) -> tuple:
        # The actual Task objects, not a hand-picked subset of their fields —
        # a signature built from e.g. (id, title, focused) alone silently
        # misses a changed project/area/description/contexts: refresh()
        # fetches the new data into self.tasks correctly, but draw() would
        # see an "unchanged" signature and skip the redraw. Task is a frozen
        # dataclass, so tuple equality here compares every field (including
        # nested ones like checklist) without needing to enumerate them.
        if self.mode == "detail":
            task = self._find(self.detail_task_id)
            if task is None:
                return ("detail-missing", self.detail_task_id)
            return ("detail", task, self.checklist_expanded, self.checklist_page)
        return (
            "list", tuple(self.tasks), self.list_page,
            self.sort_mode, self.group_by_area, self.group_by_project,
            self.stale_since is not None,
        )

    # -- output -----------------------------------------------------------
    def draw(self, force: bool = False, partial: bool = False) -> None:
        if self.mode == "detail" and self._find(self.detail_task_id) is None:
            # Completed or deleted elsewhere between polls — nothing left to
            # show here, so fall back rather than render a stale/missing task.
            self.mode = "list"
            self.detail_task_id = None
            force = True
        signature = self._signature()
        if not force and signature == self._last_signature:
            return
        if self.mode == "detail":
            rendered = render_detail(
                self._find(self.detail_task_id),
                expanded=self.checklist_expanded, page=self.checklist_page,
            )
        else:
            rendered = render(
                self.tasks,
                self.config.view,
                total=len(self.tasks),
                stale_since=self.stale_since,
                page=self.list_page,
                sort_mode=self.sort_mode,
                group_by_area=self.group_by_area,
                group_by_project=self.group_by_project,
            )
        if partial:
            self.display.show_partial(rendered.image)
        else:
            self.display.show(rendered.image)
        self.hitboxes = rendered.hitboxes
        self._last_signature = signature

    # -- interaction ------------------------------------------------------
    def handle_tap(self, point) -> None:
        box = hit(getattr(self, "hitboxes", []), point)
        if box is None:
            return
        if box.kind == "back":
            self.mode, self.detail_task_id = "list", None
            self.draw(force=True, partial=True)
        elif box.kind == "open":
            self.mode, self.detail_task_id = "detail", box.task_id
            self.checklist_expanded, self.checklist_page = False, 0
            self.draw(force=True, partial=True)
        elif box.kind == "complete":
            self._complete_task(box.task_id)
        elif box.kind == "toggle":
            self._toggle_checklist_item(box.task_id, box.item_id)
        elif box.kind == "expand":
            self.checklist_expanded, self.checklist_page = True, 0
            self.draw(force=True, partial=True)
        elif box.kind == "collapse":
            self.checklist_expanded, self.checklist_page = False, 0
            self.draw(force=True, partial=True)
        elif box.kind == "page":
            self.checklist_page = box.page
            self.draw(force=True, partial=True)
        elif box.kind == "star":
            self._toggle_star(box.task_id)
        elif box.kind == "list_page":
            self.list_page = box.page
            self.draw(force=True, partial=True)
        elif box.kind == "home":
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "sort_newest":
            self.sort_mode = None if self.sort_mode is True else True
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "sort_oldest":
            self.sort_mode = None if self.sort_mode is False else False
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "toggle_project_view":
            self.group_by_project = not self.group_by_project
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "toggle_area_view":
            self.group_by_area = not self.group_by_area
            self.list_page = 0
            self.draw(force=True, partial=True)

    def _complete_task(self, task_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        was_detail = self.mode == "detail" and self.detail_task_id == task_id
        # Held for one beat before the row is dropped and the list reflows
        # under it — visual confirmation the tap registered, without
        # relying on motion (see the module docstring on partial refresh).
        self._flash_completion(task)
        time.sleep(CONFIRM_SECONDS)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        if was_detail:
            self.mode, self.detail_task_id = "list", None
        self.draw(force=True, partial=True)
        try:
            self.client.complete(task_id)
        except MindwtrError as exc:
            print("complete failed: %s" % exc, file=sys.stderr)
            self.tasks = [task] + self.tasks
            if was_detail:
                self.mode, self.detail_task_id = "detail", task_id
            if self.stale_since is None:
                self.stale_since = datetime.now()
            self.draw(force=True, partial=True)
            return
        self.refresh()
        self.draw()

    def _flash_completion(self, task: Task) -> None:
        if self.mode == "detail" and self.detail_task_id == task.id:
            rendered = render_detail(
                task, completing=True,
                expanded=self.checklist_expanded, page=self.checklist_page,
            )
        else:
            rendered = render(
                self.tasks, self.config.view, total=len(self.tasks),
                stale_since=self.stale_since, completing=task.id, page=self.list_page,
                sort_mode=self.sort_mode, group_by_area=self.group_by_area,
                group_by_project=self.group_by_project,
            )
        self.display.show_partial(rendered.image)
        self.hitboxes = rendered.hitboxes

    def _toggle_checklist_item(self, task_id: str, item_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        new_checklist = [
            replace(item, completed=not item.completed) if item.id == item_id else item
            for item in task.checklist
        ]
        self.tasks = [
            replace(task, checklist=new_checklist) if t.id == task_id else t
            for t in self.tasks
        ]
        # Composition of unchecked/done shifts under a toggle, so the page
        # index it was on may no longer mean the same thing — back to page 0.
        self.checklist_page = 0
        self.draw(force=True, partial=True)
        try:
            self.client.update_checklist(task_id, new_checklist)
        except MindwtrError as exc:
            print("checklist update failed: %s" % exc, file=sys.stderr)
            self.tasks = [task if t.id == task_id else t for t in self.tasks]
            if self.stale_since is None:
                self.stale_since = datetime.now()
            self.draw(force=True, partial=True)
            return
        self.refresh()
        self.draw()

    def _toggle_star(self, task_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        new_focused = not task.focused
        self.tasks = [
            replace(t, focused=new_focused) if t.id == task_id else t for t in self.tasks
        ]
        # The heavier border/filled star show up immediately; re-sorting the
        # compound focus view into its new position waits for the refresh()
        # below, same as a completion's row-removal reflow.
        self.draw(force=True, partial=True)
        try:
            self.client.set_focused(task_id, new_focused)
        except MindwtrError as exc:
            print("star update failed: %s" % exc, file=sys.stderr)
            self.tasks = [task if t.id == task_id else t for t in self.tasks]
            if self.stale_since is None:
                self.stale_since = datetime.now()
            self.draw(force=True, partial=True)
            return
        self.refresh()
        self.draw()

    # -- loop -------------------------------------------------------------
    def _peek_etag(self) -> Optional[str]:
        """None on failure — "can't tell if it changed" isn't the same
        severity as a real fetch failing, so this doesn't touch stale_since;
        the PANEL_POLL_SECONDS fallback below still catches a real outage."""
        try:
            return self.client.data_etag()
        except MindwtrError:
            return None

    def run(self) -> None:
        self.refresh()
        self.draw(force=True)
        self._last_etag = self._peek_etag()
        while True:
            deadline = time.monotonic() + self.config.poll_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                tap = self.touch.wait_for_tap(min(remaining, self.config.check_seconds))
                if tap is not None:
                    self.handle_tap(tap)
                    deadline = time.monotonic() + self.config.poll_seconds
                    continue
                # A completion on the phone or Mac changes this store file's
                # stat — cheap enough to check every check_seconds rather
                # than waiting out the full poll_seconds fallback.
                etag = self._peek_etag()
                if etag is not None and etag != self._last_etag:
                    self._last_etag = etag
                    self.refresh()
                    self.draw()
                    deadline = time.monotonic() + self.config.poll_seconds
            self.refresh()
            self.draw()
            self._last_etag = self._peek_etag()

    def close(self) -> None:
        self.touch.close()
        self.display.close()


def main() -> int:
    config = Config.from_env()
    if not config.token:
        print("MINDWTR_TOKEN is not set — copy .env.example to .env", file=sys.stderr)
        return 1
    panel = Panel(config)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        panel.run()
    except KeyboardInterrupt:
        pass
    finally:
        panel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
