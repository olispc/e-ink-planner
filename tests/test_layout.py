"""Layout and hit-testing. Run: python3 -m unittest discover tests"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.client import Task  # noqa: E402
from panel.layout import hit, render  # noqa: E402
from panel.theme import HEADER_H, HEIGHT, ROW_H, ROWS, WIDTH  # noqa: E402


def task(n: int, title: str = None, **kw) -> Task:
    return Task(
        id="task-%d" % n,
        title=title or "Task number %d" % n,
        project=kw.get("project"),
        area=kw.get("area"),
        contexts=kw.get("contexts", []),
        focused=kw.get("focused", False),
    )


class TestRender(unittest.TestCase):
    def test_is_one_bit_at_panel_size(self):
        image = render([task(1)], "next").image
        self.assertEqual(image.mode, "1")
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_shows_at_most_one_screenful(self):
        rendered = render([task(i) for i in range(20)], "next")
        self.assertEqual(len(rendered.hitboxes), ROWS)

    def test_empty_list_renders_without_hitboxes(self):
        rendered = render([], "next")
        self.assertEqual(rendered.hitboxes, [])

    def test_long_title_is_ellipsized_not_overflowed(self):
        # Failure mode this guards: text running off the panel edge, which is
        # invisible in a PNG viewer that crops but obvious on glass.
        render([task(1, "x" * 400)], "next")

    def test_stale_marker_only_when_offline(self):
        fresh = render([task(1)], "next").image
        stale = render([task(1)], "next", stale_since=datetime(2026, 9, 2, 9, 0)).image
        self.assertNotEqual(fresh.tobytes(), stale.tobytes())


class TestFocusRendering(unittest.TestCase):
    def test_boundary_rule_is_drawn_between_focused_and_backlog(self):
        split = [task(0, focused=True), task(1), task(2)]
        flat = [task(0), task(1), task(2)]
        self.assertNotEqual(
            render(split, "focus").image.tobytes(),
            render(flat, "focus").image.tobytes(),
        )

    def test_header_counts_focused_not_backlog(self):
        one = render([task(0, focused=True)] + [task(i) for i in range(1, 9)], "focus")
        two = render([task(0, focused=True), task(1, focused=True)]
                     + [task(i) for i in range(2, 9)], "focus")
        self.assertNotEqual(one.image.tobytes(), two.image.tobytes())


class TestHitTesting(unittest.TestCase):
    def setUp(self):
        self.rendered = render([task(i) for i in range(ROWS)], "next")

    def test_tap_maps_to_the_row_under_the_finger(self):
        for i in range(ROWS):
            y = HEADER_H + i * ROW_H + ROW_H // 2
            self.assertEqual(hit(self.rendered.hitboxes, (400, y)), "task-%d" % i)

    def test_tap_on_header_or_footer_hits_nothing(self):
        self.assertIsNone(hit(self.rendered.hitboxes, (400, 10)))
        self.assertIsNone(hit(self.rendered.hitboxes, (400, HEIGHT - 10)))

    def test_row_boundaries_do_not_overlap(self):
        boxes = self.rendered.hitboxes
        for earlier, later in zip(boxes, boxes[1:]):
            self.assertEqual(earlier.y1, later.y0)


if __name__ == "__main__":
    unittest.main()
