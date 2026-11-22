"""Undo snapshots and immutable operations on groups of SMD pads."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
import math
import re
from typing import Literal
import json

from .model import AxisCalibration, Calibration, Pad, Point, Sketch, _checked_point


@dataclass(frozen=True)
class Snapshot:
    calibration: Calibration | AxisCalibration | None
    origin: Point | None
    pads: tuple[Pad, ...]
    constraints: tuple = ()
    reference: str = "REF**"
    value: str = ""
    preserved_nodes: tuple[str, ...] = ()
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        object.__setattr__(self, "pads", tuple(self.pads))
        if self.origin is not None:
            object.__setattr__(self, "origin", _checked_point(self.origin))

    @classmethod
    def capture(cls, sketch: Sketch) -> Snapshot:
        return cls(sketch.calibration, sketch.origin, tuple(sketch.pads), tuple(sketch.constraints),
                   sketch.reference, sketch.value, tuple(sketch.preserved_nodes),
                   json.dumps(sketch.metadata, sort_keys=True, ensure_ascii=False))

    def restore(self, sketch: Sketch) -> None:
        """Restore model data, preserving the Sketch's pad-list identity."""
        sketch.calibration = self.calibration
        sketch.origin = self.origin
        sketch.pads[:] = self.pads
        sketch.constraints[:] = self.constraints
        sketch.reference, sketch.value = self.reference, self.value
        sketch.preserved_nodes = self.preserved_nodes
        sketch.metadata = json.loads(self.metadata_json)


class UndoHistory:
    """Store pre-operation snapshots; cancelled/failed operations add nothing."""

    def __init__(self) -> None:
        self._snapshots: list[Snapshot] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._snapshots)

    def checkpoint(self, snapshot: Snapshot) -> bool:
        """Record one operation's prior state, omitting adjacent duplicates."""
        if self._snapshots and self._snapshots[-1] == snapshot:
            return False
        self._snapshots.append(snapshot)
        return True

    def undo(self, current: Snapshot) -> Snapshot | None:
        """Return the preceding different state, without creating redo history."""
        while self._snapshots:
            previous = self._snapshots.pop()
            if previous != current:
                return previous
        return None

    def clear(self) -> None:
        self._snapshots.clear()


def _selection(pads: Sequence[Pad], indices: Iterable[int], minimum: int) -> list[int]:
    selected = list(indices)
    if len(selected) < minimum:
        raise ValueError(f"この操作にはパッドを{minimum}個以上選択してください。")
    if any(isinstance(index, bool) or not isinstance(index, int)
           or index < 0 or index >= len(pads) for index in selected):
        raise ValueError("選択したパッドが見つかりません。選択し直してください。")
    if len(set(selected)) != len(selected):
        raise ValueError("同じパッドを重複して指定できません。")
    return selected


def _checked_axis(axis: str) -> Literal["x", "y"]:
    if axis not in ("x", "y"):
        raise ValueError("対象の軸はxまたはyで指定してください。")
    return axis


def _copied_preserved(nodes: Iterable[str]) -> tuple[str, ...]:
    return tuple(node for node in nodes if not re.match(r"\(\s*(?:uuid|tstamp)(?=\s|\))", node.lstrip()))


def _copy_identity_changes(pad: Pad) -> dict:
    from .compound import CustomPad

    changes = {"preserved": _copied_preserved(pad.preserved)}
    if isinstance(pad, CustomPad):
        changes["paste_shapes"] = tuple(replace(aperture, preserved=_copied_preserved(aperture.preserved))
                                         for aperture in pad.paste_shapes)
    return changes


