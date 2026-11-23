"""Replay Stage 1 widget paths on Windows; this is not a manual acceptance test."""
from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback
import uuid
from unittest.mock import patch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path(r'<REPO>'))
    parser.add_argument('--stage', type=Path, default=Path(r'<WORKSPACE>\tasks\kicad-stage1-validation-20261123'))
    args = parser.parse_args()
    run_dir = args.stage / ('gui-run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / 'gui-result.json'
    (args.stage / 'latest-run.txt').write_text(str(run_dir), encoding='utf-8')
    report = {
        'status': 'started',
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'repository': str(args.repo),
        'run_directory': str(run_dir),
        'python': sys.version,
        'platform': platform.platform(),
        'method': 'Actual SketcherApp widgets: Entry delete/insert, Button invoke, integer Tk canvas Button-1 events; only file/folder chooser return values are supplied by the harness.',
        'unverified': [
            {'item': 'Native file and folder dialogs', 'reason': 'Chooser return values are supplied by the harness.', 'method': 'Open both samples and select output folders manually on the Windows desktop.'},
            {'item': 'USER manual operation and feel', 'reason': 'This run is an automated replay.', 'method': 'USER operates the normal application and records observations.'},
            {'item': 'KiCad footprint editor opening and pad attributes', 'reason': 'This script only exercises the Sketcher GUI.', 'method': 'Open each generated .pretty library in KiCad 10 and inspect pad properties.'},
        ],
        'numerical_evaluation': 'Differences from the sample are observations. No unapproved T02 acceptance tolerance is applied.',
        'cases': [],
        'callback_errors': [],
    }
    roots = []
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.repo, text=True).strip()
        report['commit'] = commit
        if commit != '2c4a84fbcce55b3c6c38300a89c7a9c7b1d2a3eb':
            raise RuntimeError('Unexpected working commit: ' + commit)
        sys.path.insert(0, str(args.repo))
        import tkinter as tk
        from tkinter import ttk
        import PIL
        from footprint_sketcher.gui import SketcherApp, UNIFORM_METHOD

        report['pillow'] = PIL.__version__

        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)

        for extension, case_name in [('png', 'PNG'), ('jpg', 'JPEG')]:
            case = {'image_format': case_name, 'steps': [], 'clicks': []}
            report['cases'].append(case)
            root = tk.Tk()
            roots.append(root)

            def callback_error(exc_type, value, tb):
                report['callback_errors'].append(''.join(traceback.format_exception(exc_type, value, tb)))

            root.report_callback_exception = callback_error
            app = SketcherApp(root)
            app.calibration_method.set(UNIFORM_METHOD)
            app._calibration_mode_changed()
            root.geometry('1600x1000+0+0')

            def update():
                root.update_idletasks()
                root.update()
                if report['callback_errors']:
                    raise RuntimeError('Tk callback raised an exception; see callback_errors.')

            def checkpoint(label):
                update()
                case['steps'].append({'step': label, 'status_text': app.status.get(), 'mode': app.mode})

            def button(text):
                matches = [w for w in walk(root) if isinstance(w, ttk.Button) and str(w.cget('text')) == text]
                if len(matches) != 1:
                    raise RuntimeError(f'Expected one button {text!r}, found {len(matches)}')
                widget = matches[0]
                if widget.instate(['disabled']):
                    raise RuntimeError('Button is disabled: ' + text)
                widget.invoke()
                update()

            def entry(variable, value):
                matches = [w for w in walk(root) if isinstance(w, ttk.Entry) and str(w.cget('textvariable')) == str(variable)]
                if len(matches) != 1:
                    raise RuntimeError(f'Expected one entry for {variable!s}, found {len(matches)}')
                widget = matches[0]
                widget.delete(0, 'end')
                widget.insert(0, value)
                update()
                if variable.get() != value:
                    raise RuntimeError('Entry did not retain input: ' + value)

            def click(label, image_point):
                update()
                target = app.canvas.image_to_canvas(image_point)
                actual = tuple(round(value) for value in target)
                case['clicks'].append({
                    'label': label,
                    'requested_image_pixel': image_point,
                    'canvas_target_before_rounding': target,
                    'integer_canvas_click': actual,
                    'effective_image_pixel': app.canvas.canvas_to_image(actual),
                    'display_scale': app.canvas.scale,
                    'display_offset': app.canvas.offset,
                })
                app.canvas.event_generate('<Button-1>', x=actual[0], y=actual[1], when='tail')
                update()

            def snapshot():
                return {
                    'origin_image_pixel': app.sketch.origin,
                    'pads': [dataclasses.asdict(pad) for pad in app.sketch.pads],
                    'table_rows': [app.pad_table.item(item, 'values') for item in app.pad_table.get_children()],
                    'status_text': app.status.get(),
                }

            def export(label):
                target = run_dir / (case_name + '-' + label + '.pretty')
                target.mkdir(exist_ok=False)
                entry(app.footprint_name, 'Stage1_SMD_Demo')
                with patch('footprint_sketcher.gui.filedialog.askdirectory', return_value=str(target)) as chooser:
                    button('.kicad_mod を書き出す…')
                    if chooser.call_count != 1:
                        raise RuntimeError('Export did not reach folder chooser')
                output = target / 'Stage1_SMD_Demo.kicad_mod'
                if not output.is_file():
                    raise RuntimeError('Expected output was not written: ' + app.status.get())
                payload = output.read_bytes()
                return {
                    'path': str(output),
                    'sha256': hashlib.sha256(payload).hexdigest(),
                    'size_bytes': len(payload),
                    'content': payload.decode('utf-8'),
                    'status_text': app.status.get(),
                }

            mapping_deadline = time.monotonic() + 5.0
            while True:
                update()
                mapped = bool(app.canvas.winfo_viewable())
                canvas_width = app.canvas.winfo_width()
                canvas_height = app.canvas.winfo_height()
                if mapped and canvas_width > 1 and canvas_height > 1:
                    break
                if time.monotonic() >= mapping_deadline:
                    case['initial_mapping'] = {
                        'viewable': mapped,
                        'canvas_width': canvas_width,
                        'canvas_height': canvas_height,
                        'window_geometry': root.geometry(),
                        'timeout_seconds': 5,
                    }
                    raise RuntimeError('GUI canvas did not become viewable with a usable size within 5 seconds; inspect the desktop session before treating this as an application failure.')
                time.sleep(0.025)
            case['initial_mapping'] = {'viewable': mapped, 'canvas_width': canvas_width, 'canvas_height': canvas_height}
            case['window'] = {
                'requested_geometry': '1600x1000+0+0',
                'actual_geometry': root.geometry(),
                'screen_width': root.winfo_screenwidth(),
                'screen_height': root.winfo_screenheight(),
                'requested_size_exceeds_screen': root.winfo_screenwidth() < 1600 or root.winfo_screenheight() < 1000,
                'tk_patchlevel': str(root.tk.call('info', 'patchlevel')),
                'tk_scaling': float(root.tk.call('tk', 'scaling')),
                'windowing_system': str(root.tk.call('tk', 'windowingsystem')),
            }
            image = args.repo / 'examples' / 'stage1' / ('land-pattern.' + extension)
            case['source_image'] = str(image)
            case['source_image_sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
            with patch('footprint_sketcher.gui.filedialog.askopenfilename', return_value=str(image)) as chooser:
                button('下絵を開く…')
                if chooser.call_count != 1:
                    raise RuntimeError('Image open did not reach file chooser')
            if app.canvas.image is None or app.canvas.image.size != (900, 560):
                raise RuntimeError('Sample image did not load: ' + app.status.get())
            case['canvas'] = {'width': app.canvas.winfo_width(), 'height': app.canvas.winfo_height(), 'display_scale': app.canvas.scale, 'display_offset': app.canvas.offset}
            checkpoint('image_opened')

            button('校正の2点を選ぶ')
            click('calibration_A', (150, 445))
            click('calibration_B', (750, 445))
            entry(app.distance, '10')
            button('校正を適用')
            if app.sketch.calibration is None:
                raise RuntimeError('Calibration was not applied: ' + app.status.get())
            case['calibration'] = dataclasses.asdict(app.sketch.calibration)
            case['calibration']['display_label'] = app.calibration_label.get()
            case['calibration']['difference_from_1_over_60_mm_per_pixel'] = app.sketch.calibration.mm_per_pixel - 1 / 60
            checkpoint('calibrated_10_mm')
            button('画像の中央を原点に')
            checkpoint('center_origin_selected')

            specs = [
                {'number': '1', 'width': '1.6', 'height': '1.2', 'angle': '0', 'radius': '0', 'image_point': (300, 220), 'expected_xy': (-2.5, -1.0)},
                {'number': '2', 'width': '2.0', 'height': '1.2', 'angle': '30', 'radius': '0.25', 'image_point': (600, 340), 'expected_xy': (2.5, 1.0)},
            ]
            for spec in specs:
                for variable, key in [(app.pad_number, 'number'), (app.pad_width, 'width'), (app.pad_height, 'height'), (app.pad_angle, 'angle'), (app.pad_radius, 'radius')]:
                    entry(variable, spec[key])
                button('パッドを配置')
                click('pad_' + spec['number'], spec['image_point'])
                if len(app.sketch.pads) != int(spec['number']):
                    raise RuntimeError('Pad was not placed: ' + app.status.get())
                pad = app.sketch.pads[-1]
                if pad.number != spec['number'] or (pad.width, pad.height, pad.angle, pad.radius) != tuple(float(spec[key]) for key in ['width', 'height', 'angle', 'radius']):
                    raise RuntimeError('Pad dimensions or identity differ from entry input')
                checkpoint('pad_' + spec['number'] + '_placed')
            root.event_generate('<Escape>', when='tail')
            update()
            case['center_origin'] = snapshot()
            case['center_origin']['difference_from_ideal_sample_xy_mm'] = [
                {'number': pad.number, 'dx': pad.x - spec['expected_xy'][0], 'dy': pad.y - spec['expected_xy'][1]}
                for pad, spec in zip(app.sketch.pads, specs)
            ]
            case['center_origin']['export'] = export('center')
            checkpoint('center_origin_exported')

            button('原点を選ぶ')
            click('arbitrary_origin_at_pad_1', (300, 220))
            if app.mode != 'idle':
                raise RuntimeError('Arbitrary origin was not applied: ' + app.status.get())
            case['arbitrary_origin'] = snapshot()
            case['arbitrary_origin']['difference_from_ideal_sample_xy_mm'] = [
                {'number': pad.number, 'dx': pad.x - expected[0], 'dy': pad.y - expected[1]}
                for pad, expected in zip(app.sketch.pads, [(0.0, 0.0), (5.0, 2.0)])
            ]
            case['arbitrary_origin']['export'] = export('arbitrary')
            checkpoint('arbitrary_origin_exported')
            case['status'] = 'completed'
            root.destroy()
            roots.remove(root)

        report['png_jpeg_center_outputs_identical'] = report['cases'][0]['center_origin']['export']['sha256'] == report['cases'][1]['center_origin']['export']['sha256']
        report['png_jpeg_arbitrary_outputs_identical'] = report['cases'][0]['arbitrary_origin']['export']['sha256'] == report['cases'][1]['arbitrary_origin']['export']['sha256']
        report['status'] = 'completed'
        return_code = 0
    except BaseException:
        report['status'] = 'failed'
        report['exception'] = traceback.format_exc()
        return_code = 1
    finally:
        for root in roots:
            try:
                root.destroy()
            except Exception:
                pass
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        # pythonw.exe intentionally has no standard streams. The JSON receipt above
        # is authoritative; missing or detached consoles must not break cleanup.
        def emit_if_available(stream, message):
            if stream is None:
                return
            try:
                stream.write(message + '\n')
                stream.flush()
            except Exception:
                pass

        emit_if_available(sys.stdout, json.dumps({'status': report['status'], 'report': str(report_path)}, ensure_ascii=False))
        if report.get('exception'):
            emit_if_available(sys.stderr, report['exception'])
    return return_code


if __name__ == '__main__':
    raise SystemExit(main())
