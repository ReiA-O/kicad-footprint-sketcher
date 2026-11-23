from pathlib import Path
import argparse,json,traceback,pcbnew
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();r={'status':'started','version':pcbnew.Version()}
try:
    source=a.run/'ownership-original.pretty';target=a.run/'ownership-kicad.pretty';target.mkdir(exist_ok=True)
    fp=pcbnew.FootprintLoad(str(source),'Ownership_Explicit',True);assert fp and len(list(fp.Pads()))==3
    plugin=pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
    plugin.FootprintSave(str(target),fp)
    assert (target/'Ownership_Explicit.kicad_mod').is_file()
    reset=a.run/'ownership-reset.pretty';reset.mkdir(exist_ok=True)
    reset_fp=pcbnew.FootprintLoad(str(source),'Ownership_Explicit',False)
    plugin.FootprintSave(str(reset),reset_fp)
    independent=pcbnew.FootprintLoad(str(source),'Ownership_Independent');assert independent and len(list(independent.Pads()))==4
    moved=a.run/'ownership-moved.pretty'
    r.update(status='completed',preserve_uuid=True,explicit_physical_pads=3,independent_physical_pads=4,kicad_saved=str(target/'Ownership_Explicit.kicad_mod'))
except BaseException:r.update(status='failed',exception=traceback.format_exc())
(a.run/'ownership-kicad.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(r,ensure_ascii=False));raise SystemExit(0 if r['status']=='completed' else 1)
