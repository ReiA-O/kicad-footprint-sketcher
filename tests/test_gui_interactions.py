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
        self.assertEqual(self.app._method_combo.current(), 0)
        self.app._method_combo.current(1)
        self.app._method_combo.event_generate("<<ComboboxSelected>>", when="tail")
        self._pump()

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
        self.assertEqual(self.app.mode, "draw")
        self.assertEqual(self.app.tool.get(), "rect" if tool == "矩形パッド" else "roundrect")
        self.assertTrue(self.app.pad_number.get().isdecimal())
        self.assertNotIn(self.app.pad_number.get(), {pad.number for pad in self.app.sketch.pads})
        self._assert_fields(self.app.sketch.pads[before], include_number=False)
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

    def _assert_fields(self, pad, *, include_number=True):
        if include_number:
            self.assertEqual(self.app.pad_number.get(), pad.number)
        for variable, name in [
            (self.app.pad_width, "width"), (self.app.pad_height, "height"),
            (self.app.pad_angle, "angle"), (self.app.pad_radius, "radius"),
        ]:
            self.assertEqual(float(variable.get()), getattr(pad, name))

    def test_pending_complete_calibration_blocks_selected_edits_until_explicitly_finished(self):
        from dataclasses import replace

        def select_rows(indices):
            children = self.app.pad_table.get_children()
            self.app.pad_table.selection_set([children[index] for index in indices])
            self.app.pad_table.focus(children[indices[-1]])
            self.app.pad_table.event_generate("<<TreeviewSelect>>", when="tail")
            self._pump()
            self.assertEqual(self.app.selected_indices, set(indices))

        def pending_axis_calibration():
            self.app._method_combo.current(0)
            self.app._method_combo.event_generate("<<ComboboxSelected>>", when="tail")
            self._pump()
            self._button("横2点＋縦2点を選ぶ")
            for point in ((150, 445), (750, 425), (300, 220), (320, 340)):
                self._click(point)
            self.assertEqual(len(self.app.calibration_points), 4)
            self.assertEqual(self.app.mode, "idle")
            return tuple(self.app.calibration_points)

        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_width, "4"), (self.app.pad_height, "1"),
                                (self.app.pad_angle, "0"), (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((0, 0), world=True)
        self._entry(self.app.pad_width, "1")
        self._entry(self.app.pad_height, "3")
        self._click((-1.5, 1), world=True)
        self._button("選択/移動")
        select_rows([0, 1])
        self._entry(self.app.merge_number, "L")
        self._button("選択図形を合体")
        self._button("数値サイズで配置")
        self._entry(self.app.pad_width, "2")
        self._entry(self.app.pad_height, "1")
        self._click((3, 0), world=True)
        self._click((3.5, 0.5), world=True)
        self._button("選択/移動")
        baseline, payload = self._state(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        select_rows([1, 2])
        points = pending_axis_calibration()
        self._entry(self.app.merge_number, "Blocked")
        self._entry(self.app.pad_width, "1.6")
        self.app._width_entry.focus_set()
        self.app._width_entry.event_generate("<Return>", when="tail")
        self._pump()
        actions = (self.app.apply_selected, lambda: self.app.align_selected("x"),
                   lambda: self.app.align_selected("y", spacing=True), self.app.merge_selected,
                   lambda: self.app.mirror_selected("x"), lambda: self.app.mirror_selected("y"))
        for action in actions:
            action()
            self._pump()
            self.assertEqual(self._state(), baseline)
            self.assertEqual(tuple(self.app.calibration_points), points)
            self.assertEqual(tuple(self.app.history._snapshots), history)
            self.assertEqual(self.app.selected_indices, {1, 2})
            self.assertEqual(self.app.mode, "idle")
            self.assertIn("選択/移動", self.app.status.get())
        for button in (self.app._selection_button, self.app._merge_button,
                       *(button for button, _ in self.app._align_buttons), *self.app._mirror_buttons):
            self.assertTrue(button.instate(["disabled"]))
        self.assertEqual(self._export_bytes(), payload)
        self._button("1操作戻す")
        self.assertEqual(tuple(self.app.calibration_points), points[:-1])
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._button("選択/移動")
        self.assertFalse(self.app.calibration_points)
        self._entry(self.app.pad_width, "2.4")
        self.app._width_entry.focus_set()
        self.app._width_entry.event_generate("<Return>", when="tail")
        self._pump()
        self.assertEqual(self.app.sketch.pads,
                         [baseline[2][0], *(replace(pad, width=2.4) for pad in baseline[2][1:])])
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

        select_rows([0])
        points = pending_axis_calibration()
        self.assertTrue(self.app._fillet_button.instate(["disabled"]))
        self.app.begin_fillet()
        self._entry(self.app.fillet_radius, "0.2")
        self.app._fillet_radius_entry.focus_set()
        self.app._fillet_radius_entry.event_generate("<Return>", when="tail")
        self._pump()
        self.assertEqual(self.app.mode, "idle")
        self.assertEqual(tuple(self.app.calibration_points), points)
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._button("選択/移動")
        self._button("角を選んでフィレット")
        self.assertEqual(self.app.mode, "fillet")
        self._escape()
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

    def test_pad_field_enter_updates_selection_and_validates_only_the_next_placement_template(self):
        from dataclasses import replace

        def press_return(variable):
            matches = [widget for widget in self._walk(self.root)
                       if isinstance(widget, ttk.Entry)
                       and str(widget.cget("textvariable")) == str(variable)]
            self.assertEqual(len(matches), 1)
            self.assertFalse(matches[0].instate(["disabled"]))
            matches[0].focus_set()
            matches[0].event_generate("<Return>", when="tail")
            self._pump()

        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_width, "2"), (self.app.pad_height, "1.2"),
                                (self.app.pad_angle, "0"), (self.app.pad_radius, "0.2")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((-2, 0), world=True)
        self._button("選択/移動")
        original = self.app.sketch.pads[0]
        baseline, payload = self._state(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        for variable, attribute, value in ((self.app.pad_width, "width", 2.4),
                                           (self.app.pad_height, "height", 1.6),
                                           (self.app.pad_angle, "angle", 30),
                                           (self.app.pad_radius, "radius", 0.3),
                                           (self.app.pad_number, "number", "R9")):
            with self.subTest(selected_field=attribute):
                self._click((original.x, original.y), world=True)
                self._entry(variable, value)
                press_return(variable)
                self.assertEqual(self.app.sketch.pads, [replace(original, **{attribute: value})])
                self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
                self.assertEqual(self.app.selected_indices, {0})
                self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
                self._button("1操作戻す")
                self.assertEqual(self._state(), baseline)
                self.assertEqual(tuple(self.app.history._snapshots), history)
                self.assertEqual(self._export_bytes(), payload)

        for variable, invalid in ((self.app.pad_width, ""), (self.app.pad_height, "-1"),
                                  (self.app.pad_angle, "nan"), (self.app.pad_radius, "1"),
                                  (self.app.pad_number, "")):
            with self.subTest(invalid_field=str(variable)):
                self._click((original.x, original.y), world=True)
                self._entry(variable, invalid)
                press_return(variable)
                self.assertEqual(variable.get(), invalid)
                self.assertEqual(self._state(), baseline)
                self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
                self.assertEqual(tuple(self.app.history._snapshots), history)
                self.assertEqual(self._export_bytes(), payload)

        self._button("数値サイズで配置")
        for variable, value in ((self.app.pad_width, "1.8"), (self.app.pad_height, "0.8"),
                                (self.app.pad_angle, "45"), (self.app.pad_radius, "0.2")):
            self._entry(variable, value)
        press_return(self.app.pad_height)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("pad", "pad"))
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        target = self._click((2, 1), world=True)
        second = self.app.sketch.pads[1]
        self._assert_point((second.x, second.y), target)
        self.assertEqual((second.width, second.height, second.angle, second.radius), (1.8, 0.8, 45, 0.2))
        self.assertEqual(self.app.sketch.pads[0], original)

        placed, placed_history = self._state(), tuple(self.app.history._snapshots)
        for variable, value in ((self.app.pad_width, "0.9"), (self.app.pad_height, "0.6"),
                                (self.app.pad_angle, "-20"), (self.app.pad_radius, "0.1")):
            self._entry(variable, value)
        press_return(self.app.pad_angle)
        self.assertEqual(self._state(), placed)
        self.assertEqual(tuple(self.app.history._snapshots), placed_history)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("pad", "pad"))
        self._click((4, -1), world=True)
        self.assertEqual(tuple(self.app.sketch.pads[:2]), placed[2])
        third = self.app.sketch.pads[2]
        self.assertEqual((third.width, third.height, third.angle, third.radius), (0.9, 0.6, -20, 0.1))
        placed, placed_history = self._state(), tuple(self.app.history._snapshots)
        self._entry(self.app.pad_width, "")
        press_return(self.app.pad_width)
        self.assertEqual(self.app.pad_width.get(), "")
        self.assertEqual((self.app.mode, self.app.tool.get()), ("pad", "pad"))
        self.assertEqual(self._state(), placed)
        self.assertEqual(tuple(self.app.history._snapshots), placed_history)

        self._button("矩形パッド")
        self._entry(self.app.pad_width, "1.1")
        press_return(self.app.pad_width)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("pad", "pad"))
        self.assertEqual(self._state(), placed)
        self.assertEqual(tuple(self.app.history._snapshots), placed_history)
        self._click((0, 3), world=True)
        self.assertEqual(tuple(self.app.sketch.pads[:3]), placed[2])
        fourth = self.app.sketch.pads[3]
        self.assertEqual((fourth.width, fourth.height, fourth.angle, fourth.radius), (1.1, 0.6, 0, 0))

        placed, placed_history = self._state(), tuple(self.app.history._snapshots)
        self._button("角丸パッド")
        for variable, value in ((self.app.pad_angle, "30"), (self.app.pad_radius, "0.15"),
                                (self.app.pad_number, "Q")):
            self._entry(variable, value)
            press_return(variable)
            self.assertEqual((self.app.mode, self.app.tool.get()), ("draw", "roundrect"))
            self.assertEqual(self._state(), placed)
            self.assertEqual(tuple(self.app.history._snapshots), placed_history)
        self._drag_world((0, -3), (1, -2))
        self.assertEqual(tuple(self.app.sketch.pads[:4]), placed[2])
        self.assertEqual((self.app.sketch.pads[4].number, self.app.sketch.pads[4].angle,
                          self.app.sketch.pads[4].radius), ("Q", 30, 0.15))
        self.assertEqual(len(self.app.history._snapshots), len(placed_history) + 1)

    def test_visible_number_editor_commits_only_number_cancels_and_keeps_next_draw_number_unused(self):
        from dataclasses import replace

        click_clock = int(time.monotonic() * 1000) & 0x7FFFFFFF

        def commit_header(value, *, button=False):
            self._entry(self.app.selected_number, value)
            if button:
                self._button("番号を変更")
            else:
                self.app._selected_number_entry.focus_set()
                self.app._selected_number_entry.event_generate("<Return>", when="tail")
                self._pump()

        def begin_table_editor():
            nonlocal click_clock
            # Tk synthesizes Double-1 from ordinary clicks; it does not accept
            # the Double modifier in event_generate. Separate consecutive pairs.
            click_clock += 1000
            item = self.app.pad_table.get_children()[0]
            self.app.pad_table.see(item)
            self._pump()
            x, y, width, height = self.app.pad_table.bbox(item, "number")
            for offset in (0, 100):
                for pattern, release_offset in (("<Button-1>", 0), ("<ButtonRelease-1>", 1)):
                    self.app.pad_table.event_generate(pattern, x=round(x + width / 2),
                                                      y=round(y + height / 2),
                                                      time=click_clock + offset + release_offset, when="tail")
                    self._pump()
            self.assertIsInstance(self.app._number_editor, ttk.Entry)
            self.assertEqual(self.app._number_editor.get(), "1")

        def enter_inline_number(value, key="<Return>"):
            editor = self.app._number_editor
            self.assertIsInstance(editor, ttk.Entry)
            editor.delete(0, "end")
            editor.insert(0, value)
            editor.event_generate(key, when="tail")
            self._pump()

        self._calibrate()
        self.app.center_snap.set(False)
        original, _ = self._draw("角丸パッド", (-0.8, -0.6), (0.8, 0.6), radius="0.2", angle="30")
        self.assertEqual(original.number, "1")
        baseline = self._state()
        history = tuple(self.app.history._snapshots)
        self.assertEqual(self.app.mode, "draw")
        self.assertTrue(self.app._selected_number_entry.winfo_viewable())
        self.assertFalse(self.app._selected_number_entry.instate(["disabled"]))
        self.assertEqual(self.app.selected_number.get(), "1")
        commit_header("9")
        self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
        self.assertIsNone(self.app._number_editor)
        self.assertEqual(self.app.sketch.pads, [replace(original, number="9")])
        self.assertEqual(self.app.selected_number.get(), "9")
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

        self._click((original.x, original.y), world=True)
        self.assertEqual(self.app.selected_number.get(), "1")
        commit_header("9", button=True)
        self.assertIsNone(self.app._number_editor)
        renamed = self.app.sketch.pads[0]
        self.assertEqual(renamed, replace(original, number="9"))
        self._button("矩形パッド")
        self.assertEqual(self.app.pad_number.get(), "1")
        self._drag_world((3, 1), (4, 2))
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["9", "1"])
        self.assertEqual(self.app.sketch.pads[0], renamed)
        self.assertEqual(self.app.selected_number.get(), "1")
        self.assertEqual(self.app.pad_number.get(), "2")
        self._button("1操作戻す")
        self.assertEqual(self.app.sketch.pads, [renamed])
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._button("選択/移動")
        self._click((original.x, original.y), world=True)

        self.assertEqual(self.app.selected_number.get(), "1")
        commit_header("")
        self.assertEqual(self.app.selected_number.get(), "")
        self.assertIsNone(self.app._number_editor)
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._button("番号を変更")
        self.assertEqual(self.app.selected_number.get(), "")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        commit_header("7", button=True)
        self.assertEqual(self.app.sketch.pads, [replace(original, number="7")])
        self.assertEqual(self.app.selected_number.get(), "7")
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._click((original.x, original.y), world=True)
        self.assertEqual(self.app.selected_number.get(), "1")

        begin_table_editor()
        enter_inline_number("")
        self.assertIsInstance(self.app._number_editor, ttk.Entry)
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        enter_inline_number("discarded", "<Escape>")
        self.assertIsNone(self.app._number_editor)
        self.assertEqual(self.app.selected_number.get(), "1")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

        begin_table_editor()
        editor = self.app._number_editor
        editor.delete(0, "end")
        editor.insert(0, "focus-cancelled")
        self.app.canvas.focus_set()
        self._pump()
        self.assertIsNone(self.app._number_editor)
        self.assertEqual(self.app.selected_number.get(), "1")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

        begin_table_editor()
        enter_inline_number("12")
        self.assertIsNone(self.app._number_editor)
        self.assertEqual(self.app.selected_number.get(), "12")
        self.assertEqual(self.app.sketch.pads, [replace(original, number="12")])
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

    def test_axis_mirror_copy_uses_origin_preserves_custom_copper_paste_and_undoes_as_one_operation(self):
        from footprint_sketcher.compound import CustomPad, world_outline, world_paste_shapes

        def select_rows(indices):
            children = self.app.pad_table.get_children()
            self.app.pad_table.selection_set([children[index] for index in indices])
            self.app.pad_table.focus(children[indices[-1]])
            self.app.pad_table.event_generate("<<TreeviewSelect>>", when="tail")
            self._pump()
            self.assertEqual(self.app.selected_indices, set(indices))

        def reflected(point, axis):
            return (point[0], -point[1]) if axis == "x" else (-point[0], point[1])

        def reflected_angle(angle, axis):
            return (-angle if axis == "x" else 180 - angle) % 360

        self._calibrate()
        self.app.center_snap.set(False)
        self._button("原点を選ぶ")
        origin = self._click((390, 250))
        self.assertEqual(self.app.sketch.origin, origin)
        self.assertNotEqual(origin, (450, 280))
        for variable, value in ((self.app.pad_width, "4"), (self.app.pad_height, "1"),
                                (self.app.pad_angle, "0"), (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((0, 0), world=True)
        self._entry(self.app.pad_width, "1")
        self._entry(self.app.pad_height, "3")
        self._click((-1.5, 1), world=True)
        self._button("選択/移動")
        select_rows([0, 1])
        self._entry(self.app.merge_number, "L")
        self._button("選択図形を合体")
        custom = self.app.sketch.pads[0]
        corner = max(range(len(custom.corners)),
                     key=lambda index: (custom.corners[index][0], -custom.corners[index][1]))
        self._entry(self.app.fillet_radius, "0.2")
        self._button("角を選んでフィレット")
        screen = self.app._corner_screens(custom)[corner]
        self._event("<Button-1>", screen)
        self._event("<ButtonRelease-1>", screen)
        self._escape()
        self._entry(self.app.pad_angle, "30")
        self._button("選択パッドに適用")
        self._button("数値サイズで配置")
        for variable, value in ((self.app.pad_width, "1.8"), (self.app.pad_height, "0.8"),
                                (self.app.pad_angle, "35"), (self.app.pad_radius, "0.2")):
            self._entry(variable, value)
        self._click((3, 2), world=True)
        self._button("選択/移動")
        originals = tuple(self.app.sketch.pads)
        self.assertIsInstance(originals[0], CustomPad)
        self.assertTrue(originals[0].curve_edges)
        self.assertEqual(originals[0].angle, 30)
        self.assertEqual((originals[1].angle, originals[1].radius), (35, 0.2))
        baseline, payload = self._state(), self._export_bytes()
        history = tuple(self.app.history._snapshots)

        for axis, text in (("x", "X軸で反転コピー"), ("y", "Y軸で反転コピー")):
            with self.subTest(axis=axis):
                select_rows([0, 1])
                self._button(text)
                self.assertEqual(tuple(self.app.sketch.pads[:2]), originals)
                self.assertEqual(len(self.app.sketch.pads), 4)
                self.assertEqual(self.app.selected_indices, {2, 3})
                copies = self.app.sketch.pads[2:]
                self.assertEqual(len({pad.number for pad in copies}), 2)
                self.assertFalse({pad.number for pad in copies} & {pad.number for pad in originals})
                for source, copied in zip(originals, copies):
                    self.assertIsInstance(copied, type(source))
                    self._assert_point((copied.x, copied.y), reflected((source.x, source.y), axis))
                    self.assertAlmostEqual(copied.angle % 360, reflected_angle(source.angle, axis))
                    self.assertEqual((copied.width, copied.height, copied.radius),
                                     (source.width, source.height, source.radius))
                    expected_outline = [reflected(point, axis) for point in world_outline(source)]
                    actual_outline = world_outline(copied)
                    self.assertEqual(len(actual_outline), len(expected_outline))
                    for expected in expected_outline:
                        self._assert_point(min(actual_outline, key=lambda point: math.dist(point, expected)), expected)
                    before_apertures = world_paste_shapes(source)
                    after_apertures = world_paste_shapes(copied)
                    self.assertEqual(len(after_apertures), len(before_apertures))
                    for before, after in zip(before_apertures, after_apertures):
                        self._assert_point((after.x, after.y), reflected((before.x, before.y), axis))
                        self.assertEqual((after.width, after.height, after.radius),
                                         (before.width, before.height, before.radius))
                        self.assertAlmostEqual(after.angle % 360, reflected_angle(before.angle, axis))
                self.assertEqual(self.app.sketch.origin, origin)
                self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
                mirrored_payload = self._export_bytes()
                for layers in (b'(layers "F.Cu" "F.Mask")', b'(layers "F.Paste")',
                               b'(layers "F.Cu" "F.Paste" "F.Mask")'):
                    self.assertEqual(mirrored_payload.count(layers), payload.count(layers) * 2)
                self._button("1操作戻す")
                self.assertEqual(self._state(), baseline)
                self.assertEqual(tuple(self.app.history._snapshots), history)
                self.assertEqual(self._export_bytes(), payload)

    def test_merge_fillet_attributes_move_copy_paste_and_each_undo_preserve_paste_openings(self):
        from footprint_sketcher.compound import CustomPad, world_outline, world_paste_shapes

        def select_rows(indices):
            children = self.app.pad_table.get_children()
            self.app.pad_table.selection_set([children[index] for index in indices])
            self.app.pad_table.focus(children[indices[-1]])
            self.app.pad_table.event_generate("<<TreeviewSelect>>", when="tail")
            self._pump()
            self.assertEqual(self.app.selected_indices, set(indices))
            self.assertEqual(self.app.merge_number.get(), "")

        def assert_apertures(actual, expected, delta=(0, 0)):
            self.assertEqual(len(actual), len(expected))
            for aperture, original in zip(actual, expected):
                self._assert_point((aperture.x, aperture.y),
                                   (original.x + delta[0], original.y + delta[1]))
                self.assertEqual((aperture.width, aperture.height, aperture.angle % 360, aperture.radius),
                                 (original.width, original.height, original.angle % 360, original.radius))

        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_width, "4"), (self.app.pad_height, "1"),
                                (self.app.pad_angle, "0"), (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((0, 0), world=True)
        self._entry(self.app.pad_width, "1")
        self._entry(self.app.pad_height, "3")
        self._click((-1.5, 1), world=True)
        self._button("選択/移動")
        originals = tuple(self.app.sketch.pads)
        baseline, baseline_payload = self._state(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        select_rows([0, 1])
        self._entry(self.app.merge_number, "L1")
        self.app._merge_entry.focus_set()
        self.app._merge_entry.event_generate("<Return>", when="tail")
        self._pump()
        self.assertEqual(len(self.app.sketch.pads), 1)
        merged = self.app.sketch.pads[0]
        self.assertIsInstance(merged, CustomPad)
        self.assertEqual(merged.number, "L1")
        self.assertEqual(len(merged.corners), 6)
        self.assertEqual(len(merged.paste_shapes), 2)
        assert_apertures(world_paste_shapes(merged), originals)
        self.assertEqual(self.app.selected_indices, {0})
        self.assertFalse(self.app.canvas.find_withtag("selection-handle"))
        self.assertEqual(len(self.app.canvas.find_withtag("paste-opening")), 2)
        for entry in (self.app._width_entry, self.app._height_entry, self.app._radius_entry):
            self.assertTrue(entry.instate(["disabled"]))
        self.assertEqual(tuple(self.app.history._snapshots)[:-1], history)
        merged_state = self._state()
        merged_payload = self._export_bytes()
        self.assertEqual(merged_payload.count(b'(pad "L1" smd custom'), 1)
        self.assertEqual(merged_payload.count(b'(pad "" smd rect'), 2)
        self.assertEqual(merged_payload.count(b'(layers "F.Cu" "F.Mask")'), 1)
        self.assertEqual(merged_payload.count(b'(layers "F.Paste")'), 2)

        # The rightmost top corner is convex and belongs to the horizontal leg.
        corner_index = max(range(len(merged.corners)),
                           key=lambda index: (merged.corners[index][0], -merged.corners[index][1]))
        chosen = merged.corners[corner_index]
        self._entry(self.app.fillet_radius, "0.2")
        self.app._fillet_radius_entry.focus_set()
        self.app._fillet_radius_entry.event_generate("<Return>", when="tail")
        self._pump()
        self.assertEqual(self.app.mode, "fillet")
        self.assertEqual(len(self.app.canvas.find_withtag("fillet-corner")), 6)
        screen = self.app._corner_screens(merged)[corner_index]
        self._event("<Button-1>", screen)
        self._event("<ButtonRelease-1>", screen)
        filleted = self.app.sketch.pads[0]
        self.assertIsInstance(filleted, CustomPad)
        self.assertNotEqual(filleted.outline, merged.outline)
        self.assertEqual(set(filleted.corners), set(merged.corners) - {chosen})
        self.assertNotIn(chosen, filleted.outline)
        for tangent in ((chosen[0] - 0.2, chosen[1]), (chosen[0], chosen[1] + 0.2)):
            self._assert_point(min(filleted.outline, key=lambda point: math.dist(point, tangent)), tangent)
        for corner in merged.corners:
            if corner != chosen:
                self.assertIn(corner, filleted.outline)
        self.assertEqual(filleted.paste_shapes, merged.paste_shapes)
        assert_apertures(world_paste_shapes(filleted), originals)
        self.assertEqual(len(self.app.history._snapshots), len(history) + 2)
        filleted_state = self._state()
        fillet_history = tuple(self.app.history._snapshots)
        self._event("<Button-3>", screen)
        self._event("<ButtonRelease-3>", screen)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
        self.assertFalse(self.app.canvas.find_withtag("fillet-corner"))
        self.assertEqual(self._state(), filleted_state)
        self.assertEqual(tuple(self.app.history._snapshots), fillet_history)

        self._entry(self.app.pad_number, "M1")
        self._entry(self.app.pad_angle, "30")
        self._button("選択パッドに適用")
        rotated = self.app.sketch.pads[0]
        self.assertEqual((rotated.number, rotated.angle), ("M1", 30))
        self.assertEqual((rotated.outline, rotated.corners, rotated.paste_shapes),
                         (filleted.outline, filleted.corners, filleted.paste_shapes))
        rotated_state = self._state()
        press, release = self._drag_world((rotated.x, rotated.y), (rotated.x + 0.5, rotated.y - 0.5))
        moved = self.app.sketch.pads[0]
        delta = (release[0] - press[0], release[1] - press[1])
        self._assert_point((moved.x, moved.y), (rotated.x + delta[0], rotated.y + delta[1]))
        self.assertEqual((moved.outline, moved.corners, moved.paste_shapes, moved.angle),
                         (rotated.outline, rotated.corners, rotated.paste_shapes, rotated.angle))
        assert_apertures(world_paste_shapes(moved), world_paste_shapes(rotated), delta)
        moved_state = self._state()
        self._button("コピー")
        copied = self.app.clipboard
        self.assertEqual(copied, (moved,))
        self._entry(self.app.pad_number, "M2")
        self._entry(self.app.pad_angle, "60")
        self._button("選択パッドに適用")
        self.assertEqual(self.app.clipboard, copied)
        before_paste = self._state()
        self._button("貼り付け")
        target = self._click((3, 1), world=True)
        self.assertEqual(len(self.app.sketch.pads), 2)
        pasted = self.app.sketch.pads[1]
        self.assertIsInstance(pasted, CustomPad)
        self.assertNotIn(pasted.number, {"M1", "M2"})
        self._assert_point((pasted.x, pasted.y), target)
        self.assertEqual((pasted.outline, pasted.corners, pasted.paste_shapes, pasted.angle),
                         (moved.outline, moved.corners, moved.paste_shapes, moved.angle))
        paste_delta = (pasted.x - moved.x, pasted.y - moved.y)
        assert_apertures(world_paste_shapes(pasted), world_paste_shapes(moved), paste_delta)
        self.assertEqual(len(world_outline(pasted)), len(world_outline(moved)))
        for actual, original in zip(world_outline(pasted), world_outline(moved)):
            self._assert_point(actual, (original[0] + paste_delta[0], original[1] + paste_delta[1]))
        self.assertEqual(self.app.clipboard, copied)
        self.assertEqual(len(self.app.history._snapshots), len(history) + 6)
        for expected in (before_paste, moved_state, rotated_state, filleted_state, merged_state, baseline):
            before_count = len(self.app.history._snapshots)
            self._button("1操作戻す")
            self.assertEqual(self._state(), expected)
            self.assertEqual(len(self.app.history._snapshots), before_count - 1)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), baseline_payload)

    def test_failed_nonconnected_merge_and_excessive_corner_radius_are_atomic(self):
        from footprint_sketcher.compound import CustomPad

        def select_rows(indices):
            children = self.app.pad_table.get_children()
            self.app.pad_table.selection_set([children[index] for index in indices])
            self.app.pad_table.focus(children[indices[-1]])
            self.app.pad_table.event_generate("<<TreeviewSelect>>", when="tail")
            self._pump()
            self.assertEqual(self.app.selected_indices, set(indices))

        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_width, "1"), (self.app.pad_height, "1"),
                                (self.app.pad_angle, "0"), (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((-2, 0), world=True)
        self._click((2, 0), world=True)
        self._button("選択/移動")
        select_rows([0, 1])
        baseline, payload = self._state(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        for number in ("", "NC"):
            with self.subTest(merge_number=number):
                self._entry(self.app.merge_number, number)
                self._button("選択図形を合体")
                self.assertEqual(self._state(), baseline)
                self.assertEqual(self.app.selected_indices, {0, 1})
                self.assertEqual(self.app.mode, "idle")
                self.assertEqual(tuple(self.app.history._snapshots), history)
                self.assertEqual(self._export_bytes(), payload)

        self._button("数値サイズで配置")
        self._entry(self.app.pad_width, "4")
        self._entry(self.app.pad_height, "0.5")
        self._click((0, 0), world=True)
        self._button("選択/移動")
        select_rows([0, 1, 2])
        self._entry(self.app.merge_number, "Connected")
        self._button("選択図形を合体")
        self.assertEqual(len(self.app.sketch.pads), 1)
        custom = self.app.sketch.pads[0]
        self.assertIsInstance(custom, CustomPad)
        self.assertFalse(self.app.canvas.find_withtag("selection-handle"))
        for entry in (self.app._width_entry, self.app._height_entry, self.app._radius_entry):
            self.assertTrue(entry.instate(["disabled"]))
        self.assertFalse(self.app._number_entry.instate(["disabled"]))
        baseline, payload = self._state(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        self._entry(self.app.fillet_radius, "100")
        self._button("角を選んでフィレット")
        corner = min(range(len(custom.corners)),
                     key=lambda index: (custom.corners[index][0], custom.corners[index][1]))
        screen = self.app._corner_screens(custom)[corner]
        self._event("<Button-1>", screen)
        self._event("<ButtonRelease-1>", screen)
        self.assertEqual(self._state(), baseline)
        self.assertEqual(self.app.selected_indices, {0})
        self.assertEqual(self.app.mode, "fillet")
        self.assertEqual(len(self.app.canvas.find_withtag("fillet-corner")), len(custom.corners))
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)
        self._escape()
        self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
        self.assertFalse(self.app.canvas.find_withtag("fillet-corner"))
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)

    def test_numeric_continuous_placement_frozen_copy_and_group_paste_undo(self):
        def assert_released(snapshot, history):
            self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
            self.assertEqual(self._state(), snapshot)
            self.assertEqual(tuple(self.app.history._snapshots), history)
            self.assertIsNone(self.app._drag)
            self.assertIsNone(self.app._preview_pad)
            self.assertFalse(self.app._preview_pads)
            self.assertFalse(self.app._paste_previews)
            self.assertFalse(self.app._snap_guides)
            self.assertFalse(self.app.canvas.find_withtag("preview"))
            self.assertFalse(self.app.canvas.find_withtag("center-snap-guide"))

        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_number, "1"), (self.app.pad_width, "1.6"),
                                (self.app.pad_height, "1.2"), (self.app.pad_angle, "30"),
                                (self.app.pad_radius, "0.25")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        targets = [self._click(point, world=True) for point in ((-3, -1), (1, 1))]
        originals = tuple(self.app.sketch.pads)
        self.assertEqual([pad.number for pad in originals], ["1", "2"])
        for pad, target in zip(originals, targets):
            self._assert_point((pad.x, pad.y), target)
            self.assertEqual((pad.width, pad.height, pad.angle, pad.radius), (1.6, 1.2, 30, 0.25))
        self.assertEqual((self.app.mode, self.app.tool.get(), self.app.pad_number.get()), ("pad", "pad", "3"))

        snapshot, history = self._state(), tuple(self.app.history._snapshots)
        self.app.center_snap.set(True)
        screen = self._screen((originals[0].x + 0.05, originals[0].y + 0.05), world=True)
        self._event("<Motion>", screen)
        self.assertEqual(len(self.app._paste_previews), 1)
        self.assertTrue(self.app._snap_guides)
        self._event("<Button-3>", screen)
        self._event("<ButtonRelease-3>", screen)
        assert_released(snapshot, history)
        self.app.center_snap.set(False)
        self._button("数値サイズで配置")
        self._click((0, 3), world=True)
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["1", "2", "3"])
        self._button("1操作戻す")
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self.app.mode, "pad")

        self._button("選択/移動")
        self._click((originals[0].x, originals[0].y), world=True)
        before_invalid_numeric = self._state()
        selected_number, selected_width = self.app.pad_number.get(), self.app.pad_width.get()
        self._entry(self.app.pad_width, "")
        self._button("数値サイズで配置")
        self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
        self.assertEqual(self.app.pad_number.get(), selected_number)
        self.assertEqual(self._state(), before_invalid_numeric)
        self._entry(self.app.pad_width, selected_width)
        self._button("選択パッドに適用")
        self.assertEqual(self._state(), before_invalid_numeric)
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["1", "2"])
        screen = self._screen((originals[1].x, originals[1].y), world=True)
        self._event("<Button-1>", screen, state=4)
        self._event("<ButtonRelease-1>", screen, state=4)
        self.assertEqual(self.app.selected_indices, {0, 1})
        self._button("コピー")
        copied = tuple(self.app.clipboard)
        self.assertEqual(copied, originals)

        # Updating the originals after Copy must not change the captured group.
        self.assertTrue(self.app._number_entry.instate(["disabled"]))
        for variable, value in ((self.app.pad_width, "2.4"), (self.app.pad_height, "0.8"),
                                (self.app.pad_angle, "0"), (self.app.pad_radius, "0.2")):
            self._entry(variable, value)
        self._button("選択パッドに適用")
        edited = tuple(self.app.sketch.pads)
        self.assertEqual([pad.number for pad in edited], ["1", "2"])
        self.assertNotEqual(edited, copied)
        self.assertEqual(tuple(self.app.clipboard), copied)

        self._button("貼り付け")
        before_invalid_tool = self._state()
        width = self.app.pad_width.get()
        self._entry(self.app.pad_width, "")
        self._button("数値サイズで配置")
        self.assertEqual((self.app.mode, self.app.tool.get()), ("paste", ""))
        self.assertEqual(self._state(), before_invalid_tool)
        self.assertEqual(tuple(self.app.clipboard), copied)
        self._entry(self.app.pad_width, width)
        snapshot, history = self._state(), tuple(self.app.history._snapshots)
        self.app.center_snap.set(True)
        screen = self._screen((edited[0].x + 0.05, edited[0].y + 0.05), world=True)
        self._event("<Motion>", screen)
        self.assertEqual(len(self.app._paste_previews), 2)
        self.assertTrue(self.app._snap_guides)
        self._event("<Button-3>", screen)
        self._event("<ButtonRelease-3>", screen)
        assert_released(snapshot, history)
        self.assertEqual(tuple(self.app.clipboard), copied)
        self.app.center_snap.set(False)
        self._button("貼り付け")
        target = self._click((-1, 2.5), world=True)
        self.assertEqual(tuple(self.app.sketch.pads[:2]), edited)
        pasted = tuple(self.app.sketch.pads[2:])
        self.assertEqual([pad.number for pad in pasted], ["3", "4"])
        self._assert_point((sum(pad.x for pad in pasted) / 2, sum(pad.y for pad in pasted) / 2), target)
        self._assert_point((pasted[1].x - pasted[0].x, pasted[1].y - pasted[0].y),
                           (copied[1].x - copied[0].x, copied[1].y - copied[0].y))
        for pad, source in zip(pasted, copied):
            self.assertEqual((pad.width, pad.height, pad.angle, pad.radius),
                             (source.width, source.height, source.angle, source.radius))
        self.assertEqual(self.app.mode, "paste")

        self._button("1操作戻す")
        self.assertEqual(tuple(self.app.sketch.pads), edited)
        self.assertEqual(tuple(self.app.clipboard), copied)
        self.assertEqual(self.app.mode, "paste")
        self._click((0, 2), world=True)
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["1", "2", "3", "4"])
        self.assertEqual(self.app.mode, "paste")
        self._escape()
        self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))

    def test_undo_commits_pending_points_drag_cancel_and_image_change(self):
        self.app.center_snap.set(False)
        self._button("校正の2点を選ぶ")
        self._click((150, 445))
        self._click((750, 445))
        self._entry(self.app.distance, "10")
        self._button("校正を適用")
        calibrated = self._state()
        self._button("画像の中央を原点に")
        origin_set = self._state()
        for variable, value in ((self.app.pad_number, "1"), (self.app.pad_width, "1.2"),
                                (self.app.pad_height, "0.8"), (self.app.pad_angle, "0"),
                                (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((-3, -1), world=True)
        self._button("選択/移動")
        self._click((-3, -1), world=True)
        created = self._state()

        self._button("校正の2点を選ぶ")
        a = self._click((150, 445))
        self.assertEqual(self.app.calibration_points, [a])
        self._button("1操作戻す")
        self.assertEqual(self.app.calibration_points, [])
        self.assertEqual(self._state(), created)
        self._escape()

        pad = self.app.sketch.pads[0]
        self._world_event("<Button-1>", (pad.x, pad.y))
        self._world_event("<B1-Motion>", (pad.x + 1, pad.y + 0.5))
        self.assertTrue(self.app._preview_pad is not None or self.app._preview_pads)
        self.assertEqual(self._state(), created)
        self._button("1操作戻す")
        self.assertIsNone(self.app._drag)
        self.assertIsNone(self.app._preview_pad)
        self.assertFalse(self.app._preview_pads)
        self.assertEqual(self._state(), created)
        self._world_event("<ButtonRelease-1>", (pad.x + 1, pad.y + 0.5))
        self.assertEqual(self._state(), created)

        self._entry(self.app.pad_width, "1.8")
        self._button("選択パッドに適用")
        edited = self._state()
        pad = self.app.sketch.pads[0]
        self._drag_world((pad.x, pad.y), (pad.x + 1, pad.y + 0.5))
        moved = self._state()
        pad = self.app.sketch.pads[0]
        corners = pad_corners(pad)
        fixed, moving = corners[0], corners[2]
        target = tuple(a + 1.2 * (b - a) for a, b in zip(fixed, moving))
        self._drag_world(moving, target)
        resized = self._state()
        self.assertNotEqual(resized, moved)

        # View, Copy and export must not consume an additional history step.
        self._button("＋")
        self._pan(17, -9)
        self._button("全体表示")
        self._button("コピー")
        self._export_bytes()
        for expected in (moved, edited, created, origin_set, calibrated, (None, None, ())):
            self._button("1操作戻す")
            self.assertEqual(self._state(), expected)
        self.assertFalse(self.app.history.can_undo)

        self._calibrate()
        for variable, value in ((self.app.pad_number, "1"), (self.app.pad_width, "1.2"),
                                (self.app.pad_height, "0.8"), (self.app.pad_angle, "0"),
                                (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((-3, -1), world=True)
        self.assertTrue(self.app.history.can_undo)
        with patch("footprint_sketcher.gui.filedialog.askopenfilename", return_value=str(SAMPLE)), \
             patch("footprint_sketcher.gui.messagebox.askyesno", return_value=True):
            self._button("下絵を開く…")
        self.assertEqual(self._state(), (None, None, ()))
        self.assertFalse(self.app.history.can_undo)
        self.assertTrue(self.app._undo_button.instate(["disabled"]))

    def test_multi_alignment_distribution_and_mass_attributes_preserve_numbers(self):
        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_number, "1"), (self.app.pad_width, "0.8"),
                                (self.app.pad_height, "0.6"), (self.app.pad_angle, "0"),
                                (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        for point in ((-5, -3), (-2, -1), (4, 2), (6, 3)):
            self._click(point, world=True)
        self._button("選択/移動")
        baseline = self._state()
        original = tuple(self.app.sketch.pads)

        def select_three():
            self._click((original[0].x, original[0].y), world=True)
            for pad in original[1:3]:
                screen = self._screen((pad.x, pad.y), world=True)
                self._event("<Button-1>", screen, state=4)
                self._event("<ButtonRelease-1>", screen, state=4)
            self.assertEqual(self.app.selected_indices, {0, 1, 2})
            self.assertEqual(len(self.app.canvas.find_withtag("selection-outline")), 3)
            self.assertEqual(len(self.app.canvas.find_withtag("selection-handle")), 0)

        for text, axis in (("横中心揃え", "y"), ("縦中心揃え", "x")):
            with self.subTest(alignment=text):
                select_three()
                mean = sum(getattr(pad, axis) for pad in original[:3]) / 3
                self._button(text)
                for before, after in zip(original[:3], self.app.sketch.pads[:3]):
                    self.assertAlmostEqual(getattr(after, axis), mean)
                    other = "x" if axis == "y" else "y"
                    self.assertEqual(getattr(after, other), getattr(before, other))
                    self.assertEqual((after.number, after.width, after.height, after.angle, after.radius),
                                     (before.number, before.width, before.height, before.angle, before.radius))
                self.assertEqual(self.app.sketch.pads[3], original[3])
                self._button("1操作戻す")
                self.assertEqual(self._state(), baseline)

        for text, axis in (("横等間隔", "x"), ("縦等間隔", "y")):
            with self.subTest(distribution=text):
                select_three()
                order = sorted(range(3), key=lambda index: getattr(original[index], axis))
                low, middle, high = order
                self._button(text)
                self.assertEqual(getattr(self.app.sketch.pads[low], axis), getattr(original[low], axis))
                self.assertEqual(getattr(self.app.sketch.pads[high], axis), getattr(original[high], axis))
                self.assertAlmostEqual(getattr(self.app.sketch.pads[middle], axis),
                                       (getattr(original[low], axis) + getattr(original[high], axis)) / 2)
                other = "x" if axis == "y" else "y"
                for before, after in zip(original[:3], self.app.sketch.pads[:3]):
                    self.assertEqual(getattr(after, other), getattr(before, other))
                    self.assertEqual(after.number, before.number)
                self.assertEqual(self.app.sketch.pads[3], original[3])
                self._button("1操作戻す")
                self.assertEqual(self._state(), baseline)

        select_three()
        self.assertTrue(self.app._number_entry.instate(["disabled"]))
        for variable, value in ((self.app.pad_width, "1.6"), (self.app.pad_height, "0.8"),
                                (self.app.pad_angle, "30"), (self.app.pad_radius, "0.2")):
            self._entry(variable, value)
        self._button("選択パッドに適用")
        for before, after in zip(original[:3], self.app.sketch.pads[:3]):
            self.assertEqual((after.number, after.x, after.y), (before.number, before.x, before.y))
            self.assertEqual((after.width, after.height, after.angle, after.radius), (1.6, 0.8, 30, 0.2))
        self.assertEqual(self.app.sketch.pads[3], original[3])
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)

    def test_center_snap_shift_axis_lock_and_group_move_keep_outside_pad_unchanged(self):
        self._calibrate()
        self.assertTrue(self.app.center_snap.get())
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_number, "1"), (self.app.pad_width, "1"),
                                (self.app.pad_height, "0.8"), (self.app.pad_angle, "0"),
                                (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        for point in ((-4, -1), (0, 1), (4, -2)):
            self._click(point, world=True)
        self._button("選択/移動")
        baseline = self._state()
        originals = tuple(self.app.sketch.pads)
        first = originals[0]

        self.app._snap_check.invoke()
        self._pump()
        self.assertTrue(self.app.center_snap.get())
        self._click((first.x, first.y), world=True)
        _, raw_end = self._drag_world((first.x, first.y), (0.05, -0.2))
        snapped = self.app.sketch.pads[0]
        self.assertEqual(snapped.x, originals[1].x)
        self.assertAlmostEqual(snapped.y, raw_end[1])
        self.assertEqual(tuple(self.app.sketch.pads[1:]), originals[1:])
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)

        self.app._snap_check.invoke()
        self._pump()
        self.assertFalse(self.app.center_snap.get())
        self._click((first.x, first.y), world=True)
        _, raw_end = self._drag_world((first.x, first.y), (0.05, -0.2))
        self._assert_point((self.app.sketch.pads[0].x, self.app.sketch.pads[0].y), raw_end)
        self.assertNotEqual(self.app.sketch.pads[0].x, originals[1].x)
        self._button("1操作戻す")

        self._click((first.x, first.y), world=True)
        start = self._world_event("<Button-1>", (first.x, first.y))
        end_screen = self._screen((first.x + 1, first.y + 0.3), world=True)
        self._event("<B1-Motion>", end_screen, state=1 | 256)
        image_end = self._event("<ButtonRelease-1>", end_screen, state=1)
        end = self.app.sketch.image_to_mm(image_end)
        shifted = self.app.sketch.pads[0]
        self.assertAlmostEqual(shifted.x, first.x + end[0] - start[0])
        self.assertEqual(shifted.y, first.y)
        self.assertEqual(tuple(self.app.sketch.pads[1:]), originals[1:])
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)

        self._click((first.x, first.y), world=True)
        screen = self._screen((originals[1].x, originals[1].y), world=True)
        self._event("<Button-1>", screen, state=4)
        self._event("<ButtonRelease-1>", screen, state=4)
        self.assertEqual(self.app.selected_indices, {0, 1})
        start = self._world_event("<Button-1>", (first.x, first.y))
        self._world_event("<B1-Motion>", (first.x + 1.5, first.y + 0.75))
        self.assertEqual(self._state(), baseline)
        self.assertEqual(set(self.app._preview_pads), {0, 1})
        self.assertEqual(len(self.app.canvas.find_withtag("pad-2")), 1)
        self.assertFalse(self.app.canvas.find_withtag("pad-0"))
        self.assertFalse(self.app.canvas.find_withtag("pad-1"))
        self.assertEqual(len(self.app.canvas.find_withtag("preview-pad-0")), 1)
        self.assertEqual(len(self.app.canvas.find_withtag("preview-pad-1")), 1)
        self.assertFalse(self.app.canvas.find_withtag("selection-handle"))
        end = self._world_event("<ButtonRelease-1>", (first.x + 1.5, first.y + 0.75))
        delta = (end[0] - start[0], end[1] - start[1])
        for before, after in zip(originals[:2], self.app.sketch.pads[:2]):
            self._assert_point((after.x, after.y), (before.x + delta[0], before.y + delta[1]))
            self.assertEqual((after.number, after.width, after.height, after.angle, after.radius),
                             (before.number, before.width, before.height, before.angle, before.radius))
        self._assert_point((self.app.sketch.pads[1].x - self.app.sketch.pads[0].x,
                            self.app.sketch.pads[1].y - self.app.sketch.pads[0].y),
                           (originals[1].x - first.x, originals[1].y - first.y))
        self.assertEqual(self.app.sketch.pads[2], originals[2])
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)

    def test_entry_shortcuts_do_not_invoke_document_actions_or_os_clipboard(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._draw("矩形パッド", (-4, -2), (-2, 0))
        self._button("選択/移動")
        self._button("コピー")
        baseline, clipboard = self._state(), self.app.clipboard
        entry = self.app._width_entry
        original_tags = entry.bindtags()
        # Keep the real widget-to-toplevel shortcut route, but remove Entry's
        # native class bindings so this test cannot touch the OS clipboard.
        entry.bindtags((str(entry), str(self.root)))
        try:
            entry.focus_force()
            self._pump()
            self.assertEqual(self.root.focus_get(), entry)
            with patch.object(self.app, "undo", wraps=self.app.undo) as undo, \
                 patch.object(self.app, "copy_selected", wraps=self.app.copy_selected) as copy, \
                 patch.object(self.app, "begin_paste", wraps=self.app.begin_paste) as paste:
                for key in ("z", "c", "v"):
                    entry.event_generate(f"<Control-{key}>", when="tail")
                    self._pump()
                undo.assert_not_called()
                copy.assert_not_called()
                paste.assert_not_called()
                self.assertEqual(self._state(), baseline)
                self.assertEqual(self.app.clipboard, clipboard)

                # A Canvas event proves the same bound action can reach the spy.
                self.app.canvas.focus_force()
                self._pump()
                self.app.canvas.event_generate("<Control-c>", when="tail")
                self._pump()
                copy.assert_called_once_with()
                undo.assert_not_called()
                paste.assert_not_called()
        finally:
            entry.bindtags(original_tags)

    def test_marquee_selection_ctrl_toggle_group_move_and_cancel_preserve_document(self):
        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_number, "1"), (self.app.pad_width, "0.8"),
                                (self.app.pad_height, "0.6"), (self.app.pad_angle, "0"),
                                (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        for point in ((-4, -2), (-1, -1), (2, 1), (5, 3)):
            self._click(point, world=True)
        self._button("選択/移動")
        originals = tuple(self.app.sketch.pads)
        baseline, payload = self._state(), self._export_bytes()
        history = tuple(self.app.history._snapshots)

        def ctrl_click(index):
            pad = self.app.sketch.pads[index]
            screen = self._screen((pad.x, pad.y), world=True)
            self._event("<Button-1>", screen, state=4)
            self._event("<ButtonRelease-1>", screen, state=4)

        def begin_marquee(start, end):
            self._world_event("<Button-1>", start)
            self._world_event("<B1-Motion>", end)
            self.assertIsNotNone(self.app._drag)
            self.assertEqual(self.app._drag["kind"], "marquee")
            self.assertEqual(len(self.app.canvas.find_withtag("selection-box")), 1)
            self.assertEqual(self._state(), baseline)
            self.assertEqual(tuple(self.app.history._snapshots), history)

        # The left edge cuts pad 1, whose center is still inside the rectangle.
        # Starting below it keeps the press on empty canvas rather than a pad.
        begin_marquee((-4.1, -3), (3, 2))
        self.assertEqual(self._export_bytes(), payload)
        self._world_event("<ButtonRelease-1>", (3, 2))
        self.assertEqual(self.app.selected_indices, {0, 1, 2})
        self.assertNotIn(3, self.app.selected_indices)
        self.assertFalse(self.app.canvas.find_withtag("selection-box"))
        self.assertEqual(len(self.app.canvas.find_withtag("selection-outline")), 3)
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)

        ctrl_click(3)
        self.assertEqual(self.app.selected_indices, {0, 1, 2, 3})
        ctrl_click(1)
        self.assertEqual(self.app.selected_indices, {0, 2, 3})
        ctrl_click(3)
        self.assertEqual(self.app.selected_indices, {0, 2})
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)

        first = originals[0]
        start = self._world_event("<Button-1>", (first.x, first.y))
        self.assertEqual(self.app._drag["kind"], "move")
        self._world_event("<B1-Motion>", (first.x + 1, first.y + 0.5))
        self.assertEqual(self.app.selected_indices, {0, 2})
        self.assertEqual(self._state(), baseline)
        end = self._world_event("<ButtonRelease-1>", (first.x + 1, first.y + 0.5))
        delta = (end[0] - start[0], end[1] - start[1])
        for index in (0, 2):
            self._assert_point((self.app.sketch.pads[index].x, self.app.sketch.pads[index].y),
                               (originals[index].x + delta[0], originals[index].y + delta[1]))
        for index in (1, 3):
            self.assertEqual(self.app.sketch.pads[index], originals[index])
        self.assertEqual(tuple(self.app.history._snapshots)[:-1], history)
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._state(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

        self._click((first.x, first.y), world=True)
        ctrl_click(2)
        selection, primary = set(self.app.selected_indices), self.app.selected_index
        self.assertEqual(selection, {0, 2})
        for cancel in ("escape", "secondary"):
            with self.subTest(marquee_cancel=cancel):
                begin_marquee((3, -3), (6, 4))
                if cancel == "escape":
                    self._escape()
                else:
                    screen = self._screen((6, 4), world=True)
                    self._event("<Button-3>", screen)
                    self._event("<ButtonRelease-3>", screen)
                self.assertIsNone(self.app._drag)
                self.assertFalse(self.app.canvas.find_withtag("selection-box"))
                self.assertEqual((self.app.selected_indices, self.app.selected_index), (selection, primary))
                self.assertEqual(self._state(), baseline)
                self.assertEqual(tuple(self.app.history._snapshots), history)
                self._world_event("<ButtonRelease-1>", (6, 4))
                self.assertEqual(self.app.selected_indices, selection)
                self.assertEqual(self._state(), baseline)
                self.assertEqual(self._export_bytes(), payload)
                self.assertEqual(tuple(self.app.history._snapshots), history)

    def test_drag_creation_stays_in_tool_and_advances_only_the_next_number(self):
        self._calibrate()
        cases = (
            ("矩形パッド", "rect", ((-5, -3), (-3, -2)), ((-2, -3), (0, -2))),
            ("角丸パッド", "roundrect", ((1, 1), (3, 2)), ((4, 1), (6, 2))),
        )
        for tool, shape, first_drag, second_drag in cases:
            with self.subTest(shape=shape):
                existing = tuple(self.app.sketch.pads)
                count = len(existing)
                self._button(tool)
                self._entry(self.app.pad_angle, "30")
                if shape == "roundrect":
                    self._entry(self.app.pad_radius, "0.25")
                self._drag_world(*first_drag)
                first = self.app.sketch.pads[-1]
                self.assertEqual(first.number, str(count + 1))
                self.assertEqual(self.app.pad_number.get(), str(count + 2))
                self.assertEqual((self.app.mode, self.app.tool.get()), ("draw", shape))
                self.assertTrue(self.app._selection_button.instate(["disabled"]))
                self._assert_fields(first, include_number=False)

                snapshot = self._state()
                self.app._selection_button.invoke()
                self._pump()
                self.app.apply_selected()
                self._pump()
                self.assertEqual(self._state(), snapshot)

                # No tool click between the two drags: keep the shape, angle and R.
                self._world_event("<Button-1>", second_drag[0])
                self._world_event("<B1-Motion>", second_drag[1])
                self.assertEqual(len(self.app.canvas.find_withtag("pad")), count + 1)
                self.assertIsNotNone(self.app._preview_pad)
                self._world_event("<ButtonRelease-1>", second_drag[1])
                second = self.app.sketch.pads[-1]
                self.assertEqual(len(self.app.sketch.pads), count + 2)
                self.assertEqual(tuple(self.app.sketch.pads[:count]), existing)
                self.assertEqual(self.app.sketch.pads[count], first)
                self.assertEqual(second.number, str(count + 2))
                self.assertEqual(self.app.pad_number.get(), str(count + 3))
                self.assertEqual((second.angle, second.radius), (first.angle, first.radius))
                self.assertEqual(second.radius > 0, shape == "roundrect")
                self.assertEqual((self.app.mode, self.app.tool.get()), ("draw", shape))
                self.assertTrue(self.app._selection_button.instate(["disabled"]))
                self._assert_fields(second, include_number=False)

                snapshot = self._state()
                self._escape()
                self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
                self.assertEqual(self._state(), snapshot)
                self.assertFalse(self.app._selection_button.instate(["disabled"]))
                self._assert_fields(second)

    def test_continuous_creation_skips_used_numbers_without_renumbering_existing_pads(self):
        self._calibrate()
        self._draw("矩形パッド", (-5, -3), (-3, -2))
        self._button("選択/移動")
        self._entry(self.app.pad_number, "3")
        self._button("選択パッドに適用")
        existing = self.app.sketch.pads[0]

        self._draw("矩形パッド", (-2, -3), (0, -2))
        self.assertEqual(self.app.sketch.pads[-1].number, "1")
        self.assertEqual(self.app.pad_number.get(), "2")
        self._drag_world((1, 1), (3, 2))
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["3", "1", "2"])
        self.assertEqual(self.app.pad_number.get(), "4")
        self.assertEqual(self.app.sketch.pads[0], existing)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("draw", "rect"))

        # An explicit switch restores the selected pad's number before applying.
        last = self.app.sketch.pads[-1]
        self._button("選択/移動")
        self._assert_fields(last)
        self._button("選択パッドに適用")
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["3", "1", "2"])

    def test_four_point_axis_lock_and_view_changes_preserve_anisotropic_calibration(self):
        from footprint_sketcher.gui import AXIS_METHOD

        self.app._method_combo.current(0)
        self.app._method_combo.event_generate("<<ComboboxSelected>>", when="tail")
        self._pump()
        self.assertEqual(self.app.calibration_method.get(), AXIS_METHOD)
        self._button("横2点＋縦2点を選ぶ")
        self._entry(self.app.distance, "10")
        self._entry(self.app.vertical_distance, "4")
        self.assertTrue(self.app._apply_button.instate(["disabled"]))

        x_a = self._click((150, 445))
        self.assertEqual(self.app.calibration_points, [x_a])
        self.assertTrue(self.app._apply_button.instate(["disabled"]))
        anchor = self._screen((750, 425))
        before_anchor = self.app.canvas.canvas_to_image(anchor)
        self._event("<MouseWheel>", anchor, delta=120)
        self._assert_point(self.app.canvas.canvas_to_image(anchor), before_anchor)
        self._pan(23, -17)
        self.assertEqual(self.app.calibration_points, [x_a])

        requested_x_b = self._click((750, 425))
        x_b = (requested_x_b[0], x_a[1])
        self.assertNotEqual(requested_x_b[1], x_a[1])
        self.assertEqual(self.app.calibration_points, [x_a, x_b])
        self.assertTrue(self.app._apply_button.instate(["disabled"]))
        y_a = self._click((300, 220))
        self.assertEqual(self.app.calibration_points, [x_a, x_b, y_a])
        self.assertTrue(self.app._apply_button.instate(["disabled"]))
        requested_y_b = self._click((320, 340))
        y_b = (y_a[0], requested_y_b[1])
        self.assertNotEqual(requested_y_b[0], y_a[0])
        self.assertEqual(self.app.calibration_points, [x_a, x_b, y_a, y_b])
        self.assertFalse(self.app._apply_button.instate(["disabled"]))

        self._button("校正を適用")
        calibration = self.app.sketch.calibration
        self.assertEqual((calibration.x_a, calibration.x_b, calibration.x_distance), (x_a, x_b, 10))
        self.assertEqual((calibration.y_a, calibration.y_b, calibration.y_distance), (y_a, y_b, 4))
        self.assertAlmostEqual(calibration.mm_per_pixel_x, 10 / abs(x_b[0] - x_a[0]))
        self.assertAlmostEqual(calibration.mm_per_pixel_y, 4 / abs(y_b[1] - y_a[1]))
        self.assertNotEqual(calibration.mm_per_pixel_x, calibration.mm_per_pixel_y)
        self._button("画像の中央を原点に")
        self._draw("矩形パッド", (-4, -2), (-2, 0))
        snapshot, baseline = self._state(), self._export_bytes()

        anchor = self._screen((450, 280))
        before_anchor = self.app.canvas.canvas_to_image(anchor)
        self._event("<MouseWheel>", anchor, delta=120)
        self._assert_point(self.app.canvas.canvas_to_image(anchor), before_anchor)
        self._pan(-19, 13)
        self._button("＋")
        self._button("−")
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)
        self._button("全体表示")
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)

    def test_anisotropic_pad_edit_handles_and_uniform_calibration_switch(self):
        from footprint_sketcher.gui import AXIS_METHOD, UNIFORM_METHOD

        self.app._method_combo.current(0)
        self.app._method_combo.event_generate("<<ComboboxSelected>>", when="tail")
        self._pump()
        self.assertEqual(self.app.calibration_method.get(), AXIS_METHOD)
        self._button("横2点＋縦2点を選ぶ")
        self._click((150, 445))
        self._click((750, 425))
        self._click((300, 220))
        self._click((320, 340))
        self._entry(self.app.distance, "10")
        self._entry(self.app.vertical_distance, "4")
        self._button("校正を適用")
        self.assertNotEqual(self.app.sketch.calibration.mm_per_pixel_x,
                            self.app.sketch.calibration.mm_per_pixel_y)
        self._button("画像の中央を原点に")
        pad, _ = self._draw("角丸パッド", (-4, -2), (-2, 0), radius="0.25", angle="30")
        self._button("選択/移動")
        anchor = self._screen((pad.x, pad.y), world=True)
        self._event("<MouseWheel>", anchor, delta=120)
        self._pan(17, -9)

        def assert_rendered_handles(current_pad):
            self._pump()
            for corner, world in enumerate(pad_corners(current_pad)):
                items = self.app.canvas.find_withtag(f"selection-handle-{corner}")
                self.assertEqual(len(items), 1)
                x0, y0, x1, y1 = self.app.canvas.coords(items[0])
                expected = self.app.canvas.image_to_canvas(self.app.sketch.mm_to_image(world))
                self._assert_point(((x0 + x1) / 2, (y0 + y1) / 2), expected)

        assert_rendered_handles(pad)
        start, end = self._drag_world((pad.x, pad.y), (pad.x + 1, pad.y + 0.5))
        moved = self.app.sketch.pads[0]
        self._assert_point((moved.x, moved.y), (pad.x + end[0] - start[0], pad.y + end[1] - start[1]))
        self.assertEqual((moved.number, moved.width, moved.height, moved.angle, moved.radius),
                         (pad.number, pad.width, pad.height, pad.angle, pad.radius))
        self._assert_fields(moved)
        assert_rendered_handles(moved)

        for corner in range(4):
            with self.subTest(anisotropic_handle=corner):
                original = self.app.sketch.pads[0]
                corners = pad_corners(original)
                moving, fixed = corners[corner], corners[(corner + 2) % 4]
                target = tuple(f + 1.1 * (m - f) for m, f in zip(moving, fixed))
                press, release = self._drag_world(moving, target)
                resized = self.app.sketch.pads[0]
                expected = tuple(m + b - a for m, a, b in zip(moving, press, release))
                self._assert_point(pad_corners(resized)[corner], expected)
                self._assert_point(pad_corners(resized)[(corner + 2) % 4], fixed)
                self.assertEqual((resized.number, resized.angle, resized.radius),
                                 (original.number, 30, original.radius))
                self._assert_fields(resized)
                assert_rendered_handles(resized)

        pads, origin = tuple(self.app.sketch.pads), self.app.sketch.origin
        baseline = self._export_bytes()
        self._button("全体表示")
        self.app._method_combo.current(1)
        self.app._method_combo.event_generate("<<ComboboxSelected>>", when="tail")
        self._pump()
        self.assertEqual(self.app.calibration_method.get(), UNIFORM_METHOD)
        self._button("校正の2点を選ぶ")
        a = self._click((150, 445))
        b = self._click((750, 425))
        self.assertNotEqual(a[1], b[1])
        self._entry(self.app.distance, "10")
        self._button("校正を適用")
        calibration = self.app.sketch.calibration
        self.assertEqual((calibration.a, calibration.b, calibration.distance), (a, b, 10))
        self.assertAlmostEqual(calibration.mm_per_pixel, 10 / math.dist(a, b))
        self.assertEqual((tuple(self.app.sketch.pads), self.app.sketch.origin), (pads, origin))
        self.assertEqual(self._export_bytes(), baseline)

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

        self._button("矩形パッド")
        history = tuple(self.app.history._snapshots)
        start, end = (2, -1), (4, 1)
        self._world_event("<Button-1>", start)
        self._world_event("<B1-Motion>", end)
        self.assertIsNotNone(self.app._preview_pad)
        self.assertTrue(self.app.canvas.find_withtag("preview"))
        screen = self._screen(end, world=True)
        self._event("<Button-3>", screen)
        self._event("<ButtonRelease-3>", screen)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
        self.assertIsNone(self.app._drag)
        self.assertIsNone(self.app._preview_pad)
        self.assertFalse(self.app._preview_pads)
        self.assertFalse(self.app._paste_previews)
        self.assertFalse(self.app._snap_guides)
        self.assertFalse(self.app.canvas.find_withtag("preview"))
        self.assertFalse(self.app.canvas.find_withtag("center-snap-guide"))
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._world_event("<ButtonRelease-1>", end)
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(self._export_bytes(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._draw("矩形パッド", start, end)
        self.assertEqual(len(self.app.sketch.pads), 2)
        self._button("1操作戻す")
        self.assertEqual(self._state(), snapshot)
        self.assertEqual(tuple(self.app.history._snapshots), history)


if __name__ == "__main__":
    unittest.main()
