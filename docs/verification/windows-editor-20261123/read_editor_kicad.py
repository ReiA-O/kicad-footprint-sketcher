from pathlib import Path
from datetime import datetime, timezone
import json
import traceback
import pcbnew

stage=Path(r'D:\CodexWork\tasks\kicad-stage1-validation-20261123')
run=Path((stage/'latest-editor-run.txt').read_text(encoding='utf-8').strip())
tests=json.loads((run/'editor-tests.json').read_text(encoding='utf-8'))
result={'status':'started','version':pcbnew.GetBuildVersion(),'timestamp_utc':datetime.now(timezone.utc).isoformat(),'source_commit':tests['commit'],'cases':[]}
try:
    if tests['status']!='completed': raise RuntimeError('Editor tests did not complete')
    board=pcbnew.BOARD()
    for case in tests['cases']:
        output=Path(case['output'])
        fp=pcbnew.FootprintLoad(str(output.parent),output.stem)
        if fp is None: raise RuntimeError('FootprintLoad failed')
        record={'test':case['test'],'file':str(output),'pads':[]}
        for actual,expected in zip(fp.Pads(),case['pads']):
            got={'number':actual.GetNumber(),'x':pcbnew.ToMM(actual.GetPosition().x),'y':pcbnew.ToMM(actual.GetPosition().y),'width':pcbnew.ToMM(actual.GetSize().x),'height':pcbnew.ToMM(actual.GetSize().y),'angle':actual.GetOrientationDegrees(),'radius':pcbnew.ToMM(actual.GetRoundRectCornerRadius()) if actual.GetShape()==pcbnew.PAD_SHAPE_ROUNDRECT else 0.,'layers':[board.GetLayerName(layer) for layer in actual.GetLayerSet().Seq()]}
            if got['number']!=expected['number'] or set(got['layers'])!={'F.Cu','F.Mask','F.Paste'}: raise RuntimeError('Pad identity/layers mismatch')
            got['difference_from_model']={key:got[key]-expected[key] for key in ('x','y','width','height','radius')}
            got['angle_difference']=got['angle']-(expected['angle']%360)
            record['pads'].append(got)
        if len(record['pads'])!=len(case['pads']): raise RuntimeError('Pad count mismatch')
        result['cases'].append(record)
    result['status']='completed'
except BaseException:
    result['status']='failed'; result['exception']=traceback.format_exc()
finally:
    (run/'editor-kicad.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
raise SystemExit(0 if result['status']=='completed' else 1)
