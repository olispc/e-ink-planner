"""Touch input from the GT911, once the device-tree overlay is loaded.

The overlay in deploy/overlays/ binds the mainline `goodix` driver to the chip,
after which it is an ordinary evdev device — the same class of thing as a mouse.
Nothing here talks I2C directly.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from .config import Config
from .theme import HEIGHT, WIDTH


class TouchSource:
    def wait_for_tap(self, timeout: float) -> Optional[Tuple[int, int]]:
        """Panel coordinates of one tap, or None if `timeout` elapsed."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class NoTouch(TouchSource):
    """Used when running without hardware, and as the fallback if touch fails
    to come up — a display-only panel is still worth having."""

    def wait_for_tap(self, timeout: float) -> Optional[Tuple[int, int]]:
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

    def _to_panel(self, x: int, y: int) -> Tuple[int, int]:
        # TODO(oli): with the panel in hand, check whether either axis needs
        # inverting or the pair swapping — it depends how the digitiser is
        # bonded relative to the display, and cannot be known from the datasheet.
        return (
            int(x * (WIDTH - 1) / max(self.x_max, 1)),
            int(y * (HEIGHT - 1) / max(self.y_max, 1)),
        )

    def wait_for_tap(self, timeout: float) -> Optional[Tuple[int, int]]:
        import select

        ecodes = self.evdev.ecodes
        deadline = time.monotonic() + timeout
        x = y = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if not select.select([self.device.fd], [], [], remaining)[0]:
                return None
            for event in self.device.read():
                if event.type == ecodes.EV_ABS:
                    if event.code in (ecodes.ABS_MT_POSITION_X, ecodes.ABS_X):
                        x = event.value
                    elif event.code in (ecodes.ABS_MT_POSITION_Y, ecodes.ABS_Y):
                        y = event.value
                    elif event.code == ecodes.ABS_MT_TRACKING_ID and event.value == -1:
                        # Finger lifted: act on release, not contact, so a
                        # drag off a row cancels instead of firing.
                        if x is not None and y is not None:
                            return self._to_panel(x, y)
                        x = y = None
                elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                    if event.value == 0 and x is not None and y is not None:
                        return self._to_panel(x, y)

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
