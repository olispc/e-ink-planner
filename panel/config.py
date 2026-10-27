"""Runtime configuration, read from the environment.

Everything the panel needs is a plain env var so the same code runs on a laptop
(png display, no touch) and on the Pi (e-paper, evdev) with no branching in the
call sites.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


def read_state(path: Optional[Path]) -> dict:
    """What the panel saved about itself: the rotate button's orientation and
    the sort direction.
    A missing or damaged file is simply "nothing saved" — never a startup
    error."""
    if path is None or not path.exists():
        return {}
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def read_saved_rotation(path: Optional[Path]) -> Optional[int]:
    """The orientation last chosen with the on-panel rotate button, if any."""
    value = read_state(path).get("rotation")
    return value if value in (0, 90, 180, 270) else None


def read_saved_sort(path: Optional[Path]) -> Optional[bool]:
    """The sort direction last chosen with the header's sort button, if any."""
    value = read_state(path).get("sort_newest")
    return value if isinstance(value, bool) else None


def save_state(path: Path, **values) -> None:
    """Merge `values` into the saved state. Atomic (write then rename) so a
    power cut mid-write can't leave a half-written file that would silently
    drop what was saved."""
    state = {**read_state(path), **values}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


def _passcode(raw: str) -> str:
    # Digits only, because the keypad only has digits: a passcode with a
    # letter in it could never be typed, so it would lock the panel for good.
    raw = raw.strip()
    if raw and not raw.isdigit():
        raise ValueError("PANEL_PASSCODE must be digits only")
    return raw


def _rotation(raw: str) -> int:
    # Fail loudly at startup: a typo'd rotation would otherwise silently
    # draw the UI sideways on a wall.
    if raw.strip() not in ("0", "90", "180", "270"):
        raise ValueError("PANEL_ROTATION must be 0, 90, 180 or 270, got %r" % raw)
    return int(raw)


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
    # How far the panel is turned clockwise from its native landscape, as
    # mounted: 0 | 90 | 180 | 270. See panel/rotation.py. Portrait (270) is
    # the default, because that's how the panel is mounted.
    rotation: int = 270
    # Where the rotate and sort buttons remember their choices across
    # restarts. None (the default) means nothing is written — tests and the
    # browser tester.
    state_path: Optional[Path] = None
    # Newest-created first, unless the sort button last chose otherwise (it
    # is saved, like rotation: a deploy or crash shouldn't flip the list).
    sort_newest: bool = True
    # The screen lock's code (digits). Empty means a tap on the lock screen
    # unlocks it: it still hides the tasks, it just doesn't keep anyone out.
    passcode: str = ""
    # Directory of lock-screen background images; one is picked per day.
    # None or an empty directory gives a plain clock.
    lock_images: Optional[Path] = None
    # Start behind the screen lock. The real panel always does (from_env), so
    # a reboot or a pulled plug can never be a way past it; the dataclass
    # default is off only for tests and the browser tester.
    start_locked: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv(REPO_ROOT / ".env")
        png = os.environ.get("PANEL_PNG_PATH", "local/panel.png")
        state = Path(os.environ.get("PANEL_STATE_PATH", "local/state.json"))
        state_path = state if state.is_absolute() else REPO_ROOT / state
        configured = _rotation(os.environ.get("PANEL_ROTATION", "270"))
        # What the button last chose wins over .env, because it is the more
        # recent decision and a restart must not turn a mounted panel sideways.
        # Said out loud when it overrides an explicit PANEL_ROTATION, so a
        # later .env edit that "does nothing" has an explanation.
        saved = read_saved_rotation(state_path)
        lock_images = os.environ.get("PANEL_LOCK_IMAGES", "local/lockscreen")
        if saved is not None and "PANEL_ROTATION" in os.environ and saved != configured:
            print(
                "rotation %d from %s overrides PANEL_ROTATION=%d (delete it to use .env)"
                % (saved, state_path, configured), file=sys.stderr,
            )
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
            rotation=configured if saved is None else saved,
            state_path=state_path,
            sort_newest=read_saved_sort(state_path) is not False,
            passcode=_passcode(os.environ.get("PANEL_PASSCODE", "")),
            lock_images=Path(lock_images) if Path(lock_images).is_absolute() else REPO_ROOT / lock_images,
            start_locked=True,
        )
