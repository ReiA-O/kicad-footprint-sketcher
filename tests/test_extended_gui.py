"""Interactive regressions for the adopted editor/document extensions."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

from footprint_sketcher.compound import CustomPad, world_outline, world_paste_shapes
from footprint_sketcher.editing import Snapshot
from footprint_sketcher.graphics import Graphic, graphic_outline, world_vertices


_spec = importlib.util.spec_from_file_location("_kfs_base_gui_tests", Path(__file__).with_name("test_gui_interactions.py"))
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
REPO = Path(__file__).resolve().parents[1]
OWNER = REPO / "docs" / "verification" / "windows-enter-20261123" / "owner-output" / "NE555_SOIC8_Practice.kicad_mod"


class ExtendedGuiTests(_base.GuiInteractionTests):
    def test_attached_reference_recalibrates_without_discarding_existing_geometry_or_constraints(self):
        self._calibrate()
        self._numeric([(-2, 0), (2, 0)])
        self._tools(3)
        self._select_rows([0, 1])
        self._field("constraint", "等寸法")
        self._button("選択対象へ拘束を追加")
        pads, constraints = tuple(self.app.sketch.pads), tuple(self.app.sketch.constraints)
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(_base.SAMPLE)):
            self.app.tools.attach_reference()
        self._pump()
        self.assertEqual(tuple(self.app.sketch.pads), pads)
        self.assertEqual(tuple(self.app.sketch.constraints), constraints)
        self.assertIsNone(self.app.sketch.origin)
        self.assertIsNone(self.app.sketch.calibration)
        self._calibrate()
        self.assertEqual(tuple(self.app.sketch.pads), pads)
        self.assertEqual(tuple(self.app.sketch.constraints), constraints)

    def _document(self):
        return Snapshot.capture(self.app.sketch)

    def _tools(self, tab=0):
        self.app.tools.open_panel(tab)
        self._pump()
        return self.app.tools

    def _select_rows(self, indices):
        children = self.app.pad_table.get_children()
        self.app.pad_table.selection_set([children[index] for index in indices])
        self.app.pad_table.focus(children[indices[-1]])
        self.app.pad_table.event_generate("<<TreeviewSelect>>", when="tail")
        self._pump()
        self.assertEqual(self.app.selected_indices, set(indices))

    def _field(self, name, value):
        variable = self.app.tools.fields[name]
        widgets = [widget for widget in self._walk(self.root)
                   if isinstance(widget, _base.ttk.Entry)
                   and str(widget.cget("textvariable")) == str(variable)]
        self.assertEqual(len(widgets), 1, name)
        if isinstance(widgets[0], _base.ttk.Combobox):
            self.assertIn(str(value), tuple(widgets[0].cget("values")))
            widgets[0].set(str(value))
            widgets[0].event_generate("<<ComboboxSelected>>", when="tail")
            self._pump()
        else:
            self._entry(variable, value)

    def _return(self, variable):
        widgets = [widget for widget in self._walk(self.root)
                   if isinstance(widget, _base.ttk.Entry)
                   and str(widget.cget("textvariable")) == str(variable)]
        self.assertEqual(len(widgets), 1)
        widgets[0].focus_set()
        widgets[0].event_generate("<Return>", when="tail")
        self._pump()

    def _numeric(self, points, *, width="1.2", height="0.8", radius="0", angle="0"):
        for variable, value in ((self.app.pad_width, width), (self.app.pad_height, height),
                                (self.app.pad_radius, radius), (self.app.pad_angle, angle)):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        for point in points:
            self._click(point, world=True)
        self._button("選択/移動")

    def _graphic(self, kind, points, layer="F.SilkS", *, filled=True):
        tools = self._tools(1)
        self._field("graphic", kind)
        self._field("layer", layer)
        self._field("stroke", "0.15")
        tools.filled.set(filled)
        if kind == "roundrect":
            self._field("graphic_radius", "0.2")
        count = len(self.app.sketch.pads)
        self._button("キャンバスで作図")
        if kind in ("arc", "polygon"):
            for point in points:
                self._click(point, world=True)
            if kind == "polygon":
                self._button("多角形を確定")
        else:
            self._drag_world(*points)
        self.assertEqual(len(self.app.sketch.pads), count + 1, self.app.status.get())
        result = self.app.sketch.pads[-1]
        self.assertIsInstance(result, Graphic)
        self.assertEqual((result.graphic_kind, result.layer), (kind, layer))
        self._button("選択/移動")
        return result

    def _l_custom(self):
        self._numeric([(0, 0)], width="4", height="1")
        self._numeric([(-1.5, 1)], width="1", height="3")
        self._select_rows([0, 1])
        self._entry(self.app.merge_number, "L")
        self._button("選択図形を合体")
        self.assertIsInstance(self.app.sketch.pads[0], CustomPad)
        return self.app.sketch.pads[0]

    def _export_named(self, name):
        self.export_count += 1
        directory = Path(self.temporary.name) / f"named-{self.export_count}.pretty"
        directory.mkdir()
        self._entry(self.app.footprint_name, name)
        with patch("footprint_sketcher.gui.filedialog.askdirectory", return_value=str(directory)):
            self._button(".kicad_mod を書き出す…")
        output = directory / (name + ".kicad_mod")
        self.assertTrue(output.is_file(), self.app.status.get())
        return output

    def test_circle_diameter_radius_constraint_and_line_equal_length_follow_gui_edits(self):
        self._calibrate()
        self.app.center_snap.set(False)
        circle = self._graphic("circle", [(-4, -2), (-2, 0)])
        self._click((circle.x, circle.y), world=True)
        self.assertEqual(self.app.selected_indices, {0})
        self.assertTrue(self.app._radius_entry.instate(["disabled"]))
        self._entry(self.app.pad_width, "3")
        self._return(self.app.pad_width)
        resized = self.app.sketch.pads[0]
        self.assertIsInstance(resized, Graphic)
        self.assertEqual((resized.graphic_kind, resized.width, resized.height), ("circle", 3, 3))
        self.assertEqual((resized.x, resized.y, resized.angle), (circle.x, circle.y, circle.angle))
        self._tools(3)
        self._field("constraint", "R")
        self._field("value", "1.25")
        self._button("選択対象へ拘束を追加")
        constrained_circle = self.app.sketch.pads[0]
        self.assertEqual((constrained_circle.width, constrained_circle.height, constrained_circle.radius),
                         (2.5, 2.5, 0))
        self.assertEqual((self.app.sketch.constraints[0].kind, self.app.sketch.constraints[0].value),
                         ("radius", 1.25))

        first = self._graphic("line", [(0, -2), (1.5, -1)])
        second = self._graphic("line", [(2, 1), (4, 1)])
        for line in (first, second):
            self.assertAlmostEqual(line.width, math.dist(*world_vertices(line)))
        self._tools(3)
        self._select_rows([1, 2])
        before_relation = self._document()
        self._field("constraint", "等長")
        self._button("選択対象へ拘束を追加")
        self.assertEqual(len(self.app.sketch.constraints), 2)
        self.assertEqual(self.app.sketch.constraints[1].kind, "equal_width")
        self._select_rows([1])
        self.assertFalse(self.app._width_entry.instate(["disabled"]))
        self.assertTrue(self.app._height_entry.instate(["disabled"]))
        self.assertTrue(self.app._radius_entry.instate(["disabled"]))
        before_edit, history = self._document(), tuple(self.app.history._snapshots)
        old_lines = tuple(self.app.sketch.pads[1:])
        self._entry(self.app.pad_width, "3.4")
        self._return(self.app.pad_width)
        self.assertEqual(self.app.sketch.pads[0], constrained_circle)
        for before, line in zip(old_lines, self.app.sketch.pads[1:]):
            self.assertAlmostEqual(line.width, 3.4)
            self.assertAlmostEqual(math.dist(*world_vertices(line)), 3.4)
            self.assertEqual((line.height, line.radius, line.angle), (before.height, 0, before.angle))
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._document(), before_edit)
        self._button("1操作戻す")
        self.assertEqual(self._document(), before_relation)

    def test_explicit_width_bridge_connects_separate_terminals_without_changing_openings(self):
        from shapely import LineString, Point, Polygon

        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-3, 0), (3, 0)], width="1.6", height="1.2", radius="0.1")
        self._tools(0)
        self._select_rows([0, 1])
        self._field("mask_margin", "0.05")
        self._field("paste_margin", "-0.02")
        self._button("選択へ位置・穴・面・開口を適用")
        originals = tuple(self.app.sketch.pads)
        original_paste = [world_paste_shapes(pad) for pad in originals]
        baseline, payload = self._document(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        self._tools(2)
        self._field("bridge_width", "0.3")
        self._button("選択した同一ネット端子を銅箔接続")
        self.assertEqual(tuple(self.app.sketch.pads[:2]), originals)
        self.assertEqual(len(self.app.sketch.pads), 3)
        bridge = self.app.sketch.pads[2]
        self.assertIsInstance(bridge, CustomPad)
        self.assertEqual((bridge.number, bridge.layers), (originals[0].number, ("F.Cu",)))
        self.assertFalse(world_paste_shapes(bridge))
        self.assertEqual([world_paste_shapes(pad) for pad in self.app.sketch.pads[:2]], original_paste)
        copper = Polygon(world_outline(bridge))
        self.assertTrue(copper.is_valid)
        self.assertEqual(copper.geom_type, "Polygon")
        midpoint = ((originals[0].x + originals[1].x) / 2, (originals[0].y + originals[1].y) / 2)
        self.assertTrue(copper.contains(Point(midpoint)))
        section = copper.intersection(LineString([(midpoint[0], midpoint[1]-1),
                                                  (midpoint[0], midpoint[1]+1)]))
        self.assertAlmostEqual(section.length, 0.3, places=7)
        connected = self._export_bytes()
        self.assertEqual(connected.count(b"smd custom"), 1)
        self.assertEqual(connected.count(b'(layers "F.Cu")'), 1)
        self.assertEqual(connected.count(b'(layers "F.Cu" "F.Paste" "F.Mask")'), 2)
        self.assertNotIn(b'(layers "F.Paste")', connected)
        self.assertEqual(connected.count(b'(solder_mask_margin 0.05)'), 2)
        self.assertEqual(connected.count(b'(solder_paste_margin -0.02)'), 2)
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)

    def test_placement_attribute_enter_and_delete_only_affect_the_next_pad_template(self):
        self._calibrate()
        self.app.center_snap.set(False)
        for variable, value in ((self.app.pad_width, "2"), (self.app.pad_height, "1.4"),
                                (self.app.pad_angle, "0"), (self.app.pad_radius, "0")):
            self._entry(variable, value)
        self._button("数値サイズで配置")
        self._click((-2, 0), world=True)
        original = self.app.sketch.pads[0]
        self.assertEqual(original.pad_type, "smd")
        self.assertEqual((self.app.mode, self.app.tool.get()), ("pad", "pad"))
        self.assertEqual(self.app.selected_indices, {0})
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        tools = self._tools(0)
        for name, value in dict(pad_type="thru_hole", shape="oval", side="B",
                                 drill_w="0.6", drill_h="0.4", x="3.25", y="-2").items():
            self._field(name, value)
        tools.mask.set(True)
        tools.paste.set(False)
        self._return(tools.fields["drill_h"])
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("pad", "pad"))
        self.assertEqual(self.app.selected_indices, {0})
        tools.delete()
        self._pump()
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("pad", "pad"))
        self.assertEqual(tools.fields["pad_type"].get(), "thru_hole")

        target = self._click((2, 1), world=True)
        self.assertEqual(len(self.app.sketch.pads), 2)
        self.assertEqual(self.app.sketch.pads[0], original)
        placed = self.app.sketch.pads[1]
        self.assertEqual((placed.pad_type, placed.pad_shape), ("thru_hole", "oval"))
        self.assertEqual((placed.drill_width, placed.drill_height), (0.6, 0.4))
        self.assertEqual(placed.layers, ("*.Cu", "*.Mask"))
        self._assert_point((placed.x, placed.y), target)
        self.assertEqual((placed.width, placed.height), (2, 1.4))
        self.assertNotEqual(placed.number, original.number)
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        placed_document = self._document()
        screen = self._screen(target, world=True)
        self._event("<Button-3>", screen)
        self._event("<ButtonRelease-3>", screen)
        self.assertEqual((self.app.mode, self.app.tool.get()), ("idle", "select"))
        self.assertEqual(self._document(), placed_document)
        self.assertEqual(self.app.selected_indices, {1})
        for name, value in dict(x="2.5", y="-1", drill_w="0.8", drill_h="0.5").items():
            self._field(name, value)
        self._return(tools.fields["drill_h"])
        self.assertEqual(self.app.sketch.pads[0], original)
        edited = self.app.sketch.pads[1]
        self.assertEqual((edited.pad_type, edited.pad_shape), ("thru_hole", "oval"))
        self.assertEqual((edited.drill_width, edited.drill_height), (0.8, 0.5))
        self._assert_point((edited.x, edited.y), (2.5, -1))
        self.assertEqual(len(self.app.history._snapshots), len(history) + 2)
        self._button("1操作戻す")
        self.assertEqual(self._document(), placed_document)
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

    def test_copper_connection_retains_terminals_and_smd_paste_split_retains_copper_and_mask(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-1, 0), (1, 0)], width="4", height="2", radius="0.1")
        self._tools(0)
        self._select_rows([0, 1])
        self._field("mask_margin", "0.05")
        self._button("選択へ位置・穴・面・開口を適用")
        tools = self._tools(2)
        originals = tuple(self.app.sketch.pads)
        baseline, payload = self._document(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        self._select_rows([0, 1])
        self._button("選択した同一ネット端子を銅箔接続")
        self.assertEqual(tuple(self.app.sketch.pads[:2]), originals)
        self.assertEqual(len(self.app.sketch.pads), 3)
        bridge = self.app.sketch.pads[2]
        self.assertIsInstance(bridge, CustomPad)
        self.assertEqual(bridge.number, originals[0].number)
        self.assertEqual(bridge.layers, ("F.Cu",))
        self.assertFalse(bridge.paste_shapes)
        self.assertFalse(world_paste_shapes(bridge))
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        connected_payload = self._export_bytes()
        self.assertEqual(connected_payload.count(b"smd custom"), 1)
        self.assertEqual(connected_payload.count(b'(layers "F.Cu")'), 1)
        self.assertEqual(connected_payload.count(b'(layers "F.Cu" "F.Paste" "F.Mask")'), 2)
        self.assertNotIn(b'(layers "F.Paste")', connected_payload)
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)

        self._select_rows([0])
        self._field("split_x", "2")
        self._field("split_y", "1")
        self._field("split_gap", "0.2")
        self._button("選択パッドのPasteを分割")
        self.assertEqual(len(self.app.sketch.pads), 2)
        split = self.app.sketch.pads[0]
        self.assertIsInstance(split, CustomPad)
        self.assertEqual(self.app.sketch.pads[1], originals[1])
        self.assertEqual((split.number, split.layers, split.mask_margin),
                         (originals[0].number, originals[0].layers, originals[0].mask_margin))
        old_copper, new_copper = world_outline(originals[0]), world_outline(split)
        self.assertEqual(len(old_copper), len(new_copper))
        for point in old_copper:
            self._assert_point(min(new_copper, key=lambda candidate: math.dist(candidate, point)), point)
        windows = sorted(world_paste_shapes(split), key=lambda aperture: aperture.x)
        self.assertEqual(len(windows), 2)
        for window in windows:
            self.assertAlmostEqual(window.width, 1.9)
            self.assertAlmostEqual(window.height, 2)
            self.assertAlmostEqual(window.y, originals[0].y)
            self.assertAlmostEqual(window.angle % 360, originals[0].angle % 360)
            self.assertAlmostEqual(window.radius, 0.1)
        left, right = windows
        actual_gap = (right.x - right.width / 2) - (left.x + left.width / 2)
        self.assertAlmostEqual(actual_gap, float(tools.fields["split_gap"].get()), places=7)
        self.assertAlmostEqual(left.x - left.width / 2, originals[0].x - originals[0].width / 2)
        self.assertAlmostEqual(right.x + right.width / 2, originals[0].x + originals[0].width / 2)
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        split_payload = self._export_bytes()
        self.assertEqual(split_payload.count(b"smd custom"), 1)
        self.assertEqual(split_payload.count(b'(layers "F.Cu" "F.Mask")'), 1)
        self.assertEqual(split_payload.count(b'(layers "F.Paste")'), 2)
        self.assertEqual(split_payload.count(b'(layers "F.Cu" "F.Paste" "F.Mask")'), 1)
        self.assertIn(b'(solder_mask_margin 0.05)', split_payload)
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)

    def test_delete_keeps_remaining_numbers_and_is_one_undo(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-3, 0), (0, 0), (3, 0)])
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        original = tuple(self.app.sketch.pads)
        self._select_rows([1])
        self.app.canvas.focus_set()
        self.app.canvas.event_generate("<Delete>", when="tail")
        self._pump()
        self.assertEqual(self.app.sketch.pads, [original[0], original[2]])
        self.assertEqual([pad.number for pad in self.app.sketch.pads], ["1", "3"])
        self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._tools(3)
        self._select_rows([0, 1])
        self._field("constraint", "水平")
        self._button("選択対象へ拘束を追加")
        constrained, history = self._document(), tuple(self.app.history._snapshots)
        self._select_rows([1])
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False):
            self.app.tools.delete()
        self.assertEqual(self._document(), constrained)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=True):
            self.app.tools.delete()
        self._pump()
        self.assertEqual(self.app.sketch.pads, [original[0], original[2]])
        self.assertFalse(self.app.sketch.constraints)
        self._button("1操作戻す")
        self.assertEqual(self._document(), constrained)
        self.assertEqual(tuple(self.app.history._snapshots), history)

    def test_three_copy_modes_transform_custom_and_graphic_without_changing_sources(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._l_custom()
        self._graphic("polygon", [(3, -1), (4, -1), (3.25, 0)], "B.Cu")
        tools = self._tools(2)
        originals = tuple(self.app.sketch.pads)
        baseline, history = self._document(), tuple(self.app.history._snapshots)

        def reflected(point, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            fraction = ((point[0]-a[0])*dx + (point[1]-a[1])*dy) / (dx*dx+dy*dy)
            return (2*(a[0]+fraction*dx)-point[0], 2*(a[1]+fraction*dy)-point[1])

        cases = (("点A→点Bへコピー", "reference", lambda p: (p[0]+1, p[1]+2)),
                 ("指定中心で点対称コピー", "point", lambda p: (1-p[0], -1.5-p[1])),
                 ("点A–点Bの軸で線対称コピー", "line", lambda p: reflected(p, (-1, 0), (2, 2))))
        for button, mode, transform in cases:
            with self.subTest(mode=mode):
                self._select_rows([0, 1])
                for name, value in dict(ax=-1, ay=0, bx=0, by=2, cx=0.5, cy=-0.75).items():
                    self._field(name, value)
                if mode == "line":
                    self._field("bx", 2)
                tools.keep_numbers.set(False)
                self._button(button)
                self.assertEqual(tuple(self.app.sketch.pads[:2]), originals)
                copies = self.app.sketch.pads[2:]
                self.assertEqual(len(copies), 2)
                self.assertFalse({pad.number for pad in copies} & {pad.number for pad in originals})
                for source, copied in zip(originals, copies):
                    self.assertIsInstance(copied, type(source))
                    self.assertEqual(copied.layers, source.layers)
                    self._assert_point((copied.x, copied.y), transform((source.x, source.y)))
                    if isinstance(source, Graphic):
                        old = world_vertices(source, graphic_outline(source))
                        new = world_vertices(copied, graphic_outline(copied))
                    else:
                        old, new = world_outline(source), world_outline(copied)
                        old_paste, new_paste = world_paste_shapes(source), world_paste_shapes(copied)
                        self.assertEqual(len(old_paste), len(new_paste))
                        for before, after in zip(old_paste, new_paste):
                            self._assert_point((after.x, after.y), transform((before.x, before.y)))
                            self.assertEqual((after.width, after.height, after.radius),
                                             (before.width, before.height, before.radius))
                    self.assertEqual(len(new), len(old))
                    for point in old:
                        expected = transform(point)
                        self._assert_point(min(new, key=lambda candidate: math.dist(candidate, expected)), expected)
                self.assertEqual(len(self.app.history._snapshots), len(history) + 1)
                self._button("1操作戻す")
                self.assertEqual(self._document(), baseline)
                self.assertEqual(tuple(self.app.history._snapshots), history)
        self._select_rows([0, 1])
        tools.keep_numbers.set(True)
        self._button("指定中心で点対称コピー")
        self.assertEqual([p.number for p in self.app.sketch.pads[2:]], [p.number for p in originals])
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)

    def test_hole_back_side_and_opening_attributes_apply_and_export(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(0, 0)], width="2", height="2", radius="0.2")
        tools = self._tools(0)
        original = self.app.sketch.pads[0]
        baseline = self._document()
        cases = (("thru_hole", "oval", "0.6", "0.4"),
                 ("np_thru_hole", "circle", "1", "1"))
        for kind, shape, drill_w, drill_h in cases:
            with self.subTest(kind=kind):
                self._select_rows([0])
                for name, value in dict(x="1.25", y="-1.5", pad_type=kind, shape=shape, side="B",
                                         drill_w=drill_w, drill_h=drill_h).items():
                    self._field(name, value)
                tools.mask.set(True)
                tools.paste.set(False)
                self._button("選択へ位置・穴・面・開口を適用")
                pad = self.app.sketch.pads[0]
                self.assertEqual((pad.pad_type, pad.pad_shape), (kind, shape))
                self.assertEqual(pad.number, "" if kind == "np_thru_hole" else original.number)
                self._assert_point((pad.x, pad.y), (1.25, -1.5))
                self.assertEqual((pad.width, pad.height, pad.angle, pad.radius),
                                 (original.width, original.height, original.angle, 0))
                self.assertEqual((pad.drill_width, pad.drill_height), (float(drill_w), float(drill_h)))
                self.assertEqual(pad.layers, ("*.Cu", "*.Mask"))
                payload = self._export_bytes()
                self.assertIn(f"{kind} {shape}".encode(), payload)
                self.assertIn(b'(layers "*.Cu" "*.Mask")', payload)
                if kind == "thru_hole":
                    self.assertIn(b'(drill oval 0.6 0.4)', payload)
                else:
                    self.assertRegex(payload, rb'\(drill 1(?:\.0+)?\)')
                self._button("1操作戻す")
                self.assertEqual(self._document(), baseline)
        self._select_rows([0])
        for name, value in dict(pad_type="smd", shape="roundrect", side="B", drill_w="0", drill_h="0",
                                 mask_margin="0.03", paste_margin="-0.02", paste_ratio="-0.1").items():
            self._field(name, value)
        tools.mask.set(False)
        tools.paste.set(True)
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual(self.app.sketch.pads[0].layers, ("B.Cu", "B.Paste"))
        payload = self._export_bytes()
        for token in (b'(layers "B.Cu" "B.Paste")', b'(solder_paste_margin -0.02)',
                      b'(solder_paste_margin_ratio -0.1)', b'(solder_mask_margin 0.03)'):
            self.assertIn(token, payload)

    def test_six_layer_graphics_drag_arc_polygon_select_and_layer_visibility(self):
        self._calibrate()
        self.app.center_snap.set(False)
        cases = (("rect", "F.Cu", [(-4, -2), (-3, -1)]),
                 ("roundrect", "F.Mask", [(-1, -2), (0, -1)]),
                 ("line", "F.Paste", [(2, -2), (3, -1)]),
                 ("circle", "B.Cu", [(-4, 1), (-3, 2)]),
                 ("arc", "B.Mask", [(-1, 2), (-0.5, 1), (0, 2)]),
                 ("polygon", "B.Paste", [(2, 1), (3, 1), (2.25, 2)]))
        for kind, layer, points in cases:
            self._graphic(kind, points, layer)
        originals = tuple(self.app.sketch.pads)
        self.assertEqual({p.layer for p in originals}, {case[1] for case in cases})
        payload = self._export_bytes()
        for token in (b"(fp_rect ", b"(fp_poly ", b"(fp_line ", b"(fp_circle ", b"(fp_arc "):
            self.assertIn(token, payload)
        for index, graphic in enumerate(originals):
            point = world_vertices(graphic)[1] if graphic.graphic_kind == "arc" else (graphic.x, graphic.y)
            self._click(point, world=True)
            self.assertEqual(self.app.selected_indices, {index})
        tools = self._tools(1)
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        tools.layer_visible["F.Cu"].set(False)
        tools.layers_changed()
        self._pump()
        self.assertFalse(self.app.canvas.find_withtag("pad-0"))
        tools.layer_visible["F.Cu"].set(True)
        tools.layers_changed()
        self._pump()
        self.assertTrue(self.app.canvas.find_withtag("pad-0"))
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        tools.layer_locked["F.Cu"].set(True)
        tools.layers_changed()
        self._select_rows([0])
        self._tools(0)
        self._field("x", originals[0].x + 0.5)
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        tools.layer_locked["F.Cu"].set(False)
        tools.layers_changed()
        self.assertEqual(self._export_bytes(), payload)

    def test_persistent_symmetry_equal_size_drivers_conflict_and_release(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-2, 0), (2, 0)], radius="0.1")
        tools = self._tools(3)
        self._select_rows([0, 1])
        for name in ("cx", "cy"):
            self._field(name, "0")
        for kind in ("点対称", "等寸法"):
            self._field("constraint", kind)
            self._button("選択対象へ拘束を追加")
        self.assertEqual([c.kind for c in self.app.sketch.constraints], ["symmetric", "equal_size"])
        self._select_rows([0])
        self._entry(self.app.pad_width, "1.8")
        self._return(self.app.pad_width)
        for pad in self.app.sketch.pads:
            self.assertAlmostEqual(pad.width, 1.8)
        self._tools(0)
        self._field("x", "-3")
        self._field("y", "1")
        self._return(tools.fields["x"])
        self._assert_point((self.app.sketch.pads[0].x, self.app.sketch.pads[0].y), (-3, 1))
        self._assert_point((self.app.sketch.pads[1].x, self.app.sketch.pads[1].y), (3, -1))
        self._tools(3)
        self._field("constraint", "固定")
        self._button("選択対象へ拘束を追加")
        self.assertEqual(len(self.app.sketch.constraints), 3)
        baseline, payload, history = self._document(), self._export_bytes(), tuple(self.app.history._snapshots)
        self._field("constraint", "X位置")
        self._field("value", "99")
        self._button("選択対象へ拘束を追加")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)
        self._entry(self.app.pad_width, "2.1")
        self._return(self.app.pad_width)
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertEqual(self._export_bytes(), payload)
        tools.constraint_list.selection_clear(0, "end")
        tools.constraint_list.selection_set(2)
        self._button("選んだ拘束を解除")
        self.assertEqual(len(self.app.sketch.constraints), 2)
        self._entry(self.app.pad_width, "2.1")
        self._return(self.app.pad_width)
        for pad in self.app.sketch.pads:
            self.assertAlmostEqual(pad.width, 2.1)
        self._button("1操作戻す")
        for pad in self.app.sketch.pads:
            self.assertAlmostEqual(pad.width, 1.8)
        self.assertEqual(len(self.app.sketch.constraints), 2)

    def test_project_save_reopens_in_a_fresh_app_with_image_and_live_constraints(self):
        from footprint_sketcher.gui import SketcherApp

        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-2, 0), (2, 0)])
        tools = self._tools(3)
        self._select_rows([0, 1])
        self._field("constraint", "等寸法")
        self._button("選択対象へ拘束を追加")
        self._entry(self.app.footprint_name, "Saved_Editor")
        baseline = self._document()
        pixels, size = self.app.canvas.image.tobytes(), self.app.canvas.image.size
        path = Path(self.temporary.name) / "saved.kfsproj"
        self._tools(4)
        with patch("footprint_sketcher.workspace.filedialog.asksaveasfilename", return_value=str(path)):
            self._button("プロジェクトを保存…")
        self.assertTrue(path.is_file(), self.app.status.get())
        self.root.destroy()
        self.root = _base.tk.Tk()
        self.callback_errors = []

        def callback_error(kind, value, tb):
            import traceback
            self.callback_errors.append("".join(traceback.format_exception(kind, value, tb)))

        self.root.report_callback_exception = callback_error
        self.app = SketcherApp(self.root)
        self.root.geometry("1600x1000+0+0")
        self.root.focus_force()
        self._pump()
        self._tools(4)
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(path)), \
             patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=True):
            self._button("プロジェクトを開く…")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(self.app.canvas.image.size, size)
        self.assertEqual(self.app.canvas.image.tobytes(), pixels)
        self.assertEqual(self.app.footprint_name.get(), "Saved_Editor")
        self.assertFalse(self.app.history.can_undo)
        self._select_rows([0])
        self._entry(self.app.pad_width, "1.8")
        self._return(self.app.pad_width)
        for pad in self.app.sketch.pads:
            self.assertAlmostEqual(pad.width, 1.8)
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)

    def test_constraint_propagation_cannot_move_a_pad_on_a_locked_other_layer(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-2, 0), (2, 0)])
        tools = self._tools(0)
        self._select_rows([1])
        self._field("side", "B")
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertIn("F.Cu", self.app.sketch.pads[0].layers)
        self.assertIn("B.Cu", self.app.sketch.pads[1].layers)
        self._tools(3)
        self._select_rows([0, 1])
        self._field("constraint", "水平")
        self._button("選択対象へ拘束を追加")
        self.assertEqual(len(self.app.sketch.constraints), 1)
        baseline, payload = self._document(), self._export_bytes()
        history = tuple(self.app.history._snapshots)
        tools.layer_locked["B.Cu"].set(True)
        tools.layers_changed()
        self._select_rows([0])
        self._tools(0)
        self._field("y", "1.25")
        self._return(tools.fields["y"])
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertIn("ロック", self.app.status.get())
        self.assertEqual(self._export_bytes(), payload)
        self.assertTrue(tools.layer_locked["B.Cu"].get())

    def test_unsaved_name_only_or_image_only_change_requires_discard_confirmation(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(0, 0)])
        tools = self._tools(4)
        self._entry(self.app.footprint_name, "Confirm_Case")
        path = Path(self.temporary.name) / "confirm.kfsproj"
        with patch("footprint_sketcher.workspace.filedialog.asksaveasfilename", return_value=str(path)):
            self._button("プロジェクトを保存…")
        self.assertTrue(path.is_file(), self.app.status.get())
        saved_bytes = path.read_bytes()
        baseline = self._document()
        image = self.app.canvas.image.copy()
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask:
            self.assertTrue(tools._confirm_discard())
            ask.assert_not_called()

        self._entry(self.app.footprint_name, "Renamed_Only")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(self.app.canvas.image.tobytes(), image.tobytes())
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask:
            self.assertFalse(tools._confirm_discard())
            ask.assert_called_once()
        self.assertEqual(self.app.footprint_name.get(), "Renamed_Only")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(path.read_bytes(), saved_bytes)

        self._entry(self.app.footprint_name, "Confirm_Case")
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask:
            self.assertTrue(tools._confirm_discard())
            ask.assert_not_called()
        replacement = Image.new("RGBA", image.size, "#1a2b3c")
        self.app.canvas.set_image(replacement)
        self.app._redraw()
        self._pump()
        self.assertEqual(self._document(), baseline)
        self.assertEqual(self.app.footprint_name.get(), "Confirm_Case")
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask:
            self.assertFalse(tools._confirm_discard())
            ask.assert_called_once()
        self.assertEqual(self.app.canvas.image.tobytes(), replacement.tobytes())
        self.assertEqual(self._document(), baseline)
        self.assertEqual(path.read_bytes(), saved_bytes)
        self.app.canvas.set_image(image)
        self.app._redraw()
        self._pump()
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask:
            self.assertTrue(tools._confirm_discard())
            ask.assert_not_called()

    def test_owner_footprint_import_one_edit_and_reexport_preserve_other_entities(self):
        from footprint_sketcher.persistence import load_footprint

        self.assertTrue(OWNER.is_file())
        original_bytes = OWNER.read_bytes()
        name, expected, _classification = load_footprint(OWNER)
        self._tools(4)
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(OWNER)), \
             patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=True), \
             patch("footprint_sketcher.workspace.messagebox.showinfo"):
            self._button("KiCadフットプリントを開く…")
        self.assertEqual(self.app.sketch.pads, expected.pads)
        self.assertEqual(len(self.app.sketch.pads), 8)
        first_export = self._export_named(name)
        _name, roundtrip, _classification = load_footprint(first_export)
        self.assertEqual(roundtrip.pads, expected.pads)
        self._select_rows([0])
        self._entry(self.app.pad_width, "1.7")
        self._return(self.app.pad_width)
        self.assertEqual(self.app.sketch.pads, [replace(expected.pads[0], width=1.7), *expected.pads[1:]])
        output = self._export_named(name)
        _name, edited, _classification = load_footprint(output)
        self.assertEqual(edited.pads, self.app.sketch.pads)
        self.assertEqual(OWNER.read_bytes(), original_bytes)

    def test_pdf_page_and_crop_are_loaded_through_gui_and_bad_crop_keeps_the_document(self):
        if importlib.util.find_spec("pypdfium2") is None:
            self.skipTest("PDF GUI check requires the declared pypdfium2 dependency.")
        path = Path(self.temporary.name) / "pages.pdf"
        first, second = Image.new("RGB", (120, 80), "red"), Image.new("RGB", (120, 80), "blue")
        first.save(path, "PDF", resolution=72, save_all=True, append_images=[second])
        self._calibrate()
        self._tools(4)
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(path)), \
             patch("footprint_sketcher.workspace.simpledialog.askinteger", return_value=2), \
             patch("footprint_sketcher.workspace.simpledialog.askstring", return_value="20,10,100,60"), \
             patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=True):
            self._button("PDFのページ／範囲を開く…")
        self.assertEqual(self.app.canvas.image.size, (100, 60))
        pixel = self.app.canvas.image.getpixel((50, 30))
        self.assertGreater(pixel[2], 240)
        self.assertLess(max(pixel[:2]), 15)
        self.assertIn("2ページ", self.app.image_name.get())
        self.assertIsNone(self.app.sketch.calibration)
        self.assertIsNone(self.app.sketch.origin)
        baseline, pixels = self._document(), self.app.canvas.image.tobytes()
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(path)), \
             patch("footprint_sketcher.workspace.simpledialog.askinteger", return_value=2), \
             patch("footprint_sketcher.workspace.simpledialog.askstring", return_value="1000,0,10,10"):
            self._button("PDFのページ／範囲を開く…")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(self.app.canvas.image.tobytes(), pixels)
        self.assertFalse(self.app.history.can_undo)

    def test_replacement_reference_protects_geometry_and_allows_existing_mm_edits_before_calibration(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-2, -1), (2, 1)])
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(_base.SAMPLE)):
            self.app.tools.attach_reference()
        self._pump()
        baseline = self._document()
        self.assertIsNone(self.app.sketch.calibration)
        with patch("footprint_sketcher.gui.messagebox.askyesno", return_value=False) as ask:
            self.app.load_image(_base.SAMPLE)
        self._pump()
        ask.assert_called_once()
        self.assertEqual(self._document(), baseline)
        self._select_rows([0])
        self._entry(self.app.pad_width, "1.5")
        self._return(self.app.pad_width)
        self.assertEqual(self.app.sketch.pads[0].width, 1.5)
        self._select_rows([0, 1])
        mean_y = math.fsum(p.y for p in self.app.sketch.pads) / 2
        self._button("横中心揃え")
        self.assertEqual([p.y for p in self.app.sketch.pads], [mean_y, mean_y])
        before_mirror = self._document()
        self._button("Y軸で反転コピー")
        self.assertEqual(len(self.app.sketch.pads), 4)
        self.assertEqual([(p.x, p.y) for p in self.app.sketch.pads[2:]],
                         [(-p.x, p.y) for p in before_mirror.pads])
        self._button("1操作戻す")
        self.assertEqual(self._document(), before_mirror)
        self._button("1操作戻す")
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)

    def test_through_hole_wildcard_visibility_canvas_selection_and_edit_lock(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(0, 0)], width="2", height="1.4")
        tools = self._tools(0)
        for name, value in dict(pad_type="thru_hole", drill_w="0.5", drill_h="0.5").items():
            self._field(name, value)
        self._button("選択へ位置・穴・面・開口を適用")
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        tools.layer_locked["B.Cu"].set(True)
        tools.layers_changed()
        self._field("x", "1")
        self._return(tools.fields["x"])
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self._click((0, 0), world=True)
        self.assertFalse(self.app.selected_indices)
        for layer in ("F.Cu", "B.Cu", "F.Mask", "B.Mask"):
            tools.layer_visible[layer].set(False)
        tools.layers_changed()
        self._pump()
        self.assertFalse(self.app.canvas.find_withtag("pad-0"))
        tools.layer_visible["B.Cu"].set(True)
        tools.layers_changed()
        self._pump()
        self.assertTrue(self.app.canvas.find_withtag("pad-0"))
        self.assertEqual(self._document(), baseline)

    def test_edit_lock_rejects_equal_duplicate_deletion_creation_and_entry_to_locked_layer(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(0, 0)])
        tools = self._tools(2)
        tools.keep_numbers.set(True)
        self._button("点A→点Bへコピー")
        self.assertEqual(self.app.sketch.pads[0], self.app.sketch.pads[1])
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        tools.layer_locked["F.Cu"].set(True)
        tools.layers_changed()
        self._select_rows([0])
        tools.delete()
        self._pump()
        self.assertEqual(self._document(), baseline)
        self._select_rows([0])
        self._entry(self.app.pad_width, "1.8")
        self._return(self.app.pad_width)
        self.assertEqual(self._document(), baseline)
        self._button("数値サイズで配置")
        self._click((3, 1), world=True)
        self.assertEqual(self._document(), baseline)
        self._button("選択/移動")
        tools.layer_locked["F.Cu"].set(False)
        tools.layer_locked["B.Cu"].set(True)
        tools.layers_changed()
        self._select_rows([0])
        self._field("side", "B")
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)

    def test_graphic_drag_and_partial_point_drawing_undo_only_cancel_the_preview(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-3, 0)])
        tools = self._tools(1)
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        for kind in ("rect", "line", "circle", "arc", "polygon"):
            with self.subTest(kind=kind):
                self._field("graphic", kind)
                self._button("キャンバスで作図")
                self._world_event("<Button-1>", (0, 0))
                if kind not in ("arc", "polygon"):
                    self._world_event("<B1-Motion>", (2, 2))
                    self.assertIsInstance(self.app._preview_pad, Graphic)
                    self.assertTrue(self.app.canvas.find_withtag("pad-0"))
                self.assertFalse(self.app._undo_button.instate(["disabled"]))
                self.app.canvas.focus_set()
                self.app.canvas.event_generate("<Control-z>", when="tail")
                self._pump()
                self.assertEqual(self._document(), baseline)
                self.assertEqual(tuple(self.app.history._snapshots), history)
                self.assertIsNone(self.app._preview_pad)
                self.assertFalse(tools._drawing_points)
                self.assertEqual(self.app.mode, "idle")

    def test_npth_can_be_changed_to_numbered_pth_or_smd_and_undone(self):
        self._calibrate()
        self._numeric([(-2, 0), (2, 0)], width="2", height="1.4")
        tools = self._tools(0)
        self._select_rows([0, 1])
        for name, value in dict(pad_type="np_thru_hole", drill_w="0.5", drill_h="0.5").items():
            self._field(name, value)
        self._button("選択へ位置・穴・面・開口を適用")
        baseline = self._document()
        self.assertEqual([p.number for p in self.app.sketch.pads], ["", ""])
        self._field("pad_type", "thru_hole")
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual([p.pad_type for p in self.app.sketch.pads], ["thru_hole", "thru_hole"])
        self.assertEqual({p.number for p in self.app.sketch.pads}, {"1", "2"})
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)
        self._select_rows([0])
        self.assertTrue(self.app._number_entry.instate(["disabled"]))
        for name, value in dict(pad_type="smd", drill_w="0", drill_h="0").items():
            self._field(name, value)
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual((self.app.sketch.pads[0].pad_type, self.app.sketch.pads[0].number), ("smd", "1"))
        self.assertFalse(self.app._number_entry.instate(["disabled"]))
        self._button("1操作戻す")
        self.assertEqual(self._document(), baseline)

    def test_imported_rect_radius_changes_the_exported_shape_and_roundtrips(self):
        from footprint_sketcher.model import Pad, export_footprint
        from footprint_sketcher.persistence import load_footprint

        source_dir = Path(self.temporary.name) / "radius-source.pretty"
        source_dir.mkdir()
        source = export_footprint(source_dir, "RectRadius", [Pad("1", 0, 0, 2, 1, pad_shape="rect"),
                                                           Pad("2", 3, 0, 2, 1, pad_shape="oval")])
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(source)), \
             patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=True), \
             patch("footprint_sketcher.workspace.messagebox.showinfo"):
            self.app.tools.open_footprint()
        self._pump()
        self._select_rows([0])
        self.assertFalse(self.app._radius_entry.instate(["disabled"]))
        self._entry(self.app.pad_radius, "0.2")
        self._return(self.app.pad_radius)
        self.assertEqual((self.app.sketch.pads[0].pad_shape, self.app.sketch.pads[0].radius), ("roundrect", 0.2))
        rounded = self._export_named("RectRadius")
        _, reread, _ = load_footprint(rounded)
        self.assertEqual(reread.pads, self.app.sketch.pads)
        self._entry(self.app.pad_radius, "0")
        self._return(self.app.pad_radius)
        self.assertEqual((self.app.sketch.pads[0].pad_shape, self.app.sketch.pads[0].radius), ("rect", 0))
        square = self._export_named("RectRadiusSquare")
        _, reread, _ = load_footprint(square)
        self.assertEqual(reread.pads, self.app.sketch.pads)
        self._select_rows([0, 1])
        before, history = self._document(), tuple(self.app.history._snapshots)
        self._entry(self.app.pad_radius, "0.1")
        self._return(self.app.pad_radius)
        self.assertEqual(self._document(), before)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertIn("R", self.app.status.get())

        self._select_rows([0])
        self._entry(self.app.pad_radius, "0.2")
        self._return(self.app.pad_radius)
        tools = self._tools(0)
        self._field("shape", "oval")
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual((self.app.sketch.pads[0].pad_shape, self.app.sketch.pads[0].radius), ("oval", 0))
        self._entry(self.app.pad_width, "2.5")
        self._return(self.app.pad_width)
        self.assertEqual((self.app.sketch.pads[0].pad_shape, self.app.sketch.pads[0].width), ("oval", 2.5))
        self._field("shape", "rect")
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual((self.app.sketch.pads[0].pad_shape, self.app.sketch.pads[0].radius), ("rect", 0))

    def test_custom_mask_only_view_draws_each_eroded_component(self):
        from shapely import Polygon

        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-1.5, 0), (1.5, 0)], width="2", height="2")
        self._numeric([(0, 0)], width="3", height="0.2")
        self._select_rows([0, 1, 2])
        self._entry(self.app.merge_number, "M")
        self._button("選択図形を合体")
        tools = self._tools(0)
        self._field("mask_margin", "-0.15")
        self._button("選択へ位置・穴・面・開口を適用")
        pad = self.app.sketch.pads[0]
        self.assertEqual(Polygon(pad.outline).buffer(pad.mask_margin).geom_type, "MultiPolygon")
        tools.layer_visible["F.Cu"].set(False)
        tools.layer_visible["F.Paste"].set(False)
        tools.layers_changed()
        self._pump()
        self.assertEqual(len(self.app.canvas.find_withtag("pad-0")), 2)

    def test_custom_basic_shape_change_is_rejected_without_changing_its_openings(self):
        self._calibrate()
        self._l_custom()
        tools = self._tools(0)
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        self._field("shape", "rect")
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertIn("合体", self.app.status.get())

    def test_custom_mask_erosion_that_removes_the_entire_opening_is_atomic(self):
        from shapely import Polygon

        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-0.5, 0.5), (0.5, -0.5)], width="4", height="0.2", angle="45")
        self._select_rows([0, 1])
        self._entry(self.app.merge_number, "M")
        self._button("選択図形を合体")
        tools = self._tools(0)
        pad = self.app.sketch.pads[0]
        self.assertTrue(Polygon(pad.outline).buffer(-0.2).is_empty)
        baseline, history = self._document(), tuple(self.app.history._snapshots)
        self._field("mask_margin", "-0.2")
        self._button("選択へ位置・穴・面・開口を適用")
        self.assertEqual(self._document(), baseline)
        self.assertEqual(tuple(self.app.history._snapshots), history)
        self.assertIn("Mask", self.app.status.get())

    def test_custom_mask_expansion_keeps_a_hole_without_covering_the_underlay(self):
        from shapely import Polygon, union_all

        self._calibrate()
        self.app.center_snap.set(False)
        for point, width, height in (((-2.5, 0), "1", "6"), ((0, -2.5), "6", "1"),
                                      ((0, 2.5), "6", "1"), ((2.5, -1.75), "1", "1.5"),
                                      ((2.5, 1.75), "1", "1.5")):
            self._numeric([point], width=width, height=height)
        self._select_rows(range(5))
        self._entry(self.app.merge_number, "C")
        self._button("選択図形を合体")
        tools = self._tools(0)
        self._field("mask_margin", "1.1")
        self._button("選択へ位置・穴・面・開口を適用")
        pad = self.app.sketch.pads[0]
        expected = Polygon(world_outline(pad)).buffer(pad.mask_margin)
        self.assertEqual(len(expected.interiors), 1)
        baseline, pixels = self._document(), self.app.canvas.image.tobytes()
        tools.layer_visible["F.Cu"].set(False)
        tools.layer_visible["F.Paste"].set(False)
        tools.layers_changed()
        self._pump()
        parts = []
        for item in self.app.canvas.find_withtag("pad-0"):
            if self.app.canvas.type(item) != "polygon":
                continue
            coordinates = self.app.canvas.coords(item)
            points = [self.app.sketch.image_to_mm(self.app.canvas.canvas_to_image((x, y)))
                      for x, y in zip(coordinates[::2], coordinates[1::2])]
            parts.append(Polygon(points))
            self.assertEqual(self.app.canvas.itemcget(item, "outline"), "")
        displayed = union_all(parts)
        self.assertLess(expected.symmetric_difference(displayed).area, 1e-8)
        self.assertEqual(len(displayed.interiors), 1)
        self.assertEqual(self._document(), baseline)
        self.assertEqual(self.app.canvas.image.tobytes(), pixels)


def load_tests(loader, tests, pattern):
    """Reuse helpers without discovering the base class's existing GUI tests twice."""
    names = sorted(name for name in ExtendedGuiTests.__dict__ if name.startswith("test_"))
    return unittest.TestSuite(ExtendedGuiTests(name) for name in names)


if __name__ == "__main__":
    unittest.main()
