"""Single-terminal unions and selected straight-corner fillets.

Curves are exported as polygons with a maximum sampled sagitta of 0.0001 mm.
The caps below are implementation limits, not adopted T02 acceptance criteria.
No topology repair, radius reduction, hole filling, or island removal is done.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import lru_cache
import math
import re

from shapely import LineString, Point as GeometryPoint, Polygon, STRtree, box, union_all
from shapely.errors import GEOSException

from .model import Pad, Point, _HALF_NANOMETER_MM, _MAX_PCB_MM, _checked_point


CURVE_TOLERANCE_MM = 0.0001
MAX_ARC_SEGMENTS = 4096
MAX_OUTLINE_POINTS = 32768
MAX_PASTE_WINDOWS = 32768
MIN_ANCHOR_SIDE_MM = 0.000001
MIN_REMAINING_EDGE_MM = 0.000001
_MATCH_EPSILON_MM = 0.000000001
_ANGLE_EPSILON = 1e-12


def _without_identity(nodes: Iterable[str]) -> tuple[str, ...]:
    return tuple(node for node in nodes if not re.match(r"\(\s*(?:uuid|tstamp)(?=\s|\))", node.lstrip()))


def _rotation(angle: float) -> tuple[float, float]:
    angle = math.radians(angle % 360)
    return math.cos(angle), math.sin(angle)


def _rotate(point: Point, angle: float) -> Point:
    cosine, sine = _rotation(angle)
    return point[0] * cosine + point[1] * sine, point[1] * cosine - point[0] * sine


def local_to_world(pad: Pad, point: Point) -> Point:
    x, y = _rotate(_checked_point(point), pad.angle)
    return _checked_point((pad.x + x, pad.y + y))


def world_to_local(pad: Pad, point: Point) -> Point:
    x, y = _checked_point(point)
    return _checked_point(_rotate((x - pad.x, y - pad.y), -(pad.angle % 360)))


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _clean_outline(points: Iterable[Point]) -> tuple[Point, ...]:
    result: list[Point] = []
    for point in points:
        checked = _checked_point(point)
        if not result or checked != result[-1]:
            result.append(checked)
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    # Remove only straight-through vertices. Arc tessellation turns are retained.
    changed = True
    while changed and len(result) >= 3:
        changed = False
        for index, point in enumerate(result):
            previous, following = result[index - 1], result[(index + 1) % len(result)]
            a = (point[0] - previous[0], point[1] - previous[1])
            b = (following[0] - point[0], following[1] - point[1])
            product = math.hypot(*a) * math.hypot(*b)
            if product and abs(_cross(a, b)) <= _ANGLE_EPSILON * product and a[0] * b[0] + a[1] * b[1] > 0:
                del result[index]
                changed = True
                break
    if len(result) < 3 or len(result) > MAX_OUTLINE_POINTS:
        raise ValueError("合体輪郭の頂点数が対応範囲外です。")
    return tuple(result)


def _polygon(points: Iterable[Point]) -> tuple[tuple[Point, ...], Polygon]:
    outline = _clean_outline(points)
    polygon = Polygon(outline)
    if polygon.is_empty or not polygon.is_valid or not math.isfinite(polygon.area) or polygon.area <= 0:
        raise ValueError("自己交差や面積のない輪郭はパッドにできません。")
    if polygon.interiors:
        raise ValueError("穴を含む合体形状にはまだ対応していません。元のパッドを保持します。")
    left, top, right, bottom = polygon.bounds
    if right - left > _MAX_PCB_MM or bottom - top > _MAX_PCB_MM:
        raise ValueError("合体輪郭の寸法がKiCadの表現範囲を超えています。")
    return outline, polygon


def _arc_segments(radius: float, sweep: float) -> int:
    # asin is stable even when the sagitta/radius ratio is very small.
    maximum_angle = 4 * math.asin(math.sqrt(min(2.0, CURVE_TOLERANCE_MM / radius) / 2))
    count = max(1, math.ceil(abs(sweep) / maximum_angle))
    if count > MAX_ARC_SEGMENTS:
        raise ValueError("指定Rの円弧は近似分割数の上限を超えます。Rを確認してください。")
    return count


def _edge_lines(outline: tuple[Point, ...], indices: Iterable[int]) -> list[LineString]:
    return [LineString((outline[index], outline[(index + 1) % len(outline)])) for index in indices]


def _curve_indices(outline: tuple[Point, ...], curves: list[LineString]) -> tuple[int, ...]:
    if not curves:
        return ()
    tree = STRtree(curves)
    result = []
    for index, a in enumerate(outline):
        b = outline[(index + 1) % len(outline)]
        middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        candidates = tree.query(box(middle[0] - _MATCH_EPSILON_MM, middle[1] - _MATCH_EPSILON_MM,
                                    middle[0] + _MATCH_EPSILON_MM, middle[1] + _MATCH_EPSILON_MM))
        if any(curves[int(candidate)].distance(GeometryPoint(a)) <= _MATCH_EPSILON_MM
               and curves[int(candidate)].distance(GeometryPoint(b)) <= _MATCH_EPSILON_MM
               for candidate in candidates):
            result.append(index)
    return tuple(result)


@lru_cache(maxsize=256)
def _basic_geometry(width: float, height: float, radius: float) -> tuple[tuple[Point, ...], tuple[int, ...]]:
    left, right, top, bottom = -width / 2, width / 2, -height / 2, height / 2
    if radius == 0:
        return ((left, top), (right, top), (right, bottom), (left, bottom)), ()
    points: list[Point] = []
    curves: list[LineString] = []
    count = _arc_segments(radius, math.pi / 2)
    for cx, cy, start in ((right - radius, top + radius, -90),
                          (right - radius, bottom - radius, 0),
                          (left + radius, bottom - radius, 90),
                          (left + radius, top + radius, 180)):
        arc = []
        for step in range(count + 1):
            angle = math.radians(start + step * 90 / count)
            cosine, sine = math.cos(angle), math.sin(angle)
            cosine = 0.0 if abs(cosine) < 1e-15 else cosine
            sine = 0.0 if abs(sine) < 1e-15 else sine
            arc.append((cx + radius * cosine, cy + radius * sine))
        curves.extend(LineString((a, b)) for a, b in zip(arc, arc[1:]) if a != b)
        points.extend(arc)
    outline = _clean_outline(points)
    return outline, _curve_indices(outline, curves)


def _effective_radius(pad: Pad) -> float:
    if pad.pad_shape in ("circle", "oval"):
        return min(pad.width, pad.height) / 2
    return pad.radius if pad.pad_shape in ("auto", "roundrect") else 0.0


def local_outline(pad: Pad) -> tuple[Point, ...]:
    return pad.outline if isinstance(pad, CustomPad) else _basic_geometry(
        pad.width, pad.height, _effective_radius(pad))[0]


def world_outline(pad: Pad) -> tuple[Point, ...]:
    return tuple(local_to_world(pad, point) for point in local_outline(pad))


def selectable_corners(pad: Pad) -> tuple[Point, ...]:
    if isinstance(pad, CustomPad):
        return pad.corners
    return local_outline(pad) if _effective_radius(pad) == 0 else ()


def _resolved_paste_shape(pad: Pad, *, force_margin: bool = False, keep_identity: bool = False) -> Pad:
    """Resolve KiCad's per-side paste offsets to a standalone aperture once.

    KiCad PAD::GetSolderPasteMargin uses margin + pad_size * ratio per side.
    Equal offsets change roundrect R; unequal offsets retain its radius ratio.
    See https://docs.kicad.org/doxygen/fab__pad__geometry_8cpp_source.html .
    """
    has_copper = force_margin or any(layer in ("F.Cu", "B.Cu", "*.Cu") for layer in pad.layers)
    margin_x = pad.paste_margin + pad.width * pad.paste_ratio if has_copper else 0.0
    margin_y = pad.paste_margin + pad.height * pad.paste_ratio if has_copper else 0.0
    width, height = pad.width + 2 * margin_x, pad.height + 2 * margin_y
    radius = _effective_radius(pad)
    shape = pad.pad_shape
    if shape in ("circle", "oval"):
        radius = 0.0
    else:
        if radius:
            radius = (max(0.0, radius + margin_x) if margin_x == margin_y
                      else radius / min(pad.width, pad.height) * min(width, height))
        elif margin_x > 0:
            radius = margin_x
        radius = min(radius, min(width, height) / 2)
        shape = "roundrect" if radius else "rect"
    return replace(pad, width=width, height=height, radius=radius, pad_shape=shape,
                   pad_type="smd", drill_width=0.0, drill_height=0.0,
                   layers=tuple(layer for layer in pad.layers if layer in ("F.Paste", "B.Paste")),
                   mask_margin=0.0, paste_margin=0.0, paste_ratio=0.0,
                   preserved=pad.preserved if keep_identity else _without_identity(pad.preserved))


def _world_apertures(pad: CustomPad, apertures: Iterable[Pad]) -> list[Pad]:
    result = []
    for aperture in apertures:
        x, y = local_to_world(pad, (aperture.x, aperture.y))
        angle = (pad.angle % 360 + aperture.angle % 360) % 360
        result.append(replace(aperture, x=x, y=y, angle=angle))
    return result


def _local_paste_shapes(pad: CustomPad) -> tuple[Pad, ...]:
    shapes = pad.paste_shapes if pad.paste_applied else tuple(
        _resolved_paste_shape(aperture) for aperture in pad.paste_shapes)
    if pad.paste_margin or pad.paste_ratio:
        shapes = tuple(_resolved_paste_shape(
            replace(aperture, paste_margin=pad.paste_margin, paste_ratio=pad.paste_ratio),
            force_margin=True, keep_identity=True) for aperture in shapes)
    return tuple(shapes)


def world_paste_shapes(pad: Pad) -> list[Pad]:
    if not isinstance(pad, CustomPad):
        return [_resolved_paste_shape(pad)] if any(
            layer in ("F.Paste", "B.Paste") for layer in pad.layers) else []
    return _world_apertures(pad, _local_paste_shapes(pad))


def world_paste_sources(pad: Pad) -> list[Pad]:
    """Return original pad metadata snapshots in the pad's current world pose."""
    if not isinstance(pad, CustomPad):
        return [pad]
    return _world_apertures(pad, pad.paste_sources or pad.paste_shapes)


