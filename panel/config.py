"""Runtime configuration, read from the environment.

Everything the panel needs is a plain env var so the same code runs on a laptop
(png display, no touch) and on the Pi (e-paper, evdev) with no branching in the
call sites.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader. Real values never reach the repo; see .gitignore."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    view: str
    poll_seconds: int
    check_seconds: int
    display: str
    touch: str
    png_path: Path
    touch_device: str
    # "fast" | "normal" — which full-refresh waveform the e-paper backend
    # uses for the ghosting cleanup. See EPaperDisplay.
    full_refresh: str = "fast"

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv(REPO_ROOT / ".env")
        png = os.environ.get("PANEL_PNG_PATH", "local/panel.png")
        return cls(
            url=os.environ.get("MINDWTR_URL", "http://127.0.0.1:8787").rstrip("/"),
            token=os.environ.get("MINDWTR_TOKEN", ""),
            view=os.environ.get("PANEL_VIEW", "focus"),
            poll_seconds=int(os.environ.get("PANEL_POLL_SECONDS", "300")),
            check_seconds=int(os.environ.get("PANEL_CHECK_SECONDS", "5")),
            display=os.environ.get("PANEL_DISPLAY", "png"),
            touch=os.environ.get("PANEL_TOUCH", "none"),
            png_path=Path(png) if Path(png).is_absolute() else REPO_ROOT / png,
            touch_device=os.environ.get("PANEL_TOUCH_DEVICE", "/dev/input/event0"),
            full_refresh=os.environ.get("PANEL_FULL_REFRESH", "fast"),
        )
