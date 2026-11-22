"""Regression checks for KiCad-compatible pad angles."""

import re
import tempfile
import unittest
from pathlib import Path

from footprint_sketcher.model import Pad, export_footprint


class ExportAngleTests(unittest.TestCase):
    def test_equivalent_angles_are_exported_within_one_turn(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index, angle in enumerate((30.0, -30.0, 360.0, 1e100, -1e100)):
                with self.subTest(angle=angle):
                    pad = Pad("1", 1.0, 2.0, 1.0, 1.0, angle)
                    output = export_footprint(directory, f"Angle_{index}", [pad])
                    content = output.read_text(encoding="utf-8")
                    match = re.search(r"\(pad .*?\(at\s+\S+\s+\S+\s+(\S+)\)", content, re.DOTALL)
                    self.assertIsNotNone(match)
                    self.assertEqual(float(match.group(1)), angle % 360)
                    self.assertEqual(pad.angle, angle)


if __name__ == "__main__":
    unittest.main()