def _anchor(polygon: Polygon, preferred: Point | None = None) -> tuple[Point, Point]:
    point = GeometryPoint(preferred) if preferred is not None else polygon.representative_point()
    if not polygon.contains(point):
        point = polygon.representative_point()
    distance = polygon.boundary.distance(point)
    side = math.floor(min(0.1, distance / 4) / MIN_ANCHOR_SIDE_MM) * MIN_ANCHOR_SIDE_MM
    if not math.isfinite(side) or side < MIN_ANCHOR_SIDE_MM:
        raise ValueError("形状が細すぎてKiCad用の接続点を内部に配置できません。")
    anchor = (point.x, point.y)
    rectangle = box(anchor[0] - side / 2, anchor[1] - side / 2,
                    anchor[0] + side / 2, anchor[1] + side / 2)
    if not polygon.covers(rectangle):
        raise ValueError("接続点の銅箔を合体輪郭内に収められません。")
    return anchor, (side, side)


def anchor_for_outline(outline: Iterable[Point]) -> tuple[Point, Point]:
    """Return an interior anchor and a fully contained positive rectangular size."""
    _, polygon = _polygon(outline)
    return _anchor(polygon)


@dataclass(frozen=True)
class CustomPad(Pad):
    outline: tuple[Point, ...] = ()
    paste_shapes: tuple[Pad, ...] = ()
    corners: tuple[Point, ...] = ()
    anchor_size: Point = (0.0, 0.0)
    curve_edges: tuple[int, ...] = ()
    approximation_tolerance_mm: float = CURVE_TOLERANCE_MM
    paste_sources: tuple[Pad, ...] = ()
    paste_applied: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.pad_type != "smd":
            raise ValueError("合体パッドはSMD専用です。穴のある元パッドを保持してください。")
        if self.radius != 0:
            raise ValueError("合体パッドのRは角ごとのフィレットで指定してください。")
        outline, polygon = _polygon(self.outline)
        if outline != tuple(self.outline):
            raise ValueError("合体輪郭に重複または不要な直線頂点があります。")
        left, top, right, bottom = polygon.bounds
        if not (math.isclose(self.width, right - left, rel_tol=1e-12, abs_tol=1e-12)
                and math.isclose(self.height, bottom - top, rel_tol=1e-12, abs_tol=1e-12)):
            raise ValueError("合体パッドの幅・高さは輪郭の外接寸法と一致させてください。")
        if self.mask_margin < 0 and any(layer.endswith(".Mask") for layer in self.layers):
            try:
                mask = polygon.buffer(self.mask_margin)
            except GEOSException as error:
                raise ValueError("Mask開口の拡縮を計算できません。元の形状を保持します。") from error
            if mask.is_empty:
                raise ValueError("Mask開口が消える拡縮量は指定できません。")
        anchor_width, anchor_height = _checked_point(self.anchor_size)
        if min(anchor_width, anchor_height) < MIN_ANCHOR_SIDE_MM or not polygon.covers(
            box(-anchor_width / 2, -anchor_height / 2, anchor_width / 2, anchor_height / 2)
        ):
            raise ValueError("接続点の銅箔が合体輪郭内に収まっていません。")
        corners = tuple(_checked_point(point) for point in self.corners)
        if any(point not in outline for point in corners):
            raise ValueError("選択可能な角が合体輪郭上にありません。")
        if any(isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(outline)
               for index in self.curve_edges):
            raise ValueError("円弧近似の辺情報が不正です。")
        apertures = tuple(self.paste_shapes)
        sources = tuple(self.paste_sources)
        from .graphics import Graphic
        if any(not isinstance(aperture, Pad) or isinstance(aperture, (CustomPad, Graphic))
               for aperture in (*apertures, *sources)):
            raise ValueError("元のPaste開口は矩形または角丸の形状として保持してください。")
        if not isinstance(self.paste_applied, bool):
            raise ValueError("Paste開口の拡縮適用状態が不正です。")
        if self.paste_applied and any(aperture.paste_margin or aperture.paste_ratio for aperture in apertures):
            raise ValueError("適用済みのPaste開口へ拡縮量を重複指定できません。元metadataを別に保持してください。")
        # The parent's requested opening adjustment is evaluated against each
        # stored window, not just the much larger copper bounding rectangle.
        _local_paste_shapes(self)
        if not math.isfinite(self.approximation_tolerance_mm) or self.approximation_tolerance_mm <= 0:
            raise ValueError("円弧近似条件が不正です。")
        for point in outline:
            if max(map(abs, local_to_world(self, point))) > _MAX_PCB_MM:
                raise ValueError("合体輪郭の位置がKiCadの表現範囲を超えています。")
        object.__setattr__(self, "outline", outline)
        object.__setattr__(self, "corners", corners)
        object.__setattr__(self, "paste_shapes", apertures)
        object.__setattr__(self, "paste_sources", sources)
        object.__setattr__(self, "anchor_size", (anchor_width, anchor_height))
        object.__setattr__(self, "curve_edges", tuple(self.curve_edges))


