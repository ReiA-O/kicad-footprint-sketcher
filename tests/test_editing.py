"""Group edits and undo preserve source pads and complete calibration state."""

from dataclasses import FrozenInstanceError, asdict, replace
import math
import unittest

from shapely import Polygon
from shapely.affinity import affine_transform, rotate, scale, translate

from footprint_sketcher.compound import (
    CustomPad, fillet_pad, local_to_world, merge_pads, world_outline, world_paste_shapes, world_paste_sources,
)
from footprint_sketcher.editing import (
    Snapshot, UndoHistory, align_centers, copy_pads_between_points, delete_selected_pads,
    distribute_centers, duplicate_pads, line_symmetric_copies, mirrored_copies,
    point_symmetric_copies,
)
from footprint_sketcher.graphics import Graphic, arc_points, world_vertices
from footprint_sketcher.model import AxisCalibration, Calibration, Pad, Sketch


class SnapshotTests(unittest.TestCase):
    def make_sketch(self):
        sketch = Sketch()
        sketch.set_axis_calibration((0, 0), (100, 0), 10, (0, 0), (0, 100), 2)
        sketch.set_origin((10, 20))
        sketch.add_pad(Pad("1", 1, 2, 1.6, 1.2, 30, 0.25))
        return sketch

    def test_snapshot_captures_immutable_calibration_origin_and_pads(self):
        sketch = self.make_sketch()
        snapshot = Snapshot.capture(sketch)
        calibration = sketch.calibration
        sketch.set_calibration((0, 0), (200, 0), 10)
        sketch.set_origin((30, 40))
        sketch.pads.append(Pad("2", 3, 4, 1, 1))
        self.assertIs(snapshot.calibration, calibration)
        self.assertIsInstance(snapshot.calibration, AxisCalibration)
        self.assertEqual(snapshot.origin, (10, 20))
        self.assertEqual(len(snapshot.pads), 1)
        self.assertEqual((snapshot.pads[0].x, snapshot.pads[0].y), (1, 2))
        with self.assertRaises(FrozenInstanceError):
            snapshot.origin = (0, 0)

    def test_undo_restores_entire_state_and_preserves_pad_list_identity(self):
        sketch = self.make_sketch()
        original = Snapshot.capture(sketch)
        history = UndoHistory()
        history.checkpoint(original)
        sketch.set_calibration((0, 0), (200, 0), 10)
        sketch.set_origin((30, 40))
        sketch.pads[0] = replace(sketch.pads[0], width=2)
        pads_list = sketch.pads
        previous = history.undo(Snapshot.capture(sketch))
        self.assertEqual(previous, original)
        previous.restore(sketch)
        self.assertEqual(Snapshot.capture(sketch), original)
        self.assertIs(sketch.pads, pads_list)
        self.assertFalse(history.can_undo)
        self.assertIsNone(history.undo(Snapshot.capture(sketch)))

    def test_duplicate_checkpoints_do_not_add_an_extra_undo_step(self):
        sketch = self.make_sketch()
        before = Snapshot.capture(sketch)
        history = UndoHistory()
        self.assertTrue(history.checkpoint(before))
        self.assertFalse(history.checkpoint(before))
        sketch.pads[0] = replace(sketch.pads[0], x=10)
        self.assertEqual(history.undo(Snapshot.capture(sketch)), before)
        self.assertFalse(history.can_undo)

    def test_undo_skips_current_state_and_clear_forgets_history(self):
        sketch = self.make_sketch()
        before = Snapshot.capture(sketch)
        sketch.pads[0] = replace(sketch.pads[0], x=10)
        current = Snapshot.capture(sketch)
        history = UndoHistory()
        history.checkpoint(before)
        history.checkpoint(current)
        self.assertEqual(history.undo(current), before)
        history.checkpoint(before)
        history.clear()
        self.assertFalse(history.can_undo)
        self.assertIsNone(history.undo(current))

    def test_empty_and_uniform_calibration_states_can_be_restored(self):
        sketch = self.make_sketch()
        empty = Snapshot.capture(Sketch())
        empty.restore(sketch)
        self.assertEqual((sketch.calibration, sketch.origin, sketch.pads), (None, None, []))
        sketch.set_calibration((0, 0), (3, 4), 10)
        uniform = Snapshot.capture(sketch)
        sketch.set_axis_calibration((0, 0), (100, 0), 10, (0, 0), (0, 100), 2)
        uniform.restore(sketch)
        self.assertIsInstance(sketch.calibration, Calibration)
        self.assertEqual(sketch.calibration.mm_per_pixel, 2)


