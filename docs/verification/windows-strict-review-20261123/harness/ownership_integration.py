from pathlib import Path
from dataclasses import replace
import argparse,json,sys,traceback,subprocess,hashlib
p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','verify'));p.add_argument('--run',type=Path,required=True);a=p.parse_args()
repo=Path(r'<REPO>');sys.path.insert(0,str(repo))
from footprint_sketcher.model import Pad,export_footprint
from footprint_sketcher.compound import merge_pads,world_paste_shapes
from footprint_sketcher.persistence import load_footprint,safe_export_document
report={'mode':a.mode,'status':'started','commit':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()}
try:
    if a.mode=='prepare':
        d=a.run/'ownership-original.pretty';d.mkdir()
        c=merge_pads([Pad('1',0,0,2,2),Pad('2',1,0,2,1)],'1')
        f=export_footprint(d,'Ownership_Explicit',[c])
        name,sketch,_=load_footprint(f)
        assert len(sketch.pads)==1 and len(sketch.pads[0].paste_shapes)==2
        text=f.read_text();independent='(pad "" smd rect (at 10 0) (size 1 1) (layers "F.Paste"))'
        altered=d/'Ownership_Independent.kicad_mod';altered.write_text(text.rstrip()[:-1]+'\n'+independent+'\n)\n')
        _,loaded,_=load_footprint(altered)
        assert len(loaded.pads[0].paste_shapes)==2 and independent in loaded.preserved_nodes
        loaded.pads[0]=replace(loaded.pads[0],x=5)
        try:safe_export_document(d,'Ownership_Independent_Edited',loaded)
        except ValueError:report['unowned_move_blocked']=True
        else:raise AssertionError('Unowned Paste did not block geometry export')
        report.update(explicit_apertures=2,independent_retained=True,output=str(f),sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    else:
        f=a.run/'ownership-kicad.pretty'/'Ownership_Explicit.kicad_mod'
        name,sketch,classification=load_footprint(f)
        assert len(sketch.pads)==1 and len(sketch.pads[0].paste_shapes)==2
        report.update(classification=classification,blocked_geometry=sketch.metadata.get('blocked_geometry'))
        assert not sketch.metadata.get('blocked_geometry')
        before=world_paste_shapes(sketch.pads[0]);sketch.pads[0]=replace(sketch.pads[0],x=sketch.pads[0].x+5,y=sketch.pads[0].y+2)
        d=a.run/'ownership-moved.pretty';d.mkdir(exist_ok=True);out=safe_export_document(d,name,sketch,overwrite=True)
        _,again,_=load_footprint(out);after=world_paste_shapes(again.pads[0])
        assert len(after)==2
        for b,v in zip(before,after):assert abs(v.x-b.x-5)<1e-6 and abs(v.y-b.y-2)<1e-6
        reset=a.run/'ownership-reset.pretty'/'Ownership_Explicit.kicad_mod'
        reset_before=reset.read_bytes()
        try:load_footprint(reset)
        except ValueError as error:report['external_uuid_reset_rejected']=str(error)
        else:raise AssertionError('UUID reset should stop ambiguous import')
        assert reset.read_bytes()==reset_before
        report.update(kicad_resave_preserves_ownership=True,logical_entities=1,apertures=2,moved_mm=[5,2],output=str(out),sha256=hashlib.sha256(out.read_bytes()).hexdigest())
    report['status']='completed'
except BaseException:report.update(status='failed',exception=traceback.format_exc())
report_path=a.run/('ownership-'+a.mode+'.json')
if report_path.exists():
    i=1
    while report_path.with_suffix('.attempt-'+str(i)+'.json').exists():i+=1
    report_path.with_suffix('.attempt-'+str(i)+'.json').write_bytes(report_path.read_bytes())
report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False));raise SystemExit(0 if report['status']=='completed' else 1)
