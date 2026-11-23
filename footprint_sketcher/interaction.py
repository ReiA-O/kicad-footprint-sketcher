"""Geometry for selecting and resizing footprint-local SMD pads."""

from __future__ import annotations

from dataclasses import replace
import math

from .model import Pad, Point, _HALF_NANOMETER_MM
from .compound import CustomPad, hit_custom_pad


_CORNER_SIGNS = ((-1, -1), (1, -1), (1, 1), (-1, 1))


def _rotation(pad: Pad) -> tuple[float, float]:
    # Match the canvas: positive angles are counterclockwise with screen Y down.
    angle = math.radians(pad.angle % 360)
    return math.cos(angle), math.sin(angle)


def pad_corners(pad: Pad) -> list[Point]:
    """Return the rotated bounding rectangle's TL, TR, BR, BL corners."""
    cosine, sine = _rotation(pad)
    if isinstance(pad, CustomPad):
        left, right = min(x for x, _ in pad.outline), max(x for x, _ in pad.outline)
        top, bottom = min(y for _, y in pad.outline), max(y for _, y in pad.outline)
        local = ((left, top), (right, top), (right, bottom), (left, bottom))
        return [(pad.x + x * cosine + y * sine, pad.y + y * cosine - x * sine) for x, y in local]
    return [
        (
            pad.x + sx * pad.width / 2 * cosine + sy * pad.height / 2 * sine,
            pad.y + sy * pad.height / 2 * cosine - sx * pad.width / 2 * sine,
        )
        for sx, sy in _CORNER_SIGNS
    ]


def hit_pad(pad: Pad, point_mm: Point) -> bool:
    """Test the actual rectangle/roundrect outline, including its boundary."""
    if isinstance(pad, CustomPad):
        return hit_custom_pad(pad, point_mm)
    from .graphics import Graphic, graphic_outline, world_vertices
    if isinstance(pad, Graphic):
        from shapely import LineString, Point as GeometryPoint, Polygon
        points = world_vertices(pad, graphic_outline(pad))
        if pad.graphic_kind in ("line", "arc"):
            return LineString(points).distance(GeometryPoint(point_mm)) <= max(pad.stroke/2, 0.05)
        polygon = Polygon(points)
        return polygon.covers(GeometryPoint(point_mm)) if pad.filled else polygon.boundary.distance(GeometryPoint(point_mm)) <= max(pad.stroke/2, 0.05)
    if not all(math.isfinite(value) for value in point_mm):
        return False
    cosine, sine = _rotation(pad)
    dx, dy = point_mm[0] - pad.x, point_mm[1] - pad.y
    local_x = abs(dx * cosine - dy * sine)
    local_y = abs(dx * sine + dy * cosine)
    half_width, half_height = pad.width / 2, pad.height / 2
    # Inclusive boundary tests need a few floating-point ulps after rotation.
    # This is arithmetic roundoff handling, not a footprint acceptance tolerance.
    epsilon = 8 * math.ulp(max(1.0, abs(pad.x), abs(pad.y), *map(abs, point_mm)))
    if local_x > half_width + epsilon or local_y > half_height + epsilon:
        return False
    radius = min(pad.width, pad.height)/2 if pad.pad_shape in ("circle", "oval") else pad.radius
    if radius == 0:
        return True
    corner_x = max(0.0, local_x - (half_width - radius))
    corner_y = max(0.0, local_y - (half_height - radius))
    return math.hypot(corner_x, corner_y) <= radius + epsilon


def resize_pad(pad: Pad, corner_index: int, target_point_mm: Point) -> Pad:
    """Move one corner, fixing its opposite corner and retaining pad rotation.

    Crossing the fixed corner is rejected so a drag cannot silently swap handles
    or invert a pad. The caller can retain its last valid drag preview on error.
    """
    if isinstance(pad, CustomPad):
        raise ValueError("合体形状の寸法変更は、Undoで合体前へ戻して元の図形を編集してください。")
    if corner_index not in range(4):
        raise ValueError("サイズ変更の角は0から3で指定してください。")
    fixed = pad_corners(pad)[(corner_index + 2) % 4]
    cosine, sine = _rotation(pad)
    dx, dy = target_point_mm[0] - fixed[0], target_point_mm[1] - fixed[1]
    local_x = dx * cosine - dy * sine
    local_y = dx * sine + dy * cosine
    sx, sy = _CORNER_SIGNS[corner_index]
    width, height = sx * local_x, sy * local_y
    if width <= 0 or height <= 0:
        raise ValueError("サイズ変更では、固定された対角の角を越えない位置を選んでください。")
    radius = min(pad.radius, width / 2, height / 2)
    if 0 < radius < _HALF_NANOMETER_MM:
        raise ValueError("この寸法では角丸のRをKiCadで表せません。もう少し大きくしてください。")
    return replace(
        pad,
        x=fixed[0] + dx / 2,
        y=fixed[1] + dy / 2,
        width=width,
        height=height,
        radius=radius,
    )
