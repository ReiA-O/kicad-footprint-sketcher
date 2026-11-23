from pathlib import Path
import tempfile
import unittest

from PIL import Image

from footprint_sketcher.graphics import Graphic, graphic_from_points, render_graphic, graphic_outline
from footprint_sketcher.model import FRONT_LAYERS, Pad, export_footprint
from footprint_sketcher.reference import pdf_page_count, render_pdf_page


class GraphicsReferenceTests(unittest.TestCase):
    def test_all_six_layers_and_rotated_roundrect_render_without_gui_dependencies(self):
        for layer in FRONT_LAYERS:
            rectangle = Graphic('G', 1, -2, 3, 2, angle=30, radius=.2,
                                graphic_kind='roundrect', layer=layer, preserved=('(uuid "drawing-id")',))
            text = '\n'.join(render_graphic(rectangle))
            self.assertIn('(fp_poly', text)
            self.assertIn(f'(layer "{layer}")', text)
            self.assertIn('(uuid "drawing-id")', text)
            self.assertGreater(len(graphic_outline(rectangle)), 4)

    def test_line_circle_arc_and_polygon_keep_geometry_and_reject_degenerate_input(self):
        line = graphic_from_points('G1', 'line', [(0, 0), (3, 0)])
        self.assertIn('(start 0 0) (end 3.0 0)', render_graphic(line)[0])
        circle = Graphic('G2', 0, 0, 2, 2, graphic_kind='circle')
        self.assertIn('(fp_circle', render_graphic(circle)[0])
        arc = graphic_from_points('G3', 'arc', [(1, 0), (0, 1), (-1, 0)])
        self.assertIn('(fp_arc', render_graphic(arc)[0])
        outline = graphic_outline(arc)
        self.assertAlmostEqual(outline[24][1], .5)
        with self.assertRaises(ValueError):
            graphic_from_points('bad', 'arc', [(0, 0), (1, 1), (2, 2)])
        with self.assertRaises(ValueError):
            graphic_from_points('bad', 'polygon', [(0, 0), (1, 1), (0, 1), (1, 0)])

    def test_holes_and_opening_values_are_validated_and_exported(self):
        pad = Pad('1', 0, 0, 3, 2, pad_type='thru_hole', pad_shape='oval', layers=('*.Cu', '*.Mask'),
                  drill_width=1.5, drill_height=.8, mask_margin=.1)
        with tempfile.TemporaryDirectory() as directory:
            text = export_footprint(Path(directory), 'Hole', [pad]).read_text()
        self.assertIn('thru_hole oval', text)
        self.assertIn('(drill oval 1.5 0.8)', text)
        self.assertIn('(solder_mask_margin 0.1)', text)
        with self.assertRaises(ValueError):
            Pad('1', 0, 0, 1, 1, paste_ratio=-.5)

    def test_pdf_page_selection_and_crop_are_explicit_and_do_not_modify_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'reference.pdf'
            Image.new('RGB', (200, 100), 'white').save(path, 'PDF', save_all=True,
                append_images=[Image.new('RGB', (200, 100), 'red')], resolution=72)
            original = path.read_bytes()
            self.assertEqual(pdf_page_count(path), 2)
            image = render_pdf_page(path, 1, scale=1, crop=(20, 10, 100, 40))
            self.assertEqual(image.size, (100, 40))
            self.assertGreater(image.getpixel((5, 5))[0], 240)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(ValueError): render_pdf_page(path, 2)
            with self.assertRaises(ValueError): render_pdf_page(path, 0, crop=(0, 0, 999, 1))


if __name__ == '__main__': unittest.main()
