#!/usr/bin/env python3
"""Drive a real Panel from a browser tab, so taps can be tried before the
e-paper screen and GT911 arrive.

This is not a separate implementation of tap handling — it wraps the same
Panel used by panel/app.py (panel.client, panel.layout, panel.touch's
interface) and just replaces the two hardware-shaped things: the display
(bytes over SPI -> a PNG served over HTTP) and touch (finger on glass -> a
click in the page). Everything about optimistic updates, reconciliation,
partial-refresh confirmation flashes and redraw discipline is exercised for
real.

    python3 tools/webtest.py                 # live, http://127.0.0.1:8765
    python3 tools/webtest.py --fixture        # synthetic data, no network
    python3 tools/webtest.py --port 9000
    python3 tools/webtest.py --host 0.0.0.0   # reachable from other devices on your LAN

--host 0.0.0.0 is opt-in, not the default: without --fixture, a tap here
completes real tasks with no auth of its own — its only access control today
is "can reach 127.0.0.1", which --host 0.0.0.0 gives up in exchange for
letting your phone reach it too.

--fixture runs against tests/fixtures/tasks.json with an in-memory client —
no MINDWTR_TOKEN needed, and no PATCH/POST ever reaches the real server.
Use it for anything that mutates state (completing a task, ticking a
checklist item) until you're ready to try it against your real list.
Without --fixture, MINDWTR_URL / MINDWTR_TOKEN come from .env same as the
real panel, and taps really do complete real tasks.
"""
from __future__ import annotations

import argparse
import io
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.app import Panel  # noqa: E402
from panel.client import MindwtrError, Task  # noqa: E402
from panel.config import Config  # noqa: E402
from panel.theme import HEIGHT, WHITE, WIDTH  # noqa: E402

SCALE = 2  # displayed size only; taps are mapped back to panel coordinates

# A full refresh really does take ~1.5s and visibly flashes on real e-paper,
# so "poll now" (which always does a full refresh — see panel/app.py's
# module docstring) holds a veil for that long. A tap-driven update goes
# through partial refresh instead — flicker-free on real hardware, so no
# veil — and the page just polls the frame quickly enough to catch the
# checkmark/strikethrough confirmation flash while it's held.
FULL_REFRESH_MS = 1500
FAST_POLL_MS = 100
FAST_POLL_TICKS = 25  # ~2.5s: covers confirmation flash + hold + reconcile
# Independent of taps entirely — catches a background change (Panel.run()'s
# cheap etag check reacting to an edit from another device) so this tab
# reflects it without you having to tap anything first.
IDLE_POLL_MS = 1000