def _sharp_corners(outline: tuple[Point, ...], curves: tuple[int, ...]) -> tuple[Point, ...]:
    curved = set(curves)
    result = []
    for index, vertex in enumerate(outline):
        if index in curved or (index - 1) % len(outline) in curved:
            continue
        previous, following = outline[index - 1], outline[(index + 1) % len(outline)]
        incoming = (vertex[0] - previous[0], vertex[1] - previous[1])
        outgoing = (following[0] - vertex[0], following[1] - vertex[1])
        turn = math.atan2(_cross(incoming, outgoing), incoming[0] * outgoing[0] + incoming[1] * outgoing[1])
        if _ANGLE_EPSILON < abs(turn) < math.pi - _ANGLE_EPSILON:
            result.append(vertex)
    return tuple(result)


def _make_custom(
    number: str, points: Iterable[Point], apertures: Iterable[Pad], curves: list[LineString],
    *, origin: Point = (0.0, 0.0), angle: float = 0.0, preferred: Point | None = None,
    template: Pad | None = None, source_apertures: Iterable[Pad] = (),
    retain_parent_paste: bool = False,
) -> CustomPad:
    outline, polygon = _polygon(points)
    curve_indices = _curve_indices(outline, curves)
    corners = _sharp_corners(outline, curve_indices)
    anchor, anchor_size = _anchor(polygon, preferred)
    dx, dy = _rotate(anchor, angle)
    local = tuple((point[0] - anchor[0], point[1] - anchor[1]) for point in outline)
    local_corners = tuple((point[0] - anchor[0], point[1] - anchor[1]) for point in corners)
    paste = tuple(replace(aperture, x=aperture.x - anchor[0], y=aperture.y - anchor[1]) for aperture in apertures)
    sources = tuple(replace(aperture, x=aperture.x - anchor[0], y=aperture.y - anchor[1])
                    for aperture in source_apertures)
    left, top, right, bottom = polygon.bounds
    attributes = {} if template is None else {
        "layers": template.layers, "mask_margin": template.mask_margin,
        "preserved": template.preserved, "opening_explicit": template.opening_explicit,
    }
    if retain_parent_paste and template is not None:
        attributes.update(paste_margin=template.paste_margin, paste_ratio=template.paste_ratio)
    return CustomPad(number, origin[0] + dx, origin[1] + dy, right - left, bottom - top, angle, 0,
                     outline=local, paste_shapes=paste, corners=local_corners,
                     anchor_size=anchor_size, curve_edges=curve_indices,
                     paste_sources=sources, paste_applied=True, **attributes)


