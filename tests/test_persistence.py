"""Meaningful file-boundary checks for import, retention and project recovery."""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
import unittest
import warnings
import zipfile
import zlib

from PIL import Image

from footprint_sketcher.compound import merge_pads, fillet_pad, world_paste_shapes, CustomPad
from footprint_sketcher.constraints import Constraint
from footprint_sketcher.graphics import Graphic, graphic_from_points
from footprint_sketcher.model import Pad, Sketch, export_footprint
from footprint_sketcher.persistence import load_footprint, safe_export_document, save_project, load_project


BASIC = '''(footprint "試験 \\"A\\""
 (version 20260206)
 (generator "fixture")
 (layer "F.Cu")
 (property "Reference" "REF**" (at 3 4 20) (layer "B.SilkS") (effects (font (size 2 1) bold)))
 (property "Value" "値" (at 0 -2) (layer "F.Fab") (hide yes))
 (descr "keep \\"quoted\\" and \\\\ path")
 (model "${KICAD}/part.step" (offset (xyz 0 0 1)))
 (pad "1" smd roundrect (at -2 1 30) (size 2 1) (layers "B.Cu" "B.Paste" "B.Mask") (roundrect_rratio 0.2) (uuid "pin-1"))
)\n'''


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / 'out'
        self.out.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def footprint(self, text=BASIC):
        path = self.root / 'input.kicad_mod'
        path.write_bytes(text.encode('utf-8'))
        return path

    def archive(self, document, extra=None):
        path = self.root / 'bad.kfsproj'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('project.json', json.dumps(document))
            for name, content in (extra or []):
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    archive.writestr(name, content)
        return path

    def project_document(self):
        path = self.root / 'good.kfsproj'
        sketch = Sketch()
        sketch.pads.append(Pad('1', 2, 3, 1, 2))
        save_project(path, sketch, None, 'Good')
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read('project.json'))

    def png_chunk(self, kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload)))

    def test_utf8_escape_crlf_and_unchanged_roundtrip_are_exact(self):
        text = BASIC.replace('\n', '\r\n')
        path = self.footprint(text)
        name, sketch, classification = load_footprint(path)
        self.assertEqual(name, '試験 "A"')
        self.assertEqual(sketch.pads[0].layers, ('B.Cu', 'B.Paste', 'B.Mask'))
        self.assertAlmostEqual(sketch.pads[0].radius, 0.2)
        self.assertTrue(any('編集可能: pad' in item for item in classification))
        output = safe_export_document(self.out, 'Roundtrip', sketch)
        # Renaming is a deliberate edit of only the root string.
        self.assertEqual(output.read_bytes(), text.replace('"試験 \\"A\\""', '"Roundtrip"', 1).encode())
        exact_dir = self.root / 'exact'; exact_dir.mkdir()
        # Original names are Windows-validated for export; a valid named source
        # exercises exact byte preservation independently of escaped pin text.
        valid = text.replace('試験 \\"A\\"', 'Valid', 1)
        name, sketch, _ = load_footprint(self.footprint(valid))
        self.assertEqual(safe_export_document(exact_dir, name, sketch).read_bytes(), valid.encode())
        self.assertEqual(path.read_bytes(), valid.encode())

    def test_reference_value_edit_replaces_only_string_token(self):
        name, sketch, _ = load_footprint(self.footprint(BASIC))
        sketch.reference = 'U"x\\y'
        sketch.value = '変更後'
        output = safe_export_document(self.out, 'NewName', sketch)
        expected = BASIC.replace('"試験 \\"A\\""', '"NewName"', 1)
        expected = expected.replace('"REF**"', '"U\\"x\\\\y"', 1).replace('"値"', '"変更後"', 1)
        self.assertEqual(output.read_text(encoding='utf-8'), expected)
        self.assertEqual(self.footprint(BASIC).read_text(encoding='utf-8'), BASIC)

    def test_pad_edit_keeps_uuid_and_passive_root_information(self):
        _, sketch, _ = load_footprint(self.footprint())
        sketch.pads[0] = replace(sketch.pads[0], width=2.4)
        output = safe_export_document(self.out, 'Edited', sketch)
        text = output.read_text(encoding='utf-8')
        self.assertIn('(uuid "pin-1")', text)
        self.assertIn('(model "${KICAD}/part.step" (offset (xyz 0 0 1)))', text)
        _, reread, _ = load_footprint(output)
        self.assertEqual(reread.pads[0].width, 2.4)
        self.assertEqual(reread.reference, sketch.reference)

    def test_unknown_references_retain_exact_but_block_geometry_edit(self):
        text = BASIC.replace('試験 \\"A\\"', 'Grouped', 1).replace('\n)', '\n (group "G" (uuid "group") (members "pin-1"))\n)')
        _, sketch, classification = load_footprint(self.footprint(text))
        self.assertTrue(any('保持のみ: group' in item for item in classification))
        # Known text-value editing cannot invalidate geometry/member references.
        sketch.value = 'Changed'
        safe_export_document(self.out, 'Grouped', sketch)
        sketch.pads[0] = replace(sketch.pads[0], x=9)
        with self.assertRaisesRegex(ValueError, '参照を保全'):
            safe_export_document(self.out, 'Other', sketch)
        self.assertFalse((self.out / 'Other.kicad_mod').exists())

    def test_basic_holes_and_graphics_import(self):
        text = '''(footprint "Shapes" (version 20260206) (layer "F.Cu")
        (pad "1" thru_hole oval (at 0 0 45) (size 3 2) (drill oval 1.5 0.8) (layers "*.Cu" "*.Mask"))
        (pad "" np_thru_hole circle (at 3 2) (size 1 1) (drill 1) (layers "*.Cu" "*.Mask"))
        (fp_rect (start -2 -3) (end 2 3) (stroke (width 0.1) (type solid)) (fill none) (layer "B.Fab"))
        (fp_line (start 0 0) (end 2 0) (stroke (width 0.1) (type solid)) (layer "F.SilkS"))
        (fp_circle (center 0 0) (end 1 0) (stroke (width 0.1) (type solid)) (fill none) (layer "F.CrtYd"))
        (fp_arc (start 1 0) (mid 0 -1) (end -1 0) (stroke (width 0.1) (type solid)) (layer "F.SilkS"))
        (fp_poly (pts (xy 0 0) (xy 2 0) (xy 0 1)) (stroke (width 0) (type solid)) (fill solid) (layer "F.Paste")))'''
        name, sketch, _ = load_footprint(self.footprint(text))
        self.assertEqual(name, 'Shapes')
        self.assertEqual(len(sketch.pads), 7)
        self.assertEqual(sketch.pads[0].drill_width, 1.5)
        self.assertEqual(sketch.pads[1].pad_type, 'np_thru_hole')
        self.assertEqual([pad.graphic_kind for pad in sketch.pads[2:]], ['rect', 'line', 'circle', 'arc', 'polygon'])
        sketch.pads[0] = replace(sketch.pads[0], x=1)
        output = safe_export_document(self.out, name, sketch)
        _, reread, _ = load_footprint(output)
        self.assertEqual(len(reread.pads), 7)
        self.assertEqual(reread.pads[0].x, 1)

    def test_generated_custom_and_original_paste_are_editable_together(self):
        custom = fillet_pad(merge_pads([Pad('1', 0, 0, 2, 1), Pad('2', 0.5, 0.5, 1, 2)], '3'), 0, 0.1)
        output = export_footprint(self.root, 'Compound', [custom])
        name, sketch, _ = load_footprint(output)
        self.assertEqual(len(sketch.pads), 1)
        self.assertIsInstance(sketch.pads[0], CustomPad)
        self.assertEqual(len(sketch.pads[0].paste_shapes), 2)
        sketch.pads[0] = replace(sketch.pads[0], x=3)
        edited = safe_export_document(self.out, name, sketch)
        _, reread, _ = load_footprint(edited)
        self.assertEqual(len(reread.pads[0].paste_shapes), 2)
        self.assertEqual(reread.pads[0].x, 3)

    def test_repeated_export_of_the_same_custom_is_byte_stable(self):
        custom = fillet_pad(merge_pads([Pad('1', 0, 0, 2, 2)], '1'), 0, 0.2)
        first = export_footprint(self.root, 'Stable', [custom])
        second = export_footprint(self.out, 'Stable', [custom])
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(custom.preserved, ())
        self.assertTrue(all(aperture.preserved == () for aperture in custom.paste_shapes))

    def test_identical_custom_instances_receive_distinct_stable_identities(self):
        from footprint_sketcher.persistence import _parse, _children, _atom

        custom = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        first = export_footprint(self.root, 'Identical', [custom, custom])
        second = export_footprint(self.out, 'Identical', [custom, custom])
        self.assertEqual(first.read_bytes(), second.read_bytes())
        pads = _children(_parse(first.read_text(encoding='utf-8')), 'pad')
        identities = [_atom(_children(pad, 'uuid')[0], 1) for pad in pads]
        self.assertEqual(len(identities), 4)
        self.assertEqual(len(set(identities)), 4)
        _, reopened, _ = load_footprint(first)
        self.assertEqual(len(reopened.pads), 2)
        self.assertEqual([len(pad.paste_shapes) for pad in reopened.pads], [1, 1])

    def test_generated_identity_does_not_replace_an_existing_pad_identity(self):
        from footprint_sketcher.persistence import _parse, _children, _atom

        custom = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        initial = export_footprint(self.root, 'Initial', [custom])
        initial_id = _atom(_children(_children(_parse(initial.read_text(encoding='utf-8')), 'pad')[0], 'uuid')[0], 1)
        existing = Pad('2', 5, 0, 1, 1, preserved=(f'(uuid "{initial_id}")',))
        first = export_footprint(self.root, 'Collision', [custom, existing])
        second = export_footprint(self.out, 'Collision', [custom, existing])
        self.assertEqual(first.read_bytes(), second.read_bytes())
        pads = _children(_parse(first.read_text(encoding='utf-8')), 'pad')
        identities = [_atom(_children(pad, 'uuid')[0], 1) for pad in pads]
        self.assertEqual(len(set(identities)), 3)
        self.assertEqual(identities[-1], initial_id)
        self.assertNotEqual(identities[0], initial_id)

    def test_unowned_paste_is_not_absorbed_into_a_generated_custom(self):
        custom = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        path = export_footprint(self.root, 'Ownership', [custom])
        text = path.read_text(encoding='utf-8')
        independent = '(pad "" smd rect (at 10 0) (size 1 1) (layers "F.Paste"))'
        text = text.rstrip()[:-1] + '\n' + independent + '\n)\n'
        name, sketch, _ = load_footprint(self.footprint(text))
        self.assertEqual(len(sketch.pads), 1)
        self.assertEqual(len(sketch.pads[0].paste_shapes), 1)
        self.assertIn(independent, sketch.preserved_nodes)
        exact = safe_export_document(self.out, name, sketch)
        self.assertEqual(exact.read_bytes(), text.encode())
        sketch.reference = 'U?'
        safe_export_document(self.out, name, sketch, overwrite=True)
        sketch.pads[0] = replace(sketch.pads[0], x=5)
        before = exact.read_bytes()
        with self.assertRaisesRegex(ValueError, '参照を保全'):
            safe_export_document(self.out, name, sketch, overwrite=True)
        self.assertEqual(exact.read_bytes(), before)

    def test_legacy_generated_custom_paste_without_ownership_is_retained(self):
        custom = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        from footprint_sketcher.model import _render_pad
        text = '(footprint "Legacy" (version 20260206) (generator "kicad_footprint_sketcher")\n'
        text += '\n'.join(_render_pad(custom)) + '\n)\n'
        name, sketch, _ = load_footprint(self.footprint(text))
        self.assertEqual(len(sketch.pads[0].paste_shapes), 0)
        self.assertTrue(any(node.startswith('(pad ""') for node in sketch.preserved_nodes))
        self.assertEqual(safe_export_document(self.out, name, sketch).read_bytes(), text.encode())
        sketch.value = 'Changed'
        safe_export_document(self.out, name, sketch, overwrite=True)
        sketch.pads[0] = replace(sketch.pads[0], x=3)
        with self.assertRaisesRegex(ValueError, '参照を保全'):
            safe_export_document(self.out, name, sketch, overwrite=True)

    def test_paste_ownership_uses_identities_across_order_changes_and_copy(self):
        from footprint_sketcher.editing import duplicate_pads
        from footprint_sketcher.persistence import _parse, _children, _atom

        first = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        second = merge_pads([Pad('2', 5, 0, 2, 2)], '2')
        text = export_footprint(self.root, 'Linked', [first, second]).read_text(encoding='utf-8')
        root = _parse(text)
        pads = _children(root, 'pad')
        # Reorder only source pad nodes, as an external formatter may do. Their
        # UUID ownership survives even when each window precedes its parent.
        replacements = [text[node.start:node.end] for node in reversed(pads)]
        for node, replacement in reversed(list(zip(pads, replacements))):
            text = text[:node.start] + replacement + text[node.end:]
        text = text.replace('(generator "kicad_footprint_sketcher")', '(generator "pcbnew")')
        name, sketch, _ = load_footprint(self.footprint(text))
        before = {pad.number: (pad.preserved, pad.paste_shapes[0].preserved) for pad in sketch.pads}
        self.assertEqual({pad.number: len(pad.paste_shapes) for pad in sketch.pads}, {'1': 1, '2': 1})
        parent = next(pad for pad in sketch.pads if pad.number == '1')
        sketch.pads[sketch.pads.index(parent)] = replace(parent, x=2)
        sketch.pads.extend(duplicate_pads([parent], sketch.pads, (10, 0)))
        output = safe_export_document(self.out, name, sketch)
        name, restored, _ = load_footprint(output)
        self.assertEqual(len(restored.pads), 3)
        self.assertTrue(all(len(pad.paste_shapes) == 1 for pad in restored.pads))
        for pad in restored.pads:
            if pad.number in before:
                self.assertEqual((pad.preserved, pad.paste_shapes[0].preserved), before[pad.number])
        identities = [_atom(_children(_parse(output.read_text(encoding='utf-8')), 'pad')[i].items[-1], 1)
                      for i in range(6)]
        self.assertEqual(len(set(identities)), 6)
        # The ownership property is regenerated once, never accumulated.
        restored.pads[0] = replace(restored.pads[0], y=1)
        second_output = safe_export_document(self.out, name, restored, overwrite=True)
        self.assertEqual(second_output.read_text(encoding='utf-8').count('"FootprintSketcher-PasteOwnership"'), 1)
        self.assertEqual(len(load_footprint(second_output)[1].pads), 3)

    def test_broken_duplicate_or_missing_paste_ownership_is_rejected(self):
        from footprint_sketcher.persistence import _parse, _children, _atom
        from footprint_sketcher.model import _quoted

        custom = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        text = export_footprint(self.root, 'BadOwnership', [custom]).read_text(encoding='utf-8')
        root = _parse(text)
        prop = next(node for node in _children(root, 'property')
                    if _atom(node, 1) == 'FootprintSketcher-PasteOwnership')
        ownership = json.loads(_atom(prop, 2))
        parent_id, [window_id] = ownership['pairs'][0]
        mutations = [
            {'version': 2, 'pairs': ownership['pairs']},
            {'version': 1, 'pairs': [[parent_id, ['missing']]]},
            {'version': 1, 'pairs': [[parent_id, [window_id, window_id]]]},
            {'version': 1, 'pairs': [[parent_id, [window_id]], [parent_id, [window_id]]]},
            {'version': 1, 'pairs': [[window_id, [parent_id]]]},
        ]
        value_atom = prop.items[2]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, '所有情報'):
                broken = text[:value_atom.start] + _quoted(json.dumps(mutation)) + text[value_atom.end:]
                load_footprint(self.footprint(broken))
        duplicate_property = text[:root.end-1] + text[prop.start:prop.end] + text[root.end-1:]
        with self.assertRaisesRegex(ValueError, '所有情報が重複'):
            load_footprint(self.footprint(duplicate_property))
        duplicate_identity = text.replace(f'(uuid "{window_id}")', f'(uuid "{parent_id}")')
        with self.assertRaisesRegex(ValueError, '識別子が重複または欠落'):
            load_footprint(self.footprint(duplicate_identity))

    def test_root_opening_defaults_and_local_zero_survive_moving_and_project(self):
        text = '''(footprint "Openings" (version 20260206) (generator "fixture")
        (solder_mask_margin 0.2) (solder_paste_margin -0.1) (solder_paste_ratio -0.05)
        (pad "1" smd rect (at 0 0) (size 4 2) (layers "F.Cu" "F.Paste" "F.Mask")
          (solder_mask_margin 0) (solder_paste_margin 0) (solder_paste_margin_ratio 0))
        (pad "2" smd rect (at 1 0) (size 4 2) (layers "F.Cu" "F.Paste" "F.Mask"))
        (pad "3" smd rect (at 2 0) (size 4 2) (layers "F.Cu" "F.Paste" "F.Mask")
          (solder_mask_margin -0.1) (solder_paste_margin -0.05) (solder_paste_margin_ratio 0)))'''
        name, sketch, _ = load_footprint(self.footprint(text))
        expected = [(0, 0, 0), (0.2, -0.1, -0.05), (-0.1, -0.05, 0)]
        self.assertEqual([(p.mask_margin, p.paste_margin, p.paste_ratio) for p in sketch.pads], expected)
        self.assertTrue(all(p.opening_explicit for p in sketch.pads))
        self.assertEqual(safe_export_document(self.out, name, sketch).read_bytes(), text.encode())
        project = save_project(self.root / 'openings.kfsproj', sketch, None, name)
        _, restored, _ = load_project(project)
        self.assertEqual(restored.pads, sketch.pads)
        restored.pads[0] = replace(restored.pads[0], x=5)
        output = safe_export_document(self.out, name, restored, overwrite=True)
        _, reread, _ = load_footprint(output)
        self.assertEqual([(p.mask_margin, p.paste_margin, p.paste_ratio) for p in reread.pads], expected)
        self.assertEqual(reread.pads[0].x, 5)
        self.assertEqual(output.read_text(encoding='utf-8').count('(solder_mask_margin 0)'), 1)
        self.assertEqual(output.read_text(encoding='utf-8').count('(solder_paste_margin_ratio 0)'), 2)

    def test_new_pad_does_not_inherit_retained_root_opening_values(self):
        text = '''(footprint "NewPad" (version 20260206)
        (solder_mask_margin 0.2) (solder_paste_margin -0.1) (solder_paste_ratio -0.05)
        (pad "1" smd rect (at 0 0) (size 4 2) (layers "F.Cu" "F.Paste" "F.Mask")))'''
        name, sketch, _ = load_footprint(self.footprint(text))
        sketch.pads.append(Pad('2', 5, 0, 4, 2))
        output = safe_export_document(self.out, name, sketch)
        _, reread, _ = load_footprint(output)
        self.assertEqual((reread.pads[0].mask_margin, reread.pads[0].paste_margin, reread.pads[0].paste_ratio), (0.2, -0.1, -0.05))
        self.assertEqual((reread.pads[1].mask_margin, reread.pads[1].paste_margin, reread.pads[1].paste_ratio), (0, 0, 0))
        self.assertEqual((world_paste_shapes(reread.pads[1])[0].width, world_paste_shapes(reread.pads[1])[0].height), (4, 2))

    def test_merge_uses_effective_root_paste_once_and_reopens_its_windows(self):
        text = '''(footprint "MergeOpenings" (version 20260206) (generator "fixture") (generator_version "10.0.7")
        (solder_mask_margin 0.2) (solder_paste_margin -0.1) (solder_paste_ratio -0.05)
        (pad "1" smd rect (at 0 0) (size 4 2) (layers "F.Cu" "F.Paste" "F.Mask") (solder_mask_margin 0))
        (pad "2" smd rect (at 1 0) (size 4 2) (layers "F.Cu" "F.Paste" "F.Mask") (solder_mask_margin 0)))'''
        name, sketch, _ = load_footprint(self.footprint(text))
        sketch.pads[:] = [merge_pads(sketch.pads, '1')]
        apertures = world_paste_shapes(sketch.pads[0])
        for aperture in apertures:
            self.assertAlmostEqual(aperture.width, 3.4)
            self.assertAlmostEqual(aperture.height, 1.6)
        output = safe_export_document(self.out, name, sketch)
        self.assertIn('(generator "kicad_footprint_sketcher")', output.read_text(encoding='utf-8'))
        self.assertNotIn('generator_version', output.read_text(encoding='utf-8'))
        _, reread, classification = load_footprint(output)
        self.assertEqual(len(reread.pads), 1, classification)
        self.assertIsInstance(reread.pads[0], CustomPad)
        self.assertEqual(reread.pads[0].mask_margin, 0)
        self.assertEqual(len(reread.pads[0].paste_shapes), 2)
        for aperture in world_paste_shapes(reread.pads[0]):
            self.assertAlmostEqual(aperture.width, 3.4)
            self.assertAlmostEqual(aperture.height, 1.6)
            self.assertEqual((aperture.mask_margin, aperture.paste_margin, aperture.paste_ratio), (0, 0, 0))
        reread.pads[0] = replace(reread.pads[0], x=10)
        twice = safe_export_document(self.out, name, reread, overwrite=True)
        _, reopened, _ = load_footprint(twice)
        self.assertEqual([(p.width, p.height) for p in world_paste_shapes(reopened.pads[0])], [(p.width, p.height) for p in apertures])

    def test_custom_using_copper_polygon_as_paste_is_retained_not_transformed(self):
        custom = merge_pads([Pad('1', 0, 0, 2, 1), Pad('2', 0.5, 0, 2, 1)], '3')
        path = export_footprint(self.root, 'DirectPaste', [custom])
        text = path.read_text(encoding='utf-8').replace('(layers "F.Cu" "F.Mask")', '(layers "F.Cu" "F.Mask" "F.Paste")', 1)
        name, sketch, classification = load_footprint(self.footprint(text))
        self.assertEqual(sketch.pads, [])
        self.assertTrue(any('保持のみ: pad' in c for c in classification))
        self.assertEqual(safe_export_document(self.out, name, sketch).read_bytes(), text.encode())
        sketch.pads.append(Pad('4', 5, 0, 1, 1))
        with self.assertRaisesRegex(ValueError, '参照を保全'):
            safe_export_document(self.out, 'EditedDirectPaste', sketch)

    def test_mounting_type_changes_keep_other_footprint_attribute_flags(self):
        text = '''(footprint "Mounting" (version 20260206)
        (attr smd board_only exclude_from_pos_files exclude_from_bom)
        (pad "1" smd rect (at 0 0) (size 2 2) (layers "F.Cu" "F.Mask"))
        (pad "2" smd rect (at 3 0) (size 2 2) (layers "F.Cu" "F.Mask")))'''
        name, sketch, _ = load_footprint(self.footprint(text))
        sketch.pads[0] = replace(sketch.pads[0], pad_type='thru_hole', layers=('*.Cu', '*.Mask'), drill_width=1, drill_height=1)
        output = safe_export_document(self.out, name, sketch)
        self.assertIn('(attr through_hole board_only exclude_from_pos_files exclude_from_bom)', output.read_text(encoding='utf-8'))
        _, reread, _ = load_footprint(output)
        reread.pads[0] = replace(reread.pads[0], pad_type='smd', layers=('F.Cu', 'F.Mask'), drill_width=0, drill_height=0)
        output = safe_export_document(self.out, name, reread, overwrite=True)
        self.assertIn('(attr smd board_only exclude_from_pos_files exclude_from_bom)', output.read_text(encoding='utf-8'))
        _, no_attr, _ = load_footprint(self.footprint(text.replace('(attr smd board_only exclude_from_pos_files exclude_from_bom)', '')))
        no_attr.pads[0] = replace(no_attr.pads[0], pad_type='thru_hole', layers=('*.Cu', '*.Mask'), drill_width=1, drill_height=1)
        self.assertIn('(attr through_hole)', safe_export_document(self.out, 'NewAttribute', no_attr).read_text(encoding='utf-8'))

    def test_kicad_boolean_metadata_survives_custom_geometry_edit(self):
        custom = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        text = export_footprint(self.root, 'NativeMetadata', [custom]).read_text(encoding='utf-8')
        text = text.replace('(generator "kicad_footprint_sketcher")', '(generator "pcbnew")')
        for jumpers in ('yes', 'no'):
            for fonts in ('yes', 'no'):
                with self.subTest(jumpers=jumpers, fonts=fonts):
                    flags = f'(duplicate_pad_numbers_are_jumpers {jumpers})\n (embedded_fonts {fonts})'
                    source = text.rstrip()[:-1] + flags + '\n)\n'
                    name, sketch, _ = load_footprint(self.footprint(source))
                    self.assertEqual(sketch.metadata['blocked_geometry'], [])
                    self.assertIn(f'(duplicate_pad_numbers_are_jumpers {jumpers})', sketch.preserved_nodes)
                    self.assertIn(f'(embedded_fonts {fonts})', sketch.preserved_nodes)
                    sketch.pads[0] = replace(sketch.pads[0], x=3)
                    output = safe_export_document(self.out, name, sketch, overwrite=True)
                    self.assertIn(flags, output.read_text(encoding='utf-8'))
                    _, reopened, _ = load_footprint(output)
                    self.assertEqual(reopened.pads[0].x, 3)
                    self.assertEqual(len(reopened.pads[0].paste_shapes), 1)

    def test_kicad_boolean_metadata_rejects_invalid_nested_or_duplicate_values(self):
        for key in ('duplicate_pad_numbers_are_jumpers', 'embedded_fonts'):
            for raw in (f'({key} true)', f'({key})', f'({key} yes extra)',
                        f'({key} (value yes))', f'({key} yes (future no))',
                        f'({key} yes) ({key} no)'):
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    load_footprint(self.footprint(f'(footprint "BadMetadata" (version 20260206) {raw})'))

    def test_kicad_10_native_saved_ownership_fixture_can_be_edited_without_losing_metadata(self):
        # Saved by Windows KiCad 10.0.7 with preserveUUID=True. Native save
        # reorders windows before their Custom and adds these boolean fields.
        source = Path(__file__).parent / 'fixtures' / 'kicad10-native-ownership.kicad_mod'
        original = source.read_bytes()
        name, sketch, _ = load_footprint(source)
        self.assertEqual(sketch.metadata['blocked_geometry'], [])
        self.assertEqual(len(sketch.pads), 1)
        self.assertEqual(len(sketch.pads[0].paste_shapes), 2)
        exact = safe_export_document(self.out, name, sketch)
        self.assertEqual(exact.read_bytes(), original)
        sketch.pads[0] = replace(sketch.pads[0], x=sketch.pads[0].x + 2)
        output = safe_export_document(self.out, name, sketch, overwrite=True)
        _, restored, _ = load_footprint(output)
        self.assertEqual(restored.pads[0].x, sketch.pads[0].x)
        self.assertEqual(len(restored.pads[0].paste_shapes), 2)
        for node in sketch.preserved_nodes:
            if not node.startswith('(property "FootprintSketcher-PasteOwnership"'):
                self.assertIn(node, output.read_bytes().decode('utf-8'))
        self.assertEqual(source.read_bytes(), original)

    def test_bad_syntax_wrong_profile_and_existing_output_are_rejected(self):
        for text in ['(footprint "x"', '(footprint "x" (version 20260206)) ()', '(footprint "x" (version 20241229))']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_footprint(self.footprint(text))
        _, sketch, _ = load_footprint(self.footprint())
        path = safe_export_document(self.out, 'Keep', sketch)
        original = path.read_bytes()
        sketch.value = 'edit'
        with self.assertRaises(FileExistsError):
            safe_export_document(self.out, 'Keep', sketch)
        self.assertEqual(path.read_bytes(), original)
        safe_export_document(self.out, 'Keep', sketch, overwrite=True)
        self.assertNotEqual(path.read_bytes(), original)

    def test_project_restores_image_constraints_calibration_and_metadata(self):
        sketch = Sketch()
        sketch.set_axis_calibration((0, 0), (100, 0), 10, (0, 0), (0, 50), 10)
        sketch.set_origin((50, 25))
        custom = merge_pads([Pad('1', 0, 0, 2, 1), Pad('2', 0.5, 0.5, 1, 2)], '3')
        sketch.pads.extend([custom, graphic_from_points('G', 'line', [(1, 2), (3, 4)])])
        sketch.constraints.append(Constraint('fixed', 'fixed', (0,), values=(custom.x, custom.y, custom.width, custom.height, custom.angle, custom.radius)))
        sketch.reference, sketch.value = 'U?', 'Test'
        sketch.metadata = {'nested': {'unicode': '画像', 'empty': None}, 'classification': ['保持のみ']}
        image = Image.new('RGBA', (7, 9), (12, 34, 56, 78))
        path = save_project(self.root / 'part.kfsproj', sketch, image, 'Part')
        name, restored, raster = load_project(path)
        self.assertEqual(name, 'Part')
        self.assertEqual(restored.pads, sketch.pads)
        self.assertEqual(restored.calibration, sketch.calibration)
        self.assertEqual(restored.origin, sketch.origin)
        self.assertEqual(restored.constraints, sketch.constraints)
        self.assertEqual(restored.metadata, sketch.metadata)
        self.assertEqual(raster.tobytes(), image.tobytes())

    def test_project_keeps_import_source_and_exact_export_after_reopen(self):
        text = BASIC.replace('試験 \\"A\\"', 'Retained', 1)
        name, sketch, _ = load_footprint(self.footprint(text))
        path = save_project(self.root / 'retained.kfsproj', sketch, None, name)
        name, restored, image = load_project(path)
        self.assertIsNone(image)
        self.assertEqual(safe_export_document(self.out, name, restored).read_bytes(), text.encode())

    def test_project_rejects_bad_schema_constraints_duplicate_and_intrusion(self):
        for mutate in [lambda d: d.update(version=99), lambda d: d['sketch'].update(pads=[{'type': 'wat'}]),
                       lambda d: d['sketch'].update(constraints=[{'id': 'x', 'kind': 'width', 'targets': [99], 'value': 1}]),
                       lambda d: d['sketch'].update(origin=[float('inf'), 0])]:
            document = self.project_document(); mutate(document)
            with self.assertRaises(ValueError):
                load_project(self.archive(document))
        document = self.project_document()
        for extra in [[('../escape', b'x')], [('image.png', b'invalid')], [('project.json', b'{}')]]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                load_project(self.archive(document, extra))
        self.assertFalse((self.root.parent / 'escape').exists())

    def test_project_failed_save_preserves_previous_file(self):
        path = self.root / 'Keep.kfsproj'
        sketch = Sketch(); sketch.pads.append(Pad('1', 0, 0, 1, 1))
        save_project(path, sketch, None, 'Keep')
        before = path.read_bytes()
        sketch.metadata['invalid'] = float('nan')
        with self.assertRaises(ValueError):
            save_project(path, sketch, None, 'Keep')
        self.assertEqual(path.read_bytes(), before)

    def test_unsolved_constraints_are_not_silently_solved_on_load_or_save(self):
        document = self.project_document()
        document['sketch']['constraints'] = [{'id': 'w', 'kind': 'width', 'targets': [0], 'value': 3}]
        with self.assertRaises(ValueError):
            load_project(self.archive(document))
        sketch = Sketch(); sketch.pads.append(Pad('1', 0, 0, 1, 1))
        path = save_project(self.root / 'solved.kfsproj', sketch, None, 'Solved')
        before = path.read_bytes()
        sketch.constraints.append(Constraint('w', 'width', (0,), value=3))
        with self.assertRaises(ValueError):
            save_project(path, sketch, None, 'Solved')
        with self.assertRaises(ValueError):
            safe_export_document(self.out, 'Unsolved', sketch)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sketch.pads[0].width, 1)

    def test_source_protection_metadata_cannot_be_forged_in_a_project(self):
        text = BASIC.replace('試験 \\"A\\"', 'Protected', 1).replace('\n)', '\n (group "G" (members "pin-1"))\n)')
        name, sketch, _ = load_footprint(self.footprint(text))
        path = save_project(self.root / 'protected.kfsproj', sketch, None, name)
        with zipfile.ZipFile(path) as archive:
            document = json.loads(archive.read('project.json'))
        document['sketch']['metadata']['blocked_geometry'] = []
        with self.assertRaisesRegex(ValueError, 'メタデータ'):
            load_project(self.archive(document))

    def test_nested_paste_preserved_attributes_are_validated_before_load_and_export(self):
        custom = merge_pads([Pad('1', 0, 0, 2, 2)], '1')
        sketch = Sketch(); sketch.pads.append(custom)
        project = save_project(self.root / 'custom.kfsproj', sketch, None, 'Custom')
        with zipfile.ZipFile(project) as archive:
            document = json.loads(archive.read('project.json'))
        document['sketch']['pads'][0]['paste_shapes'][0]['preserved'] = ['(layers "B.Cu")']
        with self.assertRaisesRegex(ValueError, '保持属性'):
            load_project(self.archive(document))
        aperture = replace(custom.paste_shapes[0], preserved=('(layers "B.Cu")',))
        sketch.pads[:] = [replace(custom, paste_shapes=(aperture,))]
        baseline = Sketch(); baseline.pads.append(Pad('2', 3, 0, 1, 1))
        target = safe_export_document(self.out, 'Keep', baseline)
        original = target.read_bytes()
        with self.assertRaisesRegex(ValueError, '保持属性'):
            safe_export_document(self.out, 'Keep', sketch, overwrite=True)
        self.assertEqual(target.read_bytes(), original)
        before = project.read_bytes()
        with self.assertRaisesRegex(ValueError, '保持属性'):
            save_project(project, sketch, None, 'Custom')
        self.assertEqual(project.read_bytes(), before)

    def test_nested_paste_identifiers_are_unique_but_source_snapshots_may_share_identity(self):
        custom = merge_pads([Pad('1', 0, 0, 2, 2, preserved=('(uuid "original")',))], '1')
        sketch = Sketch(); sketch.pads.append(custom)
        project = save_project(self.root / 'source.kfsproj', sketch, None, 'Source')
        _, restored, _ = load_project(project)
        self.assertEqual(restored.pads, sketch.pads)
        aperture = replace(custom.paste_shapes[0], preserved=('(uuid "original")',))
        sketch.pads[:] = [replace(custom, paste_shapes=(aperture,))]
        with self.assertRaisesRegex(ValueError, '識別子が重複'):
            safe_export_document(self.out, 'Duplicate', sketch)

    def test_uuid_graphic_edits_keep_identity_and_copies_do_not_duplicate_it(self):
        from footprint_sketcher.editing import duplicate_pads
        text = '''(footprint "Graphic" (version 20260206)
        (fp_line (start 0 0) (end 1 1) (stroke (width 0.1) (type solid)) (layer "F.Fab") (uuid "line-id")))'''
        name, sketch, _ = load_footprint(self.footprint(text))
        self.assertEqual(len(sketch.pads), 1)
        sketch.pads[0] = replace(sketch.pads[0], x=2)
        sketch.pads.extend(duplicate_pads(sketch.pads, sketch.pads, (5, 5)))
        text = safe_export_document(self.out, name, sketch).read_text(encoding='utf-8')
        self.assertEqual(text.count('(uuid "line-id")'), 1)

    def test_invalid_embedded_image_and_duplicate_json_keys_are_rejected(self):
        document = self.project_document(); document['image'] = True
        with self.assertRaises(ValueError):
            load_project(self.archive(document, [('image.png', b'not png')]))
        path = self.root / 'keys.kfsproj'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('project.json', '{"format":"kicad-footprint-sketcher","version":1,"version":2}')
        with self.assertRaises(ValueError):
            load_project(path)

    def test_corrupt_embedded_png_metadata_is_reported_as_project_load_failure(self):
        document = self.project_document(); document['image'] = True
        with Image.new('RGB', (1, 1), 'red') as source, BytesIO() as output:
            source.save(output, format='PNG')
            png = output.getvalue()
        for kind, payload in ((b'tRNS', b'\0'), (b'iCCP', b''),
                              (b'zTXt', b'private\0\1invalid')):
            # These chunks pass Image.open and fail during lazy pixel decoding.
            malformed = png[:-12] + self.png_chunk(kind, payload) + png[-12:]
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'プロジェクトを読み込めません'):
                load_project(self.archive(document, [('image.png', malformed)]))

    def test_oversized_embedded_image_is_rejected_before_pixel_decoding(self):
        document = self.project_document(); document['image'] = True
        # A real PNG header declares just over 50M pixels. The deliberately tiny
        # IDAT must never be decoded into that full raster to reject the file.
        png = (b'\x89PNG\r\n\x1a\n'
               + self.png_chunk(b'IHDR', struct.pack('>IIBBBBB', 10_000, 5_001, 8, 2, 0, 0, 0))
               + self.png_chunk(b'IDAT', zlib.compress(b'\0\xff\0\0'))
               + self.png_chunk(b'IEND', b''))
        with self.assertRaisesRegex(ValueError, '内包画像がサイズ上限'):
            load_project(self.archive(document, [('image.png', png)]))


if __name__ == '__main__':
    unittest.main()
