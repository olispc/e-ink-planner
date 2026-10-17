# E-ink planner

A 7.5in e-paper wall panel showing Mindwtr next actions, tappable to complete
them. A Raspberry Pi runs both `mindwtr-cloud` (the sync server the Mac and
phone sync to) and the renderer that drives the panel.

## Commands

```bash
python3 tools/preview.py --scale 2    # render the layout to a PNG, no hardware
python3 tools/preview.py --live       # ...against a real mindwtr-cloud
python3 tools/preview.py --portrait   # the 480x800 layout (tools/webtest.py takes it too)
python3 -m panel.app                  # the real loop
```

## Non-negotiables

**Design for 1-bit and for stillness.** Rendering happens in PIL mode `"1"`, not
greyscale-then-threshold — no antialiasing, because the panel has none. A full
refresh measured 1.73s with the fast waveform, 3.84s normal (2026-10-01). The vendored driver also does flicker-free partial refresh
(confirmed against Waveshare's epd7in5_V2 source and its own demo — see
`panel/display.py`), but repeated partial updates build up visible ghosting.
So every update is partial — taps, their reconciles, background changes, poll
ticks — and the full refresh (the black/white flash) is reserved for startup,
the rotate button (everything moves, so a partial would ghost badly) and one
scheduled cleanup: `EPaperDisplay.cleanup_due()`, which fires only
once enough partials have accumulated *and* the panel has been idle for a
while, so it never lands under a finger or right after a lone background
change. `FULL_REFRESH_EVERY` is only a safety net for a long unbroken burst. No continuous animation, no cursor, no
scrolling — a confirmation on completing a task is two discrete static frames
(checkmark + strikethrough, held, then removed), not motion.

**The typeface is bundled, not system-supplied.** `panel/fonts/` carries
Zalando Sans (OFL-1.1, license in that same directory) at its SemiExpanded
width — the default width read a little condensed at panel sizes, checked
by rendering actual panel content at real sizes before deciding, not by
eyeballing a specimen page. `theme.py`'s font-candidate lists put it first
on both platforms, so a Mac dev preview renders identically to the Pi
instead of each OS falling back to whatever it ships (DejaVu/Helvetica/
Arial stay as a safety net after it).

**Never start the panel on wiring that doesn't read right.** `epd.init()`
runs the controller's boost converter (±20V on the drive lines), which a
shifted or mirrored 24-pin ribbon delivers to 3.3V pins. `require_idle_busy()`
resets the panel and refuses to start unless BUSY reads idle-high — keep it
ahead of any new init path.

**Never blank on failure.** The image persists with the power off, so a dead
network must show the last good list *labelled offline*, not an empty screen or
stale data with no explanation. `Panel.stale_since` is what does this.

**The default view is compound, not a filter.** `focus` means starred-today
first, then next actions filling the remaining rows, with a heavy rule between.
It exists because a `next`-only panel showed 6 of 56 tasks, and because a panel
that goes blank whenever nothing is starred would not survive contact with a
normal week.

**Emphasis is never a fill.** The checkbox is the tap target for *completing* a
task, so a filled box reads as done. Focused rows get a heavier border instead.

**One tap-zone boundary, reused everywhere.** `theme.TEXT_X` splits every row
in the app: x < TEXT_X is always the checkbox column (complete a task, toggle
a checklist item, or — in the header — go back), x >= TEXT_X is always the
text (open a task's detail view, or nothing further for a leaf checklist
item, which has no next level to open). Tapping a task's title opens
`render_detail()` — full title, meta, the full description (not a snippet —
see below), and its checklist if it has one, unchecked items sorted to the
top. Checklist items get a full-width tap zone (not split) since a leaf
item has only one action.
A task row (list or detail title) adds a third, physically separate column
at the far right — `theme.STAR_ZONE_W` — for starring: a third gesture on
top of the existing two would need tap-vs-long-press disambiguation, exactly
the kind of fragile distinction this app has avoided everywhere else.