def _compatible_sources(sources: tuple[Pad, ...], *, keep_openings: bool = True) -> str:
    from .graphics import Graphic

    if not sources:
        raise ValueError("合体するパッドを選択してください。")
    for pad in sources:
        if not isinstance(pad, Pad) or isinstance(pad, Graphic) or pad.pad_type != "smd":
            raise ValueError("同じ銅箔面のSMDだけを合体・加工できます。穴や図形は元のまま保持します。")
        copper = tuple(layer for layer in pad.layers if layer in ("F.Cu", "B.Cu", "*.Cu"))
        if len(copper) != 1 or copper[0] == "*.Cu":
            raise ValueError("F.CuまたはB.Cuの一面に属するSMDを指定してください。")
    first = sources[0]
    face = "F.Cu" if "F.Cu" in first.layers else "B.Cu"
    if any(face not in pad.layers for pad in sources[1:]):
        raise ValueError("F面とB面のパッドを一つの銅箔形状へ合体できません。")
    if keep_openings and any(set(pad.layers) != set(first.layers) for pad in sources[1:]):
        raise ValueError("所属レイヤーが異なるパッドは合体できません。元の開口を保持します。")
    if keep_openings and any(pad.mask_margin != first.mask_margin for pad in sources[1:]):
        raise ValueError("Mask拡縮量が異なるパッドは合体できません。元の開口を保持します。")
    return face


