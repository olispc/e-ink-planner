"""The loop: poll, draw, wait for a tap, tick off, repeat.

Redraw discipline is the design constraint. A full refresh is ~1.7s (fast waveform; ~3.8s normal); the
vendored driver also does flicker-free partial refresh (confirmed against
Waveshare's epd7in5_V2 source and demo — see display.py), but repeated
partial updates build up visible ghosting. The split: every update — taps,
their reconciles, background changes, poll ticks — is partial, so the panel
never flashes while something is happening. The full refresh that clears the
ghosting runs only at startup and when EPaperDisplay.cleanup_due() says
enough residue has built up *and* the panel has been idle a while.
"""
from __future__ import annotations

import hmac
import signal
import sys
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .client import (
    Area, MindwtrClient, MindwtrError, Project, Task, apply_fields, checklist_field,
)
from .config import Config, save_state
from .display import open_display
from .layout import (
    VIEW_TABS, compute_group_order, hit, listed_order, render, render_detail, select_view,
)
from .lockscreen import load_background, pick_background, render_lock
from .pickers import render_picker
from .rotation import PORTRAIT_TURN, is_portrait
from .theme import (
    LOCK_COOLDOWN_MAX, LOCK_COOLDOWN_SECONDS, LOCK_FREE_TRIES, LOCK_KEYPAD_TIMEOUT,
)
from .touch import open_touch

