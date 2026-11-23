"""Stage 1 geometry and KiCad 10 footprint export."""

from __future__ import annotations

import math
import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

Point = tuple[float, float]
PAD_LAYERS = ("F.Cu", "F.Paste", "F.Mask")
FRONT_LAYERS = ("F.Cu", "F.Paste", "F.Mask", "F.SilkS", "F.Fab", "F.CrtYd")
BACK_LAYERS = tuple(layer.replace("F.", "B.") for layer in FRONT_LAYERS)
KICAD_FORMAT_VERSION = 20260206

# KiCad 10 parses distances as signed integer nanometers and clamps larger values.
_MAX_PCB_MM = 2147.483637
_HALF_NANOMETER_MM = 0.0000005
_FORBIDDEN_NAME_CHARACTERS = '<>:"/\\|?*%$'
_RESERVED_WINDOWS_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"
}


def _checked_point(point: Point) -> Point:
    x, y = point
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError("座標は有限の数値を指定してください。")
    return x, y


@dataclass(frozen=True)
class Calibration:
    a: Point
    b: Point
    distance: float
    mm_per_pixel: float = field(init=False)

    def __post_init__(self) -> None:
        a = _checked_point(self.a)
        b = _checked_point(self.b)
        pixels = math.dist(a, b)
        if pixels <= 0 or not math.isfinite(pixels):
            raise ValueError("校正には画像上の異なる2点を指定してください。")
        if self.distance <= 0 or not math.isfinite(self.distance):
            raise ValueError("校正の実距離は正の有限値を mm で指定してください。")
        scale = self.distance / pixels
        if scale <= 0 or not math.isfinite(scale):
            raise ValueError("校正の縮尺を計算できません。2点と実距離を確認してください。")
        object.__setattr__(self, "a", a)
        object.__setattr__(self, "b", b)
        object.__setattr__(self, "mm_per_pixel", scale)

    @property
    def mm_per_pixel_x(self) -> float:
        return self.mm_per_pixel

    @property
    def mm_per_pixel_y(self) -> float:
        return self.mm_per_pixel


@dataclass(frozen=True)
class AxisCalibration:
    """Independent image X/Y scales measured with horizontal/vertical pairs."""

    x_a: Point
    x_b: Point
    x_distance: float
    y_a: Point
    y_b: Point
    y_distance: float
    mm_per_pixel_x: float = field(init=False)
    mm_per_pixel_y: float = field(init=False)

    def __post_init__(self) -> None:
        x_a, x_b = _checked_point(self.x_a), _checked_point(self.x_b)
        y_a, y_b = _checked_point(self.y_a), _checked_point(self.y_b)
        if x_a[1] != x_b[1]:
            raise ValueError("横校正の2点は同じ高さに指定してください。")
        if y_a[0] != y_b[0]:
            raise ValueError("縦校正の2点は同じ横位置に指定してください。")
        pixels_x = abs(x_b[0] - x_a[0])
        pixels_y = abs(y_b[1] - y_a[1])
        for label, pixels, distance in (
            ("横", pixels_x, self.x_distance),
            ("縦", pixels_y, self.y_distance),
        ):
            if pixels <= 0 or not math.isfinite(pixels):
                raise ValueError(f"{label}校正には画像上の異なる2点を指定してください。")
            if distance <= 0 or not math.isfinite(distance):
                raise ValueError(f"{label}校正の実距離は正の有限値を mm で指定してください。")
            scale = distance / pixels
            if scale <= 0 or not math.isfinite(scale):
                raise ValueError(f"{label}校正の縮尺を計算できません。2点と実距離を確認してください。")
            attribute = "mm_per_pixel_x" if label == "横" else "mm_per_pixel_y"
            object.__setattr__(self, attribute, scale)
        for attribute, point in (("x_a", x_a), ("x_b", x_b), ("y_a", y_a), ("y_b", y_b)):
            object.__setattr__(self, attribute, point)


