"""Visual selection boundaries and PDF viewer event regressions."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from footprint_sketcher.pdf_viewer import PdfCropDialog, selection_crop, _count_pages, _render_page

try:
    import tkinter as tk
except ImportError:
    tk = None


class SelectionCropTests(unittest.TestCase):
    def test_reverse_fractional_drag_covers_all_selected_source_pixels(self):
        self.assertEqual(selection_crop((110.2, 80.1, 20.9, 10.2), (200, 100)), (20, 10, 91, 71))

    def test_drag_clamps_to_pdf_edges_and_ignores_viewport_roundoff(self):
        self.assertEqual(selection_crop((250, 200, -100, -50), (200, 100)), (0, 0, 200, 100))
        self.assertEqual(selection_crop((20.00000000001, 10, 100.00000000001, 50), (200, 100)), (20, 10, 80, 40))

    def test_click_line_outside_and_nonfinite_selections_are_rejected(self):
        for bounds in ((20, 10, 20, 10), (20, 10, 20, 50), (-20, 0, -10, 50),
                       (0, 101, 20, 150), (0, 0, float("inf"), 10), (0, 0, float("nan"), 10)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                selection_crop(bounds, (200, 100))

    def test_missing_and_corrupt_pdf_errors_do_not_disclose_source_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory)/"missing.pdf"
            corrupt = Path(directory)/"corrupt.pdf"
            corrupt.write_bytes(b"Not a PDF")
            for path in (missing, corrupt):
                for reader in (_count_pages, lambda path: _render_page(path, 0)):
                    with self.subTest(path=path.name, reader=reader):
                        with self.assertRaises(ValueError) as caught:
                            reader(path)
                        self.assertNotIn(str(path), str(caught.exception))
                        self.assertIn("PDFを開けません", str(caught.exception))

    def test_pdf_size_rejection_retains_an_actionable_reason(self):
        with patch("footprint_sketcher.pdf_viewer.render_pdf_page", side_effect=ValueError("PDFページが大きすぎます。表示倍率を下げてください。")):
            with self.assertRaisesRegex(ValueError, "大きすぎ"):
                _render_page(Path("Reference.pdf"), 0)


@unittest.skipUnless(tk is not None, "PDF viewer GUI check requires Tcl/Tk and a display.")
class PdfViewerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temporary.name) / "Reference.pdf"
        first = Image.new("RGB", (600, 780), "white")
        draw = ImageDraw.Draw(first)
        draw.text((32, 28), "SYNTHETIC DATASHEET - PAGE 1", fill="black")
        draw.rectangle((20, 20, 300, 220), outline="blue", width=4)
        draw.rectangle((40, 50, 200, 160), fill="#d5e7fa")
        second = Image.new("RGB", (480, 320), "#f6dda4")
        ImageDraw.Draw(second).text((24, 24), "SYNTHETIC DATASHEET - PAGE 2", fill="black")
        first.save(cls.path, "PDF", resolution=72, save_all=True, append_images=[second])
        first.close()
        second.close()
        cls.original_pdf = cls.path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"PDF viewer GUI check requires a display: {error}")
        self.root.geometry("200x100+0+0")
        self.root.update()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.dialog = PdfCropDialog(self.root, self.path)
        self.root.update()

    def tearDown(self):
        if hasattr(self, "dialog"):
            result = self.dialog.result
            self.dialog.destroy()
            if result is not None:
                result.image.close()
        if hasattr(self, "root"):
            self.root.update()
            self.root.destroy()
        self.assertEqual(self.path.read_bytes(), self.original_pdf)
        self.assertEqual(getattr(self, "callback_errors", []), [])

    def _unit_zoom(self):
        self.dialog.scale = 1
        self.dialog._fit_mode = False
        self.dialog._geometry()
        self.dialog.canvas.xview_moveto(0)
        self.dialog.canvas.yview_moveto(0)
        self.root.update()

    def _drag(self, start, end):
        canvas = self.dialog.canvas
        points = [tuple(round(value) for value in self.dialog.image_to_canvas(*point)) for point in (start, end)]
        canvas.event_generate("<ButtonPress-1>", x=points[0][0], y=points[0][1])
        canvas.event_generate("<B1-Motion>", x=points[1][0], y=points[1][1])
        canvas.event_generate("<ButtonRelease-1>", x=points[1][0], y=points[1][1])
        self.root.update()

    def test_crop_survives_zoom_and_scroll_and_returns_independent_exact_pixels(self):
        self._unit_zoom()
        self._drag((40, 50), (260, 190))
        self.assertEqual(self.dialog.selected_crop, (40, 50, 220, 140))
        expected = self.dialog._page_image.crop((40, 50, 260, 190))
        self.dialog.zoom_at(2)
        self.dialog._scroll_x("moveto", .15)
        self.dialog._scroll_y("moveto", .35)
        self.root.update()
        self.assertEqual(self.dialog.selected_crop, (40, 50, 220, 140))
        source = self.dialog._page_image
        self.dialog.import_button.invoke()
        self.root.update()
        result = self.dialog.result
        self.assertEqual(result.crop, (40, 50, 220, 140))
        self.assertEqual(result.image.size, (220, 140))
        self.assertEqual(result.image.tobytes(), expected.tobytes())
        self.assertIn("1ページ", result.label)
        with self.assertRaises(ValueError):
            source.getpixel((0, 0))
        expected.close()

    def test_zoomed_scrolled_drag_maps_to_original_raster_instead_of_screen_pixels(self):
        self._unit_zoom()
        self.dialog.zoom_at(2)
        self.dialog._scroll_x("moveto", .15)
        self.dialog._scroll_y("moveto", .35)
        self.root.update()
        self._drag((300, 650), (400, 730))
        self.assertEqual(self.dialog.selected_crop, (300, 650, 100, 80))

    def test_wheel_zoom_during_drag_keeps_the_start_in_source_coordinates(self):
        self._unit_zoom()
        canvas = self.dialog.canvas
        start = tuple(round(value) for value in self.dialog.image_to_canvas(100, 100))
        before_zoom = tuple(round(value) for value in self.dialog.image_to_canvas(200, 180))
        canvas.event_generate("<ButtonPress-1>", x=start[0], y=start[1])
        canvas.event_generate("<B1-Motion>", x=before_zoom[0], y=before_zoom[1])
        canvas.event_generate("<MouseWheel>", delta=120, x=before_zoom[0], y=before_zoom[1])
        self.root.update()
        self.assertGreater(self.dialog.scale, 1)

        # These source coordinates also land on whole screen pixels at 1.25x.
        end = tuple(round(value) for value in self.dialog.image_to_canvas(300, 252))
        canvas.event_generate("<B1-Motion>", x=end[0], y=end[1])
        canvas.event_generate("<ButtonRelease-1>", x=end[0], y=end[1])
        self.root.update()
        self.assertEqual(self.dialog.selected_crop, (100, 100, 200, 152))
        expected = self.dialog._page_image.crop((100, 100, 300, 252))
        try:
            self.dialog.import_selection()
            self.assertEqual(self.dialog.result.image.tobytes(), expected.tobytes())
        finally:
            expected.close()

    def test_middle_button_pan_then_reverse_drag_selects_source_pixels(self):
        self._unit_zoom()
        self.dialog.zoom_at(2, (0, 0))
        self.root.update()
        canvas = self.dialog.canvas
        origin = (canvas.canvasx(0), canvas.canvasy(0))
        canvas.event_generate("<ButtonPress-2>", x=180, y=180)
        canvas.event_generate("<B2-Motion>", x=80, y=60)
        canvas.event_generate("<ButtonRelease-2>", x=80, y=60)
        self.root.update()
        self.assertGreater(canvas.canvasx(0), origin[0])
        self.assertGreater(canvas.canvasy(0), origin[1])
        self._drag((240, 180), (140, 100))
        self.assertEqual(self.dialog.selected_crop, (140, 100, 100, 80))

    def test_reverse_drag_outside_page_clamps_and_single_click_cannot_import(self):
        self._unit_zoom()
        self._drag((250, 200), (-100, -50))
        self.assertEqual(self.dialog.selected_crop, (0, 0, 250, 200))
        self.dialog.clear_selection()
        self._drag((100, 100), (100, 100))
        self.assertIsNone(self.dialog.selected_crop)
        self.assertTrue(self.dialog.import_button.instate(("disabled",)))
        self.dialog.import_selection()
        self.assertIsNone(self.dialog.result)

    def test_page_navigation_clears_selection_and_whole_page_import_is_independent(self):
        self._unit_zoom()
        self._drag((20, 20), (100, 80))
        self.dialog.next_button.invoke()
        self.root.update()
        self.assertEqual(self.dialog.page_index, 1)
        self.assertEqual(self.dialog.page_number.get(), "2")
        self.assertIsNone(self.dialog.selected_crop)
        self.assertTrue(self.dialog.next_button.instate(("disabled",)))
        self.assertFalse(self.dialog.previous_button.instate(("disabled",)))
        expected = self.dialog._page_image.tobytes()
        self.dialog.import_page()
        self.root.update()
        self.assertIsNone(self.dialog.result.crop)
        self.assertEqual(self.dialog.result.page_index, 1)
        self.assertEqual(self.dialog.result.image.size, (960, 640))
        self.assertEqual(self.dialog.result.image.tobytes(), expected)

    def test_page_render_failure_and_bad_number_preserve_previous_page_and_selection(self):
        self._unit_zoom()
        self._drag((20, 20), (100, 80))
        source = self.dialog._page_image
        sensitive = str(self.path.parent/"private-location.pdf")
        with patch("footprint_sketcher.pdf_viewer.render_pdf_page", side_effect=RuntimeError(sensitive)):
            self.dialog.show_page(1)
        self.root.update()
        self.assertEqual(self.dialog.page_index, 0)
        self.assertIs(self.dialog._page_image, source)
        self.assertEqual(self.dialog.selected_crop, (20, 20, 80, 60))
        self.assertEqual(self.dialog.page_number.get(), "1")
        self.assertNotIn(sensitive, self.dialog.status.get())
        self.dialog.page_number.set("0")
        self.dialog.go_to_page()
        self.assertEqual(self.dialog.page_number.get(), "1")
        self.assertEqual(self.dialog.selected_crop, (20, 20, 80, 60))

    def test_modal_cancel_does_not_return_an_image_or_leave_pending_callbacks(self):
        self.root.after(30, self.dialog.cancel)
        self.assertIsNone(self.dialog.show())
        self.assertIsNone(self.dialog._page_image)
        immediate = PdfCropDialog(self.root, self.path)
        immediate.cancel()
        self.root.update()
        self.assertIsNone(immediate.result)
        self.assertIsNone(immediate._page_image)

    def test_already_visible_modal_import_returns_the_completed_page(self):
        focused_windows = []

        def accept():
            focus = self.dialog.focus_get()
            focused_windows.append(str(focus.winfo_toplevel()) if focus is not None else None)
            self.dialog.import_page()

        self.root.after(30, accept)
        result = self.dialog.show()
        self.assertIs(result, self.dialog.result)
        self.assertIsNotNone(result)
        self.assertEqual(result.image.size, (1200, 1560))
        self.assertEqual(focused_windows, [str(self.dialog)])

    def test_destroying_parent_root_releases_modal_wait_and_pending_redraw(self):
        parent = tk.Tk()
        errors = []
        parent.report_callback_exception = lambda *args: errors.append(args)
        dialog = None
        pending = []
        try:
            dialog = PdfCropDialog(parent, self.path)
            parent.update()

            def close_parent():
                dialog._queue_draw()
                pending.append(dialog._redraw_id)
                parent.destroy()

            parent.after(30, close_parent)
            self.assertIsNone(dialog.show())
            self.assertEqual(len(pending), 1)
            self.assertIsNotNone(pending[0])
            self.assertTrue(dialog._closing)
            self.assertIsNone(dialog._page_image)
            self.assertIsNone(dialog._redraw_id)
            self.assertNotIn(pending[0], parent.tk.call("after", "info"))
            self.assertEqual(errors, [])
        finally:
            if dialog is not None:
                dialog.destroy()
            try:
                parent.destroy()
            except tk.TclError:
                pass

    def test_invalid_pdf_does_not_leave_an_orphan_window(self):
        invalid = Path(self.temporary.name) / "invalid.pdf"
        invalid.write_bytes(b"Not a PDF")
        before = tuple(self.root.winfo_children())
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            PdfCropDialog(self.root, invalid)
        self.assertEqual(tuple(self.root.winfo_children()), before)


if __name__ == "__main__":
    unittest.main()
