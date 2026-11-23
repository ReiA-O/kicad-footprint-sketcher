"""Run the stage-1 GUI checks in the user's interactive Windows session."""
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import traceback
import unittest
import uuid

repo = Path(r'<REPO>')
stage = Path(r'<WORKSPACE>\tasks\kicad-stage1-validation-20261123')
run = stage / ('editor-run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
run.mkdir()
(stage / 'latest-editor-run.txt').write_text(str(run), encoding='utf-8')
report = {'status': 'started', 'started_utc': datetime.now(timezone.utc).isoformat(), 'run': str(run), 'python': sys.version, 'platform': platform.platform(), 'cases': []}
log = io.StringIO()
try:
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from footprint_sketcher.model import export_footprint
    report['commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report['source_sha256'] = {name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in ('footprint_sketcher/gui.py','footprint_sketcher/canvas.py','footprint_sketcher/interaction.py','footprint_sketcher/model.py')}
    class Result(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            app = getattr(test, 'app', None)
            if app is not None and app.sketch.pads:
                index = len(report['cases']) + 1
                directory = run / f'case-{index}.pretty'
                directory.mkdir()
                output = export_footprint(directory, f'Editor_Case_{index}', app.sketch.pads)
                report['cases'].append({'test': test.id(), 'pads': [asdict(p) for p in app.sketch.pads], 'calibration': asdict(app.sketch.calibration), 'origin': app.sketch.origin, 'output': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()})
    suite = unittest.defaultTestLoader.discover(str(repo / 'tests'))
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Result).run(suite)
    report.update(tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=[(str(t), why) for t, why in result.skipped])
    report['status'] = 'completed' if result.wasSuccessful() and not result.skipped and result.testsRun >= 13 else 'failed'
except BaseException:
    report['status'] = 'failed'
    report['exception'] = traceback.format_exc()
finally:
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    report['log'] = log.getvalue()
    (run / 'editor-tests.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
raise SystemExit(0 if report['status'] == 'completed' else 1)
