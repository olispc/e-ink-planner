# Provisioning the Pi

Order matters: the server and its backups come first, because that is the only
part of this that can lose data. The panel is decoration until that is solid.

## 1. Base image

Flash **Raspberry Pi OS Lite (64-bit)** straight to the SSD with Raspberry Pi
Imager, setting hostname, wifi and your SSH key in Imager's advanced options
before writing. It then comes up headless — no monitor, no keyboard.

64-bit is not optional: mindwtr-cloud runs on Bun, which needs aarch64.

Boot with **no SD card inserted**. A Pi 4 bought recently boots from USB out of
the box. If it does not, write Imager's *Misc utility images → Bootloader → USB
Boot* to the spare microSD, boot once from that, then remove it for good.

## 2. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
tailscale ip -4        # note this; it never changes
```

Use that `100.x.y.z` address everywhere. **Not** the `.ts.net` MagicDNS name —
Mindwtr allows plain HTTP to `100.64–127.x` out of the box, but `.ts.net` is not
on its local-hostname list and would need the app's allow-insecure-HTTP toggle.
(`packages/core/src/http-utils.ts` upstream, if you want to check.)

## 3. Sync server

```bash
sudo apt install -y docker.io docker-compose-plugin
cd deploy && cp .env.example .env
openssl rand -hex 32          # paste into MINDWTR_CLOUD_AUTH_TOKENS
docker compose up -d
curl -fsS http://127.0.0.1:8787/health
```

Then point the Mac and phone at `http://100.x.y.z:8787` under
**Settings → Sync → Self-Hosted**, seeded from the existing `data.json`.

