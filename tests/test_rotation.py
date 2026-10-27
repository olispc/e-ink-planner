"""Rotation: the image transform and the touch-point transform must agree.
Run: python3 -m unittest discover tests"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from panel.config import Config, read_saved_rotation, read_saved_sort, save_state  # noqa: E402
from panel.display import RotatedDisplay  # noqa: E402
from panel.rotation import (  # noqa: E402
    PORTRAIT_TURN, ROTATIONS, is_portrait, to_logical_point, to_panel_image,
)
from panel.theme import HEIGHT, WIDTH  # noqa: E402
from panel.touch import classify  # noqa: E402


def logical_size(rotation):
    return (HEIGHT, WIDTH) if is_portrait(rotation) else (WIDTH, HEIGHT)


def marked_image(rotation):
    """A logical canvas with asymmetric black marks, so a wrong flip or a
    swapped axis lands a mark somewhere that isn't black in the original."""
    w, h = logical_size(rotation)
    img = Image.new("1", (w, h), 1)
    for x, y in [(0, 0), (1, 0), (0, 2), (w - 1, 0), (w - 1, h - 1), (7, h - 4), (w // 3, h // 5)]:
        img.putpixel((x, y), 0)
    return img


class TestImageAndTouchAgree(unittest.TestCase):
    def test_every_panel_pixel_maps_back_to_the_logical_pixel_it_was_drawn_from(self):
        for rotation in ROTATIONS:
            with self.subTest(rotation=rotation):
                logical = marked_image(rotation)
                panel = to_panel_image(logical, rotation)
                self.assertEqual(panel.size, (WIDTH, HEIGHT))
                self.assertEqual(panel.mode, "1")
                black = [(x, y) for y in range(HEIGHT) for x in range(WIDTH) if panel.getpixel((x, y)) == 0]
                self.assertEqual(len(black), 7)
                for x, y in black:
                    lx, ly = to_logical_point(x, y, rotation)
                    self.assertEqual(logical.getpixel((lx, ly)), 0)

    def test_panel_corners_land_inside_the_logical_canvas(self):
        for rotation in ROTATIONS:
            w, h = logical_size(rotation)
            for x, y in [(0, 0), (WIDTH - 1, 0), (0, HEIGHT - 1), (WIDTH - 1, HEIGHT - 1)]:
                lx, ly = to_logical_point(x, y, rotation)
                self.assertTrue(0 <= lx < w and 0 <= ly < h, (rotation, (x, y), (lx, ly)))

    def test_rotation_zero_is_the_identity(self):
        img = marked_image(0)
        self.assertIs(to_panel_image(img, 0), img)
        self.assertEqual(to_logical_point(123, 45, 0), (123, 45))


class TestTheButtonsPortraitIsTheUITurnedClockwise(unittest.TestCase):
    """Pins the direction in plain terms: the rotate button turns the UI 90
    degrees clockwise on the panel, so the top of the UI ends up at the
    panel's right edge and its top-left corner in the panel's top-right."""

    def test_the_button_goes_to_270(self):
        self.assertEqual(PORTRAIT_TURN, 270)

    def test_the_top_left_of_the_ui_lands_in_the_panels_top_right_corner(self):
        logical = Image.new("1", (HEIGHT, WIDTH), 1)
        logical.putpixel((0, 0), 0)                     # the UI's own top-left
        panel = to_panel_image(logical, PORTRAIT_TURN)
        self.assertEqual(panel.getpixel((WIDTH - 1, 0)), 0)
        self.assertEqual(to_logical_point(WIDTH - 1, 0, PORTRAIT_TURN), (0, 0))

    def test_the_uis_top_edge_runs_down_the_panels_right_edge(self):
        logical = Image.new("1", (HEIGHT, WIDTH), 1)
        for x in range(HEIGHT):
            logical.putpixel((x, 0), 0)                 # a line along the UI's top
        panel = to_panel_image(logical, PORTRAIT_TURN)
        self.assertTrue(all(panel.getpixel((WIDTH - 1, y)) == 0 for y in range(HEIGHT)))


class TestSwipeIsClassifiedInTheFrameYouSee(unittest.TestCase):
    def test_a_vertical_drag_on_a_sideways_panel_is_a_page_swipe(self):
        # Mounted at 90, the user's left/right is the panel's up/down.
        start = to_logical_point(400, 100, 90)
        end = to_logical_point(400, 400, 90)
        gesture = classify(start, end)
        self.assertEqual(gesture.kind, "swipe")
        self.assertNotEqual(gesture.dx, 0)

    def test_opposite_drags_swipe_opposite_ways(self):
        down = classify(to_logical_point(400, 100, 90), to_logical_point(400, 400, 90))
        up = classify(to_logical_point(400, 400, 90), to_logical_point(400, 100, 90))
        self.assertEqual(down.dx, -up.dx)

    def test_a_tap_stays_a_tap_at_every_rotation(self):
        for rotation in ROTATIONS:
            p = to_logical_point(300, 200, rotation)
            self.assertEqual(classify(p, p).kind, "tap")


class TestRotatedDisplay(unittest.TestCase):
    def test_hands_the_inner_display_a_native_frame(self):
        class Inner:
            shown = []

            def show(self, image):
                self.shown.append(("full", image.size))

            def show_partial(self, image):
                self.shown.append(("partial", image.size))

        inner = Inner()
        display = RotatedDisplay(inner, 90)
        portrait = Image.new("1", (HEIGHT, WIDTH), 1)
        display.show(portrait)
        display.show_partial(portrait)
        self.assertEqual(inner.shown, [("full", (WIDTH, HEIGHT)), ("partial", (WIDTH, HEIGHT))])


class TestConfigRotation(unittest.TestCase):
    def setUp(self):
        # Never read a real saved orientation from the developer's checkout.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def rotation(self, raw):
        with mock.patch.dict(os.environ, {"PANEL_ROTATION": raw, "PANEL_STATE_PATH": str(self.state)}):
            return Config.from_env().rotation

    def test_defaults_to_portrait(self):
        env = {k: v for k, v in os.environ.items() if k != "PANEL_ROTATION"}
        env["PANEL_STATE_PATH"] = str(self.state)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(Config.from_env().rotation, 270)

    def test_what_the_button_saved_beats_env_after_a_restart(self):
        save_state(self.state, rotation=270)
        self.assertEqual(self.rotation("0"), 270)

    def test_env_applies_when_nothing_is_saved(self):
        self.assertEqual(self.rotation("90"), 90)

    def test_a_damaged_or_nonsense_state_file_is_ignored_not_fatal(self):
        for content in ("not json", "[]", '{"rotation": 45}', '{"rotation": "90"}', ""):
            self.state.write_text(content)
            self.assertIsNone(read_saved_rotation(self.state), content)
            self.assertEqual(self.rotation("90"), 90)

    def test_save_then_read_round_trips_and_leaves_no_temp_file(self):
        nested = Path(self.tmp.name) / "a" / "b" / "state.json"
        for r in ROTATIONS:
            save_state(nested, rotation=r)
            self.assertEqual(read_saved_rotation(nested), r)
        self.assertEqual(sorted(p.name for p in nested.parent.iterdir()), ["state.json"])

    def test_saving_one_choice_keeps_the_other(self):
        save_state(self.state, rotation=0)
        save_state(self.state, sort_newest=False)
        self.assertEqual(read_saved_rotation(self.state), 0)
        self.assertIs(read_saved_sort(self.state), False)

    def test_sort_is_newest_first_unless_saved_otherwise(self):
        with mock.patch.dict(os.environ, {"PANEL_STATE_PATH": str(self.state)}):
            self.assertTrue(Config.from_env().sort_newest)
            save_state(self.state, sort_newest=False)
            self.assertFalse(Config.from_env().sort_newest)
            self.state.write_text('{"sort_newest": "no"}')   # nonsense: the default
            self.assertTrue(Config.from_env().sort_newest)

    def test_missing_file_and_no_path_mean_nothing_saved(self):
        self.assertIsNone(read_saved_rotation(self.state))
        self.assertIsNone(read_saved_rotation(None))

    def test_accepts_the_four_rotations(self):
        for r in ROTATIONS:
            self.assertEqual(self.rotation(str(r)), r)

    def test_rejects_anything_else_loudly(self):
        for bad in ("45", "left", "", "-90"):
            with self.assertRaises(ValueError):
                self.rotation(bad)


if __name__ == "__main__":
    unittest.main()