class GroupEditingTests(unittest.TestCase):
    def make_pads(self):
        return [
            Pad("1", 6, 3, 1.6, 1.2, 30, 0.25),
            Pad("2", -7, 100, 2, 1, 90, 0.1),
            Pad("4", 10, 9, 1, 2, -30, 0.2),
            Pad("A", 0, 0, 1.5, 0.6, 0, 0.05),
        ]

    def assert_attributes_preserved(self, old, new, changed_axes):
        for attribute in ("number", "width", "height", "angle", "radius", "x", "y"):
            if attribute not in changed_axes:
                self.assertEqual(getattr(old, attribute), getattr(new, attribute))

    def test_copy_preserves_relative_centers_dimensions_and_immutable_clipboard(self):
        clipboard = (Pad("1", -2, 1, 1.6, 1.2, 30, 0.25), Pad("2", 2, 3, 2, 1.2, -30, 0.1))
        existing = self.make_pads()
        before_clipboard = tuple(asdict(pad) for pad in clipboard)
        before_existing = [asdict(pad) for pad in existing]
        copied = duplicate_pads(clipboard, existing, (10, 20))
        self.assertEqual([pad.number for pad in copied], ["3", "5"])
        self.assertEqual([(pad.x, pad.y) for pad in copied], [(8, 19), (12, 21)])
        for source, result in zip(clipboard, copied):
            self.assert_attributes_preserved(source, result, {"number", "x", "y"})
            self.assertIsNot(source, result)
        self.assertEqual(tuple(asdict(pad) for pad in clipboard), before_clipboard)
        self.assertEqual([asdict(pad) for pad in existing], before_existing)

    def test_copy_can_explicitly_preserve_numbers(self):
        clipboard = (Pad("1", 0, 0, 1, 1), Pad("1", 2, 0, 1, 1))
        copied = duplicate_pads(clipboard, self.make_pads(), (0, 0), keep_numbers=True)
        self.assertEqual([pad.number for pad in copied], ["1", "1"])
        self.assertEqual([(pad.x, pad.y) for pad in copied], [(-1, 0), (1, 0)])

    def test_invalid_copy_is_atomic(self):
        clipboard = (Pad("1", -2, 0, 1, 1), Pad("2", 2, 0, 1, 1))
        existing = self.make_pads()
        before = [asdict(pad) for pad in existing]
        for center in ((math.nan, 0), (0, math.inf), (2147, 0)):
            with self.subTest(center=center), self.assertRaises(ValueError):
                duplicate_pads(clipboard, existing, center)
            self.assertEqual([asdict(pad) for pad in existing], before)
        with self.assertRaises(ValueError):
            duplicate_pads((), existing, (0, 0))

    def test_mean_alignment_changes_only_selected_coordinate(self):
        pads = self.make_pads()
        before = [asdict(pad) for pad in pads]
        aligned = align_centers(pads, [0, 2], "x")
        self.assertEqual((aligned[0].x, aligned[2].x), (8, 8))
        self.assertIs(aligned[1], pads[1])
        self.assertIs(aligned[3], pads[3])
        for old, new in zip(pads, aligned):
            self.assert_attributes_preserved(old, new, {"x"})
        self.assertEqual([asdict(pad) for pad in pads], before)

    def test_last_alignment_uses_selection_order_not_index_or_position_order(self):
        pads = self.make_pads()
        aligned = align_centers(pads, [2, 0, 3], "y", reference="last")
        self.assertEqual([aligned[index].y for index in (2, 0, 3)], [0, 0, 0])
        self.assertIs(aligned[1], pads[1])
        for old, new in zip(pads, aligned):
            self.assert_attributes_preserved(old, new, {"y"})

    def test_distribution_sorts_centers_and_fixes_both_extremes(self):
        pads = self.make_pads()
        before = [asdict(pad) for pad in pads]
        distributed = distribute_centers(pads, [2, 0, 3], "x")
        self.assertEqual([distributed[index].x for index in (3, 0, 2)], [0, 5, 10])
        for index in (1, 2, 3):
            self.assertIs(distributed[index], pads[index])
        for old, new in zip(pads, distributed):
            self.assert_attributes_preserved(old, new, {"x"})
        self.assertEqual([asdict(pad) for pad in pads], before)

    def test_vertical_distribution_retains_horizontal_positions(self):
        pads = self.make_pads()
        distributed = distribute_centers(pads, [3, 0, 2], "y")
        self.assertEqual([distributed[index].y for index in (3, 0, 2)], [0, 4.5, 9])
        self.assertEqual([pad.x for pad in distributed], [pad.x for pad in pads])

    def test_invalid_group_operations_do_not_mutate_originals(self):
        pads = self.make_pads()
        before = [asdict(pad) for pad in pads]
        invalid_operations = (
            lambda: align_centers(pads, [0], "x"),
            lambda: align_centers(pads, [0, 0], "x"),
            lambda: align_centers(pads, [-1, 2], "x"),
            lambda: align_centers(pads, [0, 4], "x"),
            lambda: align_centers(pads, [True, 2], "x"),
            lambda: align_centers(pads, [0, 2], "z"),
            lambda: align_centers(pads, [0, 2], "x", reference="first"),
            lambda: distribute_centers(pads, [0, 2], "x"),
            lambda: distribute_centers(pads, [0, 2, 2], "x"),
            lambda: distribute_centers(pads, [0, 2, 3], "z"),
        )
        for operation in invalid_operations:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
            self.assertEqual([asdict(pad) for pad in pads], before)


