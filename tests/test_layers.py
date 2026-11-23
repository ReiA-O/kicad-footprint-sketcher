"""Layer visibility and lock semantics, including through-hole wildcards."""
from dataclasses import replace
import unittest

from footprint_sketcher.layers import layers_locked, visible_layers, locked_content
from footprint_sketcher.model import Pad


class LayerTests(unittest.TestCase):
    def test_through_hole_is_visible_on_either_face_and_locked_by_either_face(self):
        layers = ("*.Cu", "*.Mask")
        self.assertTrue(layers_locked(layers, {"B.Cu"}))
        self.assertTrue(layers_locked(layers, {"F.Mask"}))
        self.assertFalse(layers_locked(layers, {"F.Paste"}))
        self.assertEqual(visible_layers(layers, {"F.Cu", "F.Mask"}), {"B.Cu", "B.Mask"})
        self.assertFalse(visible_layers(layers, {"F.Cu", "B.Cu", "F.Mask", "B.Mask"}))

    def test_locked_content_detects_duplicate_removal_addition_and_layer_entry(self):
        front = Pad("1", 0, 0, 2, 1)
        back = replace(front, layers=("B.Cu", "B.Mask"))
        self.assertNotEqual(locked_content([front, front], {"F.Cu"}), locked_content([front], {"F.Cu"}))
        self.assertNotEqual(locked_content([], {"B.Cu"}), locked_content([back], {"B.Cu"}))
        self.assertNotEqual(locked_content([front], {"B.Cu"}), locked_content([back], {"B.Cu"}))
        self.assertEqual(locked_content([front], {"B.Cu"}), locked_content([replace(front, x=2)], {"B.Cu"}))
