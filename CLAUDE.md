# E-ink planner

A 7.5in e-paper wall panel showing Mindwtr next actions, tappable to complete
them. A Raspberry Pi runs both `mindwtr-cloud` (the sync server the Mac and
phone sync to) and the renderer that drives the panel.

## Commands

```bash
python3 tools/preview.py --scale 2    # render the layout to a PNG, no hardware
python3 tools/preview.py --live       # ...against a real mindwtr-cloud
python3 -m panel.app                  # the real loop
```

## Non-negotiables

**Design for 1-bit and for stillness.** Rendering happens in PIL mode `"1"`, not
greyscale-then-threshold — no antialiasing, because the panel has none. A full
refresh is ~1.5s. The vendored driver also does flicker-free partial refresh
(confirmed against Waveshare's epd7in5_V2 source and its own demo — see
`panel/display.py`), but repeated partial updates build up visible ghosting, so
it's used deliberately, not by default: a poll tick always does a full refresh
(not latency-sensitive, content can change anywhere), a tap goes through
partial refresh (latency-sensitive — that's a finger waiting), capped by
`EPaperDisplay.FULL_REFRESH_EVERY`. No continuous animation, no cursor, no
scrolling — a confirmation on completing a task is two discrete static frames
(checkmark + strikethrough, held, then removed), not motion.

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
`render_detail()` — full title, meta, a description snippet, and its
checklist if it has one, unchecked items sorted to the top. Checklist items
get a full-width tap zone (not split) since a leaf item has only one action.
A task row (list or detail title) adds a third, physically separate column
at the far right — `theme.STAR_ZONE_W` — for starring: a third gesture on
top of the existing two would need tap-vs-long-press disambiguation, exactly
the kind of fragile distinction this app has avoided everywhere else.

**Done checklist items stack, they don't just scroll off — same pattern
covers a long list view.** Eleven finished subtasks each taking a row is
worse than one "N done — tap to show" row with a disclosure triangle;
expanding swaps it for a "hide" row (re-stacks) plus the items themselves.
Whatever still doesn't fit `page`'s worth of rows gets a "page X/Y" control
on the last row instead of being silently dropped — wraps around rather than
exposing a dead end past the last page. `render()`'s list view reuses the
identical mechanism once `total > ROWS`; the one difference is the boundary
rule (focused vs. backlog) looks at the *global* task list across the page
split, not just what's visible on the current page, so it still lands
correctly when the split falls right at a page edge.

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
of its own.

## Where the detail lives

- `deploy/README.md` — provisioning the Pi, sync server, overlay, wiring
- `deploy/overlays/gt911-epaper.dts` — why touch needs a device-tree overlay
- `panel/theme.py` — every layout number, and the physical reason for it
