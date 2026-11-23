from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,traceback,pcbnew
stage=Path(r'<WORKSPACE>\tasks\kicad-stage1-validation-20261123')
source=stage/'NE555_SOIC8_Practice.kicad_mod';expected=json.loads((stage/'owner-expected.json').read_text(encoding='utf-8'))
report={'status':'started','timestamp_utc':datetime.now(timezone.utc).isoformat(),'version':pcbnew.GetBuildVersion(),'source_filename':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'owner_feedback':expected['owner_feedback'],'generator_commit':'unidentified','pads':[],'method':'Windows KiCad FootprintLoad API; owner feedback does not identify the generator commit or confirm the editor screen/checker.'}
try:
 if report['sha256']!=expected['sha256']:raise RuntimeError('Owner file hash mismatch')
 library=stage/'owner-review-20261123.pretty';library.mkdir(exist_ok=True);target=library/source.name
 if target.exists() and target.read_bytes()!=source.read_bytes():raise RuntimeError('Different owner snapshot already exists')
 target.write_bytes(source.read_bytes());fp=pcbnew.FootprintLoad(str(library),source.stem)
 if fp is None:raise RuntimeError('KiCad failed to load owner output')
 actual={p.GetNumber():p for p in fp.Pads()};board=pcbnew.BOARD()
 if len(actual)!=len(expected['pads']):raise RuntimeError('Owner pad count mismatch')
 for want in expected['pads']:
  p=actual[want['number']]
  got={'number':p.GetNumber(),'shape':'roundrect' if p.GetShape()==pcbnew.PAD_SHAPE_ROUNDRECT else str(p.GetShape()),'smd':p.GetAttribute()==pcbnew.PAD_ATTRIB_SMD,'x':pcbnew.ToMM(p.GetPosition().x),'y':pcbnew.ToMM(p.GetPosition().y),'width':pcbnew.ToMM(p.GetSize().x),'height':pcbnew.ToMM(p.GetSize().y),'angle':p.GetOrientationDegrees(),'radius':pcbnew.ToMM(p.GetRoundRectCornerRadius()),'layers':[board.GetLayerName(x) for x in p.GetLayerSet().Seq()]}
  if got['shape']!=want['shape'] or not got['smd'] or set(got['layers'])!=set(want['layers']):raise RuntimeError('Owner shape/type/layer mismatch')
  got['difference_from_file']={k:got[k]-want[k] for k in ['x','y','width','height','angle','radius']};report['pads'].append(got)
 report['status']='completed'
except BaseException:report.update(status='failed',exception=traceback.format_exc())
(stage/'owner-readback.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':report['status'],'version':report['version'],'pads':len(report['pads']),'sha256':report['sha256'],'max_abs_difference_mm':max([abs(v) for p in report['pads'] for k,v in p['difference_from_file'].items() if k!='angle'] or [0]),'exception':report.get('exception')},ensure_ascii=False))
raise SystemExit(0 if report['status']=='completed' else 1)