@dataclass(frozen=True)
class Pad:
    number: str
    x: float
    y: float
    width: float
    height: float
    angle: float = 0
    radius: float = 0
    pad_type: str = field(default="smd", kw_only=True)
    pad_shape: str = field(default="auto", kw_only=True)
    layers: tuple[str, ...] = field(default=PAD_LAYERS, kw_only=True)
    drill_width: float = field(default=0.0, kw_only=True)
    drill_height: float = field(default=0.0, kw_only=True)
    mask_margin: float = field(default=0.0, kw_only=True)
    paste_margin: float = field(default=0.0, kw_only=True)
    paste_ratio: float = field(default=0.0, kw_only=True)
    opening_explicit: bool = field(default=False, kw_only=True)
    preserved: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if not self.number.strip() and self.pad_type != "np_thru_hole":
            raise ValueError("パッド番号を入力してください。")
        if any(ord(char) < 32 for char in self.number):
            raise ValueError("パッド番号に制御文字は使用できません。")
        if self.pad_type == "np_thru_hole":
            # KiCad removes numbers from non-electrical NPTH holes on load.
            object.__setattr__(self, "number", "")
        dimensions = (self.x, self.y, self.width, self.height, self.angle, self.radius)
        if not all(math.isfinite(value) for value in dimensions):
            raise ValueError("パッドの座標・寸法・角度・Rは有限の数値を指定してください。")
        if self.width < _HALF_NANOMETER_MM or self.height < _HALF_NANOMETER_MM:
            raise ValueError("パッドの幅と高さはKiCadで表せる正の値を指定してください。")
        if any(abs(value) > _MAX_PCB_MM for value in dimensions[:4]):
            raise ValueError("パッドの座標または寸法がKiCadの表現範囲を超えています。")
        if not 0 <= self.radius <= min(self.width, self.height) / 2:
            raise ValueError("パッドのRは0から短辺の半分までを指定してください。")
        if 0 < self.radius < _HALF_NANOMETER_MM:
            raise ValueError("パッドのRは0またはKiCadで表せる正の値を指定してください。")
        if self.pad_type not in ("smd", "thru_hole", "np_thru_hole"):
            raise ValueError("パッド種別はSMD・PTH・NPTHから指定してください。")
        if self.pad_shape not in ("auto", "rect", "roundrect", "circle", "oval"):
            raise ValueError("対応していないパッド形状です。")
        if self.pad_shape == "circle" and self.width != self.height:
            raise ValueError("円パッドの幅と高さは同じ値を指定してください。")
        layers = tuple(self.layers)
        if not layers or any(layer not in (*FRONT_LAYERS, *BACK_LAYERS, "*.Cu", "*.Mask") for layer in layers):
            raise ValueError("対応するKiCadレイヤーを指定してください。")
        if not all(math.isfinite(v) for v in (self.drill_width, self.drill_height, self.mask_margin, self.paste_margin, self.paste_ratio)):
            raise ValueError("穴寸法・開口量は有限値を指定してください。")
        if self.pad_type == "smd":
            if self.drill_width or self.drill_height:
                raise ValueError("SMDパッドには穴寸法を指定できません。")
        elif min(self.drill_width, self.drill_height) < _HALF_NANOMETER_MM:
            raise ValueError("PTH・NPTHには正の丸穴または長穴寸法を指定してください。")
        elif self.drill_width > self.width or self.drill_height > self.height:
            raise ValueError("穴寸法はパッド外形以下にしてください。")
        if self.paste_ratio <= -0.5:
            raise ValueError("Pasteの片側比率は-0.5より大きい値を指定してください。")
        if min(self.width, self.height) + 2 * self.mask_margin <= 0:
            raise ValueError("Mask開口が消える拡縮量は指定できません。")
        if min(self.width, self.height) * (1 + 2*self.paste_ratio) + 2 * self.paste_margin <= 0:
            raise ValueError("Paste開口が消える拡縮量は指定できません。")
        object.__setattr__(self, "layers", layers)
        object.__setattr__(self, "preserved", tuple(self.preserved))
        if not isinstance(self.opening_explicit, bool):
            raise ValueError("開口値の指定状態が不正です。")


