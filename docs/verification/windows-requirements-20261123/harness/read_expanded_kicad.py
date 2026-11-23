"""Strict KiCad 10 readback of prepared expectations; no application imports.

Compatible with KiCad's Python 3.11. All cases are attempted and fully recorded.
The guards detect serialization/identity failures; they are not T02 production
acceptance criteria. Every nonmatching entity makes the process exit nonzero.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import traceback


DEFAULT_STAGE = Path(r"<WORKSPACE>\tasks\kicad-stage1-validation-20261123")
MM_GUARD = 0.000002  # Two integer-nanometer units, not a manufacturing tolerance.
ANGLE_GUARD = 1e-8
RATIO_GUARD = 1e-8


def angle_difference(actual, expected):
    return (actual - expected + 180) % 360 - 180


def edge_distance(point, ring):
    best = float("inf")
    for index, a in enumerate(ring):
        b = ring[(index + 1) % len(ring)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        denominator = dx * dx + dy * dy
        fraction = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator)) if denominator else 0.0
        best = min(best, math.hypot(point[0] - a[0] - fraction * dx, point[1] - a[1] - fraction * dy))
    return best


def area(ring):
    return abs(sum(a[0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * a[1]
                   for i, a in enumerate(ring))) / 2


def perimeter(ring):
    return sum(math.dist(a, ring[(i + 1) % len(ring)]) for i, a in enumerate(ring))


def polygon_record(polyset, expected):
    if polyset.OutlineCount() != 1 or polyset.HoleCount(0) != 0:
        raise RuntimeError("Polygon must have exactly one outer ring and no holes")
    chain = polyset.COutline(0)
    nm = [(chain.CPoint(i).x, chain.CPoint(i).y) for i in range(chain.PointCount())]
    actual = [(x / 1e6, y / 1e6) for x, y in nm]
    if len(actual) > 1 and actual[0] == actual[-1]:
        actual.pop()
    if len(actual) < 3 or len(expected) < 3:
        raise RuntimeError("Polygon has fewer than three vertices")
    distance = max(max(edge_distance(point, expected) for point in actual),
                   max(edge_distance(point, actual) for point in expected))
    difference = area(actual) - area(expected)
    area_guard = MM_GUARD * (perimeter(actual) + perimeter(expected)) + MM_GUARD ** 2 * (len(actual) + len(expected))
    return dict(kicad_vertices_nm=nm, model_vertices=len(expected), kicad_vertices=len(actual),
                maximum_vertex_to_edge_mm=distance, area_difference_mm2=difference,
                area_quantization_guard_mm2=area_guard,
                matches=distance <= MM_GUARD and abs(difference) <= area_guard)


def check_number(record, key, actual, expected, guard=MM_GUARD, unit="mm", angular=False):
    if actual is None:
        actual = 0.0
    actual, expected = float(actual), float(expected)
    if not (math.isfinite(actual) and math.isfinite(expected)):
        record["issues"].append(key + " is not finite")
        return
    difference = angle_difference(actual, expected) if angular else actual - expected
    record["differences"][key] = dict(value=difference, unit=unit)
    if abs(difference) > guard:
        record["issues"].append("{} differs by {} {} (guard {})".format(key, difference, unit, guard))


def point_tuple(vector):
    return vector.x / 1e6, vector.y / 1e6


def check_point(record, key, actual, expected):
    check_number(record, key + ".x", actual[0], expected[0])
    check_number(record, key + ".y", actual[1], expected[1])


def canonical_layer(api, board, layer):
    # GetLayerName may return UI labels such as F.Silkscreen; compare stable
    # file tokens through the confirmed PCB_LAYER_ID constants instead.
    for face in ("F", "B"):
        for suffix in ("Cu", "Paste", "Mask", "SilkS", "Fab", "CrtYd"):
            if layer == getattr(api, face + "_" + suffix):
                return face + "." + suffix
    return board.GetLayerName(layer)


def clean_layers(api, board, layers):
    return {canonical_layer(api, board, layer) for layer in layers.Seq()}


def expanded_layers(api, board, names):
    result = set(names) - {"*.Cu", "*.Mask"}
    if "*.Cu" in names:
        result.update(canonical_layer(api, board, layer) for layer in api.LSET.AllCuMask().Seq())
    if "*.Mask" in names:
        result.update(("F.Mask", "B.Mask"))
    return result


def pad_snapshot(api, board, pad, shapes, attributes):
    shape = shapes.get(pad.GetShape(), "unknown:" + str(pad.GetShape()))
    size, position, drill = pad.GetSize(), pad.GetPosition(), pad.GetDrillSize()
    margins = dict(mask_margin=pad.GetLocalSolderMaskMargin(),
                   paste_margin=pad.GetLocalSolderPasteMargin(),
                   paste_ratio=pad.GetLocalSolderPasteMarginRatio())
    return dict(number=pad.GetNumber(), pad_type=attributes.get(pad.GetAttribute(), "unknown:" + str(pad.GetAttribute())),
                shape=shape, layers=sorted(clean_layers(api, board, pad.GetLayerSet())),
                layer_ids=list(pad.GetLayerSet().Seq()),
                x=position.x / 1e6, y=position.y / 1e6, width=size.x / 1e6, height=size.y / 1e6,
                drill_width=drill.x / 1e6, drill_height=drill.y / 1e6, angle=pad.GetOrientationDegrees(),
                radius=api.ToMM(pad.GetRoundRectCornerRadius()) if shape == "roundrect" else 0.0,
                mask_margin=None if margins["mask_margin"] is None else api.ToMM(margins["mask_margin"]),
                paste_margin=None if margins["paste_margin"] is None else api.ToMM(margins["paste_margin"]),
                paste_ratio=None if margins["paste_ratio"] is None else float(margins["paste_ratio"]),
                margin_defined={key: value is not None for key, value in margins.items()})


def compare_pad(api, board, pad, actual, expected):
    record = dict(role=expected["role"], expected=expected, actual=actual, differences={}, issues=[])
    for key in ("x", "y", "width", "height", "radius", "drill_width", "drill_height", "mask_margin", "paste_margin"):
        check_number(record, key, actual[key], expected[key])
    check_number(record, "angle", actual["angle"], expected["angle"], ANGLE_GUARD, "degrees", angular=True)
    check_number(record, "paste_ratio", actual["paste_ratio"], expected["paste_ratio"], RATIO_GUARD, "ratio")
    for key, defined in expected["margin_defined"].items():
        if actual["margin_defined"][key] != defined:
            record["issues"].append(key + " explicit/inherited state differs")
    if expected["role"] == "custom":
        copper = next((layer for layer in ("F.Cu", "B.Cu") if layer in expected["layers"]), None)
        if copper is None:
            raise RuntimeError("Custom expectation has no copper face")
        layer_id = api.F_Cu if copper == "F.Cu" else api.B_Cu
        record["outline"] = polygon_record(pad.GetCustomShapeAsPolygon(layer_id), expected["outline"])
        if not record["outline"]["matches"]:
            record["issues"].append("Custom Cu polygon differs beyond quantization guard")
    record["status"] = "failed" if record["issues"] else "completed"
    return record


def graphic_snapshot(api, board, graphic, shapes):
    result = dict(shape=shapes.get(graphic.GetShape(), "unknown:" + str(graphic.GetShape())),
                  layer=canonical_layer(api, board, graphic.GetLayer()), layer_id=graphic.GetLayer(), stroke=graphic.GetWidth() / 1e6,
                  filled=bool(graphic.IsSolidFill()))
    shape = result["shape"]
    if shape in ("segment", "rect", "arc", "circle"):
        result.update(start=point_tuple(graphic.GetStart()), end=point_tuple(graphic.GetEnd()))
    if shape == "arc":
        result["mid"] = point_tuple(graphic.GetArcMid())
    if shape == "circle":
        result["center"] = point_tuple(graphic.GetCenter())
        result["radius"] = math.dist(result["start"], result["end"])
    return result


def endpoint_cost(actual, expected):
    return min(math.dist(actual["start"], expected["start"]) + math.dist(actual["end"], expected["end"]),
               math.dist(actual["end"], expected["start"]) + math.dist(actual["start"], expected["end"]))


def compare_graphic(graphic, actual, expected):
    record = dict(role="graphic", expected=expected, actual=actual, differences={}, issues=[])
    check_number(record, "stroke", actual["stroke"], expected["stroke"])
    if expected["filled"] is not None and actual["filled"] != expected["filled"]:
        record["issues"].append("Graphic solid fill differs")
    shape = expected["shape"]
    if shape in ("segment", "rect", "arc"):
        direct = math.dist(actual["start"], expected["start"]) + math.dist(actual["end"], expected["end"])
        reverse = math.dist(actual["end"], expected["start"]) + math.dist(actual["start"], expected["end"])
        check_point(record, "start", actual["start"] if direct <= reverse else actual["end"], expected["start"])
        check_point(record, "end", actual["end"] if direct <= reverse else actual["start"], expected["end"])
        record["kicad_reversed_endpoints"] = reverse < direct
        if shape == "arc":
            check_point(record, "arc_mid", actual["mid"], expected["mid"])
    elif shape == "circle":
        check_point(record, "center", actual["center"], expected["center"])
        check_number(record, "circle_radius", actual["radius"], expected["radius"])
    elif shape == "polygon":
        record["outline"] = polygon_record(graphic.GetPolyShape(), expected["outline"])
        if not record["outline"]["matches"]:
            record["issues"].append("Graphic polygon differs beyond quantization guard")
    else:
        record["issues"].append("Unsupported expected graphic shape")
    record["status"] = "failed" if record["issues"] else "completed"
    return record


def case_readback(api, board, case, pad_shapes, attributes, graphic_shapes):
    output = Path(case["output"])
    record = dict(index=case["index"], test=case["test"], capture=case.get("capture"), output=str(output),
                  status="started", logical_entity_count=case["logical_entity_count"], entities=[], issues=[])
    try:
        record["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        if record["sha256"] != case["sha256"]:
            raise RuntimeError("Export SHA256 differs from captured model")
        footprint = api.FootprintLoad(str(output.parent), output.stem)
        if footprint is None:
            raise RuntimeError("FootprintLoad returned None")
        pads = [(pad, pad_snapshot(api, board, pad, pad_shapes, attributes)) for pad in footprint.Pads()]
        items = list(footprint.GraphicalItems())
        graphics = [(item, graphic_snapshot(api, board, item, graphic_shapes)) for item in items if item.GetClass() == "PCB_SHAPE"]
        record.update(physical_pad_count=len(pads), graphic_count=len(graphics),
                      other_graphic_classes=[item.GetClass() for item in items if item.GetClass() != "PCB_SHAPE"],
                      actual_pads=[snapshot for _, snapshot in pads], actual_graphics=[snapshot for _, snapshot in graphics])
        expected_pads = [want for want in case["entities"] if want["role"] != "graphic"]
        expected_graphics = [want for want in case["entities"] if want["role"] == "graphic"]
        if len(pads) != len(expected_pads):
            record["issues"].append("Physical pad/aperture count mismatch")
        if len(graphics) != len(expected_graphics):
            record["issues"].append("Graphic shape count mismatch")
        for want in expected_pads:
            layers = expanded_layers(api, board, want["layers"])
            matching = [(pad, got) for pad, got in pads if got["number"] == want["number"]
                        and set(got["layers"]) == layers and got["shape"] == want["shape"] and got["pad_type"] == want["pad_type"]]
            if not matching:
                record["entities"].append(dict(role=want["role"], expected=want, status="failed",
                                               issues=["No pad matches number, type, shape and exact layer set"]))
                continue
            pad, got = min(matching, key=lambda pair: sum(abs(pair[1][key] - want[key]) for key in ("x", "y", "width", "height", "radius"))
                           + abs(angle_difference(pair[1]["angle"], want["angle"])) / 360)
            pads.remove((pad, got))
            try:
                record["entities"].append(compare_pad(api, board, pad, got, want))
            except BaseException:
                record["entities"].append(dict(role=want["role"], expected=want, actual=got, status="failed", exception=traceback.format_exc()))
        for want in expected_graphics:
            matching = [(item, got) for item, got in graphics if got["shape"] == want["shape"] and got["layer"] == want["layer"]]
            if not matching:
                record["entities"].append(dict(role="graphic", expected=want, status="failed", issues=["No graphic matches shape and layer"]))
                continue
            def cost(pair):
                got = pair[1]
                distance = endpoint_cost(got, want) if want["shape"] in ("segment", "rect", "arc") else (
                    math.dist(got["center"], want["center"]) + abs(got["radius"] - want["radius"]) if want["shape"] == "circle" else
                    polygon_record(pair[0].GetPolyShape(), want["outline"])["maximum_vertex_to_edge_mm"])
                return distance + abs(got["stroke"] - want["stroke"])
            item, got = min(matching, key=cost)
            graphics.remove((item, got))
            try:
                record["entities"].append(compare_graphic(item, got, want))
            except BaseException:
                record["entities"].append(dict(role="graphic", expected=want, actual=got, status="failed", exception=traceback.format_exc()))
        if pads or graphics:
            record["issues"].append("Unexpected unconsumed physical entities")
        if any(entity["status"] != "completed" for entity in record["entities"]):
            record["issues"].append("One or more entity comparisons failed")
        record["status"] = "failed" if record["issues"] else "completed"
    except BaseException:
        record.update(status="failed", exception=traceback.format_exc())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run = args.run or Path((args.stage / "latest-expanded-run.txt").read_text(encoding="utf-8-sig").strip())
    expected_path, output = args.expected or run / "expanded-expected.json", args.output or run / "expanded-kicad.json"
    result = dict(status="started", timestamp_utc=datetime.now(timezone.utc).isoformat(), source_commit=None, cases=[],
                  method="KiCad PAD/PCB_SHAPE readback; model-derived JSON without source imports",
                  guards=dict(scalar_mm=MM_GUARD, outline_vertex_edge_mm=MM_GUARD, angle_degrees=ANGLE_GUARD,
                              ratio=RATIO_GUARD, purpose="Serialization corruption detection; not adopted T02 acceptance criteria"))
    try:
        import pcbnew as api
        result["version"] = api.GetBuildVersion()
        expected_bytes = expected_path.read_bytes()
        expected = json.loads(expected_bytes.decode("utf-8-sig"))
        result.update(source_commit=expected["source_commit"], expected_sha256=hashlib.sha256(expected_bytes).hexdigest(),
                      expected_counts=expected.get("counts", {}))
        if expected["status"] != "completed" or not expected.get("cases"):
            raise RuntimeError("Prepared expectation file did not complete or has no cases")
        if hashlib.sha256((run / "expanded-tests.json").read_bytes()).hexdigest() != expected["source_report_sha256"]:
            raise RuntimeError("Source test report changed after expectation preparation")
        board = api.BOARD()
        pad_shapes = {getattr(api, "PAD_SHAPE_" + name.upper()): name for name in ("rect", "roundrect", "circle", "oval", "custom")}
        attributes = {api.PAD_ATTRIB_SMD: "smd", api.PAD_ATTRIB_PTH: "thru_hole", api.PAD_ATTRIB_NPTH: "np_thru_hole"}
        graphic_shapes = {api.SHAPE_T_SEGMENT: "segment", api.SHAPE_T_RECT: "rect", api.SHAPE_T_ARC: "arc",
                          api.SHAPE_T_CIRCLE: "circle", api.SHAPE_T_POLY: "polygon"}
        for case in expected["cases"]:
            result["cases"].append(case_readback(api, board, case, pad_shapes, attributes, graphic_shapes))
        entities = [entity for case in result["cases"] for entity in case["entities"]]
        result["compared_counts"] = dict(Counter(entity["role"] for entity in entities))
        maxima = {}
        for unit in ("mm", "degrees", "ratio"):
            maxima[unit] = max((abs(diff["value"]) for entity in entities for diff in entity.get("differences", {}).values()
                                if diff["unit"] == unit), default=0.0)
        maxima["outline_vertex_edge_mm"] = max((entity["outline"]["maximum_vertex_to_edge_mm"] for entity in entities if "outline" in entity), default=0.0)
        maxima["outline_area_mm2"] = max((abs(entity["outline"]["area_difference_mm2"]) for entity in entities if "outline" in entity), default=0.0)
        result.update(max_differences=maxima, outputs=len(result["cases"]),
                      failed_cases=sum(case["status"] != "completed" for case in result["cases"]))
        result["status"] = "failed" if result["failed_cases"] else "completed"
    except BaseException:
        result.update(status="failed", exception=traceback.format_exc())
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({key: result.get(key) for key in ("status", "version", "source_commit", "outputs", "failed_cases", "compared_counts", "max_differences", "exception")}, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