class _CompoundFixture:
    def reflect_point(self, point, axis):
        x, y = point
        return (x, -y) if axis == "x" else (-x, y)

    def assert_reflected_geometry(self, source, reflected, axis):
        original_polygon = Polygon(world_outline(source))
        expected = scale(original_polygon, xfact=1 if axis == "x" else -1,
                         yfact=-1 if axis == "x" else 1, origin=(0, 0))
        actual = Polygon(world_outline(reflected))
        self.assertLess(actual.hausdorff_distance(expected), 1e-10)
        self.assertLess(actual.symmetric_difference(expected).area, 1e-10)

    def make_custom(self):
        # A concave union with convex/concave fillets and a rotated roundrect
        # aperture inside its copper, so reflected Paste poses are exercised.
        pad = merge_pads((Pad("1", 0, 0, 4, 1), Pad("2", -1.5, 1, 3, 1, 90),
                          Pad("3", -1.5, 1, 0.4, 0.8, 35, 0.1)), "7")
        for target in ((2, -0.5), (-1, 0.5)):
            index = min(range(len(pad.corners)), key=lambda i: math.dist(
                local_to_world(pad, pad.corners[i]), target))
            self.assertLess(math.dist(local_to_world(pad, pad.corners[index]), target), 1e-9)
            pad = fillet_pad(pad, index, 0.2)
        return replace(pad, x=4, y=-6, angle=37)


