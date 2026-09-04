#!/usr/bin/env python3
"""Render the panel to a PNG without any hardware.

Two sources: a JSON fixture (default, so it runs with nothing configured), or a
live mindwtr-cloud if you point it at one.

    python3 tools/preview.py
    python3 tools/preview.py --live --view focus
    python3 tools/preview.py --scale 1     # actual panel pixels
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.client import ChecklistItem, MindwtrClient, Task  # noqa: E402
from panel.config import Config  # noqa: E402
from panel.layout import render  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tasks.json"


def from_fixture(path: Path):
    raw = json.loads(path.read_text())
    return [
        Task(
            id=t["id"],
            title=t["title"],
            project=t.get("project"),
            area=t.get("area"),
            contexts=t.get("contexts", []),
            focused=t.get("focused", False),
            description=t.get("description"),
            checklist=[
                ChecklistItem(id=c["id"], title=c["title"], completed=c.get("completed", False))
                for c in t.get("checklist", [])
            ],
        )
        for t in raw["tasks"]
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="fetch from mindwtr-cloud")
    ap.add_argument("--view", default=None, help="next | inbox | waiting | someday | focus")
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--out", type=Path, default=Path("local/preview.png"))
    ap.add_argument("--scale", type=int, default=1, help="upscale for screen viewing")
    args = ap.parse_args()

    config = Config.from_env()
    view = args.view or config.view

    if args.live:
        tasks = MindwtrClient(config.url, config.token).fetch(view, 50)
    else:
        tasks = from_fixture(args.fixture)

    image = render(tasks, view, total=len(tasks)).image
    if args.scale > 1:
        image = image.convert("L").resize(
            (image.width * args.scale, image.height * args.scale)
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print("%d task(s) -> %s (%dx%d)" % (len(tasks), args.out, image.width, image.height))
    return 0


if __name__ == "__main__":
    sys.exit(main())
