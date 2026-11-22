"""Compare KiCad 10 readback against logical pads, Cu outlines and Paste apertures."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,math,traceback,pcbnew
stage=Path(r'D:\CodexWork\tasks\kicad-stage1-validation-20261123')
run=Path((stage/'latest-editor-run.txt').read_text(encoding='utf-8').strip())
tests=json.loads((run/'editor-tests.json').read_text(encoding='utf-8'))
result={'status':'started','version':pcbnew.GetBuildVersion(),'timestamp_utc':datetime.now(timezone.utc).isoformat(),'source_commit':tests['commit'],'method':'KiCad PAD readback; custom Cu/Mask polygon plus separate unnumbered F.Paste apertures. Differences are serialization observations, not a T02 acceptance criterion.','cases':[]}
def entities(pads):
 for pad in pads:
  if 'outline' not in pad:
   yield dict(pad,layers={'F.Cu','F.Paste','F.Mask'},shape='roundrect' if pad['radius'] else 'rect',role='standard')
   continue
  yield dict(pad,width=pad['anchor_size'][0],height=pad['anchor_size'][1],layers={'F.Cu','F.Mask'},shape='custom',role='copper')
  a=math.radians(pad['angle']%360);co,si=math.cos(a),math.sin(a)
  for source in pad['paste_shapes']:
   yield dict(source,number='',x=pad['x']+source['x']*co+source['y']*si,y=pad['y']+source['y']*co-source['x']*si,angle=(source['angle']+pad['angle'])%360,layers={'F.Paste'},shape='roundrect' if source['radius'] else 'rect',role='paste-aperture')
def edge_distance(point,ring):
 best=float('inf')
 for i,a in enumerate(ring):
  b=ring[(i+1)%len(ring)];dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
  t=max(0.,min(1.,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/den)) if den else 0.
  best=min(best,math.hypot(point[0]-a[0]-t*dx,point[1]-a[1]-t*dy))
 return best
def area(ring):return abs(sum(a[0]*ring[(i+1)%len(ring)][1]-ring[(i+1)%len(ring)][0]*a[1] for i,a in enumerate(ring)))/2
try:
 if tests['status']!='completed':raise RuntimeError('Tests did not complete')
 board=pcbnew.BOARD();shapes={'rect':pcbnew.PAD_SHAPE_RECT,'roundrect':pcbnew.PAD_SHAPE_ROUNDRECT,'custom':pcbnew.PAD_SHAPE_CUSTOM}
 for case in tests['cases']:
  output=Path(case['output']);fp=pcbnew.FootprintLoad(str(output.parent),output.stem)
  if fp is None:raise RuntimeError('FootprintLoad failed')
  expected=list(entities(case['pads']));pool=list(fp.Pads())
  if len(pool)!=len(expected):raise RuntimeError('Physical pad/aperture count mismatch')
  record={'test':case['test'],'capture':case.get('capture','final-test-state'),'file':str(output),'logical_pad_count':len(case['pads']),'physical_pad_count':len(pool),'pads':[]}
  for want in expected:
   matching=[p for p in pool if p.GetNumber()==want['number'] and {board.GetLayerName(x) for x in p.GetLayerSet().Seq()}==want['layers']]
   if not matching:raise RuntimeError('Pad identity or layers mismatch')
   p=min(matching,key=lambda p:abs(pcbnew.ToMM(p.GetPosition().x)-want['x'])+abs(pcbnew.ToMM(p.GetPosition().y)-want['y'])+abs(pcbnew.ToMM(p.GetSize().x)-want['width'])+abs(pcbnew.ToMM(p.GetSize().y)-want['height']))
   pool.remove(p)
   if p.GetShape()!=shapes[want['shape']] or p.GetAttribute()!=pcbnew.PAD_ATTRIB_SMD:raise RuntimeError('Shape or SMD attribute mismatch')
   got={'role':want['role'],'number':p.GetNumber(),'shape':want['shape'],'x':pcbnew.ToMM(p.GetPosition().x),'y':pcbnew.ToMM(p.GetPosition().y),'width':pcbnew.ToMM(p.GetSize().x),'height':pcbnew.ToMM(p.GetSize().y),'angle':p.GetOrientationDegrees(),'radius':pcbnew.ToMM(p.GetRoundRectCornerRadius()) if want['shape']=='roundrect' else 0.,'layers':sorted(want['layers'])}
   got['difference_from_model']={key:got[key]-want[key] for key in ('x','y','width','height','radius')}
   got['angle_difference']=(got['angle']-want['angle']+180)%360-180
   if want['role']=='copper':
    ps=p.GetCustomShapeAsPolygon(pcbnew.F_Cu)
    if ps.OutlineCount()!=1 or ps.HoleCount(0)!=0:raise RuntimeError('Custom polygon topology mismatch')
    chain=ps.COutline(0);nm=[(chain.CPoint(i).x,chain.CPoint(i).y) for i in range(chain.PointCount())]
    actual=[(x/1e6,y/1e6) for x,y in nm];reference=want['outline']
    got['anchor_size_compared']=True
    got['outline']={'model_vertices':len(reference),'kicad_vertices':len(actual),'maximum_vertex_to_edge_distance_mm':max(max(edge_distance(q,reference) for q in actual),max(edge_distance(q,actual) for q in reference)),'area_difference_mm2':area(actual)-area(reference),'kicad_local_vertices_nm':nm,'model_bbox_mm':[min(x for x,y in reference),min(y for x,y in reference),max(x for x,y in reference),max(y for x,y in reference)]}
   record['pads'].append(got)
  if pool:raise RuntimeError('Unexpected unconsumed entities')
  result['cases'].append(record)
 result['status']='completed'
except BaseException:result.update(status='failed',exception=traceback.format_exc())
(run/'editor-kicad.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
pads=[p for c in result['cases'] for p in c['pads']]
print(json.dumps({'status':result['status'],'source_commit':result['source_commit'],'outputs':len(result['cases']),'entities':len(pads),'copper_custom_entities':sum(p['role']=='copper' for p in pads),'paste_apertures':sum(p['role']=='paste-aperture' for p in pads),'max_abs_scalar_difference_mm':max([abs(v) for p in pads for v in p['difference_from_model'].values()] or [0]),'max_outline_vertex_edge_difference_mm':max([p['outline']['maximum_vertex_to_edge_distance_mm'] for p in pads if 'outline' in p] or [0]),'exception':result.get('exception')},ensure_ascii=False))
raise SystemExit(0 if result['status']=='completed' else 1)
