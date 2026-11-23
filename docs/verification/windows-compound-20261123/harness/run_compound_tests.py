"""Run real Tk widget tests and preserve committed custom-pad states for KiCad."""
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib, io, json, os, platform, subprocess, sys, traceback, unittest, uuid
repo=Path(r'<REPO>')
stage=Path(r'<WORKSPACE>\tasks\kicad-stage1-validation-20261123')
run=stage/('editor-run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
run.mkdir();(stage/'latest-editor-run.txt').write_text(str(run),encoding='utf-8')
report={'status':'started','started_utc':datetime.now(timezone.utc).isoformat(),'run':str(run),'python':sys.version,'platform':platform.platform(),'cases':[]}
log=io.StringIO(); current_test=''
try:
 os.chdir(repo);sys.path.insert(0,str(repo))
 import PIL,shapely,numpy
 from footprint_sketcher.model import export_footprint
 from footprint_sketcher.compound import CustomPad
 from footprint_sketcher.editing import Snapshot
 from footprint_sketcher.gui import SketcherApp
 report.update(pillow=PIL.__version__,shapely=shapely.__version__,numpy=numpy.__version__,commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
 names=['footprint_sketcher/'+n+'.py' for n in ['gui','canvas','interaction','model','editing','compound']]+['requirements.txt']
 report['source_sha256']={name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in names}
 def save_case(app,test,kind):
  i=len(report['cases'])+1;directory=run/f'case-{i}.pretty';directory.mkdir()
  output=export_footprint(directory,f'Editor_Case_{i}',app.sketch.pads)
  report['cases'].append({'test':test,'capture':kind,'pads':[asdict(p) for p in app.sketch.pads],'calibration':asdict(app.sketch.calibration),'origin':app.sketch.origin,'output':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()})
 original=SketcherApp._record_change
 def record_change(app,before):
  original(app,before)
  if Snapshot.capture(app.sketch)!=before and any(isinstance(p,CustomPad) for p in app.sketch.pads):
   save_case(app,current_test,'committed-custom-state')
 SketcherApp._record_change=record_change
 class Result(unittest.TextTestResult):
  def startTest(self,test):
   global current_test
   current_test=test.id();super().startTest(test)
  def addSuccess(self,test):
   super().addSuccess(test);app=getattr(test,'app',None)
   if app is not None and app.sketch.pads:save_case(app,test.id(),'final-test-state')
 suite=unittest.defaultTestLoader.discover(str(repo/'tests'));expected=suite.countTestCases()
 result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(suite)
 report.update(expected_tests=expected,tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=[(str(t),why) for t,why in result.skipped])
 report['status']='completed' if result.wasSuccessful() and not result.skipped and result.testsRun==expected and expected>=46 else 'failed'
except BaseException:report.update(status='failed',exception=traceback.format_exc())
finally:
 report.update(finished_utc=datetime.now(timezone.utc).isoformat(),log=log.getvalue())
 (run/'editor-tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(0 if report['status']=='completed' else 1)