Verify the write path by hand before trusting it — the *desktop* API silently
ignores everything but `title` on POST, and this is a separate implementation:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8787/v1/tasks?status=next&limit=3" | head
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8787/v1/tasks/<id>/complete"
```

## 4. Backups — before you rely on any of it

The SSD is now the only live copy of the GTD data, and the store is a single
JSON blob with last-write-wins merging. Point `restic` or `kopia` at
`deploy/data/` on a nightly timer, off this machine.

A backup you have not restored is a guess. Test one.

## 5. Panel

```bash
sudo apt install -y python3-pil python3-evdev fonts-dejavu-core device-tree-compiler
pip3 install --break-system-packages requests
```

Waveshare's driver is not on PyPI — vendor `waveshare_epd` from
`github.com/waveshareteam/e-Paper` (`RaspberryPi_JetsonNano/python/lib/`).

Enable SPI and I2C with `raspi-config`, then compile the touch overlay for
the panel's touch chip, as described in the file itself. Panels of the same
model ship with different chips: `overlays/gt911-epaper.dts` (Goodix, answers
at 0x14) or `overlays/ft5426-epaper.dts` (FocalTech, flex labelled
SN-7F0323A, answers at 0x38 only once RST is driven high). The current panel
has the FT5426.

Finally `cp .env.example ../.env`, set `PANEL_DISPLAY=epaper` and
`PANEL_TOUCH=evdev`, and install `panel.service`.

## Wiring

The adapter that arrived is Good Display's **ESP32-FTS02** (ordered as the
STM32-T01; a different board shipped). Its P1 socket pushes straight onto the
Pi's 40-pin header — no jumper wires — with the board hanging off the Pi's
edge, Type-C/front-light end towards the Ethernet port. Spec PDF (good-display.com
is often unreachable): manuals.plus/m/9125c22507ae09f8ed4757b81d6f77e070ab247835fb5baea6a5b7c7f235597d

- **P3 switch → 0.47** (UltraChip controller; 2.2 is for SSD-series panels and
  the panel won't refresh).
- **Display ribbon → P5**, panel face up.
- **Touch → P7** (TP-FPC2, not P6). Its pins on the Pi are *not* the hardware
  I2C pair — see `overlays/gt911-epaper.dts` for the map and how it was found.
  Touch RST is GPIO26 (Pi pin 37, P7 pin 3); the FT5426 needs it.
- Front light (FL-FPC, P0, SS1, FL-SET) is unused and has no software control.

Touch needs the opposite face to the display, which is what the manual's
"7.5in must be inserted in reverse orientation" means. With the display ribbon
in P5 and the panel face up, route touch through the supplied extension
(orange tail → yellow board → white ribbon → P7) and give the long white
ribbon a gentle half-turn so it reaches P7 flipped. Both ways round make
contact at P7, so check rather than trust the face: the wrong way lands a
ground on GPIO13 and pins it low. Right way: GPIO6/13 idle high, GPIO19
(INT) driven low, and the chip returns "911" at 0x14. The FT5426 flex has
its contacts on the opposite face to the GT911 one, so it goes into the
yellow board the other way up; the rest of the extension stays as it was.
Its printed pinout (1 GND, 2 VDD, 3 RST, 4 INT, 5 SDA, 6 SCL) is P7's order.
Plugged straight into P7 it is mirrored (ground on GPIO13).

Seat every ribbon fully and square with the latch flat. A half-seated touch
ribbon reads as "nothing connected", and some boots with a badly seated
extension never came online — unplug if the Pi isn't reachable within ~2 min.

### Remote wiring (board behind the screen, Pi on the desk)

The FTS02's P1 is wired pin-for-pin like the Pi header, so a jumper bundle is
pin N → pin N. Only these are needed (checked against gpiozero's Pi 4 header
map), as physical pin numbers: 3V3 on 1 and 17; GND on 6, 20, 25; display
MOSI 19, SCLK 23, CE0 24, RST 11, DC 22, BUSY 18; touch SDA 31, SCL 33, INT 35,
RST 37. Leave 5V (pins 2, 4) empty. Verified working over 20cm male-to-female
jumpers. Pin 37 wasn't needed for the GT911 panel; the FT5426 stays in reset
without it, because the Pi's default pull-down on GPIO26 holds RST low.

Extending the panel's 24-pin ribbon works, but every joint must be right: it
carries the boost converter's drive and sense lines, not just SPI. Verified
setup (2026-10-15): a "YD" 24-pin extension board (two identical sockets, traces
straight across, fingers on the *bottom*) and an A-type FFC (contacts on the
same face at both ends). Tail into P2 and FFC into P1 contacts down (board face
up); the FFC turns over on its way to P5 and goes in contacts up (P5's fingers
are on top). Rule: contacts face the fingers at every socket, and the chain
comes out straight. Check before the panel powers up: `readtemp.py` must read
BUSY idle-high and a sensible temperature (old panel read 22°C through it). The
1/24 labels on the extension board don't decide anything.

The first attempt (2026-10-02) gave a different reading on each try
(BUSY floating, then low) and was followed by the display's power-up never
completing even when plugged straight back into P5: the controller answers
reset and accepts commands, but BUSY never releases after POWER ON. Its
temperature sensor reads back over 3-wire SPI (20°C, 2026-10-08, Good
Display's suggested test), so SPI works both ways. Measured
during a power-up attempt: PREVGH stays at 3.28V (the supply passing through),
PREVGL stays at 0V, and Q1's gate shows 0V — the boost is never being told to
switch. That points at the panel's controller (or the GDR contact, P5 pin 2),
not the FTS02's boost parts: on Good Display's schematic GDR goes straight to
Q1's gate with only R12 (1MΩ) to ground, and R12 measured 1MΩ with the panel
unplugged, so nothing on the board clamps the gate. Good Display's own Pi demo
(bcm2835_GDEY075T7, run unmodified, rebuilt with `make` for 64-bit, 2026-10-13)
hangs at POWER ON the same way. Ctrl-C on a bcm2835 program leaves CE0 in its
ALT0 mode, which breaks spidev's chip select until a reboot. A replacement GDEY075T7-T01 is on order;
TODO(oli): if it also fails on the FTS02, try it on a Waveshare e-Paper Driver
HAT to separate board from panel.

Before trying any new display wiring:
- **Disable `panel.service` first** (`sudo systemctl disable --now panel`).
  Otherwise every boot starts the display through whatever is connected.
- **Continuity-check every 24-pin joint with the power off.** "Contacts up, no
  flips" reasoning wasn't enough — these extension connectors didn't behave as
  predicted.
- Then read BUSY before driving anything: idle **high** means the panel is
  connected; floating or low means stop. The app now does this itself
  (`require_idle_busy()` in `panel/display.py`) and exits instead of starting
  the panel, so the service just retries every 15s until the wiring reads
  right. It catches a missing or badly seated ribbon, not every possible
  miswiring — the steps above still apply.
