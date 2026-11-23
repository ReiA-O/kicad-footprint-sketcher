"""Independent axis calibration and compatibility checks without a GUI."""

from dataclasses import asdict
import math
import unittest

from footprint_sketcher.model import AxisCalibration, Calibration, Pad, Sketch


class AxisCalibrationTests(unittest.TestCase):
    def make_calibration(self):
        return AxisCalibration((10, 100), (210, 100), 10, (300, 30), (300, 430), 4)

    def make_sketch(self):
        sketch = Sketch()
        sketch.set_axis_calibration((10, 100), (210, 100), 10, (300, 30), (300, 430), 4)
        sketch.set_origin((70, 60))
        return sketch

    def assertPointAlmostEqual(self, actual, expected):
        self.assertAlmostEqual(actual[0], expected[0])
        self.assertAlmostEqual(actual[1], expected[1])

    def test_nonuniform_scales_transform_each_axis_independently(self):
        calibration = self.make_calibration()
        self.assertEqual(calibration.mm_per_pixel_x, 0.05)
        self.assertEqual(calibration.mm_per_pixel_y, 0.01)
        self.assertFalse(hasattr(calibration, "mm_per_pixel"))
        sketch = self.make_sketch()
        self.assertPointAlmostEqual(sketch.image_to_mm((110, 260)), (2, 2))
        self.assertPointAlmostEqual(sketch.mm_to_image((2, 2)), (110, 260))

    def test_reversed_pairs_do_not_reverse_coordinate_axes(self):
        calibration = AxisCalibration((210, 100), (10, 100), 10, (300, 430), (300, 30), 4)
        self.assertEqual(calibration.mm_per_pixel_x, 0.05)
        self.assertEqual(calibration.mm_per_pixel_y, 0.01)
        sketch = Sketch()
        sketch.calibration = calibration
        sketch.set_origin((0, 0))
        self.assertPointAlmostEqual(sketch.image_to_mm((100, 100)), (5, 1))

    def test_pair_locations_do_not_couple_axis_scales(self):
        calibration = AxisCalibration((-150, -900), (50, -900), 10, (-700, -250), (-700, 150), 4)
        expected = self.make_calibration()
        self.assertEqual(calibration.mm_per_pixel_x, expected.mm_per_pixel_x)
        self.assertEqual(calibration.mm_per_pixel_y, expected.mm_per_pixel_y)

    def test_image_and_millimeter_roundtrips(self):
        sketch = self.make_sketch()
        for image_point in ((-20.5, 0.25), (500, -300), (70, 60)):
            with self.subTest(image_point=image_point):
                self.assertPointAlmostEqual(sketch.mm_to_image(sketch.image_to_mm(image_point)), image_point)
        for mm_point in ((-1.25, 2.75), (0, 0), (10, -5)):
            with self.subTest(mm_point=mm_point):
                self.assertPointAlmostEqual(sketch.image_to_mm(sketch.mm_to_image(mm_point)), mm_point)

    def test_origin_move_preserves_pad_image_placement_and_dimensions(self):
        sketch = self.make_sketch()
        original = Pad("8", 2, 2, 1.55, 0.6, 30, 0.05)
        sketch.add_pad(original)
        image_position = sketch.mm_to_image((original.x, original.y))
        sketch.set_origin((90, 160))
        moved = sketch.pads[0]
        self.assertPointAlmostEqual((moved.x, moved.y), (1, 1))
        self.assertPointAlmostEqual(sketch.mm_to_image((moved.x, moved.y)), image_position)
        self.assertEqual((moved.number, moved.width, moved.height, moved.angle, moved.radius),
                         (original.number, original.width, original.height, original.angle, original.radius))
        self.assertEqual((original.x, original.y), (2, 2))

    def test_invalid_axis_calibration_keeps_previous_calibration_origin_and_pads(self):
        sketch = self.make_sketch()
        sketch.add_pad(Pad("1", 1, 2, 1, 1))
        previous_calibration = sketch.calibration
        previous_origin = sketch.origin
        previous_pads = sketch.pads[:]
        valid = [(10, 100), (210, 100), 10, (300, 30), (300, 430), 4]
        changes = (
            (0, (math.nan, 100)), (1, (math.inf, 100)),
            (1, (210, 101)), (1, (10, 100)),
            (2, 0), (2, -1), (2, math.nan), (2, math.inf),
            (3, (300, math.nan)), (4, (301, 430)), (4, (300, 30)),
            (5, 0), (5, -1), (5, math.nan), (5, math.inf),
        )
        for index, value in changes:
            arguments = valid[:]
            arguments[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                sketch.set_axis_calibration(*arguments)
            self.assertIs(sketch.calibration, previous_calibration)
            self.assertEqual(sketch.origin, previous_origin)
            self.assertEqual(sketch.pads, previous_pads)

    def test_unrepresentable_scales_are_rejected(self):
        for a, b, distance in (
            ((0, 0), (1e308, 0), 5e-324),
            ((0, 0), (5e-324, 0), 1),
            ((-1e308, 0), (1e308, 0), 1),
        ):
            with self.subTest(a=a, b=b, distance=distance), self.assertRaises(ValueError):
                AxisCalibration(a, b, distance, (0, 0), (0, 10), 1)

    def test_legacy_calibration_serialization_and_uniform_transform_are_preserved(self):
        calibration = Calibration((0, 0), (3, 4), 10)
        self.assertEqual(asdict(calibration), {"a": (0, 0), "b": (3, 4), "distance": 10, "mm_per_pixel": 2})
        self.assertEqual((calibration.mm_per_pixel_x, calibration.mm_per_pixel_y), (2, 2))
        sketch = Sketch()
        sketch.set_calibration((0, 0), (3, 4), 10)
        sketch.set_origin((1, 2))
        self.assertPointAlmostEqual(sketch.image_to_mm((3, 5)), (4, 6))
        self.assertPointAlmostEqual(sketch.mm_to_image((4, 6)), (3, 5))
        sketch.set_axis_calibration((0, 0), (10, 0), 1, (0, 0), (0, 20), 1)
        sketch.set_calibration((0, 0), (3, 4), 10)
        self.assertIsInstance(sketch.calibration, Calibration)
        self.assertPointAlmostEqual(sketch.image_to_mm((3, 5)), (4, 6))


if __name__ == "__main__":
    unittest.main()