PAGE = """<!doctype html>
<title>panel webtest</title>
<style>
  body {{ background: #444; color: #ddd; font: 14px system-ui; text-align: center; }}
  #frame {{ display: inline-block; position: relative; margin-top: 24px; }}
  img {{ image-rendering: pixelated; border: 1px solid #000; cursor: pointer; display: block; }}
  #veil {{
    position: absolute; inset: 0; background: #444; opacity: 0;
    display: flex; align-items: center; justify-content: center;
    pointer-events: none; transition: opacity .1s; font-weight: bold;
  }}
  #veil.on {{ opacity: .85; }}
  button {{ margin: 12px 6px; padding: 6px 14px; }}
  #mode {{ color: #f80; font-weight: bold; }}
</style>
<div id="frame">
  <img id="panel" src="/panel.png" width="{width}" height="{height}">
  <div id="veil">redrawing…</div>
</div>
<div>
  <button onclick="forceRefresh()">poll now (full refresh)</button>
  <button onclick="toggleOffline()">toggle offline</button>
</div>
<p><span id="mode">{mode_label}</span></p>
<p>Checkbox column completes a task / ticks a checklist item. The rest of a
task row opens its detail view. The header in detail view goes back.
Tap-driven updates use partial refresh (fast, no flash, so no veil here) —
"poll now" forces the real ~1.5s full refresh and its flash instead.</p>
<script>
const img = document.getElementById('panel');
const veil = document.getElementById('veil');
let pollTimer = null;

function reload() {{
  img.src = '/panel.png?t=' + Date.now();
}}

function pollFast() {{
  if (pollTimer) clearInterval(pollTimer);
  let ticks = 0;
  pollTimer = setInterval(() => {{
    reload();
    if (++ticks > {fast_ticks}) clearInterval(pollTimer);
  }}, {fast_ms});
}}

// Real e-paper is pushed to directly — the display just changes, no one has
// to ask it to. This simulator's "display" is a browser tab, which does have
// to ask, so it needs its own idle poll independent of pollFast()'s
// post-tap burst above — otherwise a background change (Panel.run()'s cheap
// etag check picking up an edit from another device) updates the in-memory
// image server-side but the tab never notices until you tap something.
setInterval(reload, {idle_ms});

img.addEventListener('click', async (e) => {{
  const rect = img.getBoundingClientRect();
  // Hit-testing on the server works in real panel space (800x480), not the
  // displayed bitmap's 2x-scaled pixels — divide out both the CSS display
  // scaling (rect vs. the bitmap) and SCALE (the bitmap vs. the panel).
  const x = Math.round((e.clientX - rect.left) / rect.width * {real_width});
  const y = Math.round((e.clientY - rect.top) / rect.height * {real_height});
  await fetch(`/tap?x=${{x}}&y=${{y}}`, {{method: 'POST'}});
  pollFast();
}});

async function forceRefresh() {{
  veil.classList.add('on');
  await fetch('/refresh', {{method: 'POST'}});
  setTimeout(() => {{ reload(); veil.classList.remove('on'); }}, {full_ms});
}}
async function toggleOffline() {{
  await fetch('/toggle-offline', {{method: 'POST'}});
  reload();
}}
</script>
"""


