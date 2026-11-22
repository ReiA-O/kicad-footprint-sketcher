import json
import pathlib
import sys
import traceback
from datetime import datetime, timezone
import pcbnew

stage = pathlib.Path(r'D:\CodexWork\tasks\kicad-stage1-validation-20261123')
run = pathlib.Path((stage / 'latest-run.txt').read_text(encoding='utf-8').strip())
gui = json.loads((run / 'gui-result.json').read_text(encoding='utf-8'))
report = {'method': 'Windows KiCad pcbnew.FootprintLoad API readback; editor GUI opening is a separate manual check.', 'version': pcbnew.GetBuildVersion(), 'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'cases': [], 'status': 'started'}
try:
    if gui['status'] != 'completed':
        raise RuntimeError('GUI replay was not completed')
    board = pcbnew.BOARD()
    for case in gui['cases']:
        for origin_key in ('center_origin', 'arbitrary_origin'):
            snapshot = case[origin_key]
            source = pathlib.Path(snapshot['export']['path'])
            footprint = pcbnew.FootprintLoad(str(source.parent), source.stem)
            if footprint is None:
                raise RuntimeError('FootprintLoad returned None: ' + str(source))
            record = {'image_format': case['image_format'], 'origin': origin_key, 'source': str(source), 'footprint_position_mm': [pcbnew.ToMM(footprint.GetPosition().x), pcbnew.ToMM(footprint.GetPosition().y)], 'pads': []}
            for pad in footprint.Pads():
                expected = next(p for p in snapshot['pads'] if p['number'] == pad.GetNumber())
                actual = {'number': pad.GetNumber(), 'x': pcbnew.ToMM(pad.GetPosition().x), 'y': pcbnew.ToMM(pad.GetPosition().y), 'width': pcbnew.ToMM(pad.GetSize().x), 'height': pcbnew.ToMM(pad.GetSize().y), 'angle': pad.GetOrientationDegrees(), 'radius': pcbnew.ToMM(pad.GetRoundRectCornerRadius()) if pad.GetShape() == pcbnew.PAD_SHAPE_ROUNDRECT else 0.0, 'shape': 'roundrect' if pad.GetShape() == pcbnew.PAD_SHAPE_ROUNDRECT else ('rect' if pad.GetShape() == pcbnew.PAD_SHAPE_RECT else str(pad.GetShape())), 'layers': [board.GetLayerName(layer) for layer in pad.GetLayerSet().Seq()], 'smd': pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD}
                actual['difference_from_gui_model'] = {key: actual[key] - expected[key] for key in ('x','y','width','height','angle','radius')}
                if set(actual['layers']) != {'F.Cu','F.Paste','F.Mask'} or not actual['smd']:
                    raise RuntimeError('Unexpected pad layer or type')
                if actual['shape'] != ('roundrect' if expected['radius'] else 'rect'):
                    raise RuntimeError('Unexpected pad shape')
                record['pads'].append(actual)
            if len(record['pads']) != len(snapshot['pads']):
                raise RuntimeError('Pad count differs')
            report['cases'].append(record)
    report['status'] = 'completed'
except BaseException:
    report['status'] = 'failed'
    report['exception'] = traceback.format_exc()
finally:
    target = run / 'kicad-readback.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report['status'] == 'completed' else 1)
