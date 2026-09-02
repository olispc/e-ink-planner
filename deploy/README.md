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

Enable SPI and I2C with `raspi-config`, then compile the touch overlay as
described in `overlays/gt911-epaper.dts`.

Finally `cp .env.example ../.env`, set `PANEL_DISPLAY=epaper` and
`PANEL_TOUCH=evdev`, and install `panel.service`.

## Wiring

Depends on which adapter turns out to be in the box. With the **Waveshare
e-Paper Driver HAT** the display half is just the header — but set its Display
Config resistor to **0.47R, position B** for a 7.5in panel, and check whether it
leaves GPIO2/3 reachable or needs a stacking header for the touch lines.

With Good Display's **STM32-T01** it is jumper wires from its header, using the
pinout PDF from good-display.com. The "STM32" names the intended host, not a
requirement: the signals are SPI, I2C and 3.3V, which is what the Pi speaks.

Touch is six wires either way — 3V3, GND, SDA, SCL, INT (GPIO4), RST (GPIO27).
