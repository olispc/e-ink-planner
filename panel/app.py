"""The loop: poll, draw, wait for a tap, tick off, repeat.

Redraw discipline is the design constraint. A full refresh takes ~1.5s and
third-party code cannot rely on partial refresh, so the panel redraws only when
the content actually changed, or when a tap needs acknowledging.
"""
from __future__ import annotations

import signal
import sys
import time
from datetime import datetime
from typing import List, Optional

from .client import MindwtrClient, MindwtrError, Task
from .config import Config
from .display import open_display
from .layout import ROWS, hit, render
from .touch import open_touch

# One screenful, plus enough to keep the "N more" counter honest without
# pulling the whole list every five minutes.
FETCH_LIMIT = 50


class Panel:
    def __init__(self, config: Config):
        self.config = config
        self.client = MindwtrClient(config.url, config.token)
        self.display = open_display(config)
        self.touch = open_touch(config)
        self.tasks: List[Task] = []
        self.stale_since: Optional[datetime] = None
        self._last_signature: Optional[tuple] = None

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self.tasks = self.client.fetch(self.config.view, FETCH_LIMIT)
            self.stale_since = None
        except MindwtrError as exc:
            # Keep showing the last known list rather than blanking: stale but
            # labelled beats empty and unexplained.
            if self.stale_since is None:
                self.stale_since = datetime.now()
            print("refresh failed: %s" % exc, file=sys.stderr)

    def _signature(self) -> tuple:
        return (
            tuple((t.id, t.title, t.focused) for t in self.tasks[:ROWS]),
            len(self.tasks),
            self.stale_since is not None,
        )

    # -- output -----------------------------------------------------------
    def draw(self, force: bool = False) -> None:
        signature = self._signature()
        if not force and signature == self._last_signature:
            return
        rendered = render(
            self.tasks,
            self.config.view,
            total=len(self.tasks),
            stale_since=self.stale_since,
        )
        self.display.show(rendered.image)
        self.hitboxes = rendered.hitboxes
        self._last_signature = signature

    # -- interaction ------------------------------------------------------
    def handle_tap(self, point) -> None:
        task_id = hit(getattr(self, "hitboxes", []), point)
        if task_id is None:
            return
        removed = [t for t in self.tasks if t.id == task_id]
        # Optimistic: drop it and redraw straight away, so the tap feels
        # answered. The network catches up afterwards.
        self.tasks = [t for t in self.tasks if t.id != task_id]
        self.draw(force=True)
        try:
            self.client.complete(task_id)
        except MindwtrError as exc:
            print("complete failed: %s" % exc, file=sys.stderr)
            self.tasks = removed + self.tasks
            if self.stale_since is None:
                self.stale_since = datetime.now()
            self.draw(force=True)
            return
        self.refresh()
        self.draw()

    # -- loop -------------------------------------------------------------
    def run(self) -> None:
        self.refresh()
        self.draw(force=True)
        while True:
            deadline = time.monotonic() + self.config.poll_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                tap = self.touch.wait_for_tap(min(remaining, 5.0))
                if tap is not None:
                    self.handle_tap(tap)
                    deadline = time.monotonic() + self.config.poll_seconds
            self.refresh()
            self.draw()

    def close(self) -> None:
        self.touch.close()
        self.display.close()


def main() -> int:
    config = Config.from_env()
    if not config.token:
        print("MINDWTR_TOKEN is not set — copy .env.example to .env", file=sys.stderr)
        return 1
    panel = Panel(config)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        panel.run()
    except KeyboardInterrupt:
        pass
    finally:
        panel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
