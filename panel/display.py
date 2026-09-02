"""Where the rendered image goes.

Two backends. `png` writes a file, which is how the layout gets designed on a
laptop with no hardware attached. `epaper` pushes to the panel over SPI. They
exist because the whole build was sequenced to make the software finishable
before the parcel arrived — not as speculative abstraction.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .config import Config


class Display:
    def show(self, image: Image.Image) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class PngDisplay(Display):
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def show(self, image: Image.Image) -> None:
        image.save(self.path)
        print("wrote %s" % self.path)


class EPaperDisplay(Display):
    """Good Display GDEY075T7-T01 via Waveshare's UC8179 driver.

    Same controller and resolution as Waveshare's own 7.5in V2, so their
    epd7in5_V2 module drives it. Vendored on the Pi from
    github.com/waveshareteam/e-Paper — it is not on PyPI.
    """

    def __init__(self) -> None:
        from waveshare_epd import epd7in5_V2  # noqa: WPS433 — Pi-only import

        self.epd = epd7in5_V2.EPD()
        self.epd.init()
        self.epd.Clear()

    def show(self, image: Image.Image) -> None:
        # getbuffer wants a PIL image the size of the panel; mode "1" is what
        # it packs from, so no conversion needed.
        self.epd.display(self.epd.getbuffer(image))

    def close(self) -> None:
        # Leaving the panel powered does nothing useful — the image persists
        # without it, and sleeping avoids cooking the driver IC.
        self.epd.sleep()


def open_display(config: Config) -> Display:
    if config.display == "epaper":
        return EPaperDisplay()
    if config.display == "png":
        return PngDisplay(config.png_path)
    raise ValueError("unknown PANEL_DISPLAY: %s" % config.display)