class MirroredCopyTests(_CompoundFixture, unittest.TestCase):
    def test_both_origin_axes_reflect_rotated_rectangles_and_roundrects(self):
        sources = (Pad("8", 3, -4, 2, 1, 30), Pad("9", -7, 2, 1.6, 1.2, -65, 0.25))
        existing = [Pad(number, 0, 0, 1, 1) for number in ("1", "2", "4", "A")]
        original = tuple(asdict(pad) for pad in sources)
        original_existing = [asdict(pad) for pad in existing]
        for axis in ("x", "y"):
            with self.subTest(axis=axis):
                reflected = mirrored_copies(iter(sources), iter(existing), axis)
                self.assertEqual([pad.number for pad in reflected], ["3", "5"])
                for source, result in zip(sources, reflected):
                    self.assertIsNot(source, result)
                    self.assertEqual((result.x, result.y), self.reflect_point((source.x, source.y), axis))
                    self.assertEqual(result.angle, (-source.angle if axis == "x" else 180 - source.angle) % 360)
                    self.assertEqual((result.width, result.height, result.radius),
                                     (source.width, source.height, source.radius))
                    self.assert_reflected_geometry(source, result, axis)
        self.assertEqual(tuple(asdict(pad) for pad in sources), original)
        self.assertEqual([asdict(pad) for pad in existing], original_existing)

    def test_compound_reflection_preserves_fillet_edges_and_original_paste_openings(self):
        source = self.make_custom()
        original = asdict(source)
        self.assertTrue(source.curve_edges)
        self.assertEqual(len(source.corners), 4)
        for axis in ("x", "y"):
            with self.subTest(axis=axis):
                result = mirrored_copies((source,), (source,), axis)[0]
                self.assertIsInstance(result, CustomPad)
                self.assertEqual(result.number, "1")
                self.assertEqual(result.outline, tuple((x, -y) for x, y in source.outline))
                self.assertEqual(result.corners, tuple((x, -y) for x, y in source.corners))
                for name in ("curve_edges", "anchor_size", "width", "height", "radius",
                             "approximation_tolerance_mm"):
                    self.assertEqual(getattr(result, name), getattr(source, name))
                self.assert_reflected_geometry(source, result, axis)
                for before, after in zip(world_outline(source), world_outline(result)):
                    self.assertLess(math.dist(after, self.reflect_point(before, axis)), 1e-10)
                original_paste, reflected_paste = world_paste_shapes(source), world_paste_shapes(result)
                self.assertEqual(len(original_paste), len(reflected_paste))
                for before, after in zip(original_paste, reflected_paste):
                    self.assertEqual((after.number, after.width, after.height, after.radius),
                                     (before.number, before.width, before.height, before.radius))
                    self.assertLess(math.dist((after.x, after.y), self.reflect_point((before.x, before.y), axis)), 1e-10)
                    expected_angle = (-before.angle if axis == "x" else 180 - before.angle) % 360
                    self.assertAlmostEqual(after.angle % 360, expected_angle, places=10)
                    self.assert_reflected_geometry(before, after, axis)
        self.assertEqual(asdict(source), original)

    def test_reflected_compound_keeps_selected_corner_fillet_usable(self):
        source = self.make_custom()
        for axis in ("x", "y"):
            with self.subTest(axis=axis):
                reflected = mirrored_copies((source,), (), axis)[0]
                before = asdict(reflected)
                rounded = fillet_pad(reflected, 0, 0.05)
                self.assertEqual(len(rounded.corners), len(reflected.corners) - 1)
                self.assertEqual(asdict(reflected), before)
                for before_paste, after_paste in zip(world_paste_shapes(reflected), world_paste_shapes(rounded)):
                    self.assertLess(math.dist((before_paste.x, before_paste.y),
                                              (after_paste.x, after_paste.y)), 1e-10)
                    self.assertAlmostEqual(before_paste.angle, after_paste.angle, places=10)

    def test_mixed_group_numbers_are_unique_and_doubled_reflection_restores_geometry(self):
        sources = (self.make_custom(), Pad("7", -3, -4, 1.5, 0.6, 1e100, 0.1))
        existing = sources + (Pad("1", 0, 0, 1, 1), Pad("3", 0, 0, 1, 1))
        for axis in ("x", "y"):
            with self.subTest(axis=axis):
                first = mirrored_copies(sources, existing, axis)
                self.assertEqual([pad.number for pad in first], ["2", "4"])
                self.assertTrue(set(pad.number for pad in first).isdisjoint(pad.number for pad in existing))
                expected_angle = (-(sources[1].angle % 360) if axis == "x"
                                  else 180 - sources[1].angle % 360) % 360
                self.assertEqual(first[1].angle, expected_angle)
                second = mirrored_copies(first, existing + tuple(first), axis)
                for original, restored in zip(sources, second):
                    self.assertEqual((restored.x, restored.y), (original.x, original.y))
                    self.assertEqual(restored.angle, original.angle % 360)
                    original_polygon, restored_polygon = Polygon(world_outline(original)), Polygon(world_outline(restored))
                    self.assertLess(original_polygon.hausdorff_distance(restored_polygon), 1e-10)
                    if isinstance(original, CustomPad):
                        for name in ("outline", "corners", "curve_edges", "paste_shapes", "anchor_size"):
                            self.assertEqual(getattr(restored, name), getattr(original, name))

    def test_invalid_axis_empty_and_nonfinite_sources_are_atomic(self):
        source = Pad("1", 2, 3, 1.6, 1.2, 30, 0.25)
        existing = [source]
        before = asdict(source)
        for axis in ("z", "X", "", None):
            with self.subTest(axis=axis), self.assertRaises(ValueError):
                mirrored_copies((source,), existing, axis)
        with self.assertRaises(ValueError):
            mirrored_copies((), existing, "x")
        # Simulate invalid imported state; replace validates every returned Pad
        # before the group is handed to the caller for a single atomic append.
        for name in ("x", "y", "angle", "width", "height", "radius"):
            for value in (math.nan, math.inf):
                invalid = replace(source)
                object.__setattr__(invalid, name, value)
                for axis in ("x", "y"):
                    with self.subTest(name=name, value=value, axis=axis), self.assertRaises(ValueError):
                        mirrored_copies((source, invalid), existing, axis)
        self.assertEqual(asdict(source), before)
        self.assertEqual(existing, [source])


