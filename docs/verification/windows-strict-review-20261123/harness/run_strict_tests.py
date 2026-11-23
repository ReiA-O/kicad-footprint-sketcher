"""Run actual Windows Tk events and retain model/outputs for KiCad validation."""
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib, io, json, os, platform, subprocess, sys, traceback, unittest, uuid

repo = Path(r'D:\CodexWork\projects\kicad-footprint-sketcher')
stage = Path(r'D:\CodexWork\tasks\kicad-stage1-validation-20261123')
run = stage / ('strict-run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
run.mkdir()
(stage/'latest-strict-run.txt').write_text(str(run), encoding='utf-8')
report = dict(status='started', started_utc=datetime.now(timezone.utc).isoformat(), run=str(run),
              python=sys.version, platform=platform.platform(), cases=[])
log = io.StringIO()
current_test = ''
try:
    os.chdir(repo); sys.path.insert(0, str(repo))
    import importlib.metadata
    from footprint_sketcher.gui import SketcherApp
    from footprint_sketcher.editing import Snapshot
    from footprint_sketcher.graphics import Graphic
    from footprint_sketcher.compound import CustomPad
    from footprint_sketcher.model import export_footprint
    report['commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report['dependencies'] = {n: importlib.metadata.version(n) for n in ('Pillow','Shapely','numpy','pypdfium2')}
    report['source_sha256'] = {str(p.relative_to(repo)).replace('\\','/'): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted((repo/'footprint_sketcher').glob('*.py'))}
    report['test_sha256'] = {str(p.relative_to(repo)).replace('\\','/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((repo/'tests').glob('test*.py'))}
    report['git_status'] = subprocess.check_output(['git', 'status', '--porcelain'], text=True)
    def capture(app, kind):
        i = len(report['cases'])+1
        directory = run/f'case-{i:03}.pretty'; directory.mkdir()
        output = export_footprint(directory, f'Expanded_Case_{i}', app.sketch.pads,
                                  reference=app.sketch.reference, value=app.sketch.value or f'Expanded_Case_{i}')
        report['cases'].append(dict(test=current_test, capture=kind, output=str(output), sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            pads=[dict(asdict(p), entity_type='graphic' if isinstance(p, Graphic) else 'custom' if isinstance(p, CustomPad) else 'pad') for p in app.sketch.pads]))
    original = SketcherApp._record_change
    def record_change(app, before):
        result = original(app, before)
        if result is not False and before != Snapshot.capture(app.sketch) and app.sketch.pads and 'ExtendedGuiTests.' in current_test:
            capture(app, 'committed-state')
        return result
    SketcherApp._record_change = record_change
    class Result(unittest.TextTestResult):
        def startTest(self, test):
            global current_test
            current_test = test.id(); (run/'progress.txt').write_text(current_test, encoding='utf-8'); super().startTest(test)
        def addSuccess(self, test):
            super().addSuccess(test)
            app = getattr(test, 'app', None)
            if app is not None and app.sketch.pads:
                capture(app, 'final-state')
    suite = unittest.defaultTestLoader.discover(str(repo/'tests'))
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Result).run(suite)
    report.update(expected_tests=expected, tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  skipped=[(str(t), why) for t, why in result.skipped])
    changed = [name for name, digest in {**report['source_sha256'], **report['test_sha256']}.items() if hashlib.sha256((repo/name).read_bytes()).hexdigest() != digest]
    report['changed_during_run'] = changed
    report['status'] = 'completed' if result.wasSuccessful() and not result.skipped and not changed and not report['git_status'] else 'failed'
except BaseException:
    report.update(status='failed', exception=traceback.format_exc())
finally:
    report.update(finished_utc=datetime.now(timezone.utc).isoformat(), log=log.getvalue())
    (run/'expanded-tests.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
raise SystemExit(0 if report['status'] == 'completed' else 1)
