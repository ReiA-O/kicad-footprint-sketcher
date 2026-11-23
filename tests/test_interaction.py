"""Interaction geometry checks independent of Tk and display scaling."""

from dataclasses import asdict
import math
import unittest

from footprint_sketcher.interaction import hit_pad, pad_corners, resize_pad
from footprint_sketcher.model import Pad


class PadInteractionTests(unittest.TestCase):
    def assertPointAlmostEqual(self, actual, expected):
        self.assertAlmostEqual(actual[0], expected[0])
        self.assertAlmostEqual(actual[1], expected[1])

    def test_counterclockwise_corners_and_hit_at_thirty_degrees(self):
        pad = Pad("1", 5, -2, 4, 2, 30)
        cosine = math.sqrt(3) / 2
        expected = [
            (5 - 2 * cosine - 0.5, -1 - cosine),
            (5 + 2 * cosine - 0.5, -3 - cosine),
            (5 + 2 * cosine + 0.5, -3 + cosine),
            (5 - 2 * cosine + 0.5, -1 + cosine),
        ]
        for actual, wanted in zip(pad_corners(pad), expected):
            self.assertPointAlmostEqual(actual, wanted)
            self.assertTrue(hit_pad(pad, actual))
        self.assertTrue(hit_pad(pad, (5, -2)))
        self.assertFalse(hit_pad(pad, (5 + 2.1 * cosine, -2 - 1.05)))

    def test_roundrect_cutout_is_not_selectable_after_rotation(self):
        pad = Pad("2", 0, 0, 2, 1, 90, 0.25)
        # At +90 degrees the local (.99, .49) corner maps to (.49, -.99).
        self.assertFalse(hit_pad(pad, (0.49, -0.99)))
        self.assertTrue(hit_pad(pad, (0.5, -0.75)))
        self.assertTrue(hit_pad(pad, (0, 0)))
        self.assertFalse(hit_pad(pad, (math.nan, 0)))

    def test_rotated_resize_keeps_opposite_corner_and_original_unchanged(self):
        pad = Pad("A", 10, 20, 4, 2, 30, 0.4)
        before = asdict(pad)
        cosine = math.sqrt(3) / 2
        fixed = (10 + 2 * cosine + 0.5, 19 + cosine)
        target = (fixed[0] - 6 * cosine - 1.5, fixed[1] - 3 * cosine + 3)
        resized = resize_pad(pad, 0, target)
        self.assertPointAlmostEqual(pad_corners(resized)[2], fixed)
        self.assertPointAlmostEqual(pad_corners(resized)[0], target)
        self.assertAlmostEqual(resized.width, 6)
        self.assertAlmostEqual(resized.height, 3)
        self.assertEqual((resized.number, resized.angle, resized.radius), ("A", 30, 0.4))
        self.assertEqual(asdict(pad), before)

    def test_resize_clamps_radius_to_new_short_side(self):
        pad = Pad("1", 0, 0, 2, 2, radius=0.75)
        resized = resize_pad(pad, 2, (-0.8, -0.6))
        self.assertAlmostEqual(resized.width, 0.2)
        self.assertAlmostEqual(resized.height, 0.4)
        self.assertAlmostEqual(resized.radius, 0.1)
        self.assertPointAlmostEqual(pad_corners(resized)[0], (-1, -1))

    def test_every_handle_keeps_its_opposite_corner_when_rotated(self):
        pad = Pad("3", -3, 5, 4, 2, -30, 0.2)
        corners = pad_corners(pad)
        for index, moving in enumerate(corners):
            with self.subTest(corner=index):
                fixed = corners[(index + 2) % 4]
                target = tuple(f + 1.5 * (m - f) for m, f in zip(moving, fixed))
                resized = resize_pad(pad, index, target)
                self.assertPointAlmostEqual(pad_corners(resized)[index], target)
                self.assertPointAlmostEqual(pad_corners(resized)[(index + 2) % 4], fixed)
                self.assertAlmostEqual(resized.width, 6)
                self.assertAlmostEqual(resized.height, 3)
                self.assertEqual(resized.angle, -30)

    def test_crossing_fixed_corner_is_rejected_without_mutation(self):
        pad = Pad("1", 0, 0, 2, 2, radius=0.5)
        before = asdict(pad)
        for target in ((-1, -0.5), (-2, 0), (0, -2)):
            with self.subTest(target=target), self.assertRaises(ValueError):
                resize_pad(pad, 2, target)
        self.assertEqual(asdict(pad), before)

    def test_resize_rejects_nonfinite_or_unrepresentable_geometry(self):
        pad = Pad("1", 0, 0, 2, 2)
        for target in ((math.nan, 1), (math.inf, 1), (-0.99999999, 1)):
            with self.subTest(target=target), self.assertRaises(ValueError):
                resize_pad(pad, 2, target)
        with self.assertRaises(ValueError):
            resize_pad(pad, 4, (1, 1))

    def test_roundrect_resize_rejects_unrepresentable_radius_without_rectangularizing(self):
        pad = Pad("R", 0, 0, 2, 2, radius=0.5)
        before = asdict(pad)
        # The width is representable, but its maximum R is below KiCad's minimum.
        with self.assertRaises(ValueError):
            resize_pad(pad, 2, (-0.9999995, 0))
        self.assertEqual(asdict(pad), before)
        self.assertEqual(pad.radius, 0.5)

    def test_very_large_angles_have_equivalent_finite_geometry(self):
        original = Pad("1", 1, 2, 4, 2, 1e100)
        equivalent = Pad("1", 1, 2, 4, 2, 1e100 % 360)
        self.assertEqual(pad_corners(original), pad_corners(equivalent))
        self.assertTrue(hit_pad(original, (1, 2)))


if __name__ == "__main__":
    unittest.main()
