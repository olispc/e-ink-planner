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
greyscale-then-threshold — no antialiasing, because the panel has none. (The
one exception is a lock-screen background photo, which is greyscale by nature
and so is dithered, never thresholded; text is still drawn 1-bit on top.) A full
refresh measured 1.73s with the fast waveform, 3.84s normal (2026-10-01). The vendored driver also does flicker-free partial refresh
(confirmed against Waveshare's epd7in5_V2 source and its own demo — see
`panel/display.py`), but repeated partial updates build up visible ghosting.
So every update is partial — taps, their reconciles, background changes, poll
ticks — and the full refresh (the black/white flash) is reserved for startup,
the rotate button (everything moves, so a partial would ghost badly), engaging
the screen lock (a ghost of the list is exactly what it must not leave, and
its photo needs the thorough waveform — see screen lock below) and one
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

**One fetch, views split locally.** `client.fetch()` asks for every open task
(no status filter — the list route then returns all but done/archived) plus
areas and projects, and `layout.select_view()` decides what each tab shows. So
switching tab never waits on the network, the inbox count is free, and a task
processed out of the inbox simply turns up in Focus with no extra request. The
list signature covers only the visible slice, so a change to a Someday task
doesn't redraw the Focus list.

**Emphasis is never a fill.** The checkbox is the tap target for *completing* a
task, so a filled box reads as done. Focused rows get a heavier border instead.

**Glyphs land on whole pixels.** PIL rasterises the two flanks of a 1-bit line
differently and rounds fractional ellipse edges unevenly, which at icon size
shows as a pixel out of true. Circles use whole-pixel centres and radii (the
Focus rings); a symmetric polygon goes through `_stamp_symmetric()`, which
mirrors its left half (the star). The unstarred star is a 1px outline — light
for off — because a 2px one filled the shallow notch between its legs. A font
glyph used as an icon (the contexts @) is placed by its measured 1-bit ink
(`_glyph_ink_centre()`), not its drawing origin, and sized so that ink matches
the drawn icons' (17×17).

**One tap-zone boundary, reused everywhere.** `theme.TEXT_X` splits every row
in the app: x < TEXT_X is always the checkbox column (complete a task, or
toggle a checklist item), x >= TEXT_X is always the
text (open a task's detail view, or unfold a checklist item too long for
one line). Rows with nothing to complete — an inbox task's processing rows, the
detail view's info block and a picker's choices — are a single target across
their whole width instead,
since there is no second action to keep apart. Tapping a task's title opens
`render_detail()` — full title, meta, the full description (not a snippet —
see below), and its checklist if it has one, unchecked items sorted to the
top. Checklist items split at `TEXT_X` too: only the checkbox column ticks,
so the text is free for unfolding. An item that doesn't fit one line ends
in a small ▼; tapping its text wraps it in full (▲ folds it back), the row
growing by `DETAIL_ITEM_LINE_H` per line. `Panel.expanded_items` holds which
are open and resets on "open". Rows therefore pack into pages by height
(`_pack_pages()`), and the `expand_item` hitbox carries the page the item
lands on once flipped, so unfolding never pushes it off the page you're on.
A task row (list or detail title) adds a third, physically separate column
at the far right — `theme.STAR_ZONE_W` — for starring: a third gesture on
top of the existing two would need tap-vs-long-press disambiguation, exactly
the kind of fragile distinction this app has avoided everywhere else.

**Done checklist items stack, they don't just scroll off — same pattern
covers a long list view.** Eleven finished subtasks each taking a row is
worse than one "N/M done — tap to show" row with a disclosure triangle — it
doubles as the checklist's progress count; expanding swaps it for a
"N/M done — tap to hide" row (re-stacks) plus the items themselves. With
nothing done there's no such row, and no count: every item is on show.
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
Page turns are partial refreshes like every other update. On a task's
detail view a right swipe from its first page (or with no pager) is "back"
instead of wrapping to the last page — it borrows the header's `back` box
the same way, so the touch lock gates it like the tap. A swipe that *starts* on the
header bar (`Gesture.y` is the stroke's start; above `header_h`, both portrait
rows included) switches view tabs instead — left for the next (Focus → Inbox →
… → Done), right for the previous, wrapping like the pagers — by borrowing the
target tab's own `view` hitbox, so it does what tapping that tab does, from
any screen.

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
`.env` is where it starts — 270 (portrait) when unset, since portrait is how the
panel is mounted and used; the header's rotate button toggles landscape ↔
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
changes nothing about it. Portrait's header is two rows — tabs/status/date, then
the same nine controls spread in equal ~51px slots — because the landscape row
is built for 800px; it shows ~10 list rows (6 landscape) and ~11 checklist
items. Narrower text (~300px of title) means more ellipsis; wrapping long titles
to two lines is the obvious next step if that bites on real tasks.
TODO(oli): confirm on the panel that 270 (UI turned clockwise) matches how it's mounted.

