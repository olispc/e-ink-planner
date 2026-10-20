"""The lock screen's renderer, picture handling and passcode config.
Run: python3 -m unittest discover tests"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.config import _passcode  # noqa: E402
from panel.lockscreen import load_background, pick_background, render_lock  # noqa: E402

NOW = datetime(2026, 10, 20, 9, 41)


class TestRenderLock(unittest.TestCase):
    def test_both_faces_are_one_bit_at_canvas_size(self):
        for portrait, size in ((False, (800, 480)), (True, (480, 800))):
            for keypad in (False, True):
                img = render_lock(now=NOW, portrait=portrait, keypad=keypad, code_length=4).image
                self.assertEqual((img.mode, img.size), ("1", size))

    def test_the_clock_face_is_one_tap_target(self):
        r = render_lock(now=NOW, code_length=4)
        self.assertEqual([(b.kind, b.x0, b.y0, b.x1, b.y1) for b in r.hitboxes],
                         [("lock_wake", 0, 0, 800, 480)])

    def test_the_keypad_has_twelve_separate_keys_on_the_canvas(self):
        for portrait in (False, True):
            r = render_lock(now=NOW, portrait=portrait, keypad=True, code_length=4)
            w, h = r.image.size
            keys = [b for b in r.hitboxes if b.kind == "lock_key"]
            self.assertEqual(sorted(b.key for b in keys),
                             sorted(list("0123456789") + ["cancel", "delete"]))
            for b in keys:
                self.assertTrue(0 <= b.x0 < b.x1 <= w and 0 <= b.y0 < b.y1 <= h)
                self.assertGreaterEqual(min(b.x1 - b.x0, b.y1 - b.y0), 80)   # well past a thumb
            for a in keys:
                for b in keys:
                    if a is not b:
                        self.assertFalse(a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1)

    def test_typed_digits_show_as_dots(self):
        a = render_lock(now=NOW, keypad=True, entered=0, code_length=4).image
        b = render_lock(now=NOW, keypad=True, entered=2, code_length=4).image
        self.assertNotEqual(a.tobytes(), b.tobytes())

    def test_dark_inverts_a_plain_clock_but_not_a_picture(self):
        light = render_lock(now=NOW).image.convert("L")
        dark = render_lock(now=NOW, dark=True).image.convert("L")
        self.assertEqual(list(dark.getdata()), [255 - p for p in light.getdata()])

        picture = Image.new("1", (800, 480), 0)
        a = render_lock(now=NOW, background=picture).image
        b = render_lock(now=NOW, background=picture, dark=True).image
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_portrait_keeps_the_clock_up_top_off_the_picture(self):
        # Portrait is the main orientation: the photo's lower two thirds
        # must come through untouched, not sit under the clock.
        picture = Image.new("1", (480, 800), 1)
        img = render_lock(now=NOW, portrait=True, background=picture).image
        self.assertEqual(set(img.crop((0, 320, 480, 800)).getdata()), {1})
        self.assertGreater(len(set(img.crop((0, 0, 480, 320)).getdata())), 1)   # the clock


class TestBackground(unittest.TestCase):
    def test_none_without_a_directory_or_pictures(self):
        self.assertIsNone(pick_background(None, NOW))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pick_background(Path(tmp), NOW))
            (Path(tmp) / "notes.txt").write_text("x")
            self.assertIsNone(pick_background(Path(tmp), NOW))

    def test_one_picture_a_day_cycling_in_name_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("b.png", "a.jpg"):
                Image.new("L", (10, 10)).save(Path(tmp) / name)
            picks = {pick_background(Path(tmp), datetime(2026, 10, d)).name for d in (20, 21)}
            self.assertEqual(picks, {"a.jpg", "b.png"})
            self.assertEqual(pick_background(Path(tmp), datetime(2026, 10, 20, 8)),
                             pick_background(Path(tmp), datetime(2026, 10, 20, 23)))

    def test_a_picture_is_cropped_to_fill_and_dithered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grey.png"
            Image.new("L", (300, 100), 128).save(path)
            img = load_background(path, (480, 800))
            self.assertEqual((img.mode, img.size), ("1", (480, 800)))
            values = set(img.getdata())
            self.assertEqual(len(values), 2)   # mid-grey dithers to both, not one blob

    def test_an_unreadable_picture_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.jpg"
            path.write_text("not an image")
            self.assertIsNone(load_background(path, (800, 480)))


class TestPasscodeConfig(unittest.TestCase):
    def test_digits_only(self):
        self.assertEqual(_passcode(" 2468 "), "2468")
        self.assertEqual(_passcode(""), "")
        with self.assertRaises(ValueError):
            _passcode("12a4")


if __name__ == "__main__":
    unittest.main()