@dataclass
class Sketch:
    calibration: Calibration | AxisCalibration | None = field(default=None, init=False)
    origin: Point | None = field(default=None, init=False)
    pads: list[Pad] = field(default_factory=list, init=False)
    constraints: list = field(default_factory=list, init=False)
    reference: str = field(default="REF**", init=False)
    value: str = field(default="", init=False)
    preserved_nodes: tuple[str, ...] = field(default=(), init=False)
    metadata: dict = field(default_factory=dict, init=False)

    def set_calibration(self, a: Point, b: Point, distance: float) -> None:
        self.calibration = Calibration(a, b, distance)

    def set_axis_calibration(
        self, x_a: Point, x_b: Point, x_distance: float,
        y_a: Point, y_b: Point, y_distance: float,
    ) -> None:
        self.calibration = AxisCalibration(x_a, x_b, x_distance, y_a, y_b, y_distance)

    def set_origin(self, point: Point) -> None:
        new_origin = _checked_point(point)
        if self.pads and self.origin is not None:
            dx, dy = self.image_to_mm(new_origin)
            shifted = [replace(pad, x=pad.x - dx, y=pad.y - dy) for pad in self.pads]
            self.pads[:] = shifted
        self.origin = new_origin

    def _image_frame(self) -> tuple[Point, float, float]:
        if self.calibration is None or self.origin is None:
            raise ValueError("校正を行い、原点を指定してください。")
        return self.origin, self.calibration.mm_per_pixel_x, self.calibration.mm_per_pixel_y

    def image_to_mm(self, point: Point) -> Point:
        """Map image pixels to footprint-local mm with X right and Y down."""
        (origin_x, origin_y), scale_x, scale_y = self._image_frame()
        x, y = _checked_point(point)
        return _checked_point(((x - origin_x) * scale_x, (y - origin_y) * scale_y))

    def mm_to_image(self, point: Point) -> Point:
        (origin_x, origin_y), scale_x, scale_y = self._image_frame()
        x, y = _checked_point(point)
        return _checked_point((origin_x + x / scale_x, origin_y + y / scale_y))

    def add_pad(self, pad: Pad) -> None:
        self._image_frame()
        self.pads.append(pad)


def _validate_footprint_name(name: str) -> None:
    if not name.strip() or name.endswith((".", " ")):
        raise ValueError("有効なフットプリント名を指定してください。")
    if any(char in _FORBIDDEN_NAME_CHARACTERS or ord(char) < 32 for char in name):
        raise ValueError("フットプリント名にWindowsまたはKiCadで使用できない文字があります。")
    if name.split(".", 1)[0].rstrip().upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError("フットプリント名にWindowsの予約名は使用できません。")