class ReferenceCopyAndDeletionTests(_CompoundFixture, unittest.TestCase):
    def make_sources(self):
        return (self.make_custom(), Pad("7", -3, 4, 1.6, 1.2, 35, 0.25))

    def assert_same_shape(self, actual, expected):
        actual = Polygon(world_outline(actual))
        self.assertLess(actual.hausdorff_distance(expected), 1e-10)
        self.assertLess(actual.symmetric_difference(expected).area, 1e-10)

    def test_reference_point_copy_translates_group_without_changing_attributes(self):
        sources = self.make_sources()
        existing = sources + (Pad("1", 0, 0, 1, 1), Pad("3", 0, 0, 1, 1))
        before = tuple(asdict(pad) for pad in existing)
        result = copy_pads_between_points(iter(sources), iter(existing), (1, -2), (9, 7))
        self.assertEqual([pad.number for pad in result], ["2", "4"])
        for source, copied in zip(sources, result):
            self.assertEqual((copied.x, copied.y), (source.x + 8, source.y + 9))
            self.assertEqual((copied.width, copied.height, copied.angle, copied.radius),
                             (source.width, source.height, source.angle, source.radius))
            self.assert_same_shape(copied, translate(Polygon(world_outline(source)), xoff=8, yoff=9))
            if isinstance(source, CustomPad):
                for field in ("outline", "corners", "paste_shapes", "curve_edges", "anchor_size"):
                    self.assertEqual(getattr(copied, field), getattr(source, field))
            for old, new in zip(world_paste_shapes(source), world_paste_shapes(copied)):
                self.assertLess(math.dist((new.x, new.y), (old.x + 8, old.y + 9)), 1e-10)
                self.assertEqual((new.width, new.height, new.angle, new.radius),
                                 (old.width, old.height, old.angle, old.radius))
        self.assertEqual((result[1].x - result[0].x, result[1].y - result[0].y),
                         (sources[1].x - sources[0].x, sources[1].y - sources[0].y))
        self.assertEqual(tuple(asdict(pad) for pad in existing), before)

    def test_point_symmetry_uses_requested_center_and_rotates_copper_and_paste(self):
        sources = self.make_sources()
        before = tuple(asdict(pad) for pad in sources)
        center = (2, -3)
        result = point_symmetric_copies(sources, sources, center)
        for source, copied in zip(sources, result):
            self.assertEqual((copied.x, copied.y), (4 - source.x, -6 - source.y))
            self.assertEqual(copied.angle, (source.angle % 360 + 180) % 360)
            self.assert_same_shape(copied, rotate(Polygon(world_outline(source)), 180, origin=center))
            if isinstance(source, CustomPad):
                for field in ("outline", "corners", "paste_shapes", "curve_edges", "anchor_size"):
                    self.assertEqual(getattr(copied, field), getattr(source, field))
            for old, new in zip(world_paste_shapes(source), world_paste_shapes(copied)):
                self.assertLess(math.dist((new.x, new.y), (4 - old.x, -6 - old.y)), 1e-10)
                self.assertEqual((new.width, new.height, new.radius), (old.width, old.height, old.radius))
                self.assertAlmostEqual(new.angle % 360, (old.angle % 360 + 180) % 360, places=10)
        self.assertEqual(tuple(asdict(pad) for pad in sources), before)

    def test_arbitrary_oblique_line_reflects_compound_fillets_paste_and_rotated_roundrect(self):
        sources = self.make_sources()
        before = tuple(asdict(pad) for pad in sources)
        # Independent reflection matrix for line (1,2) -> (5,5), whose unit
        # normal is (-3/5,4/5) and signed offset is 1 mm.
        matrix = [0.28, 0.96, 0.96, -0.28, -1.2, 1.6]
        result = line_symmetric_copies(sources, sources, (1, 2), (5, 5))
        for source, copied in zip(sources, result):
            expected_center = (0.28 * source.x + 0.96 * source.y - 1.2,
                               0.96 * source.x - 0.28 * source.y + 1.6)
            self.assertLess(math.dist((copied.x, copied.y), expected_center), 1e-10)
            expected_angle = (2 * math.degrees(math.atan2(-3, 4)) - source.angle % 360) % 360
            self.assertAlmostEqual(copied.angle, expected_angle, places=10)
            self.assertEqual((copied.width, copied.height, copied.radius),
                             (source.width, source.height, source.radius))
            self.assert_same_shape(copied, affine_transform(Polygon(world_outline(source)), matrix))
            if isinstance(source, CustomPad):
                self.assertEqual(copied.outline, tuple((x, -y) for x, y in source.outline))
                self.assertEqual(copied.corners, tuple((x, -y) for x, y in source.corners))
                self.assertEqual(copied.curve_edges, source.curve_edges)
                self.assertEqual(copied.anchor_size, source.anchor_size)
            for old, new in zip(world_paste_shapes(source), world_paste_shapes(copied)):
                self.assert_same_shape(new, affine_transform(Polygon(world_outline(old)), matrix))
                expected_number = old.number if isinstance(source, CustomPad) else copied.number
                self.assertEqual((new.number, new.width, new.height, new.radius),
                                 (expected_number, old.width, old.height, old.radius))
        self.assertEqual(tuple(asdict(pad) for pad in sources), before)

    def test_reversed_line_points_and_double_reflection_preserve_world_geometry(self):
        sources = self.make_sources()
        first = line_symmetric_copies(sources, sources, (1, 2), (5, 5))
        reversed_axis = line_symmetric_copies(sources, sources, (5, 5), (1, 2))
        twice = line_symmetric_copies(first, sources, (1, 2), (5, 5))
        for original, reflected, reverse, restored in zip(sources, first, reversed_axis, twice):
            self.assert_same_shape(reverse, Polygon(world_outline(reflected)))
            self.assertAlmostEqual(reverse.angle, reflected.angle, places=10)
            self.assert_same_shape(restored, Polygon(world_outline(original)))
            self.assertLess(math.dist((restored.x, restored.y), (original.x, original.y)), 1e-10)
            self.assertAlmostEqual(restored.angle, original.angle % 360, places=10)
            for old, new in zip(world_paste_shapes(original), world_paste_shapes(restored)):
                self.assert_same_shape(new, Polygon(world_outline(old)))

    def test_all_copy_variants_explicitly_support_keeping_numbers(self):
        sources = self.make_sources()
        operations = (
            lambda: copy_pads_between_points(sources, sources, (1, 2), (1, 2), keep_numbers=True),
            lambda: point_symmetric_copies(sources, sources, (1, 2), keep_numbers=True),
            lambda: line_symmetric_copies(sources, sources, (1, 2), (5, 5), keep_numbers=True),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.assertEqual([pad.number for pad in operation()], ["7", "7"])

    def test_axis_direction_does_not_depend_on_vector_length_even_at_float_limits(self):
        sources = self.make_sources()
        expected = line_symmetric_copies(sources, sources, (0, 0), (1, 1))
        for vector in ((5e-324, 5e-324), (1e-250, 1e-250), (1.7e308, 1.7e308)):
            with self.subTest(vector=vector):
                reflected = line_symmetric_copies(sources, sources, (0, 0), vector)
                for reference, copied in zip(expected, reflected):
                    self.assert_same_shape(copied, Polygon(world_outline(reference)))
                    self.assertEqual(copied.angle, reference.angle)

    def test_reflection_transforms_graphic_vertices_and_arc_sweep_without_changing_layer(self):
        sources = (
            Graphic("1", 3, -4, 4, 2, 27, graphic_kind="line", layer="B.SilkS",
                    vertices=((-0.5, -0.4), (0.3, 0.5))),
            Graphic("2", -3, 2, 3, 2, -17, graphic_kind="polygon", layer="F.Fab", filled=True,
                    vertices=((-0.5, -0.5), (0.5, -0.3), (0.2, 0.5))),
            Graphic("3", 2, 1, 2, 2, 35, graphic_kind="arc", layer="B.CrtYd",
                    vertices=((-0.5, 0), (0, -0.5), (0.5, 0))),
        )
        before = tuple(asdict(graphic) for graphic in sources)
        operations = (
            (lambda: mirrored_copies(sources, sources, "x"), lambda x, y: (x, -y)),
            (lambda: mirrored_copies(sources, sources, "y"), lambda x, y: (-x, y)),
            (lambda: line_symmetric_copies(sources, sources, (1, 2), (5, 5)),
             lambda x, y: (0.28 * x + 0.96 * y - 1.2, 0.96 * x - 0.28 * y + 1.6)),
        )
        for operation, transform in operations:
            for source, copied in zip(sources, operation()):
                self.assertIsInstance(copied, Graphic)
                self.assertEqual(copied.vertices, tuple((x, -y) for x, y in source.vertices))
                for field in ("graphic_kind", "layer", "layers", "stroke", "filled", "width", "height"):
                    self.assertEqual(getattr(copied, field), getattr(source, field))
                old_points = world_vertices(source, arc_points(source)) if source.graphic_kind == "arc" else world_vertices(source)
                new_points = world_vertices(copied, arc_points(copied)) if copied.graphic_kind == "arc" else world_vertices(copied)
                for old, new in zip(old_points, new_points):
                    self.assertLess(math.dist(new, transform(*old)), 1e-10)
        self.assertEqual(tuple(asdict(graphic) for graphic in sources), before)

    def test_copy_transforms_preserve_hole_back_layer_and_opening_metadata(self):
        source = Pad("5", 3, -2, 3, 2, 25, pad_type="thru_hole", pad_shape="oval",
                     layers=("*.Cu", "*.Mask"), drill_width=1.2, drill_height=0.8,
                     mask_margin=0.1, paste_margin=-0.05, paste_ratio=0.1,
                     preserved=('(thermal_bridge_angle 45)',))
        operations = (
            lambda: copy_pads_between_points((source,), (), (0, 0), (1, 2)),
            lambda: point_symmetric_copies((source,), (), (1, 2)),
            lambda: line_symmetric_copies((source,), (), (1, 2), (5, 5)),
            lambda: mirrored_copies((source,), (), "x"),
        )
        for operation in operations:
            copied = operation()[0]
            for field in ("pad_type", "pad_shape", "layers", "drill_width", "drill_height",
                          "mask_margin", "paste_margin", "paste_ratio", "preserved"):
                self.assertEqual(getattr(copied, field), getattr(source, field))

    def test_copy_apis_remove_output_uuids_but_keep_and_transform_raw_metadata_snapshots(self):
        preserved = ('(uuid "original")', ' ( tstamp "legacy")', '(clearance 0.2)', '(uuid_extra "keep")')
        custom = self.make_custom()
        custom = replace(custom, preserved=preserved,
                         paste_shapes=tuple(replace(pad, preserved=preserved) for pad in custom.paste_shapes),
                         paste_sources=tuple(replace(pad, preserved=preserved) for pad in custom.paste_sources))
        sources = (custom, Pad("2", 0, 0, 1, 1, preserved=preserved),
                   Graphic("3", 0, 0, 2, 2, preserved=preserved))
        before = tuple(asdict(pad) for pad in sources)
        operations = (
            lambda: duplicate_pads(sources, sources, (0, 0)),
            lambda: copy_pads_between_points(sources, sources, (1, 2), (3, 4)),
            lambda: point_symmetric_copies(sources, sources, (1, 2)),
            lambda: mirrored_copies(sources, sources, "x"),
            lambda: mirrored_copies(sources, sources, "y"),
            lambda: line_symmetric_copies(sources, sources, (1, 2), (5, 5)),
        )
        for operation in operations:
            for copied in operation():
                self.assertEqual(copied.preserved, preserved[2:])
                if isinstance(copied, CustomPad):
                    self.assertTrue(all(pad.preserved == preserved[2:] for pad in copied.paste_shapes))
                    self.assertTrue(all(pad.preserved == preserved for pad in copied.paste_sources))
        for axis in ("x", "y"):
            reflected = mirrored_copies((custom,), (), axis)[0]
            for original, copied in zip(world_paste_sources(custom), world_paste_sources(reflected)):
                self.assertLess(math.dist((copied.x, copied.y), self.reflect_point((original.x, original.y), axis)), 1e-10)
                self.assert_reflected_geometry(original, copied, axis)
        self.assertEqual(tuple(asdict(pad) for pad in sources), before)

    def test_invalid_points_axes_and_corrupt_sources_fail_without_changing_originals(self):
        sources = self.make_sources()
        before = tuple(asdict(pad) for pad in sources)
        operations = [
            lambda: copy_pads_between_points((), sources, (0, 0), (1, 2)),
            lambda: point_symmetric_copies((), sources, (0, 0)),
            lambda: line_symmetric_copies((), sources, (0, 0), (1, 2)),
            lambda: copy_pads_between_points(sources, sources, (0, 0), (3000, 0)),
            lambda: point_symmetric_copies(sources, sources, (3000, 0)),
            lambda: line_symmetric_copies(sources, sources, (3000, 0), (3000, 1)),
            lambda: line_symmetric_copies(sources, sources, (1, 2), (1, 2)),
            lambda: line_symmetric_copies(sources, sources, (-1e308, 0), (1e308, 0)),
        ]
        for bad in ((math.nan, 0), (0, math.inf)):
            operations.extend((
                lambda bad=bad: copy_pads_between_points(sources, sources, bad, (0, 0)),
                lambda bad=bad: copy_pads_between_points(sources, sources, (0, 0), bad),
                lambda bad=bad: point_symmetric_copies(sources, sources, bad),
                lambda bad=bad: line_symmetric_copies(sources, sources, bad, (1, 2)),
                lambda bad=bad: line_symmetric_copies(sources, sources, (1, 2), bad),
            ))
        corrupt = replace(sources[1])
        object.__setattr__(corrupt, "angle", math.nan)
        operations.extend((
            lambda: copy_pads_between_points((sources[0], corrupt), sources, (0, 0), (1, 2)),
            lambda: point_symmetric_copies((sources[0], corrupt), sources, (0, 0)),
            lambda: line_symmetric_copies((sources[0], corrupt), sources, (0, 0), (1, 2)),
        ))
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
            self.assertEqual(tuple(asdict(pad) for pad in sources), before)

    def test_deletion_preserves_unselected_objects_order_attributes_and_original_list(self):
        custom, rounded = self.make_sources()
        pads = [Pad("1", 1, 2, 1, 1), custom, Pad("1", 3, 4, 1, 1), rounded]
        before = [asdict(pad) for pad in pads]
        remaining = delete_selected_pads(pads, iter((2, 0)))
        self.assertEqual(remaining, [custom, rounded])
        self.assertIs(remaining[0], custom)
        self.assertIs(remaining[1], rounded)
        self.assertEqual([asdict(pad) for pad in pads], before)
        self.assertEqual(delete_selected_pads(pads, range(len(pads))), [])
        for indices in ((), (0, 0), (-1,), (4,), (True,), (1.5,)):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                delete_selected_pads(pads, indices)
            self.assertEqual([asdict(pad) for pad in pads], before)


if __name__ == "__main__":
    unittest.main()
