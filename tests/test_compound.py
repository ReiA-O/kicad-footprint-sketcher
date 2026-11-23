"""Compound copper geometry, isolated corner fillets, and retained apertures."""

from dataclasses import asdict, replace
import math
import unittest

from shapely import LineString, Point, Polygon, union_all

from footprint_sketcher.compound import (
    CURVE_TOLERANCE_MM, MAX_ARC_SEGMENTS, CustomPad, anchor_for_outline,
    connect_terminals, fillet_pad, hit_custom_pad, local_to_world, merge_pads, selectable_corners,
    split_paste_apertures, world_outline, world_paste_shapes, world_paste_sources, world_to_local,
)
from footprint_sketcher.graphics import Graphic
from footprint_sketcher.model import Pad, _render_footprint


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

    def test_custom_mask_margin_rejects_actual_empty_opening_without_changing_copper(self):
        original = self.l_pad()
        before = asdict(original)
        # Both values pass the parent's bounding-box check. Only the actual
        # concave copper outline identifies the point where the mask disappears.
        smaller = replace(original, mask_margin=-0.55)
        self.assertFalse(Polygon(world_outline(smaller)).buffer(smaller.mask_margin).is_empty)
        self.assertEqual(smaller.outline, original.outline)
        with self.assertRaisesRegex(ValueError, "Mask開口が消える"):
            replace(original, mask_margin=-0.6)
        self.assertEqual(asdict(original), before)

    def test_custom_without_mask_can_keep_latent_margin_but_enabling_mask_validates_it(self):
        original = self.l_pad()
        disabled = replace(original, layers=("F.Cu", "F.Paste"), mask_margin=-0.6)
        self.assertEqual(disabled.mask_margin, -0.6)
        with self.assertRaisesRegex(ValueError, "Mask開口が消える"):
            replace(disabled, layers=("F.Cu", "F.Paste", "F.Mask"))

    def test_custom_mask_erosion_can_keep_multiple_nonempty_opening_components(self):
        original = merge_pads([Pad("1", -1.5, 0, 2, 2), Pad("2", 1.5, 0, 2, 2),
                               Pad("3", 0, 0, 2, 0.4)], "L")
        reduced = replace(original, mask_margin=-0.3)
        mask = Polygon(world_outline(reduced)).buffer(reduced.mask_margin)
        self.assertEqual(mask.geom_type, "MultiPolygon")
        self.assertEqual(len(mask.geoms), 2)
        self.assertFalse(mask.is_empty)
        self.assertEqual(reduced.outline, original.outline)

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

    def test_circle_and_oval_use_curved_copper_and_have_no_sharp_corners(self):
        for shape, width, height in (("circle", 2, 2), ("oval", 4, 2), ("oval", 2, 4)):
            with self.subTest(shape=shape, width=width, height=height):
                source = Pad("1", 3, -2, width, height, 30, pad_shape=shape)
                merged = merge_pads((source,), "1")
                expected_area = width * height - (4 - math.pi) * (min(width, height) / 2) ** 2
                self.assertAlmostEqual(Polygon(world_outline(merged)).area, expected_area,
                                       delta=2 * CURVE_TOLERANCE_MM * math.pi * min(width, height))
                self.assertEqual(selectable_corners(source), ())
                self.assertEqual(merged.corners, ())
                self.assertTrue(merged.curve_edges)
        rect = Pad("1", 0, 0, 2, 2, radius=0.4, pad_shape="rect")
        self.assertEqual(len(selectable_corners(rect)), 4)
        self.assertEqual(Polygon(world_outline(rect)).area, 4)

    def test_back_side_merge_and_fillet_preserve_mask_layers_and_raw_paste_metadata(self):
        source = Pad("9", 3, -4, 4, 2, 30, layers=("B.Cu", "B.Paste", "B.Mask"),
                     mask_margin=0.08, paste_margin=-0.05, paste_ratio=-0.05,
                     preserved=('(uuid "original")', '(clearance 0.2)'))
        before = asdict(source)
        merged = merge_pads((source,), "9")
        self.assertEqual(merged.layers, source.layers)
        self.assertEqual(merged.mask_margin, source.mask_margin)
        self.assertTrue(merged.paste_applied)
        self.assertEqual((merged.paste_margin, merged.paste_ratio), (0, 0))
        raw = world_paste_sources(merged)[0]
        for attribute in ("number", "width", "height", "radius", "layers", "mask_margin",
                          "paste_margin", "paste_ratio", "preserved"):
            self.assertEqual(getattr(raw, attribute), getattr(source, attribute))
        self.assertPointAlmostEqual((raw.x, raw.y), (source.x, source.y))
        actual = world_paste_shapes(merged)[0]
        self.assertAlmostEqual(actual.width, 3.5)
        self.assertAlmostEqual(actual.height, 1.7)
        self.assertEqual(actual.layers, ("B.Paste",))
        self.assertEqual((actual.mask_margin, actual.paste_margin, actual.paste_ratio), (0, 0, 0))
        self.assertEqual(actual.preserved, ('(clearance 0.2)',))
        rounded = fillet_pad(merged, 0, 0.2)
        nested = merge_pads((rounded,), "10")
        for edited in (rounded, nested):
            self.assertEqual(edited.layers, source.layers)
            self.assertEqual(edited.mask_margin, source.mask_margin)
            self.assertPasteEqual(world_paste_shapes(edited), [actual])
            self.assertEqual(world_paste_sources(edited)[0].preserved, source.preserved)
        self.assertEqual(asdict(source), before)

    def test_paste_offsets_use_per_side_ratio_and_roundrect_radius_rules_once(self):
        cases = (
            (Pad("1", 0, 0, 4, 2, radius=0.3, paste_margin=-0.1), (3.8, 1.8, 0.2)),
            (Pad("1", 0, 0, 4, 2, radius=0.3, paste_ratio=-0.05), (3.6, 1.8, 0.27)),
            (Pad("1", 0, 0, 4, 2, paste_margin=0.1), (4.2, 2.2, 0.1)),
            (Pad("1", 0, 0, 2, 2, pad_shape="circle", paste_ratio=-0.1), (1.6, 1.6, 0)),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                first = merge_pads((source,), "1")
                second = merge_pads((first,), "2")
                for pad in (first, second):
                    aperture = world_paste_shapes(pad)[0]
                    for actual, value in zip((aperture.width, aperture.height, aperture.radius), expected):
                        self.assertAlmostEqual(actual, value, places=10)
                    self.assertEqual((aperture.paste_margin, aperture.paste_ratio), (0, 0))
                self.assertEqual(world_paste_sources(second)[0].paste_ratio, source.paste_ratio)

    def test_incompatible_layers_margins_graphics_and_holes_are_rejected_without_mutation(self):
        first = Pad("1", 0, 0, 2, 2)
        incompatible = (
            replace(first, layers=("B.Cu", "B.Paste", "B.Mask")),
            replace(first, layers=("F.Cu", "F.Mask")),
            replace(first, mask_margin=0.1),
            replace(first, pad_type="thru_hole", layers=("*.Cu", "*.Mask"), drill_width=1, drill_height=1),
            Pad("", 0, 0, 2, 2, pad_type="np_thru_hole", layers=("*.Cu", "*.Mask"), drill_width=1, drill_height=1),
            Graphic("G", 0, 0, 2, 2),
        )
        for other in incompatible:
            before = (asdict(first), asdict(other))
            with self.subTest(other=other), self.assertRaises(ValueError):
                merge_pads((first, other), "1")
            self.assertEqual((asdict(first), asdict(other)), before)
        for other in incompatible[3:]:
            with self.assertRaises(ValueError):
                fillet_pad(other, 0, 0.1)

    def test_connect_terminals_adds_only_copper_and_keeps_all_source_numbers_and_openings(self):
        sources = (
            Pad("1", -0.5, 0, 2, 1, layers=("B.Cu", "B.Paste", "B.Mask"),
                mask_margin=0.1, paste_ratio=-0.1, preserved=('(uuid "one")',)),
            Pad("2", 0.5, 0, 2, 1, layers=("B.Cu",), mask_margin=0.2,
                preserved=('(uuid "two")', '(clearance 0.2)')),
        )
        before = tuple(asdict(pad) for pad in sources)
        connected = connect_terminals(sources, "2")
        self.assertEqual(connected.number, "2")
        self.assertEqual(connected.layers, ("B.Cu",))
        self.assertEqual(connected.paste_shapes, ())
        self.assertEqual((connected.mask_margin, connected.paste_margin, connected.paste_ratio), (0, 0, 0))
        self.assertEqual(connected.preserved, ('(clearance 0.2)',))
        expected = union_all([Polygon(world_outline(pad)) for pad in sources])
        self.assertEqual(Polygon(world_outline(connected)).symmetric_difference(expected).area, 0)
        self.assertEqual([pad.number for pad in world_paste_sources(connected)], ["1", "2"])
        self.assertEqual(tuple(asdict(pad) for pad in sources), before)
        for invalid in (lambda: connect_terminals(sources, "3"),
                        lambda: connect_terminals((sources[0], replace(sources[1], x=5)), "1"),
                        lambda: connect_terminals((sources[0], replace(sources[1], layers=("F.Cu",))), "1")):
            with self.assertRaises(ValueError):
                invalid()
            self.assertEqual(tuple(asdict(pad) for pad in sources), before)

    def test_split_preserves_copper_metadata_and_exact_local_gaps_for_rotated_aperture(self):
        source = Pad("1", 0, 0, 4, 3, 35, 0.15,
                     layers=("B.Cu", "B.Paste", "B.Mask"), mask_margin=0.08)
        merged = merge_pads((source,), "1")
        merged = replace(merged, x=merged.x + 2, y=merged.y - 3, angle=25)
        before = asdict(merged)
        original_aperture = world_paste_shapes(merged)[0]
        divided = split_paste_apertures(merged, 2, 2, 0.2)
        self.assertEqual(len(divided.paste_shapes), 4)
        for attribute in ("outline", "corners", "curve_edges", "anchor_size", "x", "y", "angle",
                          "width", "height", "layers", "mask_margin", "paste_sources", "preserved"):
            self.assertEqual(getattr(divided, attribute), getattr(merged, attribute))
        centers = []
        source_polygon = Polygon(world_outline(original_aperture))
        for aperture in world_paste_shapes(divided):
            self.assertAlmostEqual(aperture.width, 1.9)
            self.assertAlmostEqual(aperture.height, 1.4)
            self.assertAlmostEqual(aperture.radius, 0.15)
            self.assertAlmostEqual(aperture.angle % 360, 60)
            self.assertEqual(aperture.layers, ("B.Paste",))
            centers.append(world_to_local(original_aperture, (aperture.x, aperture.y)))
            self.assertTrue(source_polygon.buffer(1e-9).covers(Polygon(world_outline(aperture))))
        for actual, expected in zip(centers, ((-1.05, -0.8), (1.05, -0.8), (-1.05, 0.8), (1.05, 0.8))):
            self.assertPointAlmostEqual(actual, expected)
        self.assertEqual(asdict(merged), before)

    def test_raw_aperture_flag_resolves_margin_once_and_keeps_source_snapshot(self):
        source = Pad("1", 0, 0, 4, 3, paste_margin=-0.1, paste_ratio=-0.05,
                     preserved=('(uuid "raw")',))
        merged = merge_pads((source,), "1")
        raw = replace(merged, paste_shapes=(source,), paste_sources=(source,), paste_applied=False)
        expected = world_paste_shapes(merged)[0]
        self.assertPasteEqual(world_paste_shapes(raw), [expected])
        divided = split_paste_apertures(raw, 2, 2, 0.2)
        self.assertTrue(divided.paste_applied)
        self.assertEqual(divided.paste_sources, (source,))
        self.assertAlmostEqual(divided.paste_shapes[0].width, 1.6)
        self.assertAlmostEqual(divided.paste_shapes[0].height, 1.15)
        self.assertTrue(all(aperture.preserved == () for aperture in divided.paste_shapes))
        with self.assertRaises(ValueError):
            replace(raw, paste_applied=True)

    def test_invalid_or_protruding_paste_split_is_atomic_instead_of_resizing(self):
        merged = merge_pads((Pad("1", 0, 0, 4, 2, radius=0.6),), "1")
        before = asdict(merged)
        invalid = ((0, 2, 0.2), (True, 2, 0.2), (1.5, 2, 0.2), (2, 0, 0.2),
                   (2, 2, 0), (2, 2, math.nan), (2, 2, math.inf), (2, 2, 3), (2, 2, 0.2))
        for columns, rows, gap in invalid:
            with self.subTest(columns=columns, rows=rows, gap=gap), self.assertRaises(ValueError):
                split_paste_apertures(merged, columns, rows, gap)
            self.assertEqual(asdict(merged), before)
        with self.assertRaisesRegex(ValueError, "はみ出す"):
            split_paste_apertures(merged, 2, 2, 0.2)
        without_paste = connect_terminals((Pad("1", 0, 0, 2, 2),), "1")
        with self.assertRaisesRegex(ValueError, "ありません"):
            split_paste_apertures(without_paste, 2, 2, 0.2)

    def test_parent_paste_adjustment_updates_export_once_and_survives_fillet(self):
        original = merge_pads((Pad("1", 0, 0, 4, 2),), "1")
        adjusted = replace(original, paste_margin=-0.1, paste_ratio=-0.05)
        self.assertEqual(adjusted.paste_shapes, original.paste_shapes)
        self.assertNotEqual(_render_footprint("test", (adjusted,)), _render_footprint("test", (original,)))
        for _ in range(3):
            aperture = world_paste_shapes(adjusted)[0]
            self.assertAlmostEqual(aperture.width, 3.4)
            self.assertAlmostEqual(aperture.height, 1.6)
            self.assertEqual((aperture.paste_margin, aperture.paste_ratio), (0, 0))
        rounded = fillet_pad(adjusted, 0, 0.2)
        self.assertEqual((rounded.paste_margin, rounded.paste_ratio), (-0.1, -0.05))
        self.assertPasteEqual(world_paste_shapes(rounded), world_paste_shapes(adjusted))
        self.assertEqual(rounded.paste_sources, adjusted.paste_sources)
        flattened = merge_pads((rounded,), "2")
        self.assertEqual((flattened.paste_margin, flattened.paste_ratio), (0, 0))
        self.assertPasteEqual(world_paste_shapes(flattened), world_paste_shapes(adjusted))

    def test_parent_paste_validation_checks_each_window_and_split_keeps_requested_gap(self):
        original = merge_pads((Pad("1", 0, 0, 4, 3),), "1")
        divided = split_paste_apertures(original, 2, 2, 0.2)
        before = asdict(divided)
        # Both would leave the copper bbox positive, but erase a smaller window.
        for margin, ratio in ((-0.8, 0), (-0.02, -0.49)):
            with self.subTest(margin=margin, ratio=ratio), self.assertRaises(ValueError):
                replace(divided, paste_margin=margin, paste_ratio=ratio)
            self.assertEqual(asdict(divided), before)
        adjusted = replace(original, paste_margin=-0.1)
        result = split_paste_apertures(adjusted, 2, 2, 0.2)
        self.assertEqual((result.paste_margin, result.paste_ratio), (0, 0))
        actual = world_paste_shapes(result)
        self.assertAlmostEqual(actual[0].width, 1.8)
        self.assertAlmostEqual(actual[0].height, 1.3)
        self.assertAlmostEqual(actual[1].x - actual[0].x - actual[0].width, 0.2)
        self.assertAlmostEqual(actual[2].y - actual[0].y - actual[0].height, 0.2)
        self.assertEqual(result.outline, original.outline)

    def test_regular_smd_roundrect_splits_directly_without_changing_world_copper_or_mask(self):
        source = Pad("7", 3, -2, 4, 3, 27, 0.2, layers=("B.Cu", "B.Paste", "B.Mask"),
                     mask_margin=0.0, paste_margin=-0.1, paste_ratio=-0.02, opening_explicit=True,
                     preserved=('(uuid "smd-seven")', '(clearance 0.2)'))
        before = asdict(source)
        result = split_paste_apertures(source, 2, 2, 0.2)
        self.assertIsInstance(result, CustomPad)
        self.assertEqual((result.number, result.layers, result.mask_margin, result.opening_explicit),
                         (source.number, source.layers, source.mask_margin, True))
        self.assertEqual(result.preserved, source.preserved)
        original_copper, actual_copper = Polygon(world_outline(source)), Polygon(world_outline(result))
        self.assertLess(actual_copper.hausdorff_distance(original_copper), 1e-10)
        self.assertLess(actual_copper.symmetric_difference(original_copper).area, 1e-10)
        self.assertEqual(len(result.paste_shapes), 4)
        original_aperture = world_paste_shapes(source)[0]
        original_opening = Polygon(world_outline(original_aperture))
        for aperture in world_paste_shapes(result):
            self.assertAlmostEqual(aperture.width, 1.72)
            self.assertAlmostEqual(aperture.height, 1.24)
            self.assertAlmostEqual(aperture.angle % 360, 27)
            self.assertTrue(original_opening.buffer(1e-9).covers(Polygon(world_outline(aperture))))
        raw = world_paste_sources(result)[0]
        for attribute in ("number", "layers", "width", "height", "radius", "mask_margin",
                          "paste_margin", "paste_ratio", "opening_explicit", "preserved"):
            self.assertEqual(getattr(raw, attribute), getattr(source, attribute))
        self.assertPointAlmostEqual((raw.x, raw.y), (source.x, source.y))
        self.assertEqual(asdict(source), before)

    def test_direct_split_rejects_holes_graphics_missing_paste_and_protrusions_atomically(self):
        candidates = (
            Pad("1", 0, 0, 2, 2, pad_type="thru_hole", layers=("*.Cu", "*.Mask"), drill_width=1, drill_height=1),
            Pad("", 0, 0, 2, 2, pad_type="np_thru_hole", layers=("*.Cu", "*.Mask"), drill_width=1, drill_height=1),
            Graphic("G", 0, 0, 2, 2),
            Pad("1", 0, 0, 4, 3, layers=("F.Cu", "F.Mask")),
            Pad("1", 0, 0, 4, 2, radius=0.6),
        )
        for source in candidates:
            before = asdict(source)
            with self.subTest(source=source), self.assertRaises(ValueError):
                split_paste_apertures(source, 2, 2, 0.2)
            self.assertEqual(asdict(source), before)

    def test_bridge_width_connects_separated_terminals_as_one_copper_piece_only(self):
        sources = (Pad("1", -3, 0, 2, 1, layers=("B.Cu", "B.Paste", "B.Mask")),
                   Pad("2", 3, 0, 2, 1, layers=("B.Cu", "B.Mask"), mask_margin=0.1))
        before = tuple(asdict(pad) for pad in sources)
        with self.assertRaises(ValueError):
            connect_terminals(sources, "2")
        connected = connect_terminals(sources, "2", bridge_width=0.3)
        polygon = Polygon(world_outline(connected))
        expected = union_all([*(Polygon(world_outline(pad)) for pad in sources),
                              LineString(((-3, 0), (3, 0))).buffer(0.15)])
        self.assertTrue(polygon.is_valid)
        self.assertEqual(len(polygon.interiors), 0)
        self.assertEqual(polygon.geom_type, "Polygon")
        self.assertLess(polygon.symmetric_difference(expected).area, 1e-10)
        self.assertAlmostEqual(polygon.area, 5.2)
        self.assertTrue(polygon.contains(Point(0, 0)))
        self.assertEqual((connected.number, connected.layers, connected.paste_shapes), ("2", ("B.Cu",), ()))
        self.assertEqual((connected.mask_margin, connected.paste_margin, connected.paste_ratio), (0, 0, 0))
        self.assertEqual([pad.number for pad in world_paste_sources(connected)], ["1", "2"])
        self.assertEqual(tuple(asdict(pad) for pad in sources), before)

    def test_bridge_path_follows_selection_order_and_invalid_widths_are_atomic(self):
        sources = (Pad("1", -2, 0, 1, 1), Pad("2", 2, 0, 1, 1), Pad("3", 2, 3, 1, 1))
        before = tuple(asdict(pad) for pad in sources)
        connected = connect_terminals(sources, "1", bridge_width=0.3)
        polygon = Polygon(world_outline(connected))
        self.assertTrue(polygon.contains(Point(0, 0)))
        self.assertTrue(polygon.contains(Point(2, 1.5)))
        self.assertFalse(polygon.covers(Point(0, 1.5)))
        for width in (-1, math.nan, math.inf, 1e100, 1e-8, None, "invalid", True):
            with self.subTest(width=width), self.assertRaises(ValueError):
                connect_terminals(sources, "1", bridge_width=width)
            self.assertEqual(tuple(asdict(pad) for pad in sources), before)
        for invalid in ((sources[0], replace(sources[1], layers=("B.Cu",))),
                        (sources[0], replace(sources[1], pad_type="thru_hole", drill_width=0.5, drill_height=0.5))):
            with self.assertRaises(ValueError):
                connect_terminals(invalid, "1", bridge_width=0.3)
        coincident = connect_terminals((sources[0], replace(sources[0], number="2")), "1", bridge_width=0.3)
        self.assertEqual(Polygon(world_outline(coincident)).area, 1)


if __name__ == "__main__":
    unittest.main()