# Every open task in one request, at the server's own cap (LIST_PAGE_MAX_LIMIT).
# The list view paginates, so a lower cap silently hid every task past it (73
# live next actions against a cap of 50), and each completion then pulled a
# new task in from beyond it. client.fetch says on stderr if it ever bites.
FETCH_LIMIT = 1000

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
        # Starts at the configured (or last-saved) orientation; the header's
        # rotate button toggles it between landscape and portrait. Unlike the view toggles it
        # survives a restart — see _rotate.
        self.rotation: int = config.rotation
        # Every open task, whatever its status; which of them the list shows
        # is the view tab's job (layout.select_view), so switching tab never
        # waits on the network. Areas and projects name them, and are what
        # an inbox task can be filed under.
        self.tasks: List[Task] = []
        self.areas: List[Area] = []
        self.projects: List[Project] = []
        if config.view in VIEW_TABS:
            self.view: str = config.view
        else:
            print("PANEL_VIEW=%s has no tab; starting on focus" % config.view, file=sys.stderr)
            self.view = "focus"
        self.stale_since: Optional[datetime] = None
        self.mode: str = "list"  # "list" | "detail" | "picker"
        self.detail_task_id: Optional[str] = None
        # A picker (panel/pickers.py) is opened from an inbox task's detail
        # view, for that task: "where", "contexts" or "due". Its page (where,
        # contexts) or month (due, a pickers.month_index) resets on opening.
        self.picker: Optional[str] = None
        self.picker_page: int = 0
        self.picker_month: Optional[int] = None
        # A non-inbox task's Edit screen is itself a picker ("edit") whose
        # rows open the others; while it's in play, a choice or "back" in one
        # of those returns to it rather than to the task.
        self.editing: bool = False
        # Done checklist items stack into one summary row by default; a tap
        # on it un-stacks them for this detail view. Resets on the next
        # "open" — see handle_tap — so reopening a task starts stacked again.
        self.checklist_expanded: bool = False
        self.checklist_page: int = 0
        # Checklist items unfolded to their full wrapped title by a tap on
        # their text. Same lifetime as checklist_expanded.
        self.expanded_items: frozenset = frozenset()
        # Which page of the list view is showing — clamped, not reset, by
        # render() itself when the list shrinks, same as checklist_page.
        self.list_page: int = 0
        # Sorted by created date, newest-first unless the sort button last
        # chose otherwise — that choice is saved (config.sort_newest), the
        # rest of these toggles are not. Focused-today tasks win over sort
        # order whenever focus_sticky is on (the default) — starring
        # something is a stronger signal than when it was created. "home"
        # deliberately leaves all of these alone (see handle_tap's "home" case).
        self.sort_newest: bool = config.sort_newest
        self.focus_sticky: bool = True
        self.group_by_area: bool = False
        self.group_by_project: bool = False
        # Pushes due-dated backlog tasks to the top of their list/group,
        # soonest due first — never touches the focus-sticky leading
        # section, which stays exempt as "the starred list" regardless.
        self.due_first: bool = False
        # Display and input state, both in-memory like the toggles above.
        # `dark` inverts the whole render. `touch_locked` makes every tap
        # except the touch-lock button's own a no-op — for wiping the glass or
        # a child's curious hand. The header (that button included) is on both
        # views, so touch can be locked while showing a task's detail;
        # unlocking is always one tap away, and "back" is blocked like
        # everything else.
        self.dark: bool = False
        self.touch_locked: bool = False
        # The screen lock is the opposite case: not "don't let taps change
        # anything" but "don't show the tasks at all" — a clock instead, and
        # a passcode to get back. The panel always starts locked (see
        # Config.start_locked), so restarting it is never a way around this.
        self.screen_locked: bool = False
        self._set_screen_locked(config.start_locked)
        self.lock_keypad: bool = False       # clock face, or the keypad face
        self.lock_entry: str = ""            # digits typed so far
        self.lock_message: Optional[str] = None
        self.lock_failures: int = 0          # wrong codes since the last unlock
        self.lock_blocked_until: float = 0.0  # monotonic; digits ignored until then
        self.lock_last_input: float = 0.0     # monotonic; folds the keypad away
        # Frozen group order (see layout.compute_group_order): taken lazily
        # on the first grouped draw, dropped whenever the user changes a
        # toggle or taps home, so completing/starring tasks never reshuffles
        # the groups — only a deliberate re-sort does.
        self._group_order: Optional[dict] = None
        self._last_signature: Optional[tuple] = None
        self._last_etag: Optional[str] = None

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        try:
            store = self.client.fetch(FETCH_LIMIT)
            self.tasks, self.areas, self.projects = store.tasks, store.areas, store.projects
            self.stale_since = None
        except MindwtrError as exc:
            # Keep showing the last known list rather than blanking: stale but
            # labelled beats empty and unexplained.
            if self.stale_since is None:
                self.stale_since = datetime.now()
            print("refresh failed: %s" % exc, file=sys.stderr)

    def _frozen_group_order(self) -> Optional[dict]:
        if not (self.group_by_area or self.group_by_project):
            return None
        if self._group_order is None:
            self._group_order = compute_group_order(
                self._visible(), self.sort_newest, self.focus_sticky,
                self.group_by_area, self.group_by_project,
            )
        return self._group_order

    def _visible(self) -> List[Task]:
        return select_view(self.tasks, self.view)

    def _inbox_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "inbox")

    def _contexts_in_use(self) -> List[str]:
        return sorted({c for t in self.tasks for c in t.contexts})

    def _find(self, task_id: Optional[str]) -> Optional[Task]:
        if task_id is None:
            return None
        return next((t for t in self.tasks if t.id == task_id), None)

    @property
    def portrait(self) -> bool:
        return is_portrait(self.rotation)

    def _shared_state(self) -> dict:
        """The state every screen draws with: the header's tabs and toggles,
        and orientation."""
        return dict(
            sort_newest=self.sort_newest, focus_sticky=self.focus_sticky,
            group_by_area=self.group_by_area, group_by_project=self.group_by_project,
            due_first=self.due_first, dark=self.dark, touch_locked=self.touch_locked,
            portrait=self.portrait, rotation=self.rotation,
            view=self.view, inbox_count=self._inbox_count(),
        )

    def _signature(self) -> tuple:
        # The actual Task objects, not a hand-picked subset of their fields —
        # a signature built from e.g. (id, title, focused) alone silently
        # misses a changed project/area/description/contexts: refresh()
        # fetches the new data into self.tasks correctly, but draw() would
        # see an "unchanged" signature and skip the redraw. Task is a frozen
        # dataclass, so tuple equality here compares every field (including
        # nested ones like checklist) without needing to enumerate them.
        if self.screen_locked:
            # Nothing about the tasks: a background change while locked has
            # nothing to show. The minute is in it so the clock ticks.
            now = datetime.now()
            return (
                "lock", now.strftime("%Y-%m-%d %H:%M"), self.lock_keypad,
                len(self.lock_entry), self.lock_message, self.dark, self.portrait,
            )
        if self.mode in ("detail", "picker"):
            task = self._find(self.detail_task_id)
            if task is None:
                return ("detail-missing", self.detail_task_id)
            shared = tuple(sorted(self._shared_state().items()))
            if self.mode == "picker":
                # What the picker offers, too: a project renamed elsewhere
                # changes the "where" list without touching this task.
                return (
                    "picker", self.picker, task, self.picker_page, self.picker_month,
                    tuple(self.areas), tuple(self.projects), tuple(self._contexts_in_use()), shared,
                )
            return (
                "detail", task, self.checklist_expanded, self.checklist_page, self.expanded_items,
                shared,
            )
        # The visible slice, not every open task: a change to a Someday task
        # has nothing to redraw on the Focus list (bar the inbox badge).
        return (
            "list", self.view, tuple(self._visible()), self._inbox_count(), self.list_page,
            self.sort_newest, self.focus_sticky, self.group_by_area, self.group_by_project,
            self.due_first, self.stale_since is not None, self.dark, self.touch_locked,
        )

    # -- output -----------------------------------------------------------
    def draw(self, force: bool = False, partial: bool = False) -> None:
        if self.mode in ("detail", "picker") and self._find(self.detail_task_id) is None:
            # Completed or deleted elsewhere between polls — nothing left to
            # show here, so fall back rather than render a stale/missing task.
            self.mode, self.detail_task_id, self.picker = "list", None, None
            self.editing = False
            force = True
        signature = self._signature()
        if not force and signature == self._last_signature:
            return
        if self.screen_locked:
            rendered = self._render_lock()
        elif self.mode == "picker":
            rendered = render_picker(
                self.picker, self._find(self.detail_task_id),
                projects=self.projects, areas=self.areas, contexts=self._contexts_in_use(),
                page=self.picker_page, month=self.picker_month, **self._shared_state(),
            )
        elif self.mode == "detail":
            rendered = render_detail(
                self._find(self.detail_task_id),
                expanded=self.checklist_expanded, page=self.checklist_page,
                expanded_items=self.expanded_items, **self._shared_state(),
            )
        else:
            visible = self._visible()
            rendered = render(
                visible,
                total=len(visible),
                stale_since=self.stale_since,
                page=self.list_page,
                group_order=self._frozen_group_order(),
                **self._shared_state(),
            )
        if partial:
            self.display.show_partial(rendered.image)
        else:
            self.display.show(rendered.image)
        self.hitboxes = rendered.hitboxes
        self._last_signature = signature

    def _render_lock(self):
        size = (480, 800) if self.portrait else (800, 480)
        now = datetime.now()
        background = load_background(pick_background(self.config.lock_images, now), size)
        return render_lock(
            now=now, portrait=self.portrait, keypad=self.lock_keypad,
            entered=len(self.lock_entry), code_length=len(self.config.passcode),
            message=self.lock_message, background=background, dark=self.dark,
        )

    # -- interaction ------------------------------------------------------
    def handle_gesture(self, gesture) -> None:
        if gesture.kind == "swipe":
            if self.touch_locked or self.screen_locked:
                return
            self.handle_swipe(gesture.dx)
        else:
            self.handle_tap((gesture.x, gesture.y))

    def handle_swipe(self, dx: int) -> None:
        """Page-turn like a Kindle: dragging left reveals the next page,
        dragging right the previous. The current screen's own pager hitboxes
        already say which page that is (and wrap at the ends), so this just
        borrows them — a screen with no pager has nothing to turn.

        On a task's detail view or a picker, dragging right from its first
        page (or with no pager) is "back" instead: it borrows the screen's back
        box the same way, so it is gated exactly like tapping it. The date
        picker is the exception — its pager is the month, which swipes turn
        either way; its title row is the way back."""
        boxes = getattr(self, "hitboxes", [])
        if self.mode == "detail" and dx < 0:
            # An inbox task being processed: swiping on skips it, leaving it
            # in the inbox untouched. Its processing view has no pager to
            # turn (the description is capped there), so nothing is lost.
            task = self._find(self.detail_task_id)
            if task is not None and task.status == "inbox":
                after = self._next_inbox(task)
                if after is not None:
                    self._open(after)
                    self.draw(force=True, partial=True)
                return
        first_page = {"detail": self.checklist_page == 0,
                      "picker": self.picker != "due" and self.picker_page == 0}.get(self.mode, False)
        if first_page and dx > 0:
            back = next((b for b in boxes if b.kind == "back"), None)
            if back is not None:
                self.handle_tap((back.x0 + 1, back.y0 + 1))
            return
        pagers = [b for b in boxes if b.kind in ("list_page", "page", "picker_page", "month")]
        if not pagers:
            return
        box = max(pagers, key=lambda b: b.x0) if dx < 0 else min(pagers, key=lambda b: b.x0)
        self.handle_tap((box.x0 + 1, box.y0 + 1))

    def handle_tap(self, point) -> None:
        box = hit(getattr(self, "hitboxes", []), point)
        if box is None:
            return
        # The lock screen draws only its own boxes, but a tap can race a
        # redraw, so it is gated by kind here too: while screen-locked nothing
        # but the lock screen's own boxes does anything.
        if self.screen_locked:
            if box.kind in ("lock_wake", "lock_key"):
                self._handle_lock_tap(box)
            return
        # The one gate for touch input: touch-locked, its button is the only
        # live target — everything else (rows, pagers, other toggles) falls
        # through to nothing, so no future box kind can forget to check.
        if self.touch_locked and box.kind != "toggle_touch_lock":
            return
        if box.kind == "back":
            # One screen back: a picker returns to the Edit screen it was
            # opened from, or to its task; a task returns to the list.
            if self.mode == "picker":
                self._back_to_task()
            else:
                self.mode, self.detail_task_id = "list", None
            self.draw(force=True, partial=True)
        elif box.kind == "view":
            self._show_view(box.key)
        elif box.kind == "open":
            self._open(box.task_id)
            self.draw(force=True, partial=True)
        elif box.kind == "pick":
            self.mode, self.picker, self.picker_page = "picker", box.key, 0
            self.picker_month = None
            if box.key == "edit":
                self.editing = True
            self.draw(force=True, partial=True)
        elif box.kind == "picker_page":
            self.picker_page = box.page
            self.draw(force=True, partial=True)
        elif box.kind == "month":
            self.picker_month = box.page
            self.draw(force=True, partial=True)
        elif box.kind == "choose_where":
            self._file_task(box.task_id, box.key)
        elif box.kind == "toggle_context":
            self._toggle_context(box.task_id, box.key)
        elif box.kind == "choose_due":
            self._set_due(box.task_id, box.key)
        elif box.kind == "set_status":
            self._set_status(box.task_id, box.key)
        elif box.kind == "complete":
            self._complete_task(box.task_id)
        elif box.kind == "toggle":
            self._toggle_checklist_item(box.task_id, box.item_id)
        elif box.kind == "expand_item":
            self.expanded_items ^= {box.item_id}
            self.checklist_page = box.page
            self.draw(force=True, partial=True)
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
            self._group_order = None
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "sort_toggle":
            self.sort_newest = not self.sort_newest
            self._save(sort_newest=self.sort_newest)
            self._group_order = None
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "focus_sticky":
            self.focus_sticky = not self.focus_sticky
            self._group_order = None
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "toggle_project_view":
            self.group_by_project = not self.group_by_project
            self._group_order = None
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "toggle_area_view":
            self.group_by_area = not self.group_by_area
            self._group_order = None
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "toggle_due_first":
            self.due_first = not self.due_first
            self._group_order = None
            self.list_page = 0
            self.draw(force=True, partial=True)
        elif box.kind == "toggle_dark":
            # Same content, same page — only the polarity changes, so no
            # page or group-order reset like the view toggles above.
            self.dark = not self.dark
            self.draw(force=True, partial=True)
        elif box.kind == "toggle_touch_lock":
            self.touch_locked = not self.touch_locked
            self.draw(force=True, partial=True)
        elif box.kind == "lock_screen":
            self._lock_screen()
        elif box.kind == "toggle_rotate":
            self._rotate()

    def _open(self, task_id: Optional[str]) -> None:
        """Show a task's detail view, folded and on its first page."""
        self.mode, self.detail_task_id, self.picker = "detail", task_id, None
        self.editing = False
        self.checklist_expanded, self.checklist_page = False, 0
        self.expanded_items = frozenset()

    def _show_view(self, view: str) -> None:
        """A tab tap: the list for `view`, from whatever screen is showing.
        A different view starts on its first page with its groups freshly
        ordered; the one already showing keeps its page (from a picker, its
        tab is the way out to the list, not "home")."""
        self.mode, self.detail_task_id, self.picker = "list", None, None
        self.editing = False
        if view != self.view:
            self.view = view
            self.list_page = 0
            self._group_order = None
        self.draw(force=True, partial=True)

    def _rotate(self) -> None:
        """Toggle between landscape and portrait (PORTRAIT_TURN). From any
        other configured orientation — 90 or 180 set in .env — the first tap
        goes back to landscape. Everything changes place, so this is a full
        refresh (the flash), not a partial: the one tap-driven update besides
        the rule's own exceptions, because a whole-screen repaint in new
        positions is exactly what partials ghost on."""
        self.rotation = 0 if self.rotation else PORTRAIT_TURN
        # png backends and test stubs have no set_rotation: they draw upright.
        for device in (self.display, self.touch):
            set_rotation = getattr(device, "set_rotation", None)
            if set_rotation is not None:
                set_rotation(self.rotation)
        # A page number from the old canvas means something else on this one
        # (6 rows a page vs 10), so start from the top rather than guess.
        self.list_page, self.checklist_page = 0, 0
        self._save(rotation=self.rotation)
        self.draw(force=True)

    def _save(self, **values) -> None:
        """Remember a choice across restarts (local/state.json). Failing to is
        said on stderr but never stops the tap that made it."""
        if self.config.state_path is None:
            return
        try:
            save_state(self.config.state_path, **values)
        except OSError as exc:
            print("could not save %s: %s" % (", ".join(values), exc), file=sys.stderr)

    # -- screen lock --------------------------------------------------------
    # Refresh rule for the screen lock, from what the panel showed (2026-10-20):
    # the fast full waveform leaves a dithered photo pale with earlier frames
    # showing through, and partials drawn over that pale base come out as
    # stencilled, half-dark digits. So while locked the display makes every
    # full refresh thorough (set_thorough: the normal waveform, ~3.9s), and
    # the photo is always painted by one: on locking (which also leaves no
    # ghost of the list), on starting up locked, and on folding back to the
    # clock. Waking to the keypad and unlocking stay partial — white keys
    # cover the photo, and partial back to the list was fine on the panel —
    # as do a typed digit and the minute ticking.
    def _set_screen_locked(self, locked: bool) -> None:
        self.screen_locked = locked
        # png backends and test stubs have one kind of full refresh.
        set_thorough = getattr(self.display, "set_thorough", None)
        if set_thorough is not None:
            set_thorough(locked)

    def _lock_screen(self) -> None:
        self._set_screen_locked(True)
        self.lock_keypad, self.lock_entry, self.lock_message = False, "", None
        self.draw(force=True)

    def _unlock_screen(self) -> None:
        self._set_screen_locked(False)
        self.lock_keypad, self.lock_entry, self.lock_message = False, "", None
        self.lock_failures, self.lock_blocked_until = 0, 0.0
        self.draw(force=True, partial=True)

    def _handle_lock_tap(self, box) -> None:
        now = time.monotonic()
        self.lock_last_input = now
        if box.kind == "lock_wake":
            # No passcode set: the lock only hides the list, so a tap is enough.
            if not self.config.passcode:
                self._unlock_screen()
                return
            self.lock_keypad, self.lock_entry = True, ""
            if now >= self.lock_blocked_until:
                self.lock_message = None
            self.draw(force=True, partial=True)
            return
        if box.key == "cancel":
            self.lock_keypad, self.lock_entry = False, ""
            self.draw(force=True)
            return
        if box.key == "delete":
            self.lock_entry = self.lock_entry[:-1]
        elif now < self.lock_blocked_until:
            return   # cooling down after too many misses: digits do nothing
        else:
            self.lock_entry += box.key
            self.lock_message = None
            if len(self.lock_entry) >= len(self.config.passcode):
                # Constant-time compare: cheap, and there's no reason not to.
                if hmac.compare_digest(self.lock_entry, self.config.passcode):
                    self._unlock_screen()
                    return
                self._wrong_code(now)
        self.draw(partial=True)

    def _wrong_code(self, now: float) -> None:
        """The code's length is shown (one dot per digit), so a guess is
        checked as soon as it's complete — no enter key. After
        LOCK_FREE_TRIES misses each further one starts a cooldown, doubling
        each time up to LOCK_COOLDOWN_MAX."""
        self.lock_failures += 1
        self.lock_entry = ""
        over = self.lock_failures - LOCK_FREE_TRIES
        if over < 0:
            self.lock_message = "Wrong code"
            return
        wait = min(LOCK_COOLDOWN_SECONDS * 2 ** over, LOCK_COOLDOWN_MAX)
        self.lock_blocked_until = now + wait
        self.lock_message = "Wrong code. Try again in %s" % (
            "%d s" % wait if wait < 60 else "%d min" % round(wait / 60)
        )

    def tick(self) -> None:
        """Called between gestures by run(): the lock screen's clock and its
        timeouts. Drawing goes through the signature as always, so this only
        reaches the panel when the minute (or what's shown) actually changed."""
        if not self.screen_locked:
            return
        now = time.monotonic()
        if self.lock_keypad and now - self.lock_last_input >= LOCK_KEYPAD_TIMEOUT:
            self.lock_keypad, self.lock_entry = False, ""
            self.draw(force=True)   # a face change: see the refresh rule above
        if self.lock_blocked_until and now >= self.lock_blocked_until:
            # The cooldown is over, so its "try again in" stops being true.
            self.lock_blocked_until, self.lock_message = 0.0, None
        self.draw(partial=True)

    def _complete_task(self, task_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        was_detail = self.mode == "detail" and self.detail_task_id == task_id
        # Completing an inbox task from its detail view is processing it
        # (the two-minute rule), so the panel moves on to the next one, as
        # sending it on with a status does.
        after = self._next_inbox(task) if was_detail and task.status == "inbox" else None
        # Held for one beat before the row is dropped and the list reflows
        # under it — visual confirmation the tap registered, without
        # relying on motion (see the module docstring on partial refresh).
        self._flash_completion(task)
        time.sleep(CONFIRM_SECONDS)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        if after is not None:
            self._open(after)
        elif was_detail:
            self.mode, self.detail_task_id = "list", None
        self.draw(force=True, partial=True)
        try:
            self.client.complete(task_id)
        except MindwtrError as exc:
            print("complete failed: %s" % exc, file=sys.stderr)
            self.tasks = [task] + self.tasks
            if was_detail:
                self._open(task_id)
            if self.stale_since is None:
                self.stale_since = datetime.now()
            self.draw(force=True, partial=True)
            return
        self.refresh()
        # Still tap-driven, so partial: the reconcile can differ from the
        # optimistic frame (an edit from another device landing meanwhile),
        # and clearing the ghosting is the idle cleanup's job, not this one's.
        self.draw(partial=True)

    def _flash_completion(self, task: Task) -> None:
        if self.mode == "detail" and self.detail_task_id == task.id:
            rendered = render_detail(
                task, completing=True,
                expanded=self.checklist_expanded, page=self.checklist_page,
                expanded_items=self.expanded_items, **self._shared_state(),
            )
        else:
            visible = self._visible()
            rendered = render(
                visible, total=len(visible),
                stale_since=self.stale_since, completing=task.id, page=self.list_page,
                group_order=self._frozen_group_order(),
                **self._shared_state(),
            )
        self.display.show_partial(rendered.image)
        self.hitboxes = rendered.hitboxes

    def _patch_task(
        self, task_id: str, fields: Dict[str, Any], navigate: Optional[Callable[[], None]] = None,
    ) -> None:
        """The optimistic write every edit shares: apply `fields` to the
        local copy as the server will (client.apply_fields), make any screen
        change, redraw, then PATCH. A failed write puts the task and the
        screen back as they were and labels the panel offline; a good one
        reconciles with a refresh."""
        task = self._find(task_id)
        if task is None:
            return
        screen = (self.mode, self.detail_task_id, self.picker, self.editing, self.picker_page)
        updated = apply_fields(task, fields, self.projects, self.areas)
        self.tasks = [updated if t.id == task_id else t for t in self.tasks]
        if navigate is not None:
            navigate()
        self.draw(force=True, partial=True)
        try:
            self.client.update_task(task_id, fields)
        except MindwtrError as exc:
            print("update failed: %s" % exc, file=sys.stderr)
            self.tasks = [task if t.id == task_id else t for t in self.tasks]
            self.mode, self.detail_task_id, self.picker, self.editing, self.picker_page = screen
            if self.stale_since is None:
                self.stale_since = datetime.now()
            self.draw(force=True, partial=True)
            return
        self.refresh()
        self.draw(partial=True)

    def _toggle_checklist_item(self, task_id: str, item_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        new_checklist = [
            replace(item, completed=not item.completed) if item.id == item_id else item
            for item in task.checklist
        ]
        # Composition of unchecked/done shifts under a toggle, so the page
        # index it was on may no longer mean the same thing — back to page 0.
        self.checklist_page = 0
        self._patch_task(task_id, {"checklist": checklist_field(new_checklist)})

    def _toggle_star(self, task_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        self._patch_task(task_id, {"isFocusedToday": not task.focused})

    # -- processing an inbox task ---------------------------------------------
    def _back_to_task(self) -> None:
        """Leave a picker: to the Edit screen it came from, else the task."""
        if self.editing and self.picker != "edit":
            self.picker, self.picker_page = "edit", 0
        else:
            self.mode, self.picker, self.editing = "detail", None, False

    def _file_task(self, task_id: str, key: str) -> None:
        """A project (which brings its area: Mindwtr clears the task's own
        areaId when it has a project), an area alone, or neither."""
        kind, _, entity_id = key.partition(":")
        self._patch_task(task_id, {
            "projectId": entity_id if kind == "project" else None,
            "areaId": entity_id if kind == "area" else None,
        }, navigate=self._back_to_task)

    def _toggle_context(self, task_id: str, context: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        if context in task.contexts:
            contexts = [c for c in task.contexts if c != context]
        else:
            contexts = task.contexts + [context]
        self._patch_task(task_id, {"contexts": contexts})

    def _set_due(self, task_id: str, day: str) -> None:
        """`day` is "YYYY-MM-DD", or "" to clear it. A bare date, as
        Mindwtr's own processor writes one when no time is set."""
        self._patch_task(task_id, {"dueDate": day or None}, navigate=self._back_to_task)

    def _set_status(self, task_id: str, status: str) -> None:
        """Send an inbox task on — the last step of processing it, so the
        panel moves straight to the next inbox task (back to the inbox list
        once there are none). From any other task's Edit screen it is just
        a change, and goes back to the task."""
        task = self._find(task_id)
        if task is None:
            return
        if task.status != "inbox":
            def back() -> None:
                self.mode, self.picker, self.editing = "detail", None, False
            self._patch_task(task_id, {"status": status}, navigate=back)
            return
        after = self._next_inbox(task)

        def navigate() -> None:
            if after is None:
                self.mode, self.detail_task_id, self.picker = "list", None, None
            else:
                self._open(after)

        self._patch_task(task_id, {"status": status}, navigate=navigate)

    def _next_inbox(self, task: Task) -> Optional[str]:
        """The inbox task listed after `task`, in the inbox list's own order
        under the current toggles — wrapping to the top from the last, like
        every pager, so skipped tasks come round again."""
        frozen = self._frozen_group_order() if self.view == "inbox" else None
        inbox = [t.id for t in listed_order(
            select_view(self.tasks, "inbox"), self.sort_newest, self.focus_sticky,
            self.group_by_area, self.group_by_project, self.due_first, frozen,
        )]
        if task.id not in inbox or len(inbox) < 2:
            return None
        return inbox[(inbox.index(task.id) + 1) % len(inbox)]

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
                gesture = self.touch.wait_for_gesture(min(remaining, self.config.check_seconds))
                if gesture is not None:
                    self.handle_gesture(gesture)
                    deadline = time.monotonic() + self.config.poll_seconds
                    continue
                self.tick()
                # Every update is partial; the full refresh that clears their
                # ghosting is the display's call (see cleanup_due) and only
                # ever happens while the panel is idle.
                if self.display.cleanup_due():
                    self.draw(force=True)
                # A completion on the phone or Mac changes this store file's
                # stat — cheap enough to check every check_seconds rather
                # than waiting out the full poll_seconds fallback.
                etag = self._peek_etag()
                if etag is not None and etag != self._last_etag:
                    self._last_etag = etag
                    self.refresh()
                    self.draw(partial=True)
                    deadline = time.monotonic() + self.config.poll_seconds
            self.refresh()
            self.draw(partial=True)
            self._last_etag = self._peek_etag()

    def close(self) -> None:
        self.touch.close()
        self.display.close()


def main() -> int:
    config = Config.from_env()
    if not config.token:
        print("MINDWTR_TOKEN is not set — copy .env.example to .env", file=sys.stderr)
        return 1
    if not config.passcode:
        print("PANEL_PASSCODE is not set: the screen lock hides the list, "
              "but a tap unlocks it", file=sys.stderr)
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
