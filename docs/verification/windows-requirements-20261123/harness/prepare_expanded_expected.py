"""Prepare model-derived expectations using the application's Python runtime.

The KiCad reader consumes this JSON without importing application code. Run this
with the same checkout and Python/dependencies used for expanded-tests.json.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import traceback


DEFAULT_STAGE = Path(r"D:\CodexWork\tasks\kicad-stage1-validation-20261123")
DEFAULT_REPO = Path(r"D:\CodexWork\projects\kicad-footprint-sketcher")


def decoded_data(data):
    value = dict(data)
    kind = value.pop("entity_type", value.pop("type", None))
    value["type"] = kind or ("custom" if "outline" in value else "graphic" if "graphic_kind" in value else "pad")
    for key in ("paste_shapes", "paste_sources"):
        if key in value:
            value[key] = [decoded_data(child) for child in value[key]]
    return value


def pad_expectation(pad, role="pad", *, number=None, layers=None):
    shape = ("roundrect" if pad.radius else "rect") if pad.pad_shape == "auto" else pad.pad_shape
    return dict(role=role, number=pad.number if number is None else number,
                pad_type=pad.pad_type, shape=shape, layers=sorted(pad.layers if layers is None else layers),
                x=pad.x, y=pad.y, width=pad.width, height=pad.height, angle=pad.angle % 360,
                radius=pad.radius if shape == "roundrect" else 0.0,
                drill_width=pad.drill_width, drill_height=pad.drill_height,
                mask_margin=pad.mask_margin, paste_margin=pad.paste_margin, paste_ratio=pad.paste_ratio,
                margin_defined={"mask_margin": bool(pad.mask_margin or pad.opening_explicit),
                                "paste_margin": bool(pad.paste_margin or pad.opening_explicit),
                                "paste_ratio": bool(pad.paste_ratio or pad.opening_explicit)})


def expected_entities(data):
    from footprint_sketcher.compound import CustomPad, world_outline, world_paste_shapes
    from footprint_sketcher.graphics import Graphic, arc_points, graphic_outline, world_vertices
    from footprint_sketcher.persistence import _decode_entity

    entity = _decode_entity(decoded_data(data))
    if isinstance(entity, Graphic):
        kind = entity.graphic_kind
        result = dict(role="graphic", source_kind=kind, layer=entity.layer,
                      stroke=entity.stroke, filled=entity.filled if kind not in ("line", "arc") else None)
        if kind == "line":
            a, b = world_vertices(entity)
            result.update(shape="segment", start=a, end=b)
        elif kind == "arc":
            a, input_mid, b = world_vertices(entity)
            # KiCad may reverse endpoints and reconstruct a half-sweep midpoint.
            canonical_mid = world_vertices(entity, [arc_points(entity, 2)[1]])[0]
            result.update(shape="arc", start=a, mid=canonical_mid, end=b, input_mid=input_mid)
        elif kind == "circle":
            result.update(shape="circle", center=(entity.x, entity.y), radius=entity.width / 2)
        else:
            points = world_vertices(entity, graphic_outline(entity))
            if kind == "rect" and entity.angle % 90 == 0:
                xs, ys = zip(*points)
                result.update(shape="rect", start=(min(xs), min(ys)), end=(max(xs), max(ys)))
            else:
                result.update(shape="polygon", outline=points)
        yield result
    elif isinstance(entity, CustomPad):
        copper_layers = tuple(layer for layer in entity.layers if layer not in ("F.Paste", "B.Paste"))
        record = pad_expectation(entity, "custom", layers=copper_layers)
        record.update(shape="custom", width=entity.anchor_size[0], height=entity.anchor_size[1],
                      radius=0.0, outline=entity.outline, world_outline=world_outline(entity),
                      paste_margin=0.0, paste_ratio=0.0)
        record["margin_defined"].update(paste_margin=False, paste_ratio=False)
        yield record
        paste_layers = tuple(layer for layer in entity.layers if layer in ("F.Paste", "B.Paste"))
        if paste_layers:
            for aperture in world_paste_shapes(entity):
                record = pad_expectation(aperture, "paste", number="", layers=paste_layers)
                # Custom export explicitly writes zero on standalone apertures.
                record["margin_defined"] = {key: True for key in record["margin_defined"]}
                yield record
    else:
        yield pad_expectation(entity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run = args.run or Path((args.stage / "latest-expanded-run.txt").read_text(encoding="utf-8-sig").strip())
    output = args.output or run / "expanded-expected.json"
    result = dict(status="started", created_utc=datetime.now(timezone.utc).isoformat(),
                  python=sys.version, source_commit=None, cases=[])
    try:
        sys.path.insert(0, str(args.repo))
        source_path = run / "expanded-tests.json"
        source_bytes = source_path.read_bytes()
        tests = json.loads(source_bytes.decode("utf-8-sig"))
        result.update(source_commit=tests["commit"], source_report_sha256=hashlib.sha256(source_bytes).hexdigest(),
                      source_sha256=tests.get("source_sha256", {}))
        if tests["status"] != "completed" or tests.get("failures", 0) or tests.get("errors", 0) or tests.get("skipped"):
            raise RuntimeError("Expanded tests did not complete successfully without skips")
        if not tests.get("cases"):
            raise RuntimeError("No exported cases were captured")
        for name, expected_hash in result["source_sha256"].items():
            if hashlib.sha256((args.repo / name).read_bytes()).hexdigest() != expected_hash:
                raise RuntimeError("Expectation checkout differs from tested source: " + name)
        for index, case in enumerate(tests["cases"], 1):
            file_path = Path(case["output"])
            actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if actual_hash != case["sha256"]:
                raise RuntimeError("Export SHA256 differs from test capture: " + str(file_path))
            entities = [record for pad in case["pads"] for record in expected_entities(pad)]
            if not entities:
                raise RuntimeError("Captured case has no comparable entities")
            result["cases"].append(dict(index=index, test=case["test"], capture=case.get("capture"),
                                        output=str(file_path), sha256=actual_hash,
                                        logical_entity_count=len(case["pads"]), entities=entities))
        counts = Counter(entity["role"] for case in result["cases"] for entity in case["entities"])
        result.update(status="completed", counts=dict(counts), outputs=len(result["cases"]))
    except BaseException:
        result.update(status="failed", exception=traceback.format_exc())
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({key: result.get(key) for key in ("status", "source_commit", "outputs", "counts", "exception")}, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