def merge_pads(pads: Iterable[Pad], number: str) -> CustomPad:
    sources = tuple(pads)
    return _merge_sources(sources, number)


def _merge_sources(sources: tuple[Pad, ...], number: str, *, keep_openings: bool = True,
                   bridge_width: float = 0.0) -> CustomPad:
    face = _compatible_sources(sources, keep_openings=keep_openings)
    polygons = []
    apertures: list[Pad] = []
    source_apertures: list[Pad] = []
    curves: list[LineString] = []
    try:
        for pad in sources:
            outline = world_outline(pad)
            _, polygon = _polygon(outline)
            polygons.append(polygon)
            curve_indices = pad.curve_edges if isinstance(pad, CustomPad) else _basic_geometry(
                pad.width, pad.height, _effective_radius(pad))[1]
            curves.extend(_edge_lines(outline, curve_indices))
            if keep_openings:
                apertures.extend(world_paste_shapes(pad))
            source_apertures.extend(world_paste_sources(pad))
        if bridge_width:
            centers = tuple((pad.x, pad.y) for pad in sources)
            if len(centers) < 2:
                raise ValueError("幅を指定した銅箔接続には2個以上の端子を選択してください。")
            if any(a != b for a, b in zip(centers, centers[1:])):
                radius = bridge_width / 2
                connector = LineString(centers).buffer(
                    radius, quad_segs=_arc_segments(radius, math.pi / 2),
                    cap_style="round", join_style="round")
                if connector.is_empty or connector.geom_type != "Polygon" or not connector.is_valid:
                    raise ValueError("指定した幅の接続銅箔を作成できません。元の端子を保持します。")
                polygons.append(connector)
                ring = tuple(connector.exterior.coords)
                # Arc chords lie on a radius around an original path vertex.
                # Straight bridge edges remain ordinary selectable boundaries.
                for a, b in zip(ring, ring[1:]):
                    if any(abs(math.dist(a, center) - radius) <= _MATCH_EPSILON_MM
                           and abs(math.dist(b, center) - radius) <= _MATCH_EPSILON_MM for center in centers):
                        curves.append(LineString((a, b)))
        merged = union_all(polygons)
        if merged.geom_type != "Polygon":
            raise ValueError("離れた形状や点でしか接しない形状は、1個のパッドに合体できません。")
        if merged.interiors:
            raise ValueError("穴を含む合体形状にはまだ対応していません。元のパッドを保持します。")
        template = sources[0]
        if not keep_openings:
            primary = next(pad for pad in sources if pad.number == number)
            template = replace(primary, layers=(face,), mask_margin=0.0,
                               paste_margin=0.0, paste_ratio=0.0,
                               preserved=_without_identity(primary.preserved))
        return _make_custom(number, merged.exterior.coords, apertures, curves,
                            template=template, source_apertures=source_apertures)
    except GEOSException as error:
        raise ValueError("合体形状を計算できませんでした。元のパッドを保持します。") from error