**The detail view spends nothing on a footer.** It is a bare rule, like the
list's collapsed one, and checklist progress ("N/M done") is the label of the
stacked done row instead. Its pager is the list's thin `PAGER_H` strip below the
rows rather than a whole row's height, so paging costs less than one item. Rows
stay `DETAIL_ROW_H` (46px, the ~9mm tap floor) — the space came from around the
rows, not from shrinking them: six checklist items fit per page (four before),
and a six-item list needs no pager at all.

**The header holds real controls, not just a title — and it is the same bar
on every screen.** `_draw_header()` draws it for the list, the detail view and
the pickers, so the locks and dark mode are reachable without leaving a task
and the bars can't drift apart. The view toggles still apply to the list you
return to.

It starts with five **view tabs** where a title used to be — Focus, Inbox,
Waiting, Someday, Done (`layout.VIEW_TABS`), in Mindwtr's own sidebar order and
drawn as its icons (Lucide `Target`, `Inbox`, `PauseCircle`, `Clock3`, `Check`,
read off `Layout.tsx` and `task-status-icons.ts`). Next has no tab: Focus
already ends in every next action. In landscape the cluster starts at
`CLUSTER_X`, far enough right for the five tabs to get ~46px each; portrait's
get ~48 (above the 46px tap floor). The tabs are bare icons with only the active one boxed
(heavy border), so they don't read as more toggles; in landscape, where they
share the cluster's row, a hairline separates the two. The inbox tab carries a
count badge, shifted left as far as a wide count needs to stay inside its own
tab's slot. The active tab is "home" in the list (list page back to 0 — the
toggles below are deliberately left alone, since a stray tap shouldn't undo a
view you set up on purpose) and "back" in the detail view; in a picker it is a
plain link to its list, because the picker's own title row is its "back". Any
other tab goes to that list from wherever you are, on page 1. `PANEL_VIEW`
picks the starting tab; one without a tab falls back to Focus, on stderr.

Nine controls live in a fixed-position cluster at header-center,
anchored there rather than relative to the (variable-width) tabs or date
text so neither can ever collide with it:
- **focus-sticky** (star) — whether starred-today tasks pin to the top ahead
  of sort order. On by default; it's its own toggle because starring is a
  stronger signal than creation date, but sometimes you want a pure
  chronological view instead.
- **sort direction** (single arrow, up=newest/down=oldest by `created_at`) —
  one button, not two: with focus-sticky handling "important first"
  separately, there's no third state a bare sort control needs to reach, so
  a tap just flips it. Newest-first by default, and the one view toggle
  that's saved (`local/state.json`, merged alongside rotation by
  `config.save_state()`), so a deploy or crash doesn't flip the list back.
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
- **rotate** (circular arrow, heavy border in landscape — away from the portrait
  default) — toggles landscape ↔ portrait. Not like the other
  toggles: it is saved across restarts, does a full refresh, and is gated by
  the touch lock like everything else. See Portrait above.
- **touch lock** (pointing hand, struck through when on) — while on, its own
  button is the only live tap target and swipes are dropped; the single gate is
  the top of `Panel.handle_tap`, keyed on `box.kind`, so a new control can't
  forget to check — "back" included, so a panel touch-locked on a task's detail
  view stays there until unlocked. Background refreshes still draw while it's
  on. In-memory like the other toggles: a restart unlocks and returns to light.
  For wiping the glass, not privacy — the tasks stay on show.
