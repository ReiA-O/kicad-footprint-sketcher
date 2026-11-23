"""Persistent center/dimension constraints with atomic, local least-squares solving.

The residual tolerance and Jacobian rank threshold are numerical solver settings,
not manufacturing tolerances or the project's T02 acceptance criteria. Driving
values are temporary hard equations; reported freedom counts persistent relations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import math
from typing import Literal

import numpy as np

from .compound import CustomPad
from .graphics import Graphic
from .model import Pad, Point


FIELDS = ("x", "y", "width", "height", "angle", "radius")
_DIMENSIONS = {"x", "y", "width", "height", "angle", "radius"}
_PAIR_KINDS = {"coincidence", "horizontal", "vertical", "symmetric", "symmetry_x", "symmetry_y",
               "equal_width", "equal_height", "equal_size", "distance", "spacing_x", "spacing_y"}
_VALUE_KINDS = _DIMENSIONS | {"distance", "spacing_x", "spacing_y"}
_KINDS = _PAIR_KINDS | _DIMENSIONS | {"fixed"}
_RESIDUAL_TOL = 1e-9
_RANK_RCOND = 1e-12


def _error(owner: str, message: str) -> ValueError:
    return ValueError(f"[{owner}] {message}")


def _finite(value, owner: str) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise _error(owner, "拘束値には有限の数値を指定してください。") from None
    if not math.isfinite(result):
        raise _error(owner, "拘束値には有限の数値を指定してください。")
    return result


@dataclass(frozen=True)
class Constraint:
    id: str
    kind: str
    targets: tuple[int, ...]
    value: float | None = None
    axis: str = "x"
    point: Point = (0, 0)
    values: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        owner = self.id if isinstance(self.id, str) and self.id.strip() else "constraint"
        if owner == "constraint" and (not isinstance(self.id, str) or not self.id.strip()):
            raise _error(owner, "拘束IDを入力してください。")
        if not isinstance(self.kind, str) or self.kind not in _KINDS:
            raise _error(owner, f"未対応の拘束種別です: {self.kind}")
        try:
            targets = tuple(self.targets)
        except TypeError:
            raise _error(owner, "拘束対象の番号を指定してください。") from None
        expected = 2 if self.kind in _PAIR_KINDS else 1
        if len(targets) != expected or any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in targets
        ) or len(set(targets)) != len(targets):
            raise _error(owner, f"この拘束には異なる対象を{expected}個指定してください。")
        if self.axis not in ("x", "y"):
            raise _error(owner, "軸はxまたはyを指定してください。")
        try:
            point = tuple(self.point)
        except TypeError:
            raise _error(owner, "対称基準点をX/Yで指定してください。") from None
        if len(point) != 2:
            raise _error(owner, "対称基準点をX/Yで指定してください。")
        point = tuple(_finite(item, owner) for item in point)
        value = self.value
        if self.kind in _VALUE_KINDS or value is not None:
            value = _finite(value, owner)
            if self.kind == "distance" and value < 0:
                raise _error(owner, "中心間距離には0以上の値を指定してください。")
        try:
            values = tuple(_finite(item, owner) for item in self.values)
        except TypeError:
            raise _error(owner, "固定の保存値を数値の配列で指定してください。") from None
        if self.kind == "fixed" and len(values) != len(FIELDS):
            raise _error(owner, "固定にはx/y/width/height/angle/radiusの保存値6個が必要です。")
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "point", point)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class SolveResult:
    pads: list[Pad]
    state: Literal["underconstrained", "fully_constrained"]
    rank: int
    dof: int
    max_residual: float


@dataclass(frozen=True)
class _Equation:
    owner: str
    terms: tuple[tuple[int, str, float], ...] = ()
    target: float = 0
    angular: bool = False
    distance: tuple[int, int] | None = None


def _is_circle(pad: Pad) -> bool:
    return (isinstance(pad, Graphic) and pad.graphic_kind == "circle") or (
        not isinstance(pad, (Graphic, CustomPad)) and pad.pad_shape == "circle")


def _variable_fields(pad: Pad) -> tuple[str, ...]:
    if isinstance(pad, CustomPad):
        return ("x", "y", "angle")
    if isinstance(pad, Graphic):
        if pad.graphic_kind == "line":
            return ("x", "y", "width", "angle")
        return FIELDS if pad.graphic_kind == "roundrect" else FIELDS[:-1]
    return FIELDS[:-1] if pad.pad_shape in ("rect", "oval", "circle") else FIELDS


def _equations(constraint: Constraint, pads: Sequence[Pad]) -> list[_Equation]:
    c, kind, targets = constraint, constraint.kind, constraint.targets
    i = targets[0]

    def row(field, coefficients, target=0, angular=False):
        return _Equation(c.id, tuple((index, field, coefficient)
                                    for index, coefficient in coefficients), _finite(target, c.id), angular)

    if kind in _DIMENSIONS:
        if kind == "radius" and _is_circle(pads[i]):
            return [row("width", [(i, 0.5)], c.value), row("height", [(i, 0.5)], c.value)]
        return [row(kind, [(i, 1)], c.value, kind == "angle")]
    if kind == "fixed":
        return [row(field, [(i, 1)], value, field == "angle") for field, value in zip(FIELDS, c.values)]
    j = targets[1]
    difference = [(j, 1), (i, -1)]
    total = [(i, 1), (j, 1)]
    if kind == "horizontal":
        return [row("y", difference)]
    if kind == "vertical":
        return [row("x", difference)]
    if kind in ("coincidence", "distance"):
        if kind == "distance" and c.value:
            return [_Equation(c.id, target=c.value, distance=(i, j))]
        return [row("x", difference), row("y", difference)]
    if kind == "symmetric":
        return [row("x", total, 2 * c.point[0]), row("y", total, 2 * c.point[1])]
    if kind == "symmetry_x":
        return [row("x", difference), row("y", total, 2 * c.point[1])]
    if kind == "symmetry_y":
        return [row("x", total, 2 * c.point[0]), row("y", difference)]
    if kind == "equal_width":
        return [row("width", difference)]
    if kind == "equal_height":
        return [row("height", difference)]
    if kind == "equal_size":
        return [row("width", difference), row("height", difference)]
    return [row(kind[-1], difference, c.value)]  # signed spacing_x / spacing_y


def _rank(matrix: np.ndarray) -> int:
    if not matrix.size:
        return 0
    singular = np.linalg.svd(matrix, compute_uv=False)
    return int(np.count_nonzero(singular > singular[0] * _RANK_RCOND)) if singular[0] else 0


def _solve_constraints(
    pads: Sequence[Pad], constraints: Iterable[Constraint],
    driving: Mapping[int, Mapping[str, float]] | None = None,
) -> SolveResult:
    """Return new records near the input, or raise ValueError with offending IDs.

    ``fixed.values`` stores all six FIELDS, so a fixed relation never acquires a
    new anchor from an edited candidate. X/Y line symmetry passes through point;
    ``symmetric`` is point symmetry about point. Angle equations are modulo 360.
    Custom geometry exposes only its connection X/Y and rotation as variables.
    Lines expose X/Y, length and angle. Non-round shapes omit corner radius;
    circles additionally include their intrinsic equal-diameter relation.
    """
    original = tuple(pads)
    relations = tuple(constraints)
    owners: set[str] = set()
    persistent: list[_Equation] = []
    for constraint in relations:
        if not isinstance(constraint, Constraint):
            raise _error("constraint", "Constraint形式の拘束を指定してください。")
        if constraint.id in owners:
            raise _error(constraint.id, "拘束IDが重複しています。")
        owners.add(constraint.id)
        if any(index >= len(original) for index in constraint.targets):
            raise _error(constraint.id, "拘束先の図形が存在しません。")
        persistent.extend(_equations(constraint, original))
    circle_indices = [index for index, pad in enumerate(original) if _is_circle(pad)]
    intrinsic_owners = {f"shape[{index}].circle": index for index in circle_indices}
    persistent.extend(_Equation(owner, ((index, "width", 1), (index, "height", -1)))
                      for owner, index in intrinsic_owners.items())
    columns = {(index, field): column for column, (index, field) in enumerate(
        (index, field) for index, pad in enumerate(original)
        for field in _variable_fields(pad))}
    initial = np.asarray([float(getattr(original[index], field)) % 360 if field == "angle"
                          else float(getattr(original[index], field)) for index, field in columns], dtype=float)
    temporary: list[_Equation] = []
    if driving is not None and not isinstance(driving, Mapping):
        raise _error("driving", "編集値を対象番号ごとの辞書で指定してください。")
    for index, updates in (driving or {}).items():
        owner = f"driving[{index}]"
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(original):
            raise _error(owner, "編集対象の図形が存在しません。")
        if not isinstance(updates, Mapping):
            raise _error(owner, "編集属性と値を辞書で指定してください。")
        for field, value in updates.items():
            owner = f"driving[{index}.{field}]"
            if field not in FIELDS:
                raise _error(owner, "編集対象の数値属性に対応していません。")
            value = _finite(value, owner)
            if (index, field) not in columns and value != getattr(original[index], field):
                raise _error(owner, "この形状で固定される数値属性は変更できません。")
            temporary.append(_Equation(owner, ((index, field, 1),), value, field == "angle"))
    equations = persistent + temporary
    active = set()
    anchors: dict[int, tuple[float, str]] = {}
    for row in equations:
        if row.distance is not None:
            active.update(columns[index, field] for index in row.distance for field in ("x", "y"))
        else:
            active.update(columns[index, field] for index, field, _ in row.terms if (index, field) in columns)
            if len(row.terms) == 1:
                index, field, coefficient = row.terms[0]
                column = columns.get((index, field))
                if column is not None:
                    value = row.target / coefficient
                    if row.angular:
                        value = initial[column] + (value % 360 - initial[column] + 180) % 360 - 180
                    previous = anchors.get(column)
                    if previous is not None and abs(previous[0] - value) > _RESIDUAL_TOL:
                        raise _error(f"{previous[1]}, {row.owner}", "同じ属性への指定値が矛盾しています。")
                    anchors[column] = (value, row.owner)
    for index in circle_indices:
        width, height = columns[index, "width"], columns[index, "height"]
        if width in anchors and height in anchors and anchors[width][0] != anchors[height][0]:
            raise _error(f"{anchors[width][1]}, {anchors[height][1]}", "円の幅と高さは同じ値で指定してください。")
    free = sorted(active - anchors.keys())

    def stabilize(values):
        # Exact user-specified scalar values and untouched fields must not gain
        # tiny SVD roundoff (notably a formerly zero pad radius).
        for column in range(len(initial)):
            if column not in active:
                values[column] = initial[column]
        for column, (value, _owner) in anchors.items():
            values[column] = value
        for index in circle_indices:
            width, height = columns[index, "width"], columns[index, "height"]
            diameter = (values[width] if width in anchors else values[height] if height in anchors
                        else values[width] / 2 + values[height] / 2)
            values[width] = values[height] = diameter
        return values

    def evaluate(values, rows):
        residual = np.zeros(len(rows), dtype=float)
        jacobian = np.zeros((len(rows), len(columns)), dtype=float)

        def get(index, field):
            column = columns.get((index, field))
            return values[column] if column is not None else getattr(original[index], field)

        for number, row in enumerate(rows):
            if row.distance is not None:
                i, j = row.distance
                dx, dy = get(j, "x") - get(i, "x"), get(j, "y") - get(i, "y")
                length = math.hypot(dx, dy)
                residual[number] = length - row.target
                for field, derivative in (("x", dx / length if length else 1),
                                          ("y", dy / length if length else 0)):
                    for index, sign in ((i, -1), (j, 1)):
                        jacobian[number, columns[index, field]] += sign * derivative
            else:
                target = row.target % 360 if row.angular else row.target
                residual[number] = sum(coefficient * get(index, field)
                                       for index, field, coefficient in row.terms) - target
                if row.angular:
                    residual[number] = (residual[number] + 180) % 360 - 180
                for index, field, coefficient in row.terms:
                    column = columns.get((index, field))
                    if column is not None:
                        jacobian[number, column] += coefficient
        return residual, jacobian

    def fail(residual, rows, detail="拘束が矛盾するか、近傍の解を求められません。"):
        bad = list(dict.fromkeys(row.owner for row, value in zip(rows, residual)
                                if not math.isfinite(value) or abs(value) > _RESIDUAL_TOL))
        for owner in tuple(bad):
            if owner in intrinsic_owners:
                index = intrinsic_owners[owner]
                bad.extend(c.id for c in relations if index in c.targets)
                bad.extend(row.owner for row in temporary if row.terms[0][0] == index)
        bad = list(dict.fromkeys(bad))
        raise _error(", ".join(bad or [row.owner for row in rows]) or "constraint", detail)

    values = stabilize(initial.copy())
    linear = [row for row in equations if row.distance is None]
    residual, matrix = evaluate(values, linear)
    if linear:
        values[free] += np.linalg.lstsq(matrix[:, free], -residual, rcond=_RANK_RCOND)[0]
        stabilize(values)
        remaining, _ = evaluate(values, linear)
        if not np.all(np.isfinite(values)) or np.max(np.abs(remaining), initial=0) > _RESIDUAL_TOL:
            fail(remaining, linear)
    # A positive distance at coincident centers has no unique derivative. Seed
    # a feasible direction in the nullspace of the already satisfied linear rows.
    seed_rows = [row for row in equations if row.distance is not None and math.hypot(
        values[columns[row.distance[1], "x"]] - values[columns[row.distance[0], "x"]],
        values[columns[row.distance[1], "y"]] - values[columns[row.distance[0], "y"]],
    ) <= _RESIDUAL_TOL]
    if seed_rows and free:
        reduced = matrix[:, free]
        if linear:
            _, _, right = np.linalg.svd(reduced, full_matrices=True)
            nullspace = right[_rank(reduced):].T
        else:
            nullspace = np.eye(len(free))
        free_lookup = {column: reduced_column for reduced_column, column in enumerate(free)}
        for row in seed_rows:
            i, j = row.distance
            delta = np.zeros((2, len(free)))
            for axis, field in enumerate(("x", "y")):
                for index, sign in ((i, -1), (j, 1)):
                    column = free_lookup.get(columns[index, field])
                    if column is not None:
                        delta[axis, column] += sign
            for axis in range(2):
                direction = nullspace @ (nullspace.T @ delta[axis])
                length = np.linalg.norm(delta @ direction)
                if length > _RANK_RCOND:
                    values[free] += row.target / length * direction
                    stabilize(values)
                    break
    residual, jacobian = evaluate(values, equations)
    for _iteration in range(128):
        if np.max(np.abs(residual), initial=0) <= _RESIDUAL_TOL:
            break
        step = np.zeros(len(columns))
        step[free] = np.linalg.lstsq(jacobian[:, free], -residual, rcond=_RANK_RCOND)[0]
        if not np.all(np.isfinite(step)):
            break
        scale = max(1, float(np.max(np.abs(residual), initial=0)))
        score = np.linalg.norm(residual / scale)
        for exponent in range(24):
            candidate = stabilize(values + step * (0.5 ** exponent))
            trial, trial_jacobian = evaluate(candidate, equations)
            if np.all(np.isfinite(candidate)) and np.all(np.isfinite(trial)) and (
                np.linalg.norm(trial / scale) < score * (1 - 1e-12)
                or np.max(np.abs(trial), initial=0) <= _RESIDUAL_TOL
            ):
                values, residual, jacobian = candidate, trial, trial_jacobian
                break
        else:
            break
    maximum = float(np.max(np.abs(residual), initial=0))
    if not np.all(np.isfinite(values)) or not math.isfinite(maximum) or maximum > _RESIDUAL_TOL:
        fail(residual, equations)
    result = []
    for index, pad in enumerate(original):
        updates = {}
        for field in FIELDS:
            column = columns.get((index, field))
            if column is not None and values[column] != initial[column]:
                updates[field] = float(values[column] % 360 if field == "angle" else values[column])
        try:
            result.append(replace(pad, **updates) if updates else pad)
        except ValueError as error:
            involved = [c.id for c in relations if index in c.targets]
            involved.extend(row.owner for row in temporary if row.terms[0][0] == index)
            raise _error(", ".join(involved) or "constraint", str(error)) from error
    _, persistent_jacobian = evaluate(values, persistent)
    rank = _rank(persistent_jacobian)
    freedom = len(columns) - rank
    return SolveResult(result, "fully_constrained" if freedom == 0 else "underconstrained",
                       rank, freedom, maximum)


def solve_constraints(
    pads: Sequence[Pad], constraints: Iterable[Constraint],
    driving: Mapping[int, Mapping[str, float]] | None = None,
) -> SolveResult:
    """Atomically solve persistent relations and temporary hard driving values.

    Fixed snapshots use ``values`` in FIELDS order. Reported rank/freedom include
    intrinsic circle equality and exclude transient driving equations. A circle's
    radius constraint specifies half its diameter, rather than its corner R.
    Symmetry axes pass through Constraint.point;
    ``symmetric`` uses that point as the point-symmetry center. The nonlinear
    solution is local; failure retains every input record and reports its IDs.
    """
    try:
        relations = tuple(constraints)
    except TypeError:
        raise _error("constraint", "拘束の配列を指定してください。") from None
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return _solve_constraints(pads, relations, driving)
    except (np.linalg.LinAlgError, FloatingPointError, OverflowError) as error:
        names = [constraint.id for constraint in relations if isinstance(constraint, Constraint)]
        if driving:
            names.append("driving")
        raise _error(", ".join(names) or "constraint", "数値計算の範囲を超えました。指定値を確認してください。") from error
