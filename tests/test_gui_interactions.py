"""Interactive-desktop checks for viewport and direct SMD pad editing.

These tests replay Tk events, rather than OS mouse input. Native file/folder
chooser return values are supplied by the harness. Arithmetic comparisons below
check internal geometry after integer event rounding; they do not adopt a T02
acceptance tolerance or claim image-reading precision.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile
import time
import traceback
import unittest
from unittest.mock import patch

from footprint_sketcher.interaction import pad_corners

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None
    ttk = None


REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "examples" / "stage1" / "land-pattern.png"


class GuiInteractionTests(unittest.TestCase):
    def setUp(self):
        if os.name != "nt" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            self.skipTest("Tk GUI tests need a display; run on an interactive desktop.")
        if tk is None:
            self.skipTest("Tk is not installed; install Tcl/Tk before interactive GUI testing.")
        from footprint_sketcher.gui import SketcherApp

        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest("Tk display unavailable: " + str(error))
        self.addCleanup(self._destroy_root)
        self.callback_errors = []

        def callback_error(kind, value, tb):
            self.callback_errors.append("".join(traceback.format_exception(kind, value, tb)))

        self.root.report_callback_exception = callback_error
        self.app = SketcherApp(self.root)
        self.root.geometry("1600x1000+0+0")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self._pump()
            if self.app.canvas.winfo_viewable() and min(
                self.app.canvas.winfo_width(), self.app.canvas.winfo_height()
            ) > 1:
                break
            time.sleep(0.025)
        else:
            self.fail("Canvas did not map within 5 seconds; use an interactive desktop session.")
        self.root.focus_force()
        self._pump()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.export_count = 0
        with patch("footprint_sketcher.gui.filedialog.askopenfilename", return_value=str(SAMPLE)):
            self._button("下絵を開く…")
        self.assertEqual(self.app.canvas.image.size, (900, 560))

    def _destroy_root(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _pump(self):
        self.root.update_idletasks()
        self.root.update()
        if self.callback_errors:
            self.fail("Tk callback raised:\n" + "\n".join(self.callback_errors))

    def _walk(self, widget):
        yield widget
        for child in widget.winfo_children():
            yield from self._walk(child)

    def _button(self, text):
        matches = [
            widget for widget in self._walk(self.root)
            if isinstance(widget, (ttk.Button, ttk.Radiobutton)) and str(widget.cget("text")) == text
        ]
        self.assertEqual(len(matches), 1, text)
        self.assertFalse(matches[0].instate(["disabled"]), text)
        matches[0].invoke()
        self._pump()

    def _entry(self, variable, value):
        matches = [
            widget for widget in self._walk(self.root)
            if isinstance(widget, ttk.Entry) and str(widget.cget("textvariable")) == str(variable)
        ]
        self.assertEqual(len(matches), 1, str(variable))
        matches[0].delete(0, "end")
        matches[0].insert(0, str(value))
        self._pump()
        self.assertEqual(variable.get(), str(value))

    def _screen(self, point, *, world=False):
        self._pump()
        image_point = self.app.sketch.mm_to_image(point) if world else point
        return tuple(round(value) for value in self.app.canvas.image_to_canvas(image_point))

    def _event(self, pattern, screen, **kwargs):
        x, y = (round(value) for value in screen)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLess(x, self.app.canvas.winfo_width())
        self.assertLess(y, self.app.canvas.winfo_height())
        image_point = self.app.canvas.canvas_to_image((x, y))
        self.app.canvas.event_generate(pattern, x=x, y=y, when="tail", **kwargs)
        self._pump()
        return image_point

    def _world_event(self, pattern, point):
        image_point = self._event(pattern, self._screen(point, world=True))
        return self.app.sketch.image_to_mm(image_point)

    def _click(self, point, *, world=False):
        screen = self._screen(point, world=world)
        image_point = self._event("<Button-1>", screen)
        self._event("<ButtonRelease-1>", screen)
        return self.app.sketch.image_to_mm(image_point) if world else image_point

    def _drag_world(self, start, end):
        actual_start = self._world_event("<Button-1>", start)
        self._world_event("<B1-Motion>", end)
        actual_end = self._world_event("<ButtonRelease-1>", end)
        return actual_start, actual_end

    def _pan(self, dx, dy):
        start = (200, 200)
        end = (200 + dx, 200 + dy)
        self._event("<Button-2>", start)
        self._event("<B2-Motion>", end)
        self._event("<ButtonRelease-2>", end)

    def _escape(self):
        self.root.focus_force()
        self._pump()
        self.root.event_generate("<Escape>", when="tail")
        self._pump()

    def _calibrate(self):
        self._button("校正の2点を選ぶ")
        a = self._click((150, 445))
        b = self._click((750, 445))
        self._entry(self.app.distance, "10")
        self._button("校正を適用")
        self.assertEqual(self.app.sketch.calibration.a, a)
        self.assertEqual(self.app.sketch.calibration.b, b)
        self._button("画像の中央を原点に")

    def _draw(self, tool, start, end, *, radius=None, angle=None):
        before = len(self.app.sketch.pads)
        self._button(tool)
        if radius is not None:
            self._entry(self.app.pad_radius, radius)
        if angle is not None:
            self._entry(self.app.pad_angle, angle)
        points = self._drag_world(start, end)
        self.assertEqual(len(self.app.sketch.pads), before + 1, self.app.status.get())
        self.assertEqual(self.app.selected_index, before)
        self.assertIsNone(self.app._drag)
        self.assertIsNone(self.app._preview_pad)
        self._assert_fields(self.app.sketch.pads[before])
        return self.app.sketch.pads[before], points

    def _state(self):
        return (self.app.sketch.calibration, self.app.sketch.origin, tuple(self.app.sketch.pads))

    def _export_bytes(self):
        self.export_count += 1
        directory = Path(self.temporary.name) / (str(self.export_count) + ".pretty")
        directory.mkdir()
        self._entry(self.app.footprint_name, "Interaction_Check")
        with patch("footprint_sketcher.gui.filedialog.askdirectory", return_value=str(directory)):
            self._button(".kicad_mod を書き出す…")
        result = directory / "Interaction_Check.kicad_mod"
        self.assertTrue(result.is_file(), self.app.status.get())
        return result.read_bytes()

    def _assert_point(self, actual, expected):
        self.assertAlmostEqual(actual[0], expected[0], places=7)
        self.assertAlmostEqual(actual[1], expected[1], places=7)

    def _assert_fields(self, pad):
        self.assertEqual(self.app.pad_number.get(), pad.number)
        for variable, name in [
            (self.app.pad_width, "width"), (self.app.pad_height, "height"),
            (self.app.pad_angle, "angle"), (self.app.pad_radius, "radius"),
        ]:
            self.assertEqual(float(variable.get()), getattr(pad, name))

    def test_zoom_pan_during_calibration_and_display_changes_preserve_export(self):
        self._button("校正の2点を選ぶ")
        a = self._click((150, 445))
        self.assertEqual(self.app.calibration_points, [a])
        anchor = self._screen((750, 445))
        before_anchor = self.app.canvas.canvas_to_image(anchor)
        self._event("<MouseWheel>", anchor, delta=120)
        self._assert_point(self.app.canvas.canvas_to_image(anchor), before_anchor)
        self.assertEqual(self.app.calibration_points, [a])
        self._pan(23, -17)
        self.assertEqual(self.app.calibration_points, [a])
        b = self._click((750, 445))
        self._entry(self.app.distance, "10")
        self._button("校正を適用")
        calibration = self.app.sketch.calibration
        self.assertEqual((calibration.a, calibration.b, calibration.distance), (a, b, 10))
        self.assertAlmostEqual(calibration.mm_per_pixel, 10 / math.dist(a, b))
        self._button("画像の中央を原点に")
        self._button("全体表示")
        self._draw("矩形パッド", (-4, -2), (-2, 0))
        snapshot, baseline = self._state(), self._export_bytes()

        anchor = self._screen((450, 280))
        before_anchor = self.app.canvas.canvas_to_image(anchor)
        self._event("<MouseWheel>", anchor, delta=120)
        self._assert_point(self.app.canvas.canvas_to_image(anchor), before_anchor)
        self._button("＋")
        self._button("−")
        self._pan(31, -19)
        viewport = (self.app.canvas.scale, self.app.canvas.offset)
        self.root.geometry("1500x960+0+0")
        self._pump()
        self.assertEqual((self.app.canvas.scale, self.app.canvas.offset), viewport)
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)

        self._button("全体表示")
        anchor = self._screen((450, 280))
        for _ in range(10):
            before_anchor = self.app.canvas.canvas_to_image(anchor)
            self._event("<MouseWheel>", anchor, delta=600)
            self._assert_point(self.app.canvas.canvas_to_image(anchor), before_anchor)
        self.assertEqual(self.app.canvas.scale, self.app.canvas.MAX_SCALE)
        self.assertIsNotNone(self.app.canvas._photo)
        self.assertLessEqual(self.app.canvas._photo.width(), self.app.canvas.winfo_width())
        self.assertLessEqual(self.app.canvas._photo.height(), self.app.canvas.winfo_height())
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)
        self._button("全体表示")
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)

    def test_drag_shapes_move_rotate_all_resize_handles_and_apply_from_table(self):
        self._calibrate()
        rectangle, (start, end) = self._draw("矩形パッド", (-4, -2), (-2, 0))
        self._assert_point((rectangle.x, rectangle.y), tuple((a + b) / 2 for a, b in zip(start, end)))
        self.assertAlmostEqual(rectangle.width, abs(end[0] - start[0]))
        self.assertAlmostEqual(rectangle.height, abs(end[1] - start[1]))
        self.assertEqual(rectangle.radius, 0)
        rounded, _ = self._draw("角丸パッド", (2, -2), (4, 0), radius="0.75")
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["1", "2"])
        self.assertEqual(rounded.radius, 0.75)

        self._button("選択/移動")
        start, end = self._drag_world((rounded.x, rounded.y), (rounded.x, rounded.y + 2))
        moved = self.app.sketch.pads[1]
        self._assert_point((moved.x, moved.y), (rounded.x + end[0] - start[0], rounded.y + end[1] - start[1]))
        self.assertEqual((moved.width, moved.height, moved.radius, moved.number),
                         (rounded.width, rounded.height, rounded.radius, rounded.number))
        self._entry(self.app.pad_angle, "30")
        self._button("選択パッドに適用")
        self.assertEqual(self.app.sketch.pads[1].angle, 30)

        for corner in range(4):
            with self.subTest(corner=corner):
                original = self.app.sketch.pads[1]
                old_corners = pad_corners(original)
                moving, fixed = old_corners[corner], old_corners[(corner + 2) % 4]
                target = tuple(f + 1.15 * (m - f) for m, f in zip(moving, fixed))
                press, release = self._drag_world(moving, target)
                expected_corner = tuple(m + b - a for m, a, b in zip(moving, press, release))
                resized = self.app.sketch.pads[1]
                self._assert_point(pad_corners(resized)[corner], expected_corner)
                self._assert_point(pad_corners(resized)[(corner + 2) % 4], fixed)
                self.assertEqual((resized.number, resized.angle), (original.number, 30))
                self.assertGreater(resized.width, original.width)
                self.assertGreater(resized.height, original.height)
                self._assert_fields(resized)
                self.assertEqual(self.app.sketch.pads[0], rectangle)

        original = self.app.sketch.pads[1]
        corners = pad_corners(original)
        fixed = corners[0]
        angle = math.radians(30)
        target = (fixed[0] + 0.4 * math.cos(angle) + 1.2 * math.sin(angle),
                  fixed[1] + 1.2 * math.cos(angle) - 0.4 * math.sin(angle))
        self._drag_world(corners[2], target)
        small = self.app.sketch.pads[1]
        self.assertLess(small.radius, original.radius)
        self.assertAlmostEqual(small.radius, min(small.width, small.height) / 2)
        self._assert_point(pad_corners(small)[0], fixed)
        self._assert_fields(small)

        item = self.app.pad_table.get_children()[0]
        self.app.pad_table.see(item)
        self._pump()
        x, y, width, height = self.app.pad_table.bbox(item, "number")
        for pattern in ("<Button-1>", "<ButtonRelease-1>"):
            self.app.pad_table.event_generate(pattern, x=round(x + width / 2), y=round(y + height / 2), when="tail")
            self._pump()
        self.assertEqual(self.app.selected_index, 0)
        self._assert_fields(rectangle)
        for variable, value in [(self.app.pad_number, "R1"), (self.app.pad_width, "1.8"),
                                (self.app.pad_height, "0.8"), (self.app.pad_radius, "0")]:
            self._entry(variable, value)
        self._button("選択パッドに適用")
        edited = self.app.sketch.pads[0]
        self.assertEqual((edited.number, edited.width, edited.height), ("R1", 1.8, 0.8))
        self.assertEqual((edited.x, edited.y), (rectangle.x, rectangle.y))
        self.assertEqual(self.app.sketch.pads[1], small)
        self._assert_fields(edited)

        diagonal, _ = self._draw("矩形パッド", (0, 3), (2, 3), angle="45")
        self.assertEqual(diagonal.angle, 45)
        self.assertGreater(diagonal.width, 0)
        self.assertAlmostEqual(diagonal.width, diagonal.height)

    def test_selection_and_cancelled_drag_previews_never_change_saved_geometry(self):
        self._calibrate()
        pad, _ = self._draw("角丸パッド", (-2, -1), (0, 1), radius="0.25")
        snapshot, baseline = self._state(), self._export_bytes()
        self._button("選択/移動")
        self._click((pad.x, pad.y), world=True)
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)

        corner = self._screen(pad_corners(pad)[2], world=True)
        grabbed = (corner[0] + 3, corner[1] - 2)
        self._event("<Button-1>", grabbed)
        self._event("<ButtonRelease-1>", grabbed)
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)

        for kind in ("move", "resize", "draw"):
            with self.subTest(preview=kind):
                if kind == "draw":
                    self._button("矩形パッド")
                    start, end = (2, -1), (4, 1)
                else:
                    self._button("選択/移動")
                    start = (pad.x, pad.y) if kind == "move" else pad_corners(pad)[2]
                    end = (start[0] + 1, start[1] + 1)
                self._world_event("<Button-1>", start)
                self._world_event("<B1-Motion>", end)
                self.assertIsNotNone(self.app._preview_pad)
                self.assertEqual(self._state(), snapshot)
                self.assertEqual(self._export_bytes(), baseline)
                self._escape()
                self.assertIsNone(self.app._drag)
                self.assertIsNone(self.app._preview_pad)
                self.assertEqual(self._state(), snapshot)
                self.assertEqual(self._export_bytes(), baseline)
                self._world_event("<ButtonRelease-1>", end)
                self.assertEqual(self._state(), snapshot)


if __name__ == "__main__":
    unittest.main()
