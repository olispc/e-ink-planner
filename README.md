# E-ink planner

A 7.5in e-paper panel on the workspace wall showing live Mindwtr next actions,
tappable to tick them off.

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
python3 tools/preview.py --live --view focus
```

`--live` needs `MINDWTR_URL` and `MINDWTR_TOKEN` in `.env`; without it, it
renders the fixture in `tests/fixtures/`.

## Running it for real

See [deploy/README.md](deploy/README.md) — Pi provisioning, the sync server, the
device-tree overlay for touch, and wiring.

```bash
python3 -m panel.app
```

## Configuration

Everything is environment variables; see `.env.example`. The two that change
behaviour most are `PANEL_DISPLAY` (`png` or `epaper`) and `PANEL_TOUCH`
(`none` or `evdev`).
