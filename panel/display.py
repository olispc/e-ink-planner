"""Where the rendered image goes.

Two backends. `png` writes a file, which is how the layout gets designed on a
laptop with no hardware attached. `epaper` pushes to the panel over SPI. They
exist because the whole build was sequenced to make the software finishable
before the parcel arrived — not as speculative abstraction.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import Image

from .config import Config
from .rotation import to_panel_image


class Display:
    def show(self, image: Image.Image) -> None:
        raise NotImplementedError

    def show_partial(self, image: Image.Image) -> None:
        """A faster, flicker-free update — the app's default for every
        change. Backends that have no such mode (or shouldn't bother — png
        has no ghosting to avoid) just fall back to a full show()."""
        self.show(image)

    def set_thorough(self, on: bool) -> None:
        """While on, every full refresh — whoever asks for it: a tap, the idle
        cleanup, the partial-burst safety net — takes the slow, thorough
        waveform, never the fast one. For screens the fast one measurably
        fails on: the lock screen's dithered photo, where it left pale blacks
        and earlier frames showing through. Backends with one kind of full
        refresh have nothing to switch."""

    def set_rotation(self, rotation: int) -> None:
        """Turn subsequent frames. Only the e-paper wrapper has anything to
        turn — the png backends draw the upright logical image as is."""

    def cleanup_due(self) -> bool:
        """Whether enough partial-update residue has built up, and the panel
        has been quiet long enough, for a full refresh to clear it now. The
        app polls this every check tick; it's the only full refresh left
        besides startup."""
        return False

    def close(self) -> None:
        pass


class PngDisplay(Display):
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def show(self, image: Image.Image) -> None:
        image.save(self.path)
        print("wrote %s" % self.path)


class DisplayWiringError(RuntimeError):
    pass


def require_idle_busy(cfg, epd) -> None:
    """Refuse to start the panel unless BUSY reads idle-high after a reset.

    epd.init() runs the controller's boost converter, which puts roughly ±20V
    on the panel's drive lines. On a shifted or mirrored 24-pin ribbon those
    land on pins built for 3.3V. A correctly connected, idle UC8179 holds BUSY
    high once reset; floating (the driver's pull-down reads it low) or held low
    means the ribbon isn't connected the way we think. A reset alone never
    starts the boost, so this check is safe on any wiring.
    """
    cfg.digital_write(cfg.PWR_PIN, 1)  # Waveshare HAT Rev2.3 gates panel power here
    cfg.delay_ms(50)
    epd.reset()
    if cfg.digital_read(epd.busy_pin) != 1:
        cfg.digital_write(cfg.PWR_PIN, 0)
        raise DisplayWiringError(
            "e-paper BUSY not idle-high after reset: ribbon not connected as "
            "expected, refusing to start the panel (see deploy/README.md)"
        )


class EPaperDisplay(Display):
    """Good Display GDEY075T7-T01 via Waveshare's UC8179 driver.

    Same controller and resolution as Waveshare's own 7.5in V2, so their
    epd7in5_V2 module drives it. Vendored on the Pi from
    github.com/waveshareteam/e-Paper — it is not on PyPI.

    That driver also exposes init_part()/display_Partial(): confirmed against
    the vendored source and its own demo (epd_7in5_V2_test.py), which runs
    ten consecutive partial updates with no full refresh between them. Partial
    mode is flicker-free and much faster than a full refresh (measured 1.73s fast, 3.84s normal), but
    repeated partial updates build up visible ghosting on this panel — hence
    FULL_REFRESH_EVERY below, matching the vendor's own bounded-burst usage
    rather than relying on partial mode indefinitely.

    So every update is partial, and the ghosting-clearing full refresh is
    scheduled by `cleanup_due()` instead: only once CLEANUP_AFTER_PARTIALS
    have accumulated (the vendor demo's own burst length) and nothing has
    changed for CLEANUP_IDLE_SECONDS — so the flash lands after you walk
    away, never under your finger, and a lone background update doesn't
    earn one. CLEANUP_MAX_AGE bounds how long light residue from a few
    updates can sit before an idle cleanup anyway. FULL_REFRESH_EVERY is
    only a safety net for a long unbroken burst. All four are first
    guesses, not measured: tune them against what the panel actually shows.
    """

    FULL_REFRESH_EVERY = 40
    CLEANUP_AFTER_PARTIALS = 10
    CLEANUP_IDLE_SECONDS = 60
    CLEANUP_MAX_AGE = 6 * 3600
    FAST_PER_NORMAL = 3  # fast full refreshes between each normal one

    def __init__(self, fast: bool = True) -> None:
        from waveshare_epd import epd7in5_V2, epdconfig  # noqa: WPS433 — Pi-only import

        self.epd = epd7in5_V2.EPD()
        require_idle_busy(epdconfig, self.epd)
        # Startup always takes the thorough normal waveform, once: the panel
        # may be holding an image from before a crash or power cut.
        self.epd.init()
        self.epd.Clear()
        # init_fast() is the driver's own shorter full refresh: it forces the
        # controller's temperature reading to 90°C (0xE5=0x5A), which selects
        # the panel's quicker high-temperature waveform from OTP — the same
        # trick init_part() plays at 110°C. Still a real full refresh with a
        # flash, just a shorter one (1.73s vs 3.84s, measured). It clears most
        # ghosting but not all of it by eye, so after FAST_PER_NORMAL fast
        # ones the next full refresh takes the normal waveform to mop up what
        # they left. PANEL_FULL_REFRESH=normal makes every one normal.
        self._fast = fast
        self._thorough = False  # see set_thorough
        self._fasts_since_normal = 0  # startup's init()+Clear() just counted as normal
        self._mode = "normal"  # which init the controller is currently in
        self._partials_since_full = 0
        self._last_full_at = time.monotonic()
        self._last_partial_at = 0.0

    def set_thorough(self, on: bool) -> None:
        self._thorough = on

    def show(self, image: Image.Image) -> None:
        # getbuffer wants a PIL image the size of the panel; mode "1" is what
        # it packs from, so no conversion needed.
        fast = self._fast and not self._thorough and self._fasts_since_normal < self.FAST_PER_NORMAL
        want = "fast" if fast else "normal"
        if self._mode != want:
            (self.epd.init_fast if want == "fast" else self.epd.init)()
            self._mode = want
        started = time.monotonic()
        self.epd.display(self.epd.getbuffer(image))
        self._last_full_at = time.monotonic()
        self._fasts_since_normal = self._fasts_since_normal + 1 if want == "fast" else 0
        # Logged so the two waveforms can be compared on the real panel.
        print(
            "full refresh (%s) %.2fs after %d partials"
            % (want, self._last_full_at - started, self._partials_since_full),
            file=sys.stderr,
        )
        self._partials_since_full = 0

    def show_partial(self, image: Image.Image) -> None:
        if self._partials_since_full >= self.FULL_REFRESH_EVERY:
            self.show(image)
            return
        if self._mode != "part":
            self.epd.init_part()
            self._mode = "part"
        self.epd.display_Partial(self.epd.getbuffer(image), 0, 0, self.epd.width, self.epd.height)
        self._partials_since_full += 1
        self._last_partial_at = time.monotonic()

    def cleanup_due(self) -> bool:
        if self._partials_since_full == 0:
            return False
        now = time.monotonic()
        if now - self._last_partial_at < self.CLEANUP_IDLE_SECONDS:
            return False
        return (
            self._partials_since_full >= self.CLEANUP_AFTER_PARTIALS
            or now - self._last_full_at >= self.CLEANUP_MAX_AGE
        )

    def close(self) -> None:
        # Leaving the panel powered does nothing useful — the image persists
        # without it, and sleeping avoids cooking the driver IC.
        self.epd.sleep()


class RotatedDisplay(Display):
    """Turns the logical (possibly portrait) image onto the panel's native
    frame, then hands it to the real backend — so nothing above this knows the
    panel is mounted sideways. Only wraps the e-paper: the png backend keeps
    writing the upright logical image, which is what you want to look at."""

    def __init__(self, inner: Display, rotation: int):
        self.inner = inner
        self.rotation = rotation

    def set_rotation(self, rotation: int) -> None:
        self.rotation = rotation

    def show(self, image: Image.Image) -> None:
        self.inner.show(to_panel_image(image, self.rotation))

    def show_partial(self, image: Image.Image) -> None:
        self.inner.show_partial(to_panel_image(image, self.rotation))

    def set_thorough(self, on: bool) -> None:
        self.inner.set_thorough(on)

    def cleanup_due(self) -> bool:
        return self.inner.cleanup_due()

    def close(self) -> None:
        self.inner.close()


def open_display(config: Config) -> Display:
    if config.display == "epaper":
        # Always wrapped, even at rotation 0: the rotate button can turn it later.
        return RotatedDisplay(EPaperDisplay(fast=config.full_refresh != "normal"), config.rotation)
    if config.display == "png":
        return PngDisplay(config.png_path)
    raise ValueError("unknown PANEL_DISPLAY: %s" % config.display)
