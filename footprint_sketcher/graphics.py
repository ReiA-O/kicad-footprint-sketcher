"""Editable KiCad graphics in the same local coordinate frame as pads."""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from .model import Pad, Point, FRONT_LAYERS, BACK_LAYERS, _quoted, _decimal


@dataclass(frozen=True)
class Graphic(Pad):
    graphic_kind: str = "rect"
    layer: str = "F.SilkS"
    stroke: float = 0.15
    filled: bool = False
    # Points are relative to the object's unrotated W/H frame. This keeps
    # resizing, copy and the constraint engine independent of screen pixels.
    vertices: tuple[Point, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "layers", (self.layer,))
        super().__post_init__()
        if self.layer not in FRONT_LAYERS + BACK_LAYERS:
            raise ValueError("対応するF/Bレイヤーを指定してください。")
        if self.graphic_kind not in ("rect", "roundrect", "line", "circle", "arc", "polygon"):
            raise ValueError("対応していない図形です。")
        if not math.isfinite(self.stroke) or self.stroke < 0 or (not self.filled and self.stroke == 0):
            raise ValueError("線幅には正のmm値を指定してください。")
        if self.graphic_kind == "circle" and self.width != self.height:
            raise ValueError("円の幅と高さは同じ値にしてください。")
        points = tuple(tuple(p) for p in self.vertices)
        if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
            raise ValueError("図形の頂点は有限のX/Yで指定してください。")
        required = {"line": 2, "arc": 3}
        if self.graphic_kind in required and len(points) != required[self.graphic_kind]:
            raise ValueError("線は2点、円弧は始点・中間点・終点の3点が必要です。")
        if self.graphic_kind == "line" and points[0] == points[1]:
            raise ValueError("線の始点と終点は異なる位置を指定してください。")
        if self.graphic_kind == "polygon":
            from shapely import Polygon
            polygon = Polygon(points)
            if len(points) < 3 or not polygon.is_valid or polygon.area <= 0:
                raise ValueError("閉じた多角形には自己交差のない3点以上を指定してください。")
        if self.graphic_kind == "arc":
            arc_points(self)
        object.__setattr__(self, "vertices", points)


def local_vertices(graphic: Graphic) -> list[Point]:
    return [(x * graphic.width, y * graphic.height) for x, y in graphic.vertices]


def world_vertices(graphic: Graphic, points=None) -> list[Point]:
    angle = math.radians(graphic.angle % 360)
    c, s = math.cos(angle), math.sin(angle)
    return [(graphic.x + x * c + y * s, graphic.y + y * c - x * s)
            for x, y in (local_vertices(graphic) if points is None else points)]


def arc_points(graphic: Graphic, count: int = 48) -> list[Point]:
    a, b, c = local_vertices(graphic)
    determinant = 2 * (a[0] * (b[1]-c[1]) + b[0] * (c[1]-a[1]) + c[0] * (a[1]-b[1]))
    if abs(determinant) < 1e-16:
        raise ValueError("円弧の3点を一直線または同一点にできません。")
    aa, bb, cc = (x*x+y*y for x, y in (a, b, c))
    cx = (aa*(b[1]-c[1])+bb*(c[1]-a[1])+cc*(a[1]-b[1]))/determinant
    cy = (aa*(c[0]-b[0])+bb*(a[0]-c[0])+cc*(b[0]-a[0]))/determinant
    start, middle, end = (math.atan2(y-cy, x-cx) for x, y in (a, b, c))
    sweep = (end-start) % (2*math.pi)
    if (middle-start) % (2*math.pi) > sweep:
        sweep -= 2*math.pi
    radius = math.hypot(a[0]-cx, a[1]-cy)
    return [(cx+radius*math.cos(start+sweep*i/count), cy+radius*math.sin(start+sweep*i/count))
            for i in range(count+1)]


def graphic_outline(graphic: Graphic) -> list[Point]:
    if graphic.graphic_kind in ("line", "polygon"):
        return local_vertices(graphic)
    if graphic.graphic_kind == "arc":
        return arc_points(graphic)
    if graphic.graphic_kind == "circle":
        return [(graphic.width/2*math.cos(i*math.tau/64), graphic.height/2*math.sin(i*math.tau/64)) for i in range(64)]
    from .compound import local_outline
    return list(local_outline(Pad("graphic", 0, 0, graphic.width, graphic.height,
                                  radius=graphic.radius if graphic.graphic_kind == "roundrect" else 0)))


def graphic_from_points(number: str, kind: str, points: list[Point], **properties) -> Graphic:
    if kind == "line":
        if len(points) != 2 or points[0] == points[1]:
            raise ValueError("線には異なる2点を指定してください。")
        a, b = points
        length = math.dist(a, b)
        angle = math.degrees(math.atan2(a[1]-b[1], b[0]-a[0]))
        return Graphic(number, (a[0]+b[0])/2, (a[1]+b[1])/2, length, 0.000001,
                       angle=angle, graphic_kind=kind, vertices=((-0.5, 0), (0.5, 0)), **properties)
    xs, ys = zip(*points)
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    width, height = max(right-left, 0.000001), max(bottom-top, 0.000001)
    x, y = (left+right)/2, (top+bottom)/2
    vertices = tuple(((px-x)/width, (py-y)/height) for px, py in points)
    return Graphic(number, x, y, width, height, graphic_kind=kind, vertices=vertices, **properties)


def render_graphic(graphic: Graphic) -> list[str]:
    def complete(line):
        extras = ' '.join(graphic.preserved)
        return [line[:-1] + ((' '+extras) if extras else '') + ')']
    def xy(point):
        return f"{_decimal(point[0])} {_decimal(point[1])}"
    kind = graphic.graphic_kind
    stroke = f"(stroke (width {_decimal(graphic.stroke)}) (type solid))"
    layer = f"(layer {_quoted(graphic.layer)})"
    fill = f"(fill {'solid' if graphic.filled else 'none'})"
    if kind == "line":
        a, b = world_vertices(graphic)
        return complete(f"  (fp_line (start {xy(a)}) (end {xy(b)}) {stroke} {layer})")
    if kind == "arc":
        a, b, c = world_vertices(graphic)
        return complete(f"  (fp_arc (start {xy(a)}) (mid {xy(b)}) (end {xy(c)}) {stroke} {layer})")
    if kind == "circle":
        edge = world_vertices(graphic, [(graphic.width/2, 0)])[0]
        return complete(f"  (fp_circle (center {xy((graphic.x, graphic.y))}) (end {xy(edge)}) {stroke} {fill} {layer})")
    points = world_vertices(graphic, graphic_outline(graphic))
    if kind == "rect" and graphic.angle % 90 == 0:
        xs, ys = zip(*points)
        return complete(f"  (fp_rect (start {xy((min(xs), min(ys)))}) (end {xy((max(xs), max(ys)))}) {stroke} {fill} {layer})")
    vertices = ' '.join(f"(xy {xy(p)})" for p in points)
    return complete(f"  (fp_poly (pts {vertices}) {stroke} {fill} {layer})")