def duplicate_pads(
    clipboard: Iterable[Pad],
    existing_pads: Iterable[Pad],
    target_center: Point,
    *,
    keep_numbers: bool = False,
) -> list[Pad]:
    """Copy a group around its mean center, retaining its relative positions.

    New numbers default to the first unused positive integer strings, scanning
    upward from 1. With keep_numbers=True the original identifiers are retained.
    Only the newly created pads are returned; neither input is changed.
    """
    copied = tuple(clipboard)
    if not copied:
        raise ValueError("貼り付けるパッドがありません。先にコピーしてください。")
    target_x, target_y = _checked_point(target_center)
    center_x = math.fsum(pad.x for pad in copied) / len(copied)
    center_y = math.fsum(pad.y for pad in copied) / len(copied)
    dx, dy = target_x - center_x, target_y - center_y
    used = {pad.number for pad in existing_pads}
    next_number = 1
    result = []
    for pad in copied:
        number = pad.number
        if not keep_numbers:
            while str(next_number) in used:
                next_number += 1
            number = str(next_number)
            used.add(number)
            next_number += 1
        result.append(replace(pad, number=number, x=pad.x + dx, y=pad.y + dy,
                              **_copy_identity_changes(pad)))
    return result


def _transformed_copies(
    sources: Sequence[Pad],
    existing_pads: Iterable[Pad],
    transform: Callable[[Pad], tuple[float, float, float]],
    *,
    keep_numbers: bool = False,
    reflected: bool = False,
) -> list[Pad]:
    from .compound import CustomPad
    from .graphics import Graphic

    existing = tuple(existing_pads)
    if any(not isinstance(pad, Pad) for group in (sources, existing) for pad in group):
        raise ValueError("コピー元または配置済みのパッドが不正です。")
    used = {pad.number for pad in existing}
    next_number = 1
    result = []
    for pad in sources:
        number = pad.number
        if not keep_numbers:
            while str(next_number) in used:
                next_number += 1
            number = str(next_number)
            used.add(number)
            next_number += 1
        try:
            x, y, angle = transform(pad)
        except OverflowError as error:
            raise ValueError("コピー後の座標が計算できる範囲を超えています。") from error
        changes = {**_copy_identity_changes(pad), "number": number, "x": x, "y": y, "angle": angle}
        if reflected and isinstance(pad, CustomPad):
            changes.update(
                outline=tuple((x, -y) for x, y in pad.outline),
                corners=tuple((x, -y) for x, y in pad.corners),
                paste_shapes=tuple(replace(aperture, y=-aperture.y,
                                           angle=(-(aperture.angle % 360)) % 360,
                                           preserved=_copied_preserved(aperture.preserved))
                                   for aperture in pad.paste_shapes),
                paste_sources=tuple(replace(aperture, y=-aperture.y,
                                            angle=(-(aperture.angle % 360)) % 360)
                                    for aperture in pad.paste_sources),
            )
        elif reflected and isinstance(pad, Graphic):
            changes["vertices"] = tuple((x, -y) for x, y in pad.vertices)
        result.append(replace(pad, **changes))
    return result


def mirrored_copies(
    pads: Iterable[Pad], existing_pads: Iterable[Pad], axis: Literal["x", "y"]
) -> list[Pad]:
    """Copy across an origin axis, retaining layer and reflected Cu/Paste data."""
    axis = _checked_axis(axis)
    sources = tuple(pads)
    if not sources:
        raise ValueError("反転コピーするパッドを選択してください。")

    def transform(pad: Pad) -> tuple[float, float, float]:
        angle = pad.angle % 360
        return (pad.x if axis == "x" else -pad.x,
                -pad.y if axis == "x" else pad.y,
                (-angle if axis == "x" else 180 - angle) % 360)

    return _transformed_copies(sources, existing_pads, transform, reflected=True)


def copy_pads_between_points(
    pads: Iterable[Pad], existing_pads: Iterable[Pad], source_point: Point,
    target_point: Point, *, keep_numbers: bool = False,
) -> list[Pad]:
    """Translate copies so the chosen source point A coincides with target B.

    Source/target points are footprint-local mm. New numbers use the first
    unused positive integers; keep_numbers=True explicitly keeps source IDs.
    """
    sources = tuple(pads)
    if not sources:
        raise ValueError("基準点コピーするパッドを選択してください。")
    source_x, source_y = _checked_point(source_point)
    target_x, target_y = _checked_point(target_point)
    dx, dy = _checked_point((target_x - source_x, target_y - source_y))
    return _transformed_copies(
        sources, existing_pads, lambda pad: (pad.x + dx, pad.y + dy, pad.angle),
        keep_numbers=keep_numbers,
    )


