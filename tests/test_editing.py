"""Group edits and undo preserve source pads and complete calibration state."""

from dataclasses import FrozenInstanceError, asdict, replace
import math
import unittest

from footprint_sketcher.editing import (
    Snapshot, UndoHistory, align_centers, distribute_centers, duplicate_pads,
)
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


if __name__ == "__main__":
    unittest.main()