def connect_terminals(pads: Iterable[Pad], primary_number: str, *, bridge_width: float = 0.0) -> CustomPad:
    """Return extra copper joining retained, explicitly same-net terminals.

    The caller retains every original numbered pad and its Mask/Paste. This
    extra Custom has one selected terminal's number and the copper layer only;
    it does not create a Net-tie or infer circuit connectivity. With a positive
    bridge_width, a round-ended path joins selected anchors in supplied order.
    Zero preserves the previous overlap-only behavior.
    """
    sources = tuple(pads)
    _compatible_sources(sources, keep_openings=False)
    try:
        width = float(bridge_width)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("接続銅箔の幅は0または正の有限値をmmで指定してください。") from error
    if (isinstance(bridge_width, bool) or not math.isfinite(width) or width < 0
            or width > _MAX_PCB_MM or 0 < width < _HALF_NANOMETER_MM):
        raise ValueError("接続銅箔の幅は0またはKiCadで表せる正の有限値をmmで指定してください。")
    if not any(pad.number == primary_number for pad in sources):
        raise ValueError("接続の主番号は選択したパッド番号から指定してください。")
    return _merge_sources(sources, primary_number, keep_openings=False, bridge_width=width)


def split_paste_apertures(pad: Pad, columns: int, rows: int, gap: float) -> CustomPad:
    """Split each aperture into an equally sized grid with the exact given gap.

    Each grid follows its original opening's own angle. Its windows must fit
    the original opening; unsupported corner protrusions are rejected without
    silently shrinking sizes, moving centers, or changing the requested gap.
    """
    if not isinstance(pad, CustomPad):
        from .graphics import Graphic

        if not isinstance(pad, Pad) or isinstance(pad, Graphic) or pad.pad_type != "smd":
            raise ValueError("Paste分割には穴なしSMDパッドを選択してください。PTH・NPTH・図形は変更しません。")
        pad = merge_pads((pad,), pad.number)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (columns, rows)):
        raise ValueError("Paste分割数は縦横それぞれ正の整数で指定してください。")
    if not math.isfinite(gap) or gap <= 0:
        raise ValueError("Pasteの窓間隔は正の有限値をmmで指定してください。")
    apertures = _local_paste_shapes(pad)
    if not apertures:
        raise ValueError("分割するPaste開口がありません。")
    windows: list[Pad] = []
    for aperture in apertures:
        try:
            width = (aperture.width - gap * (columns - 1)) / columns
            height = (aperture.height - gap * (rows - 1)) / rows
        except OverflowError as error:
            raise ValueError("分割数または窓間隔が開口寸法を超えています。") from error
        if min(width, height) < _HALF_NANOMETER_MM or not all(map(math.isfinite, (width, height))):
            raise ValueError("窓間隔が開口を使い切ります。分割数または間隔を小さくしてください。")
        if len(windows) + columns * rows > MAX_PASTE_WINDOWS:
            raise ValueError("Paste窓数が内部処理の上限を超えています。")
        radius = min(_effective_radius(aperture), width / 2, height / 2)
        source_polygon = Polygon(local_outline(aperture))
        for row in range(rows):
            for column in range(columns):
                dx = -aperture.width / 2 + width / 2 + column * (width + gap)
                dy = -aperture.height / 2 + height / 2 + row * (height + gap)
                window = replace(aperture, x=dx, y=dy, width=width, height=height,
                                 angle=0.0, radius=radius, pad_shape="roundrect" if radius else "rect",
                                 mask_margin=0.0, paste_margin=0.0, paste_ratio=0.0,
                                 preserved=_without_identity(aperture.preserved))
                # A tiny matching tolerance absorbs arithmetic at a shared
                # boundary, without modifying either exported geometry.
                if not source_polygon.buffer(_MATCH_EPSILON_MM).covers(Polygon(world_outline(window))):
                    raise ValueError("元開口からはみ出す分割なので、分割数または間隔を変えてください。")
                x, y = local_to_world(aperture, (dx, dy))
                windows.append(replace(window, x=x, y=y, angle=aperture.angle))
    return replace(pad, paste_shapes=tuple(windows), paste_applied=True,
                   paste_margin=0.0, paste_ratio=0.0,
                   paste_sources=pad.paste_sources or pad.paste_shapes)


