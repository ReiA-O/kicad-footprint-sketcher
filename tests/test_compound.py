"""Compound copper geometry, isolated corner fillets, and retained apertures."""

from dataclasses import asdict, replace
import math
import unittest

from shapely import LineString, Point, Polygon, union_all

from footprint_sketcher.compound import (
    CURVE_TOLERANCE_MM, MAX_ARC_SEGMENTS, CustomPad, anchor_for_outline,
    fillet_pad, hit_custom_pad, local_to_world, merge_pads, selectable_corners,
    world_outline, world_paste_shapes, world_to_local,
)
from footprint_sketcher.model import Pad


class CompoundTests(unittest.TestCase):
    def l_sources(self):
        return (Pad("1", 0, 0, 4, 1), Pad("2", -1.5, 1, 1, 3))

    def l_pad(self):
        return merge_pads(self.l_sources(), "7")

    def corner_at(self, pad, point):
        distances = [math.dist(local_to_world(pad, corner), point) for corner in selectable_corners(pad)]
        index = min(range(len(distances)), key=distances.__getitem__)
        self.assertLess(distances[index], 1e-9)
        return index

    def assertPointAlmostEqual(self, actual, expected):
        self.assertAlmostEqual(actual[0], expected[0], places=10)
        self.assertAlmostEqual(actual[1], expected[1], places=10)

    def assertPasteEqual(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected):
            self.assertEqual((a.number, a.width, a.height, a.radius), (b.number, b.width, b.height, b.radius))
            self.assertPointAlmostEqual((a.x, a.y), (b.x, b.y))
            self.assertAlmostEqual(a.angle % 360, b.angle % 360, places=10)

    def test_l_union_has_one_terminal_and_exact_rectangular_union(self):
        sources = self.l_sources()
        before = tuple(asdict(pad) for pad in sources)
        merged = merge_pads(sources, "7")
        self.assertIsInstance(merged, CustomPad)
        self.assertEqual((merged.number, merged.width, merged.height, merged.radius), ("7", 4, 3, 0))
        self.assertEqual(len(merged.corners), 6)
        result = Polygon(world_outline(merged))
        expected = union_all([Polygon(world_outline(pad)) for pad in sources])
        self.assertEqual(result.symmetric_difference(expected).area, 0)
        self.assertEqual(result.area, 6)
        self.assertEqual(tuple(asdict(pad) for pad in sources), before)
        self.assertPasteEqual(world_paste_shapes(merged), list(sources))

    def test_anchor_is_in_copper_even_when_bounding_box_center_is_outside(self):
        merged = self.l_pad()
        polygon = Polygon(world_outline(merged))
        self.assertFalse(polygon.covers(Point(0, 1)))
        self.assertTrue(polygon.contains(Point(merged.x, merged.y)))
        w, h = merged.anchor_size
        base = Polygon([local_to_world(merged, point) for point in
                        ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))])
        self.assertTrue(polygon.covers(base))
        self.assertGreater(min(w, h), 0)

    def test_nested_union_flattens_original_apertures(self):
        merged = self.l_pad()
        third = Pad("3", 1.5, 1, 1, 1)
        combined = merge_pads([merged, third], "9")
        self.assertPasteEqual(world_paste_shapes(combined), [*self.l_sources(), third])
        self.assertTrue(all(type(aperture) is Pad for aperture in combined.paste_shapes))
        expected = union_all([Polygon(world_outline(merged)), Polygon(world_outline(third))])
        self.assertLess(Polygon(world_outline(combined)).symmetric_difference(expected).area, 1e-12)

    def test_roundrect_sampling_is_not_exposed_as_selectable_sharp_corners(self):
        source = Pad("R", 2, -3, 3, 1.5, 30, 0.4)
        merged = merge_pads([source], "R")
        self.assertEqual(merged.corners, ())
        self.assertTrue(merged.curve_edges)
        self.assertEqual(merged.approximation_tolerance_mm, CURVE_TOLERANCE_MM)
        self.assertPasteEqual(world_paste_shapes(merged), [source])
        with self.assertRaises(ValueError):
            fillet_pad(source, 0, 0.1)

    def test_convex_fillet_changes_only_one_corner_and_keeps_paste(self):
        merged = self.l_pad()
        before = asdict(merged)
        target = (2, -0.5)
        index = self.corner_at(merged, target)
        result = fillet_pad(merged, index, 0.2)
        expected_area = 6 - 0.2 ** 2 * (1 - math.pi / 4)
        self.assertAlmostEqual(Polygon(world_outline(result)).area, expected_area,
                               delta=2 * CURVE_TOLERANCE_MM * math.pi * 0.2)
        self.assertEqual(len(result.corners), 5)
        self.assertEqual(result.number, merged.number)
        self.assertGreater(len(result.curve_edges), 0)
        self.assertLessEqual(len(result.curve_edges), MAX_ARC_SEGMENTS)
        for corner in merged.corners:
            world = local_to_world(merged, corner)
            if world != target:
                self.corner_at(result, world)
        self.assertPasteEqual(world_paste_shapes(result), list(self.l_sources()))
        self.assertEqual(asdict(merged), before)

    def test_concave_fillet_adds_copper_and_preserves_other_edges(self):
        merged = self.l_pad()
        target = (-1, 0.5)
        index = self.corner_at(merged, target)
        result = fillet_pad(merged, index, 0.2)
        polygon = Polygon(world_outline(result))
        expected_area = 6 + 0.2 ** 2 * (1 - math.pi / 4)
        self.assertAlmostEqual(polygon.area, expected_area,
                               delta=2 * CURVE_TOLERANCE_MM * math.pi * 0.2)
        vertices = world_outline(merged)
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            if target not in (a, b):
                self.assertLess(LineString((a, b)).difference(polygon.boundary.buffer(1e-10)).length, 1e-9)
        self.assertPasteEqual(world_paste_shapes(result), list(self.l_sources()))

    def test_sampled_arc_respects_declared_sagitta_bound(self):
        original = self.l_pad()
        result = fillet_pad(original, self.corner_at(original, (2, -0.5)), 0.2)
        vertices = world_outline(result)
        center, radius = (1.8, -0.3), 0.2
        for index in result.curve_edges:
            a, b = vertices[index], vertices[(index + 1) % len(vertices)]
            self.assertAlmostEqual(math.dist(a, center), radius, places=10)
            self.assertAlmostEqual(math.dist(b, center), radius, places=10)
            middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            self.assertLessEqual(radius - math.dist(middle, center), CURVE_TOLERANCE_MM + 1e-12)

    def test_clockwise_and_counterclockwise_fillet_have_same_geometry(self):
        merged = self.l_pad()
        reverse = replace(merged, outline=tuple(reversed(merged.outline)), corners=tuple(reversed(merged.corners)))
        for target in ((2, -0.5), (-1, 0.5)):
            with self.subTest(target=target):
                forward = fillet_pad(merged, self.corner_at(merged, target), 0.2)
                backward = fillet_pad(reverse, self.corner_at(reverse, target), 0.2)
                self.assertLess(Polygon(world_outline(forward)).symmetric_difference(
                    Polygon(world_outline(backward))).area, 1e-10)

    def test_adjacent_fillet_uses_remaining_straight_edge_length(self):
        merged = self.l_pad()
        first = fillet_pad(merged, self.corner_at(merged, (2, -0.5)), 0.2)
        before = asdict(first)
        with self.assertRaises(ValueError):
            fillet_pad(first, self.corner_at(first, (2, 0.5)), 0.9)
        self.assertEqual(asdict(first), before)
        second = fillet_pad(first, self.corner_at(first, (2, 0.5)), 0.2)
        self.assertEqual(len(second.corners), 4)
        self.assertPasteEqual(world_paste_shapes(second), list(self.l_sources()))

    def test_anchor_rebases_when_fillet_removes_previous_connection_point(self):
        outline = ((-0.01, -0.01), (1.99, -0.01), (1.99, 1.99), (-0.01, 1.99))
        aperture = Pad("1", 0.99, 0.99, 2, 2)
        original = CustomPad("1", 0, 0, 2, 2, outline=outline, corners=outline,
                             paste_shapes=(aperture,), anchor_size=(0.001, 0.001))
        result = fillet_pad(original, 0, 0.5)
        self.assertFalse(hit_custom_pad(result, (0, 0)))
        self.assertTrue(hit_custom_pad(result, (result.x, result.y)))
        self.assertNotEqual((result.x, result.y), (0, 0))
        for corner in ((1.99, -0.01), (1.99, 1.99), (-0.01, 1.99)):
            self.corner_at(result, corner)
        self.assertPasteEqual(world_paste_shapes(result), [aperture])

    def test_move_rotate_and_number_replace_keep_compound_data_and_transform_apertures(self):
        original = self.l_pad()
        moved = replace(original, number="99", x=original.x + 5, y=original.y - 2, angle=90)
        self.assertEqual(moved.outline, original.outline)
        self.assertEqual(moved.paste_shapes, original.paste_shapes)
        self.assertEqual(moved.corners, original.corners)
        for aperture, transformed in zip(original.paste_shapes, world_paste_shapes(moved)):
            self.assertPointAlmostEqual((transformed.x, transformed.y),
                                        (moved.x + aperture.y, moved.y - aperture.x))
            self.assertEqual(transformed.angle % 360, (aperture.angle + 90) % 360)
        self.assertEqual(moved.number, "99")

    def test_local_world_roundtrip_and_actual_concave_hit_testing(self):
        moved = replace(self.l_pad(), angle=30)
        for point in ((0, 0), (0.25, -0.75), (1.2, 0.4)):
            self.assertPointAlmostEqual(world_to_local(moved, local_to_world(moved, point)), point)
        merged = self.l_pad()
        self.assertTrue(hit_custom_pad(merged, (-1.5, 2)))
        self.assertTrue(hit_custom_pad(merged, (1, 0)))
        self.assertFalse(hit_custom_pad(merged, (1, 2)))

    def test_disconnected_point_touching_and_holed_unions_are_rejected_atomically(self):
        groups = (
            (Pad("1", 0, 0, 1, 1), Pad("2", 3, 0, 1, 1)),
            (Pad("1", 0, 0, 1, 1), Pad("2", 1, 1, 1, 1)),
            (Pad("1", 0, -1.5, 4, 1), Pad("2", 0, 1.5, 4, 1),
             Pad("3", -1.5, 0, 1, 2), Pad("4", 1.5, 0, 1, 2)),
        )
        for pads in groups:
            before = tuple(asdict(pad) for pad in pads)
            with self.subTest(pads=pads), self.assertRaises(ValueError):
                merge_pads(pads, "1")
            self.assertEqual(tuple(asdict(pad) for pad in pads), before)

    def test_invalid_corner_radius_and_excessive_radius_are_rejected_atomically(self):
        merged = self.l_pad()
        before = asdict(merged)
        for radius in (0, -1, math.nan, math.inf, 1e100, 1e-300):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                fillet_pad(merged, 0, radius)
            self.assertEqual(asdict(merged), before)
        for index in (-1, len(merged.corners), True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                fillet_pad(merged, index, 0.1)

    def test_custom_fillet_cannot_bypass_existing_nonzero_radius_minimum(self):
        original = self.l_pad()
        before = asdict(original)
        for radius in (1e-8, 4e-7):
            with self.subTest(radius=radius), self.assertRaisesRegex(ValueError, "KiCad"):
                fillet_pad(original, self.corner_at(original, (2, -0.5)), radius)
        self.assertEqual(asdict(original), before)

    def test_unrepresentable_dimensions_and_invalid_custom_geometry_are_rejected(self):
        with self.assertRaises(ValueError):
            merge_pads([Pad("1", -1000, 0, 2000, 1), Pad("2", 1000, 0, 2000, 1)], "1")
        with self.assertRaises(ValueError):
            merge_pads([], "1")
        with self.assertRaises(ValueError):
            replace(self.l_pad(), width=10)
        with self.assertRaises(ValueError):
            replace(self.l_pad(), radius=0.1)
        with self.assertRaises(ValueError):
            anchor_for_outline(((0, 0), (1, 1), (0, 1), (1, 0)))


if __name__ == "__main__":
    unittest.main()