**Done checklist items stack, they don't just scroll off — same pattern
covers a long list view.** Eleven finished subtasks each taking a row is
worse than one "N done — tap to show" row with a disclosure triangle;
expanding swaps it for a "hide" row (re-stacks) plus the items themselves.
Whatever still doesn't fit `page`'s worth of rows gets a pager on the last
row instead of being silently dropped: the row splits left/right (`‹` / `›`)
into independent previous/next zones around a centered "page X/Y" label,
each wrapping around at its end rather than exposing a dead stop. `render()`'s
list view reuses the identical mechanism once `total > ROWS`; the one
difference is the boundary rule (focused vs. backlog) looks at the *global*
task list across the page split, not just what's visible on the current
page, so it still lands correctly when the split falls right at a page edge.

**Swiping is a second way to hit the pager, not a new paging system.** A
horizontal drag anywhere (left = next page, right = previous, Kindle-style)
borrows the current screen's own `list_page`/`page` hitboxes via
`Panel.handle_swipe()`, so wrap-around and page numbers stay in one place and
a screen with no pager ignores it. `touch.classify()` decides tap vs swipe
from the stroke's start and end: under `TAP_SLOP` is a tap (at the landing
point), over `SWIPE_MIN_DX` and mostly sideways is a swipe, anything else is
dropped — so a vertical drag no longer fires a tap where the finger lifted.
Page turns are partial refreshes like every other update.

