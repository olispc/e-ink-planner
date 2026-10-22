# E-ink planner

A 7.5in e-paper panel on the workspace wall showing live Mindwtr next actions,
tappable to tick them off.

> **Work in progress.** This is a personal project that I'm using daily and
> changing as I go. Features will be added, layout numbers will move, and
> nothing here is stable or versioned. It is built for one specific panel and
> one specific setup (below); treat it as a reference to read and adapt rather
> than something that installs cleanly. Issues and ideas are welcome, but
> expect rough edges.

Mindwtr's desktop API is bound to loopback and only runs while the app is open,
so the panel does not talk to it. Instead a Raspberry Pi runs **mindwtr-cloud**,
the self-hosted sync server Mindwtr ships, and becomes the source of truth the
Mac and phone sync to. The panel is then an ordinary HTTP client reading
`localhost` on the same board.

```
Mac ─┐
     ├── sync /v1/data ──▶ mindwtr-cloud (Pi, :8787) ◀── /v1/tasks ── renderer ──▶ SPI ──▶ panel
phone ┘                                                             ◀── I2C ◀── GT911 touch
```

## Hardware

| | |
| --- | --- |
| Panel | Good Display **GDEY075T7-T01**, 7.5in, 800×480, UC8179, GT911 touch |
| Host | Raspberry Pi 4 (2GB), booting from a USB SSD |
| Active area | 163.2 × 97.72 mm — about 124 ppi |

Pi 4 rather than Pi 5 on purpose: the GT911 works on the 4's hardware I2C, where
the 5 has been reported to fall back to software I2C.

## Working on the layout without hardware

The whole renderer runs on a laptop. This is the point of the `png` display
backend, and it is how the design was done before the panel shipped.

```bash
pip3 install -r requirements.txt
python3 tools/preview.py --scale 2        # writes local/preview-2x.png
python3 tools/preview.py --live --view next
```

`--live` needs `MINDWTR_URL` and `MINDWTR_TOKEN` in `.env`; without it, it
renders the fixture in `tests/fixtures/`.

## Running it for real

See [deploy/README.md](deploy/README.md) — Pi provisioning, the sync server, the
device-tree overlay for touch, and wiring.

```bash
python3 -m panel.app
```

## What it shows

`PANEL_VIEW=focus` is the default and it is a compound view: the tasks starred
for today first, a heavy rule, then next actions filling whatever rows are left.
Six rows fit. A `next`-only panel would be showing about a tenth of the backlog
in arbitrary order; leading with the deliberate choice and letting the rest fill
in is the difference between a planner and a random sample.

Nothing starred is not a failure state — the panel is then simply next actions,
so the focus flag earns its place without being load-bearing.

The header's first four icons are view tabs, in Mindwtr's own sidebar order and
with its icons: Focus, Inbox (with a count), Waiting and Someday. `PANEL_VIEW`
picks the one it starts on. The panel fetches every open task in one request and
splits it locally, so switching tab never waits on the network.

**Processing the inbox.** Tapping an inbox task opens it with three rows (project
or area, contexts, due date), each of which opens a picker of what Mindwtr
already has, and a Next / Waiting / Someday row to send it on. The panel does not
create areas, projects or contexts: it is a bad place to type. Each choice is
written as soon as it is made. Sending a task on, or ticking it done, moves
straight to the next inbox task, so the inbox can be worked top to bottom.

## Using a different display

Not tested on anything but the panel above, but the code is split so it should
be adaptable. The data flows one way: Mindwtr server → `panel/client.py` →
`panel/layout.py` (draws a PIL 1-bit image) → a `Display` backend. Only that
last step knows what hardware is attached.

| Piece | Display-specific? | On a different screen |
| --- | --- | --- |
| `client.py`, `app.py`, tap and swipe handling | No | Works unchanged |
| `layout.py` | No, but it assumes the size in `theme.py` | Works if the screen is about 800×480 |
| `touch.py` | Reads any evdev touchscreen and rescales to the layout size | Works; rotation and axis swaps aren't handled |
| `display.py` | `EPaperDisplay` is hard-wired to Waveshare's `epd7in5_V2` | Needs a new backend |

To drive another screen, write a `Display` subclass (`show(image)` is the only
required method; the partial-refresh and ghosting-cleanup methods default to
no-ops, which is right for an LCD) and add it to `open_display()`. A
framebuffer, a small SPI LCD, or a pygame window would each be about 20 lines.

What isn't done yet:

- **Resolution is hardcoded.** `theme.py` fixes 800×480, and every number in it
  is tuned to this panel's 124 ppi. A smaller screen needs `WIDTH`, `HEIGHT`,
  `ROW_H` and the related constants changed together; at 480×320 only about
  three task rows fit.
- **It's 1-bit on purpose.** There is no antialiasing and no colour, so on an
  LCD it will look like a monochrome screen.
- **It needs a Mindwtr sync server.** The panel talks to `mindwtr-cloud`'s
  `/v1/*` routes and nothing else, so it's of no use without one.

## Configuration

Everything is environment variables; see `.env.example`. The two that change
behaviour most are `PANEL_DISPLAY` (`png` or `epaper`) and `PANEL_TOUCH`
(`none` or `evdev`).

## License

MIT, see [LICENSE](LICENSE). The bundled Zalando Sans font is separate and
licensed under the OFL-1.1, see [panel/fonts/OFL.txt](panel/fonts/OFL.txt).