class Handler(BaseHTTPRequestHandler):
    panel: Panel  # set on the class before serving
    lock: threading.Lock
    mode_label: str

    def log_message(self, fmt, *args):  # quieter than the default
        print("  %s" % (fmt % args))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            html = PAGE.format(
                width=WIDTH * SCALE, height=HEIGHT * SCALE,
                real_width=WIDTH, real_height=HEIGHT,
                full_ms=FULL_REFRESH_MS, fast_ms=FAST_POLL_MS, fast_ticks=FAST_POLL_TICKS,
                idle_ms=IDLE_POLL_MS,
                mode_label=self.mode_label,
            )
            self._send(200, html.encode(), "text/html")
        elif path == "/panel.png":
            # No lock: a tap in flight holds it for the confirmation hold, and
            # polling during exactly that window — to see the flash frame — is
            # the point. CapturingPngDisplay.show/show_partial each do one
            # attribute assignment, atomic under the GIL, so this is never a
            # torn read, just possibly-in-progress state, same as glancing at
            # real e-paper mid-update.
            image = self.panel.display.last_image
            buf = io.BytesIO()
            image.resize((WIDTH * SCALE, HEIGHT * SCALE), resample=0).save(buf, "PNG")
            self._send(200, buf.getvalue(), "image/png")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/tap":
            q = parse_qs(urlparse(self.path).query)
            x, y = int(q["x"][0]), int(q["y"][0])
            threading.Thread(target=self._run, args=(self.panel.handle_tap, ((x, y),)), daemon=True).start()
            self._send(202, b"", "text/plain")
        elif path == "/refresh":
            def do_refresh():
                self.panel.refresh()
                self.panel.draw(force=True)
            threading.Thread(target=self._run, args=(do_refresh, ()), daemon=True).start()
            self._send(202, b"", "text/plain")
        elif path == "/toggle-offline":
            def do_toggle():
                self.panel.stale_since = None if self.panel.stale_since else datetime.now()
                self.panel.draw(force=True)
            threading.Thread(target=self._run, args=(do_toggle, ()), daemon=True).start()
            self._send(202, b"", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def _run(self, fn, args) -> None:
        # Serializes actual Panel mutation across concurrent requests — a
        # second tap arriving mid-confirmation-hold queues behind this one,
        # same as a real single-threaded device that can't take a second
        # touch until it's done with the first.
        with self.lock:
            fn(*args)


class CapturingPngDisplay:
    """Keeps the last rendered image in memory instead of touching disk —
    the browser tab is the only consumer here."""

    def __init__(self) -> None:
        from PIL import Image

        self.last_image = Image.new("1", (WIDTH, HEIGHT), WHITE)

    def show(self, image) -> None:
        self.last_image = image

    def show_partial(self, image) -> None:
        self.last_image = image

    def close(self) -> None:
        pass


class FixtureClient:
    """Stands in for MindwtrClient against tests/fixtures/tasks.json — no
    network calls, so complete()/update_checklist() are safe to try
    repeatedly without touching real Mindwtr data."""

    def __init__(self, tasks: List[Task]):
        self._tasks = {t.id: t for t in tasks}
        self._order = [t.id for t in tasks]

    def fetch(self, view: str, limit: int) -> List[Task]:
        tasks = [self._tasks[i] for i in self._order if i in self._tasks]
        if view == "focus":
            tasks = [t for t in tasks if t.focused] + [t for t in tasks if not t.focused]
        return tasks[:limit]

    def complete(self, task_id: str) -> None:
        self._order = [i for i in self._order if i != task_id]
        self._tasks.pop(task_id, None)

    def update_checklist(self, task_id: str, checklist) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            self._tasks[task_id] = replace(task, checklist=checklist)


def poll_loop(panel: Panel, lock: threading.Lock, stop: threading.Event) -> None:
    """Mirrors Panel.run()'s two-tier polling so this preview shows the real
    live-update behavior: a cheap HEAD /v1/data check every check_seconds
    (any write anywhere changes its ETag), a full fetch only when that
    changes, and poll_seconds as a fallback regardless."""
    def peek_etag():
        try:
            return panel.client.data_etag()
        except MindwtrError:
            return None

    with lock:
        last_etag = peek_etag()
    deadline = time.monotonic() + panel.config.poll_seconds
    while not stop.wait(panel.config.check_seconds):
        with lock:
            if time.monotonic() >= deadline:
                panel.refresh()
                panel.draw()
                last_etag = peek_etag()
                deadline = time.monotonic() + panel.config.poll_seconds
                continue
            etag = peek_etag()
            if etag is not None and etag != last_etag:
                last_etag = etag
                panel.refresh()
                panel.draw()
                deadline = time.monotonic() + panel.config.poll_seconds


def _lan_ip() -> str:
    """The IP another device on the LAN would use to reach this Mac — not
    necessarily "the" IP (there can be several), just a real one, found the
    standard way (open a UDP socket toward a public address; nothing is
    actually sent, it's just how the OS picks which local interface/IP a
    real connection would go out on)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--host", default="127.0.0.1",
        help="bind address; 0.0.0.0 to allow other devices on your LAN (see module docstring)",
    )
    ap.add_argument("--view", default=None, help="next | inbox | waiting | someday | focus")
    ap.add_argument("--fixture", action="store_true", help="synthetic data, no network calls")
    args = ap.parse_args()

    config = Config.from_env()
    if args.view:
        config = Config(**{**config.__dict__, "view": args.view})
    if not args.fixture and not config.token:
        print("MINDWTR_TOKEN is not set — copy .env.example to .env, or use --fixture", file=sys.stderr)
        return 1

    panel = Panel(config)
    panel.display = CapturingPngDisplay()  # override: no file, no hardware
    if args.fixture:
        from tools.preview import FIXTURE, from_fixture

        panel.client = FixtureClient(from_fixture(FIXTURE))
    panel.refresh()
    panel.draw(force=True)

    lock = threading.Lock()
    stop = threading.Event()
    if not args.fixture:
        threading.Thread(target=poll_loop, args=(panel, lock, stop), daemon=True).start()

    Handler.panel = panel
    Handler.lock = lock
    Handler.mode_label = "FIXTURE — safe, no network" if args.fixture else "LIVE — real writes"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    # 0.0.0.0 isn't itself a dialable address — print the LAN IP a phone
    # would actually use instead of the literal bind address.
    display_host = _lan_ip() if args.host == "0.0.0.0" else args.host
    print("http://%s:%d  (view=%s, %s)" % (
        display_host, args.port, config.view,
        "fixture data, no polling" if args.fixture else "polling every %ds" % config.poll_seconds,
    ))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        panel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