def point_symmetric_copies(
    pads: Iterable[Pad], existing_pads: Iterable[Pad], center: Point,
    *, keep_numbers: bool = False,
) -> list[Pad]:
    """Copy by rotating the group 180 degrees around the chosen point in mm."""
    sources = tuple(pads)
    if not sources:
        raise ValueError("点対称コピーするパッドを選択してください。")
    center_x, center_y = _checked_point(center)
    return _transformed_copies(
        sources, existing_pads,
        lambda pad: (center_x + (center_x - pad.x), center_y + (center_y - pad.y),
                     (pad.angle % 360 + 180) % 360),
        keep_numbers=keep_numbers,
    )


def line_symmetric_copies(
    pads: Iterable[Pad], existing_pads: Iterable[Pad], axis_start: Point,
    axis_end: Point, *, keep_numbers: bool = False,
) -> list[Pad]:
    """Reflect copies across the infinite line through two distinct mm points.

    Compound outlines/Paste apertures are reflected without swapping layers.
    Local vertex order and curved-edge indices remain paired after reflection.
    """
    sources = tuple(pads)
    if not sources:
        raise ValueError("線対称コピーするパッドを選択してください。")
    start_x, start_y = _checked_point(axis_start)
    end_x, end_y = _checked_point(axis_end)
    dx, dy = _checked_point((end_x - start_x, end_y - start_y))
    component_scale = max(abs(dx), abs(dy))
    if component_scale <= 0:
        raise ValueError("対称軸には異なる2点を有限の距離で指定してください。")
    # Normalize after scaling, so even subnormal nonzero vectors define a
    # unit normal instead of acquiring a rounded, non-unit hypot length.
    scaled_x, scaled_y = dx / component_scale, dy / component_scale
    length = math.hypot(scaled_x, scaled_y)
    normal_x, normal_y = -scaled_y / length, scaled_x / length
    try:
        offset = math.fsum((normal_x * start_x, normal_y * start_y))
    except OverflowError as error:
        raise ValueError("対称軸の位置が計算できる範囲を超えています。") from error
    axis_angle = math.degrees(math.atan2(-scaled_y, scaled_x))

    def transform(pad: Pad) -> tuple[float, float, float]:
        distance = math.fsum((normal_x * pad.x, normal_y * pad.y, -offset))
        return (pad.x - 2 * distance * normal_x, pad.y - 2 * distance * normal_y,
                (2 * axis_angle - pad.angle % 360) % 360)

    return _transformed_copies(sources, existing_pads, transform,
                               keep_numbers=keep_numbers, reflected=True)


def delete_selected_pads(pads: Sequence[Pad], indices: Iterable[int]) -> list[Pad]:
    """Remove exactly the selected indices, preserving every other Pad object."""
    selected = set(_selection(pads, indices, 1))
    return [pad for index, pad in enumerate(pads) if index not in selected]


def align_centers(
    pads: Sequence[Pad],
    indices: Iterable[int],
    axis: Literal["x", "y"],
    reference: Literal["mean", "last"] = "mean",
) -> list[Pad]:
    """Align one center coordinate; 'last' uses the last supplied index."""
    axis = _checked_axis(axis)
    if reference not in ("mean", "last"):
        raise ValueError("整列基準はmeanまたはlastで指定してください。")
    selected = _selection(pads, indices, 2)
    if reference == "last":
        position = getattr(pads[selected[-1]], axis)
    else:
        position = math.fsum(getattr(pads[index], axis) for index in selected) / len(selected)
    result = list(pads)
    for index in selected:
        result[index] = replace(pads[index], **{axis: position})
    return result


def distribute_centers(
    pads: Sequence[Pad], indices: Iterable[int], axis: Literal["x", "y"]
) -> list[Pad]:
    """Space selected centers equally along one axis, fixing both extremes."""
    axis = _checked_axis(axis)
    selected = _selection(pads, indices, 3)
    ordered = sorted(selected, key=lambda index: getattr(pads[index], axis))
    start = getattr(pads[ordered[0]], axis)
    end = getattr(pads[ordered[-1]], axis)
    result = list(pads)
    for position, index in enumerate(ordered[1:-1], start=1):
        coordinate = start + (end - start) * position / (len(ordered) - 1)
        result[index] = replace(pads[index], **{axis: coordinate})
    return result
