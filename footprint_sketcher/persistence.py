"""KiCad 10 import with loss-aware retention, and self-contained project files."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
from tempfile import NamedTemporaryFile
import warnings
import zipfile
from uuid import NAMESPACE_URL, uuid5

from PIL import Image

from .model import (AxisCalibration, Calibration, Pad, Sketch, KICAD_FORMAT_VERSION,
                    _quoted, _render_pad, _validate_footprint_name, export_footprint)

_MAX_TEXT = 32 * 1024 * 1024
_MAX_MEMBER = 256 * 1024 * 1024
_FORMAT = "kicad-footprint-sketcher"
_PASTE_OWNERSHIP_PROPERTY = "FootprintSketcher-PasteOwnership"


@dataclass
class _Atom:
    value: str
    start: int
    end: int


@dataclass
class _Node:
    items: list
    start: int
    end: int

    @property
    def key(self):
        return self.items[0].value if self.items and isinstance(self.items[0], _Atom) else ""


def _parse(text: str) -> _Node:
    """Retain source spans; never reconstruct unknown s-expressions."""
    if len(text.encode("utf-8")) > _MAX_TEXT:
        raise ValueError("フットプリントが読み込みサイズの上限を超えています。")
    pos, count = 0, 0

    def skip():
        nonlocal pos
        while pos < len(text):
            if text[pos].isspace() or (pos == 0 and text[pos] == "\ufeff"):
                pos += 1
            elif text[pos] == ";":
                end = text.find("\n", pos)
                pos = len(text) if end == -1 else end + 1
            else:
                break

    def item(depth=0):
        nonlocal pos, count
        count += 1
        if depth > 128 or count > 1_000_000:
            raise ValueError("S式の深さまたは要素数の上限を超えています。")
        skip()
        if pos >= len(text):
            raise ValueError("S式が途中で終了しています。")
        start = pos
        if text[pos] == "(":
            pos += 1
            items = []
            while True:
                skip()
                if pos >= len(text):
                    raise ValueError("S式の閉じ括弧がありません。")
                if text[pos] == ")":
                    pos += 1
                    return _Node(items, start, pos)
                items.append(item(depth + 1))
        if text[pos] == ")":
            raise ValueError("対応しない閉じ括弧があります。")
        if text[pos] == '"':
            pos += 1
            value = []
            while pos < len(text):
                char = text[pos]
                pos += 1
                if char == '"':
                    return _Atom("".join(value), start, pos)
                if char == "\\":
                    if pos >= len(text):
                        break
                    escaped = text[pos]
                    pos += 1
                    value.append({'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}.get(escaped, "\\" + escaped))
                else:
                    value.append(char)
            raise ValueError("文字列の閉じ引用符がありません。")
        while pos < len(text) and not text[pos].isspace() and text[pos] not in '();"':
            pos += 1
        if pos == start:
            raise ValueError("不正なS式トークンです。")
        return _Atom(text[start:pos], start, pos)

    root = item()
    skip()
    if pos != len(text) or not isinstance(root, _Node) or root.key != "footprint":
        raise ValueError("単一のKiCad footprint S式を指定してください。")
    return root


def _children(node, key):
    return [item for item in node.items if isinstance(item, _Node) and item.key == key]


def _one(node, key, required=False):
    nodes = _children(node, key)
    if len(nodes) > 1 or (required and not nodes):
        raise ValueError(f"{key}の指定が重複または欠落しています。")
    return nodes[0] if nodes else None


def _atom(node, index):
    if node is None or len(node.items) <= index or not isinstance(node.items[index], _Atom):
        raise ValueError("S式の属性が不足しています。")
    return node.items[index].value


def _num(node, index, default=None):
    if node is None and default is not None:
        return default
    try:
        value = float(_atom(node, index))
    except (ValueError, TypeError, OverflowError):
        raise ValueError("座標・寸法には有限の数値が必要です。") from None
    if not math.isfinite(value):
        raise ValueError("座標・寸法には有限の数値が必要です。")
    return value


def _xy(node):
    return _num(node, 1), _num(node, 2)


def _raw(text, node):
    return text[node.start:node.end]


def _node_identity(node):
    uuid, timestamp = _one(node, "uuid"), _one(node, "tstamp")
    if uuid is not None and timestamp is not None:
        raise ValueError("開口の所有情報に複数の識別子があります。")
    identity = uuid if uuid is not None else timestamp
    return _atom(identity, 1) if identity is not None else None


def _render_entities_with_ownership(pads):
    """Use explicit identities, never generator/adjacency, for Paste ownership."""
    from .compound import CustomPad, world_paste_shapes

    pads = list(pads)
    _validate_preserved(pads)
    lines, pairs = [], []
    prepared, reserved = [], set()

    def identity_wrapper(pad):
        return _parse(f'(footprint "identity" (version {KICAD_FORMAT_VERSION}) ' + ' '.join(pad.preserved) + ')')

    for index, pad in enumerate(pads):
        paste_layers = tuple(layer for layer in pad.layers if layer in ("F.Paste", "B.Paste"))
        windows = world_paste_shapes(pad) if isinstance(pad, CustomPad) and paste_layers else []
        prepared.append((index, pad, paste_layers, windows))
        for entity in (pad, *windows):
            wrapper = identity_wrapper(entity)
            reserved.update(_atom(node, 1) for key in ("uuid", "tstamp") for node in _children(wrapper, key))

    def identified(pad, index, role, window_index=None):
        wrapper = identity_wrapper(pad)
        identity = _node_identity(wrapper)
        if identity is None:
            # Stable IDs keep unchanged exports and Undo comparisons byte
            # stable. Role and output indices distinguish identical instances.
            seed = {"format": _FORMAT, "ownership_version": 1, "entity": _entity_data(pad),
                    "index": index, "role": role, "window_index": window_index, "salt": 0}
            while True:
                name = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                identity = str(uuid5(NAMESPACE_URL, name))
                if identity not in reserved:
                    break
                seed["salt"] += 1
            reserved.add(identity)
            pad = replace(pad, preserved=pad.preserved + (f'(uuid {_quoted(identity)})',))
        return pad, identity

    for index, pad, paste_layers, windows in prepared:
        if not windows:
            lines.extend(_render_pad(pad))
            continue
        parent, parent_id = identified(pad, index, "custom")
        lines.extend(_render_pad(parent, layers=tuple(layer for layer in pad.layers if layer not in paste_layers)))
        window_ids = []
        for window_index, aperture in enumerate(windows):
            aperture, identity = identified(aperture, index, "paste", window_index)
            window_ids.append(identity)
            lines.extend(_render_pad(replace(aperture, opening_explicit=True), layers=paste_layers, number=""))
        pairs.append([parent_id, window_ids])
    if pairs:
        value = json.dumps({"version": 1, "pairs": pairs}, separators=(",", ":"))
        lines.append(f'  (property {_quoted(_PASTE_OWNERSHIP_PROPERTY)} {_quoted(value)} '
                     '(at 0 0 0) (layer "F.Fab") (hide yes) '
                     '(effects (font (size 1 1) (thickness 0.15))))')
    return lines


def _paste_ownership(root):
    properties = [node for node in _children(root, "property") if _atom(node, 1) == _PASTE_OWNERSHIP_PROPERTY]
    if not properties:
        return {}
    if len(properties) != 1:
        raise ValueError("開口の所有情報が重複しています。")
    data = _json(_atom(properties[0], 2))
    if (not isinstance(data, dict) or set(data) != {"version", "pairs"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["pairs"], list) or not data["pairs"]):
        raise ValueError("開口の所有情報の形式が不正です。")
    by_identity = {}
    for node in [root, *(child for child in root.items if isinstance(child, _Node))]:
        identity = _node_identity(node)
        if identity is not None:
            by_identity.setdefault(identity, []).append(node)
    used, ownership = set(), {}
    for pair in data["pairs"]:
        if (not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str)
                or not isinstance(pair[1], list) or not pair[1]
                or any(not isinstance(identity, str) or not identity for identity in [pair[0], *pair[1]])):
            raise ValueError("開口の所有情報の対応が不正です。")
        nodes = []
        for identity in [pair[0], *pair[1]]:
            if identity in used or len(by_identity.get(identity, [])) != 1:
                raise ValueError("開口の所有情報の識別子が重複または欠落しています。")
            used.add(identity)
            nodes.append(by_identity[identity][0])
        parent, *apertures = nodes
        if (parent.key != "pad" or _atom(parent, 1) == "" or _atom(parent, 2) != "smd" or _atom(parent, 3) != "custom"
                or any(child.key != "pad" or _atom(child, 1) != "" or _atom(child, 2) != "smd" for child in apertures)):
            raise ValueError("開口の所有情報の図形種別が一致しません。")
        ownership[parent.start] = apertures
    return ownership


def _entity_data(entity):
    from .compound import CustomPad
    from .graphics import Graphic
    result = {field.name: getattr(entity, field.name) for field in fields(entity) if field.init}
    for key in ("paste_shapes", "paste_sources"):
        if key in result:
            result[key] = [_entity_data(pad) for pad in result[key]]
    result["type"] = "custom" if isinstance(entity, CustomPad) else "graphic" if isinstance(entity, Graphic) else "pad"
    return result


def _fingerprint(sketch, name, geometry=False):
    data = {"pads": [_entity_data(pad) for pad in sketch.pads], "preserved": sketch.preserved_nodes}
    if not geometry:
        data.update(name=name, reference=sketch.reference, value=sketch.value)
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _read_pad(node, text, *, aperture=False, opening_defaults=None):
    kind, shape = _atom(node, 2), _atom(node, 3)
    if kind not in ("smd", "thru_hole", "np_thru_hole") or shape not in ("rect", "roundrect", "circle", "oval", "custom"):
        raise ValueError("特殊パッド種別・形状は保持のみです。")
    at, size, layers = _one(node, "at", True), _one(node, "size", True), _one(node, "layers", True)
    width, height = _xy(size)
    layer_values = tuple(_atom(layers, i) for i in range(1, len(layers.items)))
    number = _atom(node, 1)
    # Empty-number Paste-only apertures are graphical manufacturing elements,
    # not numbered electrical pads; keep them verbatim rather than invent a pin.
    if not number and kind == "smd":
        if not aperture:
            raise ValueError("空番号の専用開口は保持のみです。")
        number = "aperture"
    allowed = {"at", "size", "layers", "drill", "roundrect_rratio", "solder_mask_margin",
               "solder_paste_margin", "solder_paste_margin_ratio", "uuid", "tstamp", "pinfunction", "pintype",
               "clearance", "zone_connect", "thermal_bridge_width", "thermal_gap", "options", "primitives"}
    if any(isinstance(child, _Node) and child.key not in allowed for child in node.items[4:]):
        raise ValueError("未対応のパッド属性を含むため保持のみです。")
    if any(isinstance(child, _Atom) for child in node.items[4:]):
        raise ValueError("特殊パッド属性は保持のみです。")
    drill = _one(node, "drill")
    hole_w = hole_h = 0.0
    if drill:
        if any(isinstance(item, _Node) for item in drill.items):
            raise ValueError("偏心穴等は保持のみです。")
        if _atom(drill, 1) == "oval":
            if len(drill.items) != 4:
                raise ValueError("長穴指定を保持のみとして扱います。")
            hole_w, hole_h = _num(drill, 2), _num(drill, 3)
        else:
            if len(drill.items) != 2:
                raise ValueError("丸穴指定を保持のみとして扱います。")
            hole_w = hole_h = _num(drill, 1)
    radius = _num(_one(node, "roundrect_rratio"), 1, 0.0) * min(width, height) if shape == "roundrect" else 0.0
    handled = {"at", "size", "layers", "drill", "roundrect_rratio", "solder_mask_margin", "solder_paste_margin", "solder_paste_margin_ratio", "options", "primitives"}
    preserved = tuple(_raw(text, child) for child in node.items[4:] if isinstance(child, _Node) and child.key not in handled)
    defaults = opening_defaults or {}
    common = dict(number=number, x=_num(at, 1), y=_num(at, 2), width=width, height=height,
                  angle=_num(at, 3) if len(at.items) > 3 else 0, radius=radius, pad_type=kind,
                  pad_shape=shape if shape != "custom" else "auto", layers=layer_values,
                  drill_width=hole_w, drill_height=hole_h,
                  mask_margin=_num(_one(node, "solder_mask_margin"), 1, defaults.get("mask_margin", 0.0)),
                  paste_margin=_num(_one(node, "solder_paste_margin"), 1, defaults.get("paste_margin", 0.0)),
                  paste_ratio=_num(_one(node, "solder_paste_margin_ratio"), 1, defaults.get("paste_ratio", 0.0)),
                  opening_explicit=True, preserved=preserved)
    # Paste-only entities already describe the physical window. KiCad applies
    # the pad/footprint paste adjustment to copper-backed pads, not again to
    # such manufacturing-only windows. Keep their geometry and emit zeroes.
    if aperture:
        common.update(mask_margin=0.0, paste_margin=0.0, paste_ratio=0.0)
    if shape != "custom":
        if _one(node, "options") or _one(node, "primitives"):
            raise ValueError("特殊形状データを含むパッドは保持のみです。")
        return Pad(**common)
    from .compound import CustomPad, _sharp_corners
    options, primitives = _one(node, "options", True), _one(node, "primitives", True)
    if _atom(_one(options, "anchor", True), 1) != "rect" or _atom(_one(options, "clearance", True), 1) != "outline":
        raise ValueError("特殊Custom接続方式は保持のみです。")
    if kind != "smd" or len(primitives.items) != 2 or not isinstance(primitives.items[1], _Node) or primitives.items[1].key != "gr_poly":
        raise ValueError("複数・特殊プリミティブのCustomは保持のみです。")
    if any(layer in ("F.Paste", "B.Paste") for layer in layer_values):
        raise ValueError("Cu輪郭自体をPasteにも使うCustomは保持のみです。")
    polygon = primitives.items[1]
    if _num(_one(polygon, "width", True), 1) != 0 or _atom(_one(polygon, "fill", True), 1) != "yes":
        raise ValueError("非塗潰しのCustomは保持のみです。")
    outline = tuple(_xy(point) for point in _one(polygon, "pts", True).items[1:])
    if len(outline) > 1 and outline[0] == outline[-1]:
        outline = outline[:-1]
    common.update(width=max(x for x, _ in outline)-min(x for x, _ in outline),
                  height=max(y for _, y in outline)-min(y for _, y in outline), radius=0,
                  paste_margin=0.0, paste_ratio=0.0)
    return CustomPad(**common, outline=outline, corners=_sharp_corners(outline, ()), anchor_size=(width, height))


def _read_graphic(node, text, number):
    from .graphics import Graphic, graphic_from_points
    kind = {"fp_rect": "rect", "fp_line": "line", "fp_circle": "circle", "fp_arc": "arc", "fp_poly": "polygon"}[node.key]
    allowed = {"start", "end", "center", "mid", "pts", "stroke", "width", "fill", "layer", "uuid", "tstamp"}
    if any(not isinstance(child, _Node) or child.key not in allowed for child in node.items[1:]):
        raise ValueError("特殊な図形属性は保持のみです。")
    # Keep identity children on edits; reference-bearing unknown root nodes
    # independently block geometry changes that could invalidate membership.
    stroke_node = _one(node, "stroke")
    if stroke_node:
        if _atom(_one(stroke_node, "type", True), 1) not in ("solid", "default") or _one(stroke_node, "color"):
            raise ValueError("特殊線種・色を含む図形は保持のみです。")
        stroke = _num(_one(stroke_node, "width", True), 1)
    else:
        stroke = _num(_one(node, "width", True), 1)
    fill = _one(node, "fill")
    filled = fill is not None and _atom(fill, 1) in ("solid", "yes")
    props = dict(layer=_atom(_one(node, "layer", True), 1), stroke=stroke, filled=filled,
                 preserved=tuple(_raw(text, child) for child in node.items if isinstance(child, _Node) and child.key in ("uuid", "tstamp")))
    if kind == "circle":
        center, end = _xy(_one(node, "center", True)), _xy(_one(node, "end", True))
        diameter = 2 * math.dist(center, end)
        return Graphic(number, *center, diameter, diameter, graphic_kind=kind, **props)
    if kind in ("rect", "line", "arc"):
        points = [_xy(_one(node, key, True)) for key in (("start", "mid", "end") if kind == "arc" else ("start", "end"))]
    else:
        points = [_xy(point) for point in _one(node, "pts", True).items[1:]]
    return graphic_from_points(number, kind, points, **props)


def load_footprint(path: Path) -> tuple[str, Sketch, list[str]]:
    try:
        with Path(path).open("r", encoding="utf-8", newline="") as source:
            text = source.read(_MAX_TEXT + 1)
    except UnicodeError as error:
        raise ValueError("UTF-8のフットプリントを指定してください。") from error
    return _load_text(text)


def _load_text(text):
    root = _parse(text)
    name = _atom(root, 1)
    version = _one(root, "version", True)
    if _atom(version, 1) != str(KICAD_FORMAT_VERSION):
        raise ValueError("KiCad 10.0形式（20260206）のみ読み込めます。元ファイルは変更しません。")
    sketch, classification, editable, retained, blocked, properties = Sketch(), [], [], [], [], {}
    sketch.value = name
    passive = {"descr", "tags", "attr", "model", "uuid", "tstamp", "fp_text", "fp_text_box", "solder_mask_margin", "solder_paste_margin", "solder_paste_ratio",
               "clearance", "zone_connect", "thermal_width", "thermal_gap", "autoplace_cost90", "autoplace_cost180"}
    # KiCad adds these independent footprint settings during a native save.
    # Keep their raw values, including jumper connectivity semantics, while
    # permitting geometry edits only for the known scalar boolean form.
    for key in ("duplicate_pad_numbers_are_jumpers", "embedded_fonts"):
        node = _one(root, key)
        if node is not None and (len(node.items) != 2 or not isinstance(node.items[1], _Atom)
                                 or _atom(node, 1) not in ("yes", "no")):
            raise ValueError(f"{key}は単一のyes/no値を指定してください。")
        passive.add(key)
    _one(root, "generator")
    ownership = _paste_ownership(root)
    owned_apertures = {child.start for children in ownership.values() for child in children}
    opening_defaults = {
        "mask_margin": _num(_one(root, "solder_mask_margin"), 1, 0.0),
        "paste_margin": _num(_one(root, "solder_paste_margin"), 1, 0.0),
        "paste_ratio": _num(_one(root, "solder_paste_ratio"), 1, 0.0),
    }
    for child in root.items[2:]:
        if not isinstance(child, _Node):
            retained.append(_raw(text, child)); blocked.append("未対応のルート属性")
            continue
        if child.key in ("version", "generator", "generator_version", "layer"):
            continue
        if child.start in owned_apertures:
            continue
        if child.key == "property" and _atom(child, 1) in ("Reference", "Value"):
            key = _atom(child, 1)
            if key in properties:
                raise ValueError("Reference/Valueが重複しています。")
            properties[key] = _raw(text, child)
            setattr(sketch, "reference" if key == "Reference" else "value", _atom(child, 2))
            classification.append(f"編集可能: {key}（文字値、他属性は保持）")
            continue
        try:
            if child.key == "pad":
                from .compound import CustomPad, world_to_local
                entity = _read_pad(child, text, opening_defaults=opening_defaults)
                if child.start in ownership:
                    if not isinstance(entity, CustomPad):
                        raise ValueError("開口の所有情報に対応するCustomがありません。")
                    apertures, layers = [], entity.layers
                    for aperture_node in ownership[child.start]:
                        aperture = _read_pad(aperture_node, text, aperture=True, opening_defaults=opening_defaults)
                        if (any(layer not in ("F.Paste", "B.Paste") for layer in aperture.layers)
                                or any(layer.replace(".Paste", ".Cu") not in entity.layers for layer in aperture.layers)):
                            raise ValueError("所有するPaste開口の面がCustomと一致しません。")
                        x, y = world_to_local(entity, (aperture.x, aperture.y))
                        apertures.append(replace(aperture, x=x, y=y, angle=(aperture.angle-entity.angle)%360))
                        layers = tuple(dict.fromkeys(layers + aperture.layers))
                    entity = replace(entity, paste_shapes=tuple(apertures), paste_sources=tuple(apertures),
                                     paste_applied=True, layers=layers)
            elif child.key in ("fp_rect", "fp_line", "fp_circle", "fp_arc", "fp_poly"):
                entity = _read_graphic(child, text, f"graphic-{len(sketch.pads)+1}")
            else:
                raise ValueError("未対応の要素")
        except (ValueError, TypeError, IndexError, KeyError) as error:
            raw = _raw(text, child)
            retained.append(raw)
            classification.append(f"保持のみ: {child.key}（{error}）")
            if child.key not in passive and child.key != "property":
                blocked.append(f"{child.key}: {error}")
            for aperture_node in ownership.get(child.start, []):
                retained.append(_raw(text, aperture_node))
                classification.append("保持のみ: 所有するPaste開口（親Customを保全できないため）")
        else:
            editable.append(child.start)
            editable.extend(aperture.start for aperture in ownership.get(child.start, []))
            sketch.pads.append(entity)
            classification.append(f"編集可能: {child.key} {entity.number}")
    sketch.preserved_nodes = tuple(retained)
    sketch.metadata = {"original_content": text, "source_name": name, "source_version": KICAD_FORMAT_VERSION,
                       "editable_nodes": editable, "original_preserved_nodes": retained,
                       "blocked_geometry": blocked, "source_properties": properties, "classification": classification}
    sketch.metadata["original_fingerprint"] = _fingerprint(sketch, name)
    sketch.metadata["original_geometry_fingerprint"] = _fingerprint(sketch, name, True)
    return name, sketch, classification


def _atomic_bytes(path: Path, data: bytes, overwrite: bool):
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".sketcher-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        elif os.name == "nt":
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _validate_preserved(pads):
    from .compound import CustomPad

    allowed = {"uuid", "tstamp", "pinfunction", "pintype", "clearance", "zone_connect", "thermal_bridge_width", "thermal_gap"}
    identifiers = set()

    def validate(pad, *, rendered=True):
        for raw in pad.preserved:
            if not isinstance(raw, str):
                raise ValueError("保持されたパッド属性が不正です。")
            wrapped = _parse(f'(footprint "check" (version {KICAD_FORMAT_VERSION}) {raw})')
            if len(wrapped.items) != 4 or not isinstance(wrapped.items[-1], _Node) or wrapped.items[-1].key not in allowed:
                raise ValueError("編集可能パッドの保持属性が未対応です。")
            child = wrapped.items[-1]
            if rendered and child.key in ("uuid", "tstamp"):
                identity = _atom(child, 1)
                if identity in identifiers:
                    raise ValueError("識別子が重複しているため保存を停止します。コピーの識別子を再生成してください。")
                identifiers.add(identity)
        if isinstance(pad, CustomPad):
            paste_rendered = rendered and any(layer in ("F.Paste", "B.Paste") for layer in pad.layers)
            for aperture in pad.paste_shapes:
                validate(aperture, rendered=paste_rendered)
            # Source snapshots are metadata, not additional rendered pads. They
            # may legitimately share an identity with the retained source pad.
            for source in pad.paste_sources:
                validate(source, rendered=False)

    for pad in pads:
        validate(pad)


def _validate_constraints(sketch):
    """Validate existing geometry as hard data; saving must never solve it away."""
    if not sketch.constraints:
        return
    from .constraints import FIELDS, solve_constraints
    driving = {index: {field: getattr(pad, field) for field in FIELDS}
               for index, pad in enumerate(sketch.pads)}
    result = solve_constraints(sketch.pads, sketch.constraints, driving=driving)
    if result.pads != sketch.pads:
        raise ValueError("未解決の拘束があります。形状を確定してから保存してください。")


def _update_footprint_type(root, pads, editable):
    """Change only mounting-type tokens, never discard the other attr flags.

    KiCad's footprint attr TYPE is smd or through_hole, distinct from the
    individual pad's thru_hole token. Mechanical NPTH-only objects have no
    electrical mounting-type token. See the official Footprint Attributes
    section in https://dev-docs.kicad.org/en/file-formats/sexpr-intro/ .
    """
    from .graphics import Graphic
    old = sorted(_atom(node, 2) for node in _children(root, "pad")
                 if node.start in editable and (_atom(node, 1) or _atom(node, 2) != "smd"))
    current = sorted(pad.pad_type for pad in pads if not isinstance(pad, Graphic))
    if old == current:
        return [], None
    kind = "through_hole" if "thru_hole" in current else "smd" if "smd" in current else None
    attr = _one(root, "attr")
    if attr is None:
        return [], f"  (attr {kind})" if kind else None
    types = [item for item in attr.items[1:] if isinstance(item, _Atom) and item.value in ("smd", "through_hole")]
    changes = [(item.start, item.end, kind if i == 0 and kind else "") for i, item in enumerate(types)]
    if kind and not types:
        changes.append((attr.items[0].end, attr.items[0].end, " " + kind))
    return changes, None


def safe_export_document(directory: Path, name: str, sketch: Sketch, *, overwrite: bool = False) -> Path:
    _validate_footprint_name(name)
    _validate_constraints(sketch)
    metadata = sketch.metadata
    original = metadata.get("original_content")
    if not original:
        if sketch.preserved_nodes:
            raise ValueError("保持要素の入力原文がないため、意味を保全した出力を保証できません。")
        _validate_preserved(sketch.pads)
        return export_footprint(directory, name, sketch.pads, overwrite=overwrite,
                                reference=sketch.reference, value=sketch.value or name,
                                preserved_nodes=sketch.preserved_nodes)
    source_root = _parse(original)
    if _atom(_one(source_root, "version", True), 1) != str(KICAD_FORMAT_VERSION) or _atom(source_root, 1) != metadata.get("source_name"):
        raise ValueError("保持された入力元の形式情報が不正です。")
    if _fingerprint(sketch, name) == metadata.get("original_fingerprint"):
        return _atomic_bytes(Path(directory) / f"{name}.kicad_mod", original.encode("utf-8"), overwrite)
    _validate_preserved(sketch.pads)
    geometry_changed = _fingerprint(sketch, name, True) != metadata.get("original_geometry_fingerprint")
    if metadata.get("blocked_geometry") and (geometry_changed or name != metadata.get("source_name")):
        raise ValueError("保持要素の参照を保全できないため保存を停止します: " + "; ".join(metadata["blocked_geometry"]))
    if list(sketch.preserved_nodes) != metadata.get("original_preserved_nodes"):
        raise ValueError("保持のみの要素が変更されているため保存を停止します。")
    root = _parse(original)
    changes = []
    name_atom = root.items[1]
    changes.append((name_atom.start, name_atom.end, _quoted(name)))
    edited = set(metadata.get("editable_nodes", []))
    # A newly placed pad has its own numeric opening values too. Do not let a
    # retained footprint-wide setting silently override a new pad's zeroes.
    rendered = "\n".join(_render_entities_with_ownership(replace(pad, opening_explicit=True) for pad in sketch.pads))
    inserted, found = False, set()
    generator_found = False
    for child in root.items[2:]:
        if not isinstance(child, _Node):
            continue
        if geometry_changed and child.start in edited:
            changes.append((child.start, child.end, rendered if not inserted else "")); inserted = True
        if geometry_changed and child.key == "generator":
            atom = child.items[1]
            changes.append((atom.start, atom.end, _quoted("kicad_footprint_sketcher")))
            generator_found = True
        if geometry_changed and child.key == "generator_version":
            # The source program's release is not this application's version.
            changes.append((child.start, child.end, ""))
        if geometry_changed and child.key == "property" and _atom(child, 1) == _PASTE_OWNERSHIP_PROPERTY:
            changes.append((child.start, child.end, ""))
        if child.key == "property" and _atom(child, 1) in ("Reference", "Value"):
            key = _atom(child, 1); found.add(key)
            atom = child.items[2]
            changes.append((atom.start, atom.end, _quoted(sketch.reference if key == "Reference" else sketch.value)))
    additions = []
    if geometry_changed:
        type_changes, type_addition = _update_footprint_type(root, sketch.pads, edited)
        changes.extend(type_changes)
        if type_addition:
            additions.append(type_addition)
    if geometry_changed and not generator_found:
        additions.append('  (generator "kicad_footprint_sketcher")')
    if geometry_changed and not inserted:
        additions.append(rendered)
    for key, value in (("Reference", sketch.reference), ("Value", sketch.value)):
        if key not in found:
            additions.append(f'  (property {_quoted(key)} {_quoted(value)} (at 0 0 0) (layer "F.Fab") (hide yes) (effects (font (size 1 1) (thickness 0.15))))')
    if additions:
        changes.append((root.end-1, root.end-1, "\n" + "\n".join(additions) + "\n"))
    for start, end, replacement in sorted(changes, reverse=True):
        original = original[:start] + replacement + original[end:]
    return _atomic_bytes(Path(directory) / f"{name}.kicad_mod", original.encode("utf-8"), overwrite)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSONキーが重複しています: {key}")
        result[key] = value
    return result


def _json(text):
    def invalid(value):
        raise ValueError(f"非有限JSON値です: {value}")
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=invalid)


def _decode_entity(data):
    from .compound import CustomPad
    from .graphics import Graphic
    if not isinstance(data, dict) or data.get("type") not in ("pad", "custom", "graphic"):
        raise ValueError("プロジェクトの図形種別が不正です。")
    cls = {"pad": Pad, "custom": CustomPad, "graphic": Graphic}[data["type"]]
    valid = {field.name for field in fields(cls) if field.init}
    kwargs = {key: value for key, value in data.items() if key != "type"}
    if set(kwargs) - valid or not isinstance(kwargs.get("number"), str):
        raise ValueError("プロジェクトの図形属性が不正です。")
    for key in ("x", "y", "width", "height", "angle", "radius", "drill_width", "drill_height", "mask_margin", "paste_margin", "paste_ratio", "stroke", "approximation_tolerance_mm"):
        if key in kwargs and (isinstance(kwargs[key], bool) or not isinstance(kwargs[key], (int, float)) or not math.isfinite(kwargs[key])):
            raise ValueError("図形の数値属性が不正です。")
    for key in ("outline", "corners", "vertices"):
        if key in kwargs:
            kwargs[key] = tuple(tuple(point) for point in kwargs[key])
    for key in ("anchor_size", "layers", "preserved", "curve_edges"):
        if key in kwargs:
            if not isinstance(kwargs[key], list):
                raise ValueError("図形の配列属性が不正です。")
            if key in ("layers", "preserved") and any(not isinstance(item, str) for item in kwargs[key]):
                raise ValueError("図形の文字属性が不正です。")
            kwargs[key] = tuple(kwargs[key])
    for key in ("paste_shapes", "paste_sources"):
        if key in kwargs:
            kwargs[key] = tuple(_decode_entity(pad) for pad in kwargs[key])
    return cls(**kwargs)


def save_project(path: Path, sketch: Sketch, image: Image.Image | None, footprint_name: str) -> Path:
    from dataclasses import asdict
    _validate_footprint_name(footprint_name)
    _validate_constraints(sketch)
    _validate_preserved(sketch.pads)
    calibration = None
    if sketch.calibration is not None:
        calibration = {field.name: getattr(sketch.calibration, field.name) for field in fields(sketch.calibration) if field.init}
        calibration["type"] = "axis" if isinstance(sketch.calibration, AxisCalibration) else "uniform"
    document = {"format": _FORMAT, "version": 1, "name": footprint_name, "image": image is not None,
                "sketch": {"calibration": calibration, "origin": sketch.origin,
                           "pads": [_entity_data(pad) for pad in sketch.pads],
                           "constraints": [asdict(constraint) for constraint in sketch.constraints],
                           "reference": sketch.reference, "value": sketch.value,
                           "preserved_nodes": sketch.preserved_nodes, "metadata": sketch.metadata}}
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
    if len(encoded) > _MAX_TEXT:
        raise ValueError("プロジェクトのJSONがサイズ上限を超えています。")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", encoded)
        if image is not None:
            if image.width * image.height > 50_000_000:
                raise ValueError("内包画像がサイズ上限を超えています。")
            raster = BytesIO(); image.save(raster, format="PNG")
            if raster.tell() > _MAX_MEMBER:
                raise ValueError("内包画像ファイルがサイズ上限を超えています。")
            archive.writestr("image.png", raster.getvalue())
    return _atomic_bytes(Path(path), buffer.getvalue(), True)


def load_project(path: Path) -> tuple[str, Sketch, Image.Image | None]:
    from .constraints import Constraint
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) - {"project.json", "image.png"} or "project.json" not in names:
                raise ValueError("プロジェクトZIPの重複・未対応・侵入パスを拒否しました。")
            if any(info.file_size > _MAX_MEMBER or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1 for info in infos):
                raise ValueError("安全に読み込めないZIP要素があります。")
            if archive.getinfo("project.json").file_size > _MAX_TEXT:
                raise ValueError("JSONがサイズ上限を超えています。")
            raw = archive.read("project.json")
            if len(raw) > _MAX_TEXT:
                raise ValueError("JSONがサイズ上限を超えています。")
            document = _json(raw.decode("utf-8"))
            if not isinstance(document, dict) or set(document) != {"format", "version", "name", "image", "sketch"} or document.get("format") != _FORMAT or type(document.get("version")) is not int or document["version"] != 1:
                raise ValueError("未対応のプロジェクト形式・版です。")
            if type(document.get("image")) is not bool or document["image"] != ("image.png" in names):
                raise ValueError("内包画像の宣言とZIP内容が一致しません。")
            name = document["name"]; _validate_footprint_name(name)
            data = document["sketch"]
            if not isinstance(data, dict) or set(data) != {"calibration", "origin", "pads", "constraints", "reference", "value", "preserved_nodes", "metadata"}:
                raise ValueError("プロジェクトのスケッチ構造が不正です。")
            sketch = Sketch()
            sketch.pads[:] = [_decode_entity(pad) for pad in data["pads"]]
            sketch.reference, sketch.value = data["reference"], data["value"]
            if not all(isinstance(item, str) for item in (sketch.reference, sketch.value)):
                raise ValueError("Reference/Valueが不正です。")
            sketch.preserved_nodes = tuple(data["preserved_nodes"])
            if any(not isinstance(node, str) for node in sketch.preserved_nodes) or not isinstance(data["metadata"], dict):
                raise ValueError("保持データが不正です。")
            sketch.metadata = data["metadata"]
            json.dumps(sketch.metadata, allow_nan=False)
            original = sketch.metadata.get("original_content")
            if original is not None:
                if not isinstance(original, str):
                    raise ValueError("保持原文が不正です。")
                _, baseline, _ = _load_text(original)
                for key in ("source_name", "source_version", "editable_nodes", "original_preserved_nodes", "blocked_geometry", "original_fingerprint", "original_geometry_fingerprint"):
                    if sketch.metadata.get(key) != baseline.metadata[key]:
                        raise ValueError("保持原文と保全用メタデータが一致しません。")
            _validate_preserved(sketch.pads)
            calibration = data["calibration"]
            if calibration is not None:
                kind = calibration["type"]
                values = {key: value for key, value in calibration.items() if key != "type"}
                for key in ("a", "b", "x_a", "x_b", "y_a", "y_b"):
                    if key in values:
                        values[key] = tuple(values[key])
                sketch.calibration = {"uniform": Calibration, "axis": AxisCalibration}[kind](**values)
            if data["origin"] is not None:
                from .model import _checked_point
                sketch.origin = _checked_point(tuple(data["origin"]))
            sketch.constraints = [Constraint(**constraint) for constraint in data["constraints"]]
            if len({constraint.id for constraint in sketch.constraints}) != len(sketch.constraints) or any(
                index >= len(sketch.pads) for constraint in sketch.constraints for index in constraint.targets
            ):
                raise ValueError("拘束のIDまたは参照先が不正です。")
            _validate_constraints(sketch)
            image = None
            if document["image"]:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(BytesIO(archive.read("image.png")), formats=("PNG",)) as source:
                        if source.width * source.height > 50_000_000:
                            raise ValueError("内包画像がサイズ上限を超えています。")
                        image = source.convert("RGBA")
            return name, sketch, image
    except (zipfile.BadZipFile, UnicodeError, KeyError, TypeError, AttributeError, IndexError, OverflowError,
            OSError, RuntimeError, SyntaxError, struct.error, NotImplementedError, RecursionError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("プロジェクトを読み込めません。形式・構造・画像を確認してください。") from error
