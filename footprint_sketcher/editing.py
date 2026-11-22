"""Undo snapshots and immutable operations on groups of SMD pads."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
import math
from typing import Literal

from .model import AxisCalibration, Calibration, Pad, Point, Sketch, _checked_point


@dataclass(frozen=True)
class Snapshot:
    calibration: Calibration | AxisCalibration | None
    origin: Point | None
    pads: tuple[Pad, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pads", tuple(self.pads))
        if self.origin is not None:
            object.__setattr__(self, "origin", _checked_point(self.origin))

    @classmethod
    def capture(cls, sketch: Sketch) -> Snapshot:
        return cls(sketch.calibration, sketch.origin, tuple(sketch.pads))

    def restore(self, sketch: Sketch) -> None:
        """Restore model data, preserving the Sketch's pad-list identity."""
        sketch.calibration = self.calibration
        sketch.origin = self.origin
        sketch.pads[:] = self.pads


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
        result.append(replace(pad, number=number, x=pad.x + dx, y=pad.y + dy))
    return result


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