def _quoted(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _decimal(value: float) -> str:
    # Keep the model's precision, and emit a decimal token without exponent notation.
    return format(Decimal(str(value)), "f") if value else "0"


def _render_pad(pad: Pad, *, layers: tuple[str, ...] | None = None, number: str | None = None) -> list[str]:
    from .compound import CustomPad, world_paste_shapes
    from .graphics import Graphic, render_graphic

    if isinstance(pad, Graphic):
        return render_graphic(pad)
    layers = pad.layers if layers is None else layers

    if isinstance(pad, CustomPad):
        anchor_width, anchor_height = pad.anchor_size
        lines = [
            f"  (pad {_quoted(pad.number)} smd custom",
            f"    (at {_decimal(pad.x)} {_decimal(pad.y)} {_decimal(pad.angle % 360)})",
            f"    (size {_decimal(anchor_width)} {_decimal(anchor_height)})",
            '    (layers ' + ' '.join(_quoted(layer) for layer in layers if layer not in ("F.Paste", "B.Paste")) + ')',
            '    (options (clearance outline) (anchor rect))',
            '    (primitives',
            '      (gr_poly',
            '        (pts',
            *[f"          (xy {_decimal(x)} {_decimal(y)})" for x, y in pad.outline],
            '        )',
            '        (width 0) (fill yes)',
            '      )',
            '    )',
            '  )',
        ]
        if pad.mask_margin or pad.opening_explicit:
            lines[-1:-1] = [f"    (solder_mask_margin {_decimal(pad.mask_margin)})"]
        lines[-1:-1] = [f"    {node}" for node in pad.preserved]
        for aperture in world_paste_shapes(pad):
            paste_layers = tuple(layer for layer in layers if layer in ("F.Paste", "B.Paste"))
            if paste_layers:
                lines.extend(_render_pad(replace(aperture, opening_explicit=True), layers=paste_layers, number=""))
        return lines
    shape = ("roundrect" if pad.radius else "rect") if pad.pad_shape == "auto" else pad.pad_shape
    layer_tokens = " ".join(_quoted(layer) for layer in layers)
    lines = [
        f"  (pad {_quoted(pad.number if number is None else number)} {pad.pad_type} {shape}",
        f"    (at {_decimal(pad.x)} {_decimal(pad.y)} {_decimal(pad.angle % 360)})",
        f"    (size {_decimal(pad.width)} {_decimal(pad.height)})",
        f"    (layers {layer_tokens})",
    ]
    if pad.pad_type != "smd":
        hole = (_decimal(pad.drill_width) if pad.drill_width == pad.drill_height else
                f"oval {_decimal(pad.drill_width)} {_decimal(pad.drill_height)}")
        lines.append(f"    (drill {hole})")
    if shape == "roundrect":
        ratio = pad.radius / min(pad.width, pad.height)
        lines.append(f"    (roundrect_rratio {_decimal(ratio)})")
    for key, value in (("solder_mask_margin", pad.mask_margin), ("solder_paste_margin", pad.paste_margin),
                       ("solder_paste_margin_ratio", pad.paste_ratio)):
        if value or pad.opening_explicit:
            lines.append(f"    ({key} {_decimal(value)})")
    lines.extend(f"    {node}" for node in pad.preserved)
    lines.append("  )")
    return lines


def _render_footprint(name: str, pads: Iterable[Pad], *, reference: str = "REF**",
                      value: str | None = None, preserved_nodes: Iterable[str] = ()) -> str:
    pad_records = list(pads)
    if not pad_records:
        raise ValueError("書き出す前にSMDパッドを配置してください。")

    lines = [
        f"(footprint {_quoted(name)}",
        f"  (version {KICAD_FORMAT_VERSION})",
        '  (generator "kicad_footprint_sketcher")',
        '  (layer "F.Cu")',
    ]
    for field_name, text, layer in (("Reference", reference, "F.SilkS"), ("Value", name if value is None else value, "F.Fab")):
        lines.extend([
            f"  (property {_quoted(field_name)} {_quoted(text)}",
            f"    (at 0 0 0) (layer {_quoted(layer)}) (hide yes)",
            "    (effects (font (size 1 1) (thickness 0.15)))",
            "  )",
        ])
    if all(pad.pad_type == "smd" for pad in pad_records):
        lines.append("  (attr smd)")
    from .persistence import _render_entities_with_ownership
    lines.extend(_render_entities_with_ownership(pad_records))
    lines.extend(f"  {node}" for node in preserved_nodes)
    lines.append(")")
    return "\n".join(lines) + "\n"


def export_footprint(
    directory: Path, name: str, pads: Iterable[Pad], *, overwrite: bool = False,
    reference: str = "REF**", value: str | None = None, preserved_nodes: Iterable[str] = (),
) -> Path:
    """Write a new footprint atomically, replacing a file only with explicit consent."""
    _validate_footprint_name(name)
    output = Path(directory) / f"{name}.kicad_mod"
    content = _render_footprint(name, pads, reference=reference, value=value, preserved_nodes=preserved_nodes)
    temporary_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            dir=output.parent, prefix=".footprint-", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if overwrite:
            os.replace(temporary_path, output)
        elif os.name == "nt":
            # Windows rename fails if another writer has already created the output.
            os.rename(temporary_path, output)
        else:
            os.link(temporary_path, output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return output