def fillet_pad(pad: Pad, corner_index: int, radius: float) -> CustomPad:
    _compatible_sources((pad,))
    corners = selectable_corners(pad)
    if isinstance(corner_index, bool) or not isinstance(corner_index, int) or not 0 <= corner_index < len(corners):
        raise ValueError("フィレットする直線の角を選択してください。")
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("フィレットのRには正の有限値をmmで指定してください。")
    if radius < _HALF_NANOMETER_MM:
        raise ValueError("フィレットのRはKiCadで表せる正の値を指定してください。")
    outline = local_outline(pad)
    vertex = corners[corner_index]
    index = outline.index(vertex)
    previous, following = outline[index - 1], outline[(index + 1) % len(outline)]
    incoming = (vertex[0] - previous[0], vertex[1] - previous[1])
    outgoing = (following[0] - vertex[0], following[1] - vertex[1])
    length_in, length_out = math.hypot(*incoming), math.hypot(*outgoing)
    din = (incoming[0] / length_in, incoming[1] / length_in)
    dout = (outgoing[0] / length_out, outgoing[1] / length_out)
    sweep = math.atan2(_cross(din, dout), din[0] * dout[0] + din[1] * dout[1])
    if not _ANGLE_EPSILON < abs(sweep) < math.pi - _ANGLE_EPSILON:
        raise ValueError("直線または折り返した角にはフィレットを付けられません。")
    tangent = radius * math.tan(abs(sweep) / 2)
    if not math.isfinite(tangent) or min(length_in - tangent, length_out - tangent) < MIN_REMAINING_EDGE_MM:
        raise ValueError("指定Rが隣の角に届きます。隣接する辺に収まるRを指定してください。")
    a = (vertex[0] - tangent * din[0], vertex[1] - tangent * din[1])
    b = (vertex[0] + tangent * dout[0], vertex[1] + tangent * dout[1])
    sign = 1 if sweep > 0 else -1
    center = (a[0] - sign * radius * din[1], a[1] + sign * radius * din[0])
    start = math.atan2(a[1] - center[1], a[0] - center[0])
    count = _arc_segments(radius, sweep)
    arc = [a]
    for step in range(1, count):
        delta = sweep * step / count
        # Difference from A avoids subtracting two large almost-equal centers.
        arc.append((a[0] - 2 * radius * math.sin(start + delta / 2) * math.sin(delta / 2),
                    a[1] + 2 * radius * math.cos(start + delta / 2) * math.sin(delta / 2)))
    arc.append(b)
    points = outline[:index] + tuple(arc) + outline[index + 1:]
    curve_indices = pad.curve_edges if isinstance(pad, CustomPad) else _basic_geometry(
        pad.width, pad.height, _effective_radius(pad))[1]
    curves = _edge_lines(outline, curve_indices)
    curves.extend(LineString((left, right)) for left, right in zip(arc, arc[1:]))
    if isinstance(pad, CustomPad):
        apertures = pad.paste_shapes if pad.paste_applied else tuple(
            _resolved_paste_shape(aperture) for aperture in pad.paste_shapes)
        sources = pad.paste_sources or pad.paste_shapes
    else:
        original = replace(pad, x=0, y=0, angle=0)
        apertures = tuple(world_paste_shapes(original))
        sources = (original,)
    try:
        result = _make_custom(pad.number, points, apertures, curves,
                              origin=(pad.x, pad.y), angle=pad.angle, preferred=(0.0, 0.0),
                              template=pad, source_apertures=sources,
                              retain_parent_paste=isinstance(pad, CustomPad))
        if len(result.corners) != len(corners) - 1:
            raise ValueError("指定Rでは選択した角だけを表現できません。Rを確認してください。")
        result_world = [local_to_world(result, corner) for corner in result.corners]
        for other in (point for point in corners if point != vertex):
            world = local_to_world(pad, other)
            if not any(math.dist(world, point) <= _MATCH_EPSILON_MM for point in result_world):
                raise ValueError("指定Rが他の角に影響するためフィレットを適用できません。")
        return result
    except GEOSException as error:
        raise ValueError("フィレット形状を計算できませんでした。元のパッドを保持します。") from error


def hit_custom_pad(pad: CustomPad, point: Point) -> bool:
    return Polygon(pad.outline).covers(GeometryPoint(world_to_local(pad, point)))
