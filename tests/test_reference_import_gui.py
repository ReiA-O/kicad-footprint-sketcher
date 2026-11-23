"""Document-protection checks for explicit clipboard and PDF imports.

Clipboard adapters and viewer results are supplied by the harness; no test reads
or writes the desktop clipboard or uses a native file chooser.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from footprint_sketcher.clipboard_reference import ClipboardReference
from footprint_sketcher.editing import Snapshot


_spec = importlib.util.spec_from_file_location(
    "_kfs_reference_extended_helpers", Path(__file__).with_name("test_extended_gui.py")
)
_extended = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_extended)


class ReferenceImportGuiTests(_extended.ExtendedGuiTests):
    def _import_state(self):
        tools = self.app.tools
        image = self.app.canvas.image
        return (
            Snapshot.capture(self.app.sketch), id(image), image.mode, image.size,
            image.tobytes(), self.app.image_name.get(), self.app.footprint_name.get(),
            tools.project_path, tuple(self.app.clipboard), tuple(self.app.history._snapshots),
            tuple(self.app.calibration_points), self.app.mode, self.app.tool.get(),
            self.app.selected_index, frozenset(self.app.selected_indices),
            self.app.distance.get(), self.app.vertical_distance.get(),
            tools.saved, tools.saved_name, tools.saved_image,
        )

    def _geometry_with_copy(self):
        self._calibrate()
        self.app.center_snap.set(False)
        self._numeric([(-2, 0), (2, 0)])
        self._tools(3)
        self._select_rows([0, 1])
        self._field("constraint", "等寸法")
        self._button("選択対象へ拘束を追加")
        self._button("選択/移動")
        self._select_rows([0])
        self._button("コピー")
        self._entry(self.app.footprint_name, "Reference_Import")
        self.app.tools.project_path = Path(self.temporary.name) / "existing.kfsproj"
        self.app.tools._mark_saved()
        self.assertTrue(self.app.history.can_undo)
        self.assertEqual(len(self.app.sketch.constraints), 1)
        self.assertTrue(self.app.clipboard)

    def test_clipboard_image_keeps_geometry_constraints_name_path_and_pad_clipboard(self):
        self._geometry_with_copy()
        tools = self.app.tools
        pads, constraints = tuple(self.app.sketch.pads), tuple(self.app.sketch.constraints)
        name, project, copied = self.app.footprint_name.get(), tools.project_path, self.app.clipboard
        saved = tools.saved, tools.saved_name, tools.saved_image
        image = Image.new("RGBA", (80, 60), (24, 55, 87, 109))
        with patch("footprint_sketcher.workspace.read_clipboard_reference", return_value=ClipboardReference(image=image)) as adapter:
            self._button("画像を貼り付け")
        adapter.assert_called_once_with(self.root)
        self.assertEqual(tuple(self.app.sketch.pads), pads)
        self.assertEqual(tuple(self.app.sketch.constraints), constraints)
        self.assertEqual(self.app.footprint_name.get(), name)
        self.assertEqual(tools.project_path, project)
        self.assertEqual(self.app.clipboard, copied)
        self.assertEqual((tools.saved, tools.saved_name, tools.saved_image), saved)
        self.assertIs(self.app.canvas.image, image)
        self.assertEqual(image.getpixel((10, 10)), (24, 55, 87, 109))
        self.assertIsNone(self.app.sketch.calibration)
        self.assertIsNone(self.app.sketch.origin)
        self.assertFalse(self.app.history.can_undo)
        self.assertFalse(self.app.calibration_points)
        self.assertFalse(self.app.selected_indices)
        self.assertEqual(self.app.distance.get(), "")
        self.assertEqual(self.app.vertical_distance.get(), "")

    def test_image_only_paste_is_unsaved_and_close_can_be_cancelled(self):
        tools = self.app.tools
        tools._mark_saved()
        document = self._document()
        image = Image.new("RGBA", (25, 20), (1, 2, 3, 4))
        with patch("footprint_sketcher.workspace.read_clipboard_reference", return_value=ClipboardReference(image=image)):
            self._button("画像を貼り付け")
        self.assertEqual(self._document(), document)
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask, \
             patch.object(self.root, "destroy") as destroy:
            tools.close()
        ask.assert_called_once()
        destroy.assert_not_called()
        self.assertIs(self.app.canvas.image, image)

    def test_clipboard_error_or_empty_result_leaves_entire_document_unchanged(self):
        self._geometry_with_copy()
        baseline = self._import_state()
        for error in (ValueError("画像をコピーしてください。"), None):
            with self.subTest(error=error):
                options = {"side_effect": error} if error is not None else {"return_value": ClipboardReference()}
                with patch("footprint_sketcher.workspace.read_clipboard_reference", **options):
                    self._button("画像を貼り付け")
                self.assertEqual(self._import_state(), baseline)
                self.assertIn("クリップボード読込失敗", self.app.status.get())

    def test_pdf_cancel_and_discard_refusal_keep_state_and_release_unused_image(self):
        self._geometry_with_copy()
        self._entry(self.app.footprint_name, "Unsaved_Change")
        baseline = self._import_state()
        path = Path(self.temporary.name) / "selected.pdf"
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(path)), \
             patch("footprint_sketcher.workspace.select_pdf_reference", return_value=None) as viewer:
            self._button("PDFを切り取る…")
        viewer.assert_called_once_with(self.root, path)
        self.assertEqual(self._import_state(), baseline)

        image = Image.new("RGBA", (60, 40), "white")
        selection = SimpleNamespace(image=image, label="PDF — 1ページ")
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value=str(path)), \
             patch("footprint_sketcher.workspace.select_pdf_reference", return_value=selection), \
             patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask:
            self._button("PDFを切り取る…")
        ask.assert_called_once()
        self.assertEqual(self._import_state(), baseline)
        with self.assertRaises(ValueError):
            image.getpixel((0, 0))

    def test_new_pdf_import_resets_project_target_and_stays_unsaved(self):
        self._geometry_with_copy()
        tools = self.app.tools
        old_saved = tools.saved, tools.saved_name, tools.saved_image
        name = self.app.footprint_name.get()
        image = Image.new("RGBA", (65, 45), (10, 20, 30, 255))
        selection = SimpleNamespace(image=image, label="PDF — 2ページ・選択範囲")
        with patch("footprint_sketcher.workspace.filedialog.askopenfilename", return_value="selected.pdf"), \
             patch("footprint_sketcher.workspace.select_pdf_reference", return_value=selection):
            self._button("PDFを切り取る…")
        self.assertEqual(self.app.sketch.pads, [])
        self.assertEqual(self.app.sketch.constraints, [])
        self.assertIsNone(self.app.sketch.calibration)
        self.assertIsNone(self.app.sketch.origin)
        self.assertIsNone(tools.project_path)
        self.assertEqual(self.app.footprint_name.get(), name)
        self.assertEqual((tools.saved, tools.saved_name, tools.saved_image), old_saved)
        self.assertIs(self.app.canvas.image, image)
        self.assertIn("2ページ", self.app.image_name.get())
        self.assertFalse(self.app.history.can_undo)
        with patch("footprint_sketcher.workspace.messagebox.askyesno", return_value=False) as ask:
            self.assertFalse(tools._confirm_discard())
        ask.assert_called_once()

    def test_clipboard_pdf_uses_viewer_and_keeps_geometry_or_cancels_without_changes(self):
        self._geometry_with_copy()
        baseline = self._import_state()
        pads, constraints = tuple(self.app.sketch.pads), tuple(self.app.sketch.constraints)
        path = Path(self.temporary.name) / "clipboard.pdf"
        reference = ClipboardReference(pdf_path=path, name="クリップボードのPDF")
        with patch("footprint_sketcher.workspace.read_clipboard_reference", return_value=reference), \
             patch("footprint_sketcher.workspace.select_pdf_reference", return_value=None) as viewer:
            self._button("画像を貼り付け")
        viewer.assert_called_once_with(self.root, path)
        self.assertEqual(self._import_state(), baseline)

        image = Image.new("RGBA", (70, 35), (80, 90, 100, 255))
        selection = SimpleNamespace(image=image, label="PDF — 1ページ・選択範囲")
        with patch("footprint_sketcher.workspace.read_clipboard_reference", return_value=reference), \
             patch("footprint_sketcher.workspace.select_pdf_reference", return_value=selection), \
             patch("footprint_sketcher.workspace.messagebox.askyesno") as ask:
            self._button("画像を貼り付け")
        ask.assert_not_called()
        self.assertEqual(tuple(self.app.sketch.pads), pads)
        self.assertEqual(tuple(self.app.sketch.constraints), constraints)
        self.assertEqual(self.app.footprint_name.get(), baseline[6])
        self.assertEqual(self.app.tools.project_path, baseline[7])
        self.assertEqual(self.app.clipboard, baseline[8])
        self.assertIsNone(self.app.sketch.calibration)
        self.assertIsNone(self.app.sketch.origin)
        self.assertIs(self.app.canvas.image, image)

    def test_clipboard_image_pixels_and_alpha_survive_project_save_and_reopen(self):
        from footprint_sketcher.persistence import load_project

        self._entry(self.app.footprint_name, "Clipboard_Pixels")
        image = Image.new("RGBA", (3, 2), (10, 40, 80, 120))
        image.putpixel((2, 1), (200, 150, 100, 50))
        pixels = image.tobytes()
        with patch("footprint_sketcher.workspace.read_clipboard_reference", return_value=ClipboardReference(image=image)):
            self._button("画像を貼り付け")
        path = Path(self.temporary.name) / "clipboard.kfsproj"
        with patch("footprint_sketcher.workspace.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.tools.save()
        self._pump()
        self.assertTrue(path.is_file(), self.app.status.get())
        name, sketch, restored = load_project(path)
        self.addCleanup(restored.close)
        self.assertEqual(name, "Clipboard_Pixels")
        self.assertEqual(sketch.pads, [])
        self.assertEqual(restored.mode, "RGBA")
        self.assertEqual(restored.size, (3, 2))
        self.assertEqual(restored.tobytes(), pixels)
        self.assertEqual(self.app.tools.project_path, path)
        with patch("footprint_sketcher.workspace.messagebox.askyesno") as ask:
            self.assertTrue(self.app.tools._confirm_discard())
        ask.assert_not_called()

    def test_control_shift_v_imports_on_canvas_but_leaves_entry_shortcuts_alone(self):
        entry = self.app._width_entry
        original_tags = entry.bindtags()
        entry.bindtags((str(entry), str(self.root)))
        try:
            entry.focus_force()
            self._pump()
            self.assertEqual(self.root.focus_get(), entry)
            with patch("footprint_sketcher.workspace.read_clipboard_reference") as adapter:
                entry.event_generate("<Control-Shift-V>", when="tail")
                self._pump()
            adapter.assert_not_called()
        finally:
            entry.bindtags(original_tags)

        image = Image.new("RGBA", (12, 9), (100, 50, 10, 255))
        self.app.canvas.focus_force()
        self._pump()
        with patch("footprint_sketcher.workspace.read_clipboard_reference", return_value=ClipboardReference(image=image)) as adapter:
            self.app.canvas.event_generate("<Control-Shift-V>", when="tail")
            self._pump()
        adapter.assert_called_once_with(self.root)
        self.assertIs(self.app.canvas.image, image)

    def test_control_v_still_pastes_pads_without_reading_system_clipboard(self):
        self._geometry_with_copy()
        original = tuple(self.app.sketch.pads)
        self.app.canvas.focus_force()
        self._pump()
        with patch("footprint_sketcher.workspace.read_clipboard_reference") as adapter:
            self.app.canvas.event_generate("<Control-v>", when="tail")
            self._pump()
            self.assertEqual(self.app.mode, "paste")
            self._click((0, 2), world=True)
        adapter.assert_not_called()
        self.assertEqual(tuple(self.app.sketch.pads[:2]), original)
        self.assertEqual(len(self.app.sketch.pads), 3)
        self.assertNotIn(self.app.sketch.pads[2].number, {pad.number for pad in original})


def load_tests(loader, tests, pattern):
    """Reuse desktop helpers without repeating either inherited test class."""
    names = sorted(name for name in ReferenceImportGuiTests.__dict__ if name.startswith("test_"))
    return unittest.TestSuite(ReferenceImportGuiTests(name) for name in names)


if __name__ == "__main__":
    unittest.main()
