"""Persistent constraint solving, editing drivers, rank, and atomic failures."""

from dataclasses import asdict
import json
import math
import unittest

from footprint_sketcher.compound import merge_pads
from footprint_sketcher.constraints import Constraint, FIELDS, solve_constraints
from footprint_sketcher.graphics import Graphic, graphic_from_points, world_vertices
from footprint_sketcher.model import Pad


class ConstraintTests(unittest.TestCase):
    def assertPoint(self, pad, x, y):
        self.assertAlmostEqual(pad.x, x, places=8)
        self.assertAlmostEqual(pad.y, y, places=8)

    def fixed(self, pad, index=0, name="fixed"):
        return Constraint(name, "fixed", (index,), values=tuple(getattr(pad, field) for field in FIELDS))

    def test_line_symmetry_and_equal_size_follow_position_and_size_drivers(self):
        pads = [Pad("1", -2, -1, 1, 0.8, radius=0.1), Pad("2", -2, 1, 1, 0.8, radius=0.1)]
        before = [asdict(pad) for pad in pads]
        constraints = [Constraint("mirror", "symmetry_x", (0, 1), point=(0, 0.5)),
                       Constraint("size", "equal_size", (0, 1))]
        result = solve_constraints(pads, constraints, {0: {"x": 3, "y": -2, "width": 2, "height": 1.2}})
        self.assertPoint(result.pads[0], 3, -2)
        self.assertPoint(result.pads[1], 3, 3)
        for original, solved in zip(pads, result.pads):
            self.assertAlmostEqual(solved.width, 2)
            self.assertAlmostEqual(solved.height, 1.2)
            self.assertEqual((solved.number, solved.angle, solved.radius),
                             (original.number, original.angle, original.radius))
        self.assertEqual([asdict(pad) for pad in pads], before)
        self.assertEqual((result.rank, result.dof, result.state), (4, 8, "underconstrained"))
        self.assertLessEqual(result.max_residual, 1e-9)

    def test_point_symmetry_and_vertical_axis_use_the_specified_point(self):
        pads = [Pad("1", -1, -3, 1, 1), Pad("2", 3, -1, 1, 1)]
        result = solve_constraints(pads, [Constraint("point", "symmetric", (0, 1), point=(1, -2))],
                                   {0: {"x": -2, "y": 4}})
        self.assertPoint(result.pads[1], 4, -8)
        result = solve_constraints(pads, [Constraint("vertical-axis", "symmetry_y", (0, 1), point=(2, 8))],
                                   {0: {"x": 1, "y": 2}})
        self.assertPoint(result.pads[1], 3, 2)

    def test_each_scalar_dimension_and_fixed_snapshot_fully_constrain_one_pad(self):
        pad = Pad("1", 0, 0, 1, 1)
        target = (2, -3, 2.4, 1.2, 30, 0.2)
        constraints = [Constraint(field, field, (0,), value=value) for field, value in zip(FIELDS, target)]
        result = solve_constraints([pad], constraints)
        for field, value in zip(FIELDS, target):
            self.assertAlmostEqual(getattr(result.pads[0], field), value)
        self.assertEqual((result.rank, result.dof, result.state), (6, 0, "fully_constrained"))
        fixed = self.fixed(result.pads[0])
        self.assertEqual(solve_constraints([result.pads[0]], [fixed]).pads, result.pads)
        with self.assertRaisesRegex(ValueError, "fixed"):
            solve_constraints(result.pads, [fixed], {0: {"x": 9}})

    def test_angles_are_periodic_and_unedited_records_keep_their_original_angle(self):
        pad = Pad("1", 0, 0, 1, 1, 350)
        result = solve_constraints([pad], [Constraint("angle", "angle", (0,), value=10)])
        self.assertAlmostEqual(result.pads[0].angle, 10)
        self.assertEqual(solve_constraints([pad], []).pads[0], pad)
        equivalent = Constraint("angle", "angle", (0,), value=-10)
        self.assertEqual(solve_constraints([pad], [equivalent]).pads[0], pad)

    def test_distance_keeps_the_initial_direction_and_midpoint_nearby(self):
        pads = [Pad("1", 0, 0, 1, 1), Pad("2", 3, 4, 1, 1)]
        result = solve_constraints(pads, [Constraint("distance", "distance", (0, 1), value=10)])
        self.assertPoint(result.pads[0], -1.5, -2)
        self.assertPoint(result.pads[1], 4.5, 6)
        self.assertEqual((result.rank, result.dof), (1, 11))

    def test_coincident_distance_seed_respects_fixed_and_horizontal_vertical_relations(self):
        pads = [Pad("1", 0, 0, 1, 1), Pad("2", 0, 0, 1, 1)]
        for kind, expected in (("horizontal", (5, 0)), ("vertical", (0, 5))):
            with self.subTest(kind=kind):
                result = solve_constraints(pads, [self.fixed(pads[0]), Constraint("axis", kind, (0, 1)),
                                                  Constraint("distance", "distance", (0, 1), value=5)])
                self.assertPoint(result.pads[0], 0, 0)
                self.assertPoint(result.pads[1], *expected)

    def test_zero_distance_is_center_coincidence_with_two_independent_relations(self):
        pads = [Pad("1", 0, 0, 1, 1), Pad("2", 2, 4, 1, 1)]
        result = solve_constraints(pads, [Constraint("zero", "distance", (0, 1), value=0)])
        self.assertPoint(result.pads[0], 1, 2)
        self.assertPoint(result.pads[1], 1, 2)
        self.assertEqual(result.rank, 2)

    def test_signed_spacing_and_horizontal_relationship_follow_a_drag(self):
        pads = [Pad("1", 0, 0, 1, 1), Pad("2", 2, 0, 1, 1)]
        constraints = [Constraint("pitch", "spacing_x", (0, 1), value=-2),
                       Constraint("row", "horizontal", (0, 1))]
        result = solve_constraints(pads, constraints, {0: {"x": 5, "y": 3}})
        self.assertPoint(result.pads[1], 3, 3)
        result = solve_constraints(pads, [Constraint("pitch-y", "spacing_y", (0, 1), value=3)],
                                   {0: {"y": -1}})
        self.assertAlmostEqual(result.pads[1].y, 2)

    def test_two_distances_converge_to_the_nearby_circle_intersection(self):
        pads = [Pad("A", 0, 0, 1, 1), Pad("B", -1, 3, 1, 1), Pad("C", 6, 0, 1, 1)]
        constraints = [self.fixed(pads[0], 0, "A"), self.fixed(pads[2], 2, "C"),
                       Constraint("AB", "distance", (0, 1), value=5),
                       Constraint("CB", "distance", (2, 1), value=5)]
        result = solve_constraints(pads, constraints)
        self.assertPoint(result.pads[1], 3, 4)
        self.assertEqual((result.rank, result.dof), (14, 4))

    def test_contradictions_name_the_constraints_and_never_mutate_inputs(self):
        pads = [Pad("1", 0, 0, 1, 1)]
        before = tuple(pads)
        constraints = [Constraint("left", "x", (0,), value=-1), Constraint("right", "x", (0,), value=1)]
        with self.assertRaises(ValueError) as caught:
            solve_constraints(pads, constraints)
        self.assertIn("left", str(caught.exception))
        self.assertIn("right", str(caught.exception))
        self.assertEqual(tuple(pads), before)
        with self.assertRaisesRegex(ValueError, "radius"):
            solve_constraints(pads, [Constraint("radius", "radius", (0,), value=2)])
        self.assertEqual(tuple(pads), before)

    def test_constraint_removal_releases_the_relationship_without_implicit_links(self):
        pads = [Pad("1", 0, 0, 1, 1), Pad("2", 2, 0, 1, 1)]
        equal = Constraint("widths", "equal_width", (0, 1))
        followed = solve_constraints(pads, [equal], {0: {"width": 3}})
        released = solve_constraints(followed.pads, [], {0: {"width": 4}})
        self.assertAlmostEqual(released.pads[0].width, 4)
        self.assertAlmostEqual(released.pads[1].width, 3)
        self.assertEqual((released.rank, released.dof), (0, 12))

    def test_custom_only_exposes_position_and_angle_and_retains_copper_and_paste(self):
        custom = merge_pads([Pad("1", 0, 0, 4, 1), Pad("2", -1.5, 1, 1, 3)], "L")
        result = solve_constraints([custom], [], {0: {"x": 2, "y": -3, "angle": 45}})
        moved = result.pads[0]
        self.assertPoint(moved, 2, -3)
        self.assertAlmostEqual(moved.angle, 45)
        self.assertEqual((moved.outline, moved.corners, moved.paste_shapes),
                         (custom.outline, custom.corners, custom.paste_shapes))
        self.assertEqual((result.rank, result.dof), (0, 3))
        fixed = solve_constraints([custom], [self.fixed(custom)])
        self.assertEqual((fixed.rank, fixed.dof, fixed.state), (3, 0, "fully_constrained"))
        with self.assertRaisesRegex(ValueError, "custom-width"):
            solve_constraints([custom], [Constraint("custom-width", "width", (0,), value=2)])
        with self.assertRaisesRegex(ValueError, "driving"):
            solve_constraints([custom], [], {0: {"width": 2}})
        result = solve_constraints([custom, Pad("3", 5, 0, 1, 1)],
                                   [Constraint("size", "equal_size", (0, 1))])
        self.assertAlmostEqual(result.pads[1].width, custom.width)
        self.assertAlmostEqual(result.pads[1].height, custom.height)

    def test_redundant_relations_do_not_inflate_rank_and_drivers_do_not_fix_the_document(self):
        pads = [Pad("1", 0, 0, 1, 1), Pad("2", 0, 0, 1, 1)]
        result = solve_constraints(pads, [Constraint("coincident", "coincidence", (0, 1)),
                                          Constraint("horizontal", "horizontal", (0, 1))])
        self.assertEqual((result.rank, result.dof), (2, 10))
        result = solve_constraints([pads[0]], [], {0: dict(zip(FIELDS, (2, 3, 1, 1, 0, 0)))})
        self.assertEqual((result.rank, result.dof, result.state), (0, 6, "underconstrained"))

    def test_records_roundtrip_json_and_invalid_references_report_the_id(self):
        pad = Pad("1", 0, 0, 1, 1)
        fixed = self.fixed(pad)
        restored = Constraint(**json.loads(json.dumps(asdict(fixed))))
        self.assertEqual(restored, fixed)
        self.assertEqual(solve_constraints([pad], [restored]).pads, [pad])
        with self.assertRaisesRegex(ValueError, "missing"):
            solve_constraints([pad], [Constraint("missing", "x", (3,), value=1)])
        with self.assertRaisesRegex(ValueError, "invalid"):
            Constraint("invalid", "distance", (0, 1), value=math.nan)
        with self.assertRaisesRegex(ValueError, "snapshot"):
            Constraint("snapshot", "fixed", (0,))
        empty = solve_constraints([], [])
        self.assertEqual((empty.pads, empty.rank, empty.dof, empty.state), ([], 0, 0, "fully_constrained"))

    def test_invalid_inputs_and_extreme_values_fail_atomically_with_identifiers(self):
        pads = [Pad("1", 0, 0, 1, 1), Pad("2", 1, 0, 1, 1)]
        before = tuple(pads)
        for arguments in (("bad", "x", None), ("bad", "x", ([0],)),
                          ("bad", "unknown", (0,))):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, "bad"):
                Constraint(*arguments, value=1)
        with self.assertRaisesRegex(ValueError, "huge"):
            solve_constraints(pads, [Constraint("huge", "symmetric", (0, 1), point=(1e308, 0))])
        with self.assertRaisesRegex(ValueError, "driving"):
            solve_constraints(pads, [Constraint("distance", "distance", (0, 1), value=1)],
                              {0: {"x": 1e308}, 1: {"x": -1e308}})
        with self.assertRaisesRegex(ValueError, "tiny"):
            solve_constraints(pads, [Constraint("tiny", "radius", (0,), value=1e-10)])
        self.assertEqual(tuple(pads), before)

    def test_circle_width_driver_preserves_exact_equal_diameters_and_intrinsic_freedom(self):
        circles = [Pad("C", 1, 2, 2, 2, angle=17, pad_shape="circle"),
                   Graphic("G", 1, 2, 2, 2, angle=17, graphic_kind="circle")]
        for circle in circles:
            with self.subTest(kind=type(circle).__name__):
                initial = solve_constraints([circle], [])
                self.assertEqual(initial.pads, [circle])
                self.assertEqual((initial.rank, initial.dof), (1, 4))
                result = solve_constraints([circle], [], {0: {"width": 3.7}})
                solved = result.pads[0]
                self.assertEqual((solved.width, solved.height), (3.7, 3.7))
                self.assertEqual((solved.x, solved.y, solved.angle, solved.radius), (1, 2, 17, 0))
                self.assertEqual((result.rank, result.dof), (1, 4))
                result = solve_constraints([circle], [Constraint("diameter", "width", (0,), value=4.2)])
                self.assertEqual((result.pads[0].width, result.pads[0].height), (4.2, 4.2))
                self.assertEqual((result.rank, result.dof), (2, 3))
                fixed = solve_constraints([circle], [self.fixed(circle)])
                self.assertEqual((fixed.rank, fixed.dof, fixed.state), (5, 0, "fully_constrained"))
                self.assertEqual(circle.width, 2)

    def test_circle_radius_dimension_means_half_the_diameter_and_conflicts_are_atomic(self):
        for circle in (Pad("C", 0, 0, 2, 2, pad_shape="circle"),
                       Graphic("G", 0, 0, 2, 2, graphic_kind="circle")):
            with self.subTest(kind=type(circle).__name__):
                relation = Constraint("circle-radius", "radius", (0,), value=1.3)
                result = solve_constraints([circle], [relation])
                self.assertEqual((result.pads[0].width, result.pads[0].height), (2.6, 2.6))
                self.assertEqual(result.pads[0].radius, 0)
                self.assertEqual((result.rank, result.dof), (2, 3))
                with self.assertRaisesRegex(ValueError, "circle-radius"):
                    solve_constraints([circle], [relation], {0: {"width": 3}})
                with self.assertRaisesRegex(ValueError, "driving"):
                    solve_constraints([circle], [], {0: {"width": 3, "height": 4}})
                with self.assertRaisesRegex(ValueError, "driving"):
                    solve_constraints([circle], [], {0: {"radius": 0.1}})
                self.assertEqual((circle.width, circle.height, circle.radius), (2, 2, 0))

    def test_line_equal_width_is_equal_length_and_height_radius_remain_constants(self):
        first = graphic_from_points("L1", "line", [(0, 0), (3, 4)])
        second = graphic_from_points("L2", "line", [(10, 2), (12, 2)])
        originals = [first, second]
        result = solve_constraints(originals, [Constraint("equal-length", "equal_width", (0, 1))],
                                   {0: {"width": 7.5}})
        for old, solved in zip(originals, result.pads):
            self.assertAlmostEqual(solved.width, 7.5)
            self.assertAlmostEqual(math.dist(*world_vertices(solved)), 7.5)
            self.assertEqual((solved.height, solved.radius, solved.angle), (old.height, 0, old.angle))
        self.assertEqual((result.rank, result.dof), (1, 7))
        for field, value in (("height", 2), ("radius", 0.1)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "driving"):
                    solve_constraints([first], [], {0: {field: value}})
                with self.assertRaisesRegex(ValueError, "immutable"):
                    solve_constraints([first], [Constraint("immutable", field, (0,), value=value)])
        unchanged = solve_constraints([first], [], {0: {"height": first.height, "radius": 0}})
        self.assertEqual(unchanged.pads, [first])
        self.assertEqual((unchanged.rank, unchanged.dof), (0, 4))
        fixed = solve_constraints([first], [self.fixed(first)])
        self.assertEqual((fixed.rank, fixed.dof, fixed.state), (4, 0, "fully_constrained"))
        self.assertEqual(first.width, 5)

    def test_non_round_shapes_do_not_expose_unused_radius_but_roundrect_keeps_it(self):
        shapes = [Pad("R", 0, 0, 2, 1, pad_shape="rect"), Pad("O", 0, 0, 2, 1, pad_shape="oval"),
                  Graphic("R", 0, 0, 2, 1, graphic_kind="rect"),
                  graphic_from_points("P", "polygon", [(0, 0), (2, 0), (1, 1)]),
                  graphic_from_points("A", "arc", [(0, 0), (1, 1), (2, 0)])]
        for shape in shapes:
            with self.subTest(shape=shape):
                result = solve_constraints([shape], [])
                self.assertEqual((result.rank, result.dof), (0, 5))
                with self.assertRaisesRegex(ValueError, "unused-radius"):
                    solve_constraints([shape], [Constraint("unused-radius", "radius", (0,), value=0.1)])
        for shape in (Pad("RR", 0, 0, 2, 1, radius=0.1, pad_shape="roundrect"),
                      Graphic("RR", 0, 0, 2, 1, radius=0.1, graphic_kind="roundrect")):
            result = solve_constraints([shape], [Constraint("corner-radius", "radius", (0,), value=0.2)])
            self.assertEqual(result.pads[0].radius, 0.2)
            self.assertEqual((result.rank, result.dof), (1, 5))


if __name__ == "__main__":
    unittest.main()
