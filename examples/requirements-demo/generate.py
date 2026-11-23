"""Create a movable editing demo; this is an operation sample, not a part design."""
from pathlib import Path
import argparse
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from PIL import Image
from footprint_sketcher.model import Sketch, Pad
from footprint_sketcher.graphics import Graphic
from footprint_sketcher.compound import split_paste_apertures
from footprint_sketcher.constraints import Constraint
from footprint_sketcher.persistence import save_project, safe_export_document


def generate(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    sketch = Sketch()
    sketch.set_calibration((150, 445), (750, 445), 10)
    sketch.set_origin((450, 280))
    sketch.value = 'Requirements_Demo'
    sketch.pads.extend([
        Pad('1', -2.5, -1, 1.6, 1.2, radius=.2),
        Pad('2', 2.5, 1, 1.6, 1.2, radius=.2),
        split_paste_apertures(Pad('3', 0, 0, 2.4, 1.8, radius=.15), 2, 1, .2),
        Pad('4', 0, 3, 1.6, 1.6, pad_type='thru_hole', pad_shape='circle',
            layers=('*.Cu', '*.Mask'), drill_width=.8, drill_height=.8),
        Pad('', 0, -3, 1.2, 1.2, pad_type='np_thru_hole', pad_shape='circle',
            layers=('*.Cu', '*.Mask'), drill_width=1.2, drill_height=1.2),
        Graphic('G1', 0, 0, 8, 8, layer='F.SilkS', stroke=.12),
        Graphic('G2', 0, 0, 7.5, 7.5, layer='F.Fab', stroke=.1),
        Graphic('G3', 0, 0, 9, 9, layer='F.CrtYd', stroke=.05),
    ])
    sketch.constraints.extend([Constraint('C1', 'symmetric', (0, 1)), Constraint('C2', 'equal_size', (0, 1))])
    with Image.open(REPO/'examples/stage1/land-pattern.png') as image:
        save_project(directory/'Requirements_Demo.kfsproj', sketch, image.convert('RGBA'), 'Requirements_Demo')
    pretty = directory/'Requirements_Demo.pretty'
    pretty.mkdir(exist_ok=True)
    safe_export_document(pretty, 'Requirements_Demo', sketch, overwrite=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=Path(__file__).parent)
    generate(parser.parse_args().out)