- **screen lock** (closed padlock) — the privacy one: replaces the whole screen
  with `panel/lockscreen.py`'s clock (time, date, no hint text, and a
  background picture from `PANEL_LOCK_IMAGES` if there is one — one per day,
  cropped to fill and dithered; text straight on the picture, nothing behind
  it, and white keys. In portrait, the main orientation, the
  clock sits at the top so the picture shows below it, so pictures meant for
  the wall want to be vertical, ~3:5). A tap brings up a 3x4 keypad with
  one dot per digit of `PANEL_PASSCODE`; the code is checked as soon as it's
  the right length (no enter key), and after `LOCK_FREE_TRIES` misses each
  further one starts a doubling cooldown. The keypad folds back to the clock
  after `LOCK_KEYPAD_TIMEOUT` idle. Gated first in `handle_tap`, before the
  touch lock: only `lock_wake`/`lock_key` boxes do anything. Its signature
  carries no task data, only the minute and what the keypad shows, so the
  clock ticks once a minute via `Panel.tick()` and a background change draws
  nothing. While locked, the display
  is in thorough mode (`Display.set_thorough`): *every* full refresh — a tap's,
  the idle cleanup's, the partial-burst safety net's — takes the normal
  waveform (~3.9s), because on the panel the fast one left the dithered photo
  pale with earlier frames showing through, and partials drawn over that came
  out as stencilled half-dark digits. The photo is always painted by one: on
  locking, starting up locked, and folding back to the clock. Waking to the
  keypad and unlocking stay partial (white keys cover the photo; partial back
  to the list was fine on the panel), as do typed digits and minute ticks. The panel always *starts* locked (`Config.start_locked`, set by
  `from_env`), so no reboot, pulled plug or lost state file is a way past it —
  deliberately not saved state, which a restart while unlocked would bypass. No passcode set
  means a tap unlocks — it still hides the list, said on stderr at startup.
  A swipe right on the clock face hides the time and date
  (`Panel.lock_clock_hidden`): just the picture, or blank without one, and a
  signature with no minute in it, so the panel holds one image indefinitely
  while you're away; a swipe left brings the clock back. Both are thorough full
  refreshes (digits off a photo by partial is the stencilled-ghost case above).
  Swipes are ignored on the keypad; a tap still wakes it, and it folds back to
  whichever face it came from. Locking again always starts with the clock.
  Lock-screen swipes are handled before the touch lock's swipe gate, like taps.

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

**Processing the inbox is a form on the detail view, not a wizard.** An inbox
task's detail view (`_draw_processing()`, in place of its checklist) has three
rows — project or area, contexts, due date — each opening a picker
(`panel/pickers.py`), and a Next / Waiting / Someday row; done is the checkbox,
as everywhere. Nothing is created from the panel: typing on a wall is miserable,
so every picker chooses among what Mindwtr already has (contexts come from
those in use on open tasks — the cloud API has no list of them). Writes match
what Mindwtr's own processor writes (`process-inbox-workflow.ts`,
`date-draft.ts`): choosing a project sends `areaId: null` because a project
brings its area, an area alone sends `projectId: null`, and a due date is a
bare `YYYY-MM-DD` (`null` clears it). Each choice is written the moment it's
made — no staging, because a wall panel is easy to walk away from halfway
through. Single choices (where, due) return to the task; contexts stay open,
being several. Sending a task on, or ticking it done, opens the next inbox task
in the list's own order (`_next_inbox()`, wrapping to the top so skipped tasks
come round again), or returns to the inbox when none is left, so the inbox is
worked top to bottom without a tap per item. The status row ends in a square
trash button (inbox processing only, not the Edit screen): its first tap arms
it, turning the row into "Delete — tap again" and Cancel, and only a second tap
on it deletes — any other tap or opening another task disarms
(`Panel.delete_armed`), so brushing the wall can't throw a task away. Delete is
`DELETE /v1/tasks/:id`, soft: the task lands in Mindwtr's Trash, restorable from
the app. It moves on to the next inbox task like a status does
(`_move_on_from()`). There was no room for a separate row in landscape, which is
why it shares the status row. A swipe left on an inbox task
skips it — the next one opens and nothing is written; its processing view has
no pager for that swipe to have meant instead. A swipe right
leaves a picker like any detail view, except the date picker, whose swipes turn
the month (its pager); its title row is the way back. A checklist on an inbox
task isn't shown while it is being processed.

