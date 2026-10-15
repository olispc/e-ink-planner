"""Touch input from the panel's touch chip, once its device-tree overlay is loaded.

The overlay in deploy/overlays/ binds the mainline driver for that chip
(`goodix` or `edt-ft5x06`), after which it is an ordinary evdev device — the
same class of thing as a mouse.
Nothing here talks I2C directly.
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

from .config import Config
from .theme import HEIGHT, WIDTH

# A stroke that travels less than TAP_SLOP is a tap; one that travels at least
# SWIPE_MIN_DX sideways, and mostly sideways, is a page swipe. Anything in
# between (a wobbly press, a vertical drag) is neither and does nothing —
# the same cancel-by-dragging-off behaviour taps have always promised.
TAP_SLOP = 30
SWIPE_MIN_DX = 80


@dataclass(frozen=True)
class Gesture:
    """What one finger-down..finger-up stroke amounted to, in panel pixels.
    `x, y` is where it landed; `dx` is the horizontal travel (swipes only)."""
    kind: str  # "tap" | "swipe"
    x: int
    y: int
    dx: int = 0


def classify(start: Tuple[int, int], end: Tuple[int, int]) -> Optional[Gesture]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if abs(dx) < TAP_SLOP and abs(dy) < TAP_SLOP:
        return Gesture("tap", start[0], start[1])
    if abs(dx) >= SWIPE_MIN_DX and abs(dx) > 2 * abs(dy):
        return Gesture("swipe", start[0], start[1], dx)
    return None


class TouchSource:
    def wait_for_gesture(self, timeout: float) -> Optional[Gesture]:
        """One completed tap or swipe, or None if `timeout` elapsed (or the
        stroke was neither)."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class NoTouch(TouchSource):
    """Used when running without hardware, and as the fallback if touch fails
    to come up — a display-only panel is still worth having."""

    def wait_for_gesture(self, timeout: float) -> Optional[Gesture]:
        time.sleep(timeout)
        return None


class EvdevTouch(TouchSource):
    def __init__(self, device_path: str):
        import evdev  # noqa: WPS433 — Pi-only import

        self.evdev = evdev
        self.device = evdev.InputDevice(device_path)
        caps = self.device.capabilities(absinfo=True).get(evdev.ecodes.EV_ABS, [])
        ranges = {code: info for code, info in caps}
        x_info = ranges.get(evdev.ecodes.ABS_MT_POSITION_X) or ranges.get(evdev.ecodes.ABS_X)
        y_info = ranges.get(evdev.ecodes.ABS_MT_POSITION_Y) or ranges.get(evdev.ecodes.ABS_Y)
        # The digitiser's coordinate space need not match the panel's pixels,
        # so scale from what the driver reports rather than assuming 800x480.
        self.x_max = x_info.max if x_info else WIDTH - 1
        self.y_max = y_info.max if y_info else HEIGHT - 1
        # Stroke state lives on the instance, not in the read loop, so a
        # swipe that straddles a wait timeout isn't forgotten mid-drag.
        self._start: Optional[list] = None  # [x, y] of the first sample
        self._last: Optional[list] = None   # [x, y] of the latest sample
        self._samples = 0
        # Events read from the device but not yet processed. A batch can hold
        # the end of one stroke and the start of the next (the panel is busy
        # redrawing between them); returning mid-batch used to drop the rest.
        self._pending: deque = deque()
        self._debug = bool(os.environ.get("PANEL_TOUCH_DEBUG"))

    def _to_panel(self, x: int, y: int) -> Tuple[int, int]:
        # Checked on the real panel: corner taps read (12, 3) and (793, 479),
        # so the digitiser's axes match the display — no inversion or swap.
        return (
            int(x * (WIDTH - 1) / max(self.x_max, 1)),
            int(y * (HEIGHT - 1) / max(self.y_max, 1)),
        )

    def _finish(self) -> Optional[Gesture]:
        start, last, samples = self._start, self._last, self._samples
        self._start = self._last = None
        self._samples = 0
        if start is None or None in start or last is None or None in last:
            return None
        s, e = self._to_panel(*start), self._to_panel(*last)
        gesture = classify(s, e)
        if self._debug:
            print("touch stroke %s -> %s (%d samples): %s" % (s, e, samples, gesture), file=sys.stderr)
        return gesture

    def wait_for_gesture(self, timeout: float) -> Optional[Gesture]:
        import select

        ecodes = self.evdev.ecodes
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if not self._pending:
                if not select.select([self.device.fd], [], [], remaining)[0]:
                    return None
                self._pending.extend(self.device.read())
            while self._pending:
                event = self._pending.popleft()
                if event.type == ecodes.EV_ABS:
                    axis = None
                    if event.code in (ecodes.ABS_MT_POSITION_X, ecodes.ABS_X):
                        axis = 0
                    elif event.code in (ecodes.ABS_MT_POSITION_Y, ecodes.ABS_Y):
                        axis = 1
                    if axis is not None:
                        if self._start is None:
                            self._start, self._last = [None, None], [None, None]
                        if self._start[axis] is None:
                            self._start[axis] = event.value
                        self._last[axis] = event.value
                        self._samples += 1
                    elif event.code == ecodes.ABS_MT_TRACKING_ID and event.value == -1:
                        # Finger lifted: act on release, not contact, so a
                        # drag off a row cancels instead of firing.
                        return self._finish()
                elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                    if event.value == 0:
                        return self._finish()

    def close(self) -> None:
        self.device.close()


def open_touch(config: Config) -> TouchSource:
    if config.touch == "evdev":
        try:
            return EvdevTouch(config.touch_device)
        except Exception as exc:  # noqa: BLE001 — any failure degrades gracefully
            print("touch unavailable (%s); running display-only" % exc)
            return NoTouch()
    return NoTouch()
