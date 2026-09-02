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
refresh is ~1.5s and third-party code cannot rely on partial refresh, so the
panel redraws only when content changed. No animation, no cursor, no scrolling.

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

**Taps are optimistic.** Redraw immediately from local state, then POST, then
reconcile on the next poll. A tap that waits for the network feels broken at
these refresh rates.

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