**The detail view's title is a block of its own.** Its checkbox and star are
centred on the title text (cap top of the first line to baseline of the last,
as the 1-bit render actually inks them — `_glyph_ink_box()`), however many lines
it wraps to, and a thin rule closes it off with `DETAIL_TITLE_PAD` blank rows
both above the block (under the header's rule) and below it; descenders hang
into the lower pad, as they would in print. The star's tap column runs the
title's full height.

**Any task can be edited from its detail view.** Outside the inbox, under the
title's rule sits the Edit screen's rows in miniature (`DETAIL_INFO_LINE_H`,
about half a row): where it's filed, its contexts, its due date, each behind the
same icon centred under the title's checkbox (`FIELD_ICON_CX`, which the
checklist's boxes centre on too, their text also starting at `TEXT_X`) with its
text in line with the title
(`_field_rows()` / `_draw_field_icon()`, shared with the processing rows) —
but only the ones that are set. No placeholders on the detail view, and an unset
field costs no height, which matters most in landscape where every pixel is a
checklist row. The task's age rides the right edge of the first line, so a task
with nothing set still has a line. The block ends in a ▸ and is
one tap target (a row at that height is too small alone; the block is kept at
least 44px) that opens the task's **Edit** screen — picker kind `"edit"`,
`_draw_processing()`'s full-size rows under a picker title row, with their
placeholders (`FIELD_PLACEHOLDERS`) since they're tap targets there, plus the
status row with the current status boxed heavily. `Panel.editing` makes a picker
opened from Edit return to Edit (on a choice, "back" or a swipe right) rather
than to the task; one more "back" leaves Edit. A status chosen from Edit is just
a change and returns to the task — only an inbox task's moves on.

**Done is where an accidental tick is put back.** `fetch()` also asks for
`status=done&all=1` (without `all=1` the list route drops done tasks whatever
the status asks) — a short list by nature, since Mindwtr moves done tasks to
archived after `autoArchiveDays`. The Done tab lists them newest completion
first, ignoring the sort and group toggles, each row ticked (title left plain:
struck text is hard reading on e-ink), "done Nm ago" leading its meta line, and
no star. Its checkbox is "restore": the tick lifts for a beat, then the row
leaves Done. A done task's detail view restores from its checkbox the same way.
Completing a task now marks it done locally (starless, as the server does)
rather than dropping it, so it is in Done at once. Restoring follows
Mindwtr's own undo (`undo-task-completion.ts`): the previous status, the star
back if it had one (the server refuses one over the focus limit — logged,
fine), and for a recurring task, deleting the occurrence completing generated
(same series id, `createdAt` equal to the done task's `completedAt`; the delete
is soft). The status write goes first, so a failure changes nothing; the
occurrence's delete comes after. Neither the status nor the star before
completion is stored on the task — Mindwtr's own undo only knows them from its
toast — so `Panel.completions` remembers them for each task completed on the
panel, saved in `local/state.json` (the latest `COMPLETIONS_KEPT`). A task
completed elsewhere restores to next, unstarred.
TODO(oli): confirm on a real recurring task that restoring removes the
occurrence it generated — source says the cloud stamps it the same way.

**Every edit is one `PATCH` through one path.** `client.apply_fields()` is the
single mapping from API field names onto `Task`; `Panel._patch_task()` uses it
for the optimistic redraw (star, checklist and every processing choice alike),
and the test stub and the web tester's fixture client store writes through the
same function, so they can't disagree with the panel about what a write does.
A failed write restores the task *and* the screen it was on.

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
`PANEL_CHECK_SECONDS` and only does the expensive 4-request `fetch()` when it
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
`createdAt`. It returns naive *local* time: zoned timestamps (createdAt is
UTC) are converted, because day arithmetic against `datetime.now()` in UTC
put anything added in the first hour after midnight (BST) on the day before.
The Pi's clock is Europe/London.

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