**The detail view's description gets real space, not one truncated line —
but never at the checklist's expense.** `_wrap_paragraphs()` wraps the whole
description (preserving the author's own blank-line paragraph breaks,
unlike `_wrap()`'s single-run word-wrap for the title). When a task has no
checklist, description gets everything down to the footer and paginates —
same bidirectional "page X/Y" control as the checklist, factored into
`_draw_detail_pager()` and shared by both — if it's still too long for one
page. When a checklist is also present, description is capped to a fixed
`DETAIL_DESC_MIN_LINES_WITH_CHECKLIST` lines with a trailing "…" instead of
its own pager: only one pager fits on screen, and a task with both a long
description and a long checklist needs a guaranteed minimum of *each*, not
one paginated in full while the other gets crowded to zero.

**Portrait is a second canvas, not a second layout.** Rotation (0/90/180/270)
says how far the panel is turned clockwise as mounted. `PANEL_ROTATION` in
`.env` is where it starts; the header's rotate button toggles landscape ↔
portrait (UI turned 90° clockwise = rotation 270, `rotation.PORTRAIT_TURN`; 90 and
180 stay `.env`-only) and saves the result to `local/state.json` (`PANEL_STATE_PATH`), which wins
over `.env` from then on — a restart (crash, deploy) must not turn a mounted
panel sideways. `Config.from_env` says so on stderr when the saved value
overrides an explicit `PANEL_ROTATION`; delete the file to hand control back to
`.env`. Rotating is a full refresh and resets list/checklist page to 0 (a page
holds a different number of rows on the other canvas). The tester and the unit
tests run with `state_path=None` and never write it. The UI is always drawn upright on a *logical* canvas —
800x480, or 480x800 for 90/270 — via `theme.Geometry` (`LANDSCAPE`/`PORTRAIT`),
which `render()`/`render_detail()` read their width, header height and text
edge from instead of module constants. Only the two edges know about rotation,
and both live in `panel/rotation.py` so they cannot disagree: `RotatedDisplay`
turns the finished image onto the native frame (e-paper only — the png backend
stays upright), and `EvdevTouch` turns raw touch points back before classifying,
so a swipe is judged in the frame the user sees. Hitboxes, `Panel` and every
tap rule stay in logical pixels. `tests/test_rotation.py` round-trips a marked
pixel through both transforms for every rotation. Landscape output is
pixel-identical to before the refactor (checked by diffing renders), so portrait
changes nothing about it. Portrait's header is two rows — title/status/date, then
the same eight controls spread in equal ~55px slots — because the landscape row
is built for 800px; it shows ~10 list rows (6 landscape) and ~11 checklist
items. Narrower text (~300px of title) means more ellipsis; wrapping long titles
to two lines is the obvious next step if that bites on real tasks.
TODO(oli): confirm on the panel that 270 (UI turned clockwise) matches how it's mounted.

**The detail view spends nothing on a footer.** It is a bare rule, like the
list's collapsed one, and checklist progress ("N of M done") sits right-aligned
on the meta line instead. Its pager is the list's thin `PAGER_H` strip below the
rows rather than a whole row's height, so paging costs less than one item. Rows
stay `DETAIL_ROW_H` (46px, the ~9mm tap floor) — the space came from around the
rows, not from shrinking them: six checklist items fit per page (four before),
and a six-item list needs no pager at all.

**The header holds real controls, not just a title — and it is the same bar
on the list and the detail view.** `_draw_header()` draws both (title tap is
"home" in the list, "back" in detail; everything right of it is identical), so
lock and dark mode are reachable without leaving a task and the two bars can't
drift apart. The view toggles still apply to the list you return to.

In the list, tapping the view title
is "home" (list page back to 0 — the toggles below are deliberately left
alone, since a stray tap on the title shouldn't undo a view you set up on
purpose). Eight controls live in a fixed-position cluster at header-center,
anchored there rather than relative to the (variable-width) title or date
text so neither can ever collide with it:
- **focus-sticky** (star) — whether starred-today tasks pin to the top ahead
  of sort order. On by default; it's its own toggle because starring is a
  stronger signal than creation date, but sometimes you want a pure
  chronological view instead.
- **sort direction** (single arrow, up=newest/down=oldest by `created_at`) —
  one button, not two: with focus-sticky handling "important first"
  separately, there's no third state a bare sort control needs to reach, so
  a tap just flips it.
- **area / project grouping** (two small bordered boxes, heavier border when
  on) — combine rather than nest: when both are on, `_organize_tasks()`
  emits one "Area / Project" header line instead of two levels, since a page
  rarely holds more than one project under the same area anyway. The area
  icon is a custom stacked-layers glyph (Mindwtr areas carry a user-chosen
  emoji, not a fixed icon, so there's nothing to "match"); the project icon
  is a folder, matching Lucide's `Folder` used by Mindwtr's own desktop app.
- **due-date-first** (calendar icon) — pulls tasks with a `dueDate` to the
  top of whatever list or group they're in, soonest due first, ahead of the
  usual sort direction. Scoped to "the non-starred list" by construction:
  `_sort_backlog()` is what due-first changes, and the focus-sticky leading
  section always sorts through plain `_sort_by_date` instead, so a starred
  task never gets bumped by an unstarred one with a nearer due date.
- **dark mode** (half-filled circle) — the finished light render inverted
  (`_finish()` in layout.py), not a second palette threaded through every
  draw call, so layout, hitboxes and glyphs cannot drift between the two
  modes. Inversion goes via mode `"L"` because `WHITE` is 1 here and PIL's own
  invert on mode `"1"` leaves stray bits. Applies to the detail view too, and
  is a partial refresh like every other update — a whole-screen polarity flip
  is the case most likely to ghost, so if it does on the real panel, make this
  one toggle take the full-refresh path rather than loosening the rule above.
- **screen lock** (padlock, open/closed glyph) — while locked, the lock button
  is the only live tap target and swipes are dropped; the single gate is the
  top of `Panel.handle_tap`, keyed on `box.kind`, so a new control can't
  forget to check — "back" included, so a panel locked on a task's detail view
  stays there until unlocked. Background refreshes still draw while locked.
  In-memory like the other toggles: a restart unlocks and returns to light.
- **rotate** (circular arrow, heavy border whenever the panel is turned from
  native landscape) — toggles landscape ↔ portrait. Not like the other
  toggles: it is saved across restarts, does a full refresh, and is gated by
  the lock like everything else. See Portrait above.

**Focus-sticky always wins when it's on** — starred-today tasks form their
own flat leading section, sorted by `sort_newest` but never bucketed by
area/project even when grouping is also on: starring is a stronger signal
than either. Sort direction and grouping apply only to the backlog beneath
the heavy rule. A page with nothing starred today has no leading section and
no rule, same as with focus-sticky off. `_organize_tasks()` (layout.py) turns
the flat fetched list into a stream of task/header entries per these
toggles, tagging each task `in_group` so its row knows whether a header
above it already states its project/area (the leading section never is,
even when the backlog under it is grouped — those rows keep their own
project/area visible). `_paginate()` bin-packs that stream by real height (a
group header is half a task row) rather than a fixed row count, so paging
still works when rows aren't all the same size.

**Group order is frozen between deliberate re-sorts.** A group's position
comes from an aggregate of its members' dates, so completing or starring its
oldest task would reshuffle the groups mid-session. `Panel._group_order`
snapshots it (`compute_group_order()`) on the first grouped draw and keeps
it until a header toggle or the home tap drops it; groups that appear in
between go after the known ones. Tasks within a group need no freezing —
removing one never reorders the rest.

**Header status text is not a control.** "upd HH:MM" sits next to the
date on the right (just "Sat 17": both are short on purpose, header width being
the scarce thing) — deliberately smaller (footer font vs. the controls' 20px
icons) and grouped with the read-only date instead of the tappable cluster, so
it reads as status, not another toggle. No sync glyph and no task count: both
were spending header width on things the list itself already shows.

**Checklist writes are whole-array, not per-item.** Mindwtr's schema (verified
against `packages/core/src/task-sync-schema.ts`) has no per-item toggle route
— `PATCH /v1/tasks/:id` with `checklist` replaces the entire array. Toggling
one item means read-modify-write the array `panel/client.py` already holds.

**Taps are optimistic.** Redraw immediately from local state, then POST, then
reconcile. A tap that waits for the network feels broken at these refresh
rates.

**Live updates ride a cheap ETag check, not just the slow poll.** A
completion on the phone should show up on the wall in seconds, not
`PANEL_POLL_SECONDS`. `HEAD /v1/data`'s ETag is built from the store file's
stat (verified against `server-data-cache.ts`) — one filesystem `stat()`,
changes on any write from any device. `Panel.run()` checks it every
`PANEL_CHECK_SECONDS` and only does the expensive 3-request `fetch()` when it
actually moves; `PANEL_POLL_SECONDS` stays as a dumb fallback regardless.
mindwtr-cloud has no push (no SSE/WebSocket) — this is the closest thing to
it over plain REST.

**Both backends stay runnable on a laptop.** `PANEL_DISPLAY=png` and
`PANEL_TOUCH=none` are not a testing afterthought — they are how layout work
gets done. Don't add Pi-only imports at module scope.

**API shapes come from upstream source, not guesses.** `panel/client.py`
documents which routes and query params were read off
`apps/cloud/src/server.ts`. If something looks wrong, re-read it there rather
than probing blind — and remember a task inside a project reports no `areaId`
of its own. `dueDate` (confirmed against `task-sync-schema.ts` and live data)
is an optional string that's sometimes a bare `YYYY-MM-DD` and sometimes a
full ISO timestamp — `_parse_iso()` already handles both, same as
`createdAt`.

**A task's meta line carries more than project/area/contexts — on the list
view too, not just detail.** `_append_age_due()` (layout.py) appends a
relative "added N ago" (from `created_at`) and, if the task has one, "due in
N" / "overdue N" (from `due_date`) to that same row rather than spending a
new one — shared by both `render()`'s row loop and `render_detail()`, so a
task reads the same age/due info wherever it appears. `_relative_age()` /
`_format_due()` both work on calendar-day difference so "added yesterday"
doesn't depend on time-of-day.

## Where the detail lives

- `deploy/README.md` — provisioning the Pi, sync server, overlay, wiring
- `deploy/overlays/` — why touch needs a device-tree overlay, one per touch chip
- `panel/theme.py` — every layout number, and the physical reason for it
