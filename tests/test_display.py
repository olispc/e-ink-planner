from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.display import EPaperDisplay  # noqa: E402


class FakeEPD:
    width, height = 800, 480

    def __init__(self): self.inits = []
    def init(self): self.inits.append("normal")
    def init_fast(self): self.inits.append("fast")
    def init_part(self): pass
    def getbuffer(self, image): return b""
    def display(self, buf): pass
    def display_Partial(self, buf, *box): pass


def make_display(clock) -> EPaperDisplay:
    # Skip __init__: it imports the Pi-only waveshare driver.
    d = EPaperDisplay.__new__(EPaperDisplay)
    d.epd = FakeEPD()
    d._fast = True
    d._fasts_since_normal = 0
    d._mode = "normal"
    d._partials_since_full = 0
    d._last_full_at = clock[0]
    d._last_partial_at = 0.0
    return d


class TestCleanupDue(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        patcher = mock.patch("panel.display.time.monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.d = make_display(self.clock)

    def partials(self, n):
        for _ in range(n):
            self.d.show_partial(None)

    def test_a_lone_background_update_does_not_earn_a_flash(self):
        self.partials(1)
        self.clock[0] += EPaperDisplay.CLEANUP_IDLE_SECONDS + 1
        self.assertFalse(self.d.cleanup_due())

    def test_accumulated_residue_waits_for_idle(self):
        self.partials(EPaperDisplay.CLEANUP_AFTER_PARTIALS)
        self.clock[0] += EPaperDisplay.CLEANUP_IDLE_SECONDS - 1
        self.assertFalse(self.d.cleanup_due())
        self.clock[0] += 2
        self.assertTrue(self.d.cleanup_due())

    def test_old_light_residue_is_cleaned_eventually(self):
        self.clock[0] += EPaperDisplay.CLEANUP_MAX_AGE
        self.partials(1)
        self.clock[0] += EPaperDisplay.CLEANUP_IDLE_SECONDS
        self.assertTrue(self.d.cleanup_due())

    def test_full_refresh_resets_the_debt(self):
        self.partials(EPaperDisplay.CLEANUP_AFTER_PARTIALS)
        self.d.show(None)
        self.clock[0] += EPaperDisplay.CLEANUP_IDLE_SECONDS + 1
        self.assertFalse(self.d.cleanup_due())


class TestWaveformRotation(unittest.TestCase):
    def test_every_fourth_full_refresh_is_normal(self):
        d = make_display([0.0])
        modes = []
        for _ in range(8):
            d.show(None)
            modes.append(d._mode)
            d.show_partial(None)  # back to partial mode between cleanups, as on the panel
        self.assertEqual(modes, ["fast", "fast", "fast", "normal"] * 2)

    def test_normal_setting_never_uses_fast(self):
        d = make_display([0.0])
        d._fast = False
        for _ in range(4):
            d.show(None)
        self.assertNotIn("fast", d.epd.inits)


if __name__ == "__main__":
    unittest.main()
