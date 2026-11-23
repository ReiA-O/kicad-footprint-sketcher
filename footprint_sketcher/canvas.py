"""Image viewport and footprint overlays for the desktop editor."""

from __future__ import annotations

import math
from dataclasses import replace
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk

from .model import AxisCalibration, Pad, Point, Sketch
from .compound import CustomPad, world_paste_shapes
from .graphics import Graphic, graphic_outline


SURFACE = "#ffffff"
WORKSPACE = "#edf1f6"
INK = "#192636"
MUTED = "#506276"
LINE = "#c8d3df"
TEAL = "#08695e"
BLUE = "#234f9a"


class SketchCanvas(tk.Canvas):
    """Keep image coordinates independent from zoom and pan."""

    MIN_SCALE = 0.02
    MAX_SCALE = 32.0

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, background=WORKSPACE, highlightthickness=0)
        self.image: Image.Image | None = None
        self.scale = 1.0
        self.offset: Point = (0.0, 0.0)
        self._source_rgba: Image.Image | None = None
        self._fit_pending = True
        self._photo: ImageTk.PhotoImage | None = None
        self._photo_size: tuple[int, int] | None = None
        self._photo_key: tuple[object, ...] | None = None
        self._number_font = tkfont.Font(
            root=self, family=tkfont.nametofont("TkDefaultFont", root=self).actual("family"),
            size=16, weight="bold",
        )
        self.hidden_layers: set[str] = set()
        self.locked_layers: set[str] = set()

    def set_image(self, image: Image.Image) -> None:
        self.image = image
        self._source_rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        self._photo = None
        self._photo_size = None
        self._photo_key = None
        self._fit_pending = True
        self.scale = 1.0
        self.offset = (0.0, 0.0)
        self.fit_to_window()

    def image_to_canvas(self, point: Point) -> Point:
        return (
            self.offset[0] + point[0] * self.scale,
            self.offset[1] + point[1] * self.scale,
        )

    def canvas_to_image(self, point: Point) -> Point:
        return (
            (point[0] - self.offset[0]) / self.scale,
            (point[1] - self.offset[1]) / self.scale,
        )

    def _fit_scale(self) -> float:
        image = self.image
        if image is None:
            return 1.0
        margin = 32
        return min(
            1.0,
            max(1, self.winfo_width() - 2 * margin) / image.width,
            max(1, self.winfo_height() - 2 * margin) / image.height,
        )

    def fit_to_window(self) -> None:
        """Reset only the viewport, retaining calibration and footprint data."""
        image = self.image
        if image is None:
            return
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            # Tk lays the canvas out after construction. Fit once it has a size.
            self._fit_pending = True
            return
        self.scale = self._fit_scale()
        self.offset = (
            round((width - image.width * self.scale) / 2),
            round((height - image.height * self.scale) / 2),
        )
        self._fit_pending = False

    def zoom_at(self, factor: float, anchor_canvas_xy: Point) -> None:
        """Zoom while keeping the image point under the anchor stationary."""
        if self.image is None:
            return
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("Zoom factor must be a positive finite number.")
        if not all(math.isfinite(value) for value in anchor_canvas_xy):
            raise ValueError("Zoom anchor must contain finite coordinates.")
        if self._fit_pending:
            self.fit_to_window()
        image_point = self.canvas_to_image(anchor_canvas_xy)
        # A very large image may need less than MIN_SCALE to fit completely.
        minimum = min(self.MIN_SCALE, self._fit_scale())
        new_scale = min(self.MAX_SCALE, max(minimum, self.scale * factor))
        new_offset = (
            anchor_canvas_xy[0] - image_point[0] * new_scale,
            anchor_canvas_xy[1] - image_point[1] * new_scale,
        )
        if not all(math.isfinite(value) for value in new_offset):
            raise ValueError("Zoom would exceed the viewport coordinate range.")
        self.scale = new_scale
        self.offset = new_offset
        self._fit_pending = False

    def pan_by(self, dx: float, dy: float) -> None:
        if self.image is None:
            return
        if not (math.isfinite(dx) and math.isfinite(dy)):
            raise ValueError("Pan distance must contain finite coordinates.")
        if self._fit_pending:
            self.fit_to_window()
        new_offset = (self.offset[0] + dx, self.offset[1] + dy)
        if not all(math.isfinite(value) for value in new_offset):
            raise ValueError("Pan would exceed the viewport coordinate range.")
        self.offset = new_offset
        self._fit_pending = False

    def render(
        self,
        sketch: Sketch,
        candidate_points: list[Point],
        selected_index: int | None = None,
        preview_pad: Pad | None = None,
        calibration_axes: bool = False,
        selected_indices: list[int] | None = None,
        preview_pads: dict[int, Pad] | None = None,
        new_previews: list[Pad] | None = None,
        guides: list[tuple[str, float]] | None = None,
    ) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if self.image is None:
            self.create_text(
                width / 2,
                height / 2,
                text="PNG または JPEG の下絵を開いてください",
                fill=MUTED,
                font="TkDefaultFont",
            )
            return
        if self._fit_pending:
            self.fit_to_window()
        self._draw_image(width, height)

        points = candidate_points
        axes = calibration_axes
        if not points and sketch.calibration is not None:
            calibration = sketch.calibration
            axes = isinstance(calibration, AxisCalibration)
            if axes:
                points = [calibration.x_a, calibration.x_b, calibration.y_a, calibration.y_b]
            else:
                points = [calibration.a, calibration.b]
        if sketch.origin is not None:
            selected = list(dict.fromkeys(
                index for index in (selected_indices if selected_indices is not None else [selected_index])
                if index is not None and 0 <= index < len(sketch.pads)
            ))
            primary = selected_index if selected_index in selected else None
            replacements = {
                index: pad for index, pad in (preview_pads or {}).items()
                if 0 <= index < len(sketch.pads)
            }
            standalone_preview = preview_pad
            if preview_pad is not None and primary is not None:
                replacements.setdefault(primary, preview_pad)
                standalone_preview = None
            for index, pad in enumerate(sketch.pads):
                if index in replacements:
                    continue
                self._draw_pad(pad, sketch, index=index)
            for index in sorted(replacements):
                self._draw_pad(replacements[index], sketch, index=index, preview=True)
            if standalone_preview is not None:
                self._draw_pad(standalone_preview, sketch, preview=True)
            for pad in new_previews or []:
                self._draw_pad(pad, sketch, preview=True)
            # Draw the primary last so its handles remain above other outlines.
            for index in [item for item in selected if item != primary] + (
                [primary] if primary is not None else []
            ):
                self._draw_selection(
                    replacements.get(index, sketch.pads[index]),
                    sketch,
                    index=index,
                    handles=index == primary and len(selected) == 1,
                )
            for axis, value in guides or []:
                x, y = self.image_to_canvas(sketch.mm_to_image((value, 0) if axis == "x" else (0, value)))
                if axis == "x" and 0 <= x <= width:
                    self.create_line(x, 0, x, height, fill="#b56e00", dash=(3, 3),
                                     tags=("center-snap-guide", "snap-guide-x"))
                elif axis == "y" and 0 <= y <= height:
                    self.create_line(0, y, width, y, fill="#b56e00", dash=(3, 3),
                                     tags=("center-snap-guide", "snap-guide-y"))
            self._draw_origin(sketch.origin)
        self._draw_calibration(points, axes=axes, active=bool(candidate_points))

    def _draw_image(self, width: int, height: int) -> None:
        image = self.image
        source = self._source_rgba
        assert image is not None and source is not None
        left, top = self.offset
        right = left + image.width * self.scale
        bottom = top + image.height * self.scale
        visible = (
            max(0, math.floor(left)),
            max(0, math.floor(top)),
            min(width, math.ceil(right)),
            min(height, math.ceil(bottom)),
        )
        x0, y0, x1, y1 = visible
        if x1 <= x0 or y1 <= y0:
            return
        self.create_rectangle(
            *self._clipped([(left - 1, top - 1), (right + 1, bottom + 1)]),
            fill=SURFACE,
            outline=LINE,
        )

        # Render at most the viewport's dimensions, even at the highest zoom.
        key = (visible, self.scale, self.offset)
        if key != self._photo_key:
            size = (x1 - x0, y1 - y0)
            inverse_scale = 1.0 / self.scale
            visible_image = source.transform(
                size,
                Image.Transform.AFFINE,
                (
                    inverse_scale,
                    0.0,
                    (x0 - left) * inverse_scale,
                    0.0,
                    inverse_scale,
                    (y0 - top) * inverse_scale,
                ),
                resample=Image.Resampling.BICUBIC,
                fillcolor=(0, 0, 0, 0),
            )
            self._photo = ImageTk.PhotoImage(visible_image, master=self)
            self._photo_size = size
            self._photo_key = key
        self.create_image(x0, y0, image=self._photo, anchor="nw")

    @staticmethod
    def _clipped(points: list[Point]) -> list[Point]:
        return [
            (max(-30_000, min(x, 30_000)), max(-30_000, min(y, 30_000)))
            for x, y in points
        ]

    def _draw_calibration(self, points: list[Point], *, axes: bool = False, active: bool = False) -> None:
        screen_points = [self.image_to_canvas(point) for point in points]
        pairs = (screen_points[:2], screen_points[2:4]) if axes else (screen_points[:2],)
        for pair in pairs:
            if len(pair) == 2:
                self.create_line(*self._clipped(pair), fill=BLUE, dash=(5, 3))
        if axes and active and len(screen_points) in (1, 3):
            x, y = screen_points[-1]
            if len(screen_points) == 1 and 0 <= y <= self.winfo_height():
                self.create_line(0, y, self.winfo_width(), y, fill=BLUE, dash=(2, 4))
            elif len(screen_points) == 3 and 0 <= x <= self.winfo_width():
                self.create_line(x, 0, x, self.winfo_height(), fill=BLUE, dash=(2, 4))
        labels = ("X1", "X2", "Y1", "Y2") if axes else ("A", "B")
        for label, (x, y) in zip(labels, screen_points):
            if not (-15 <= x <= self.winfo_width() + 15 and -15 <= y <= self.winfo_height() + 15):
                continue
            self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=SURFACE, outline=BLUE, width=2)
            self.create_text(x + 8, y - 8, text=label, fill=BLUE, anchor="sw")

    def _draw_origin(self, point: Point) -> None:
        x, y = self.image_to_canvas(point)
        if not (-30 <= x <= self.winfo_width() + 30 and -30 <= y <= self.winfo_height() + 30):
            return
        self.create_line(x - 6, y, x + 19, y, fill=BLUE, arrow="last", width=2)
        self.create_line(x, y - 6, x, y + 19, fill=BLUE, arrow="last", width=2)
        self.create_text(x + 22, y, text="X", fill=BLUE, anchor="w")
        self.create_text(x, y + 22, text="Y", fill=BLUE, anchor="n")
        self.create_text(x - 6, y - 6, text="0", fill=BLUE, anchor="se")

    def _pad_vertices(self, pad: Pad, sketch: Sketch, points: list[Point]) -> list[Point]:
        angle = math.radians(pad.angle % 360)
        cos_angle, sin_angle = math.cos(angle), math.sin(angle)
        return [
            self.image_to_canvas(sketch.mm_to_image((
                pad.x + x * cos_angle + y * sin_angle,
                pad.y + y * cos_angle - x * sin_angle,
            )))
            for x, y in points
        ]

    def _draw_pad(
        self, pad: Pad, sketch: Sketch, *, index: int | None = None, preview: bool = False
    ) -> None:
        if all(layer in self.hidden_layers for layer in pad.layers):
            return
        if isinstance(pad, Graphic):
            vertices = self._pad_vertices(pad, sketch, graphic_outline(pad))
            color = {"F.Cu": "#b7413b", "B.Cu": "#3453b5", "F.Paste": "#aa7618",
                     "F.Mask": "#7d409c", "F.CrtYd": "#ac318c", "F.Fab": "#71817f"}.get(pad.layer, "#485467")
            coordinates = self._clipped(vertices)
            tags = ("graphic", f"pad-{index}")
            if pad.graphic_kind in ("line", "arc"):
                self.create_line(coordinates, fill=BLUE if preview else color,
                                 width=max(1, pad.stroke / sketch.calibration.mm_per_pixel_x * self.scale), tags=tags)
            else:
                self.create_polygon(coordinates, fill=color if pad.filled else "", outline=color,
                                    width=max(1, pad.stroke / sketch.calibration.mm_per_pixel_x * self.scale), tags=tags)
            return
        copper_visible = any(layer.endswith("Cu") and layer not in self.hidden_layers for layer in pad.layers)
        mask_visible = any(layer.endswith("Mask") and layer not in self.hidden_layers for layer in pad.layers)
        paste_visible = any(layer.endswith("Paste") and layer not in self.hidden_layers for layer in pad.layers)
        display_pad = pad
        if not copper_visible and not isinstance(pad, CustomPad):
            margin = pad.mask_margin if mask_visible else pad.paste_margin
            ratio = 0 if mask_visible else pad.paste_ratio
            width, height = pad.width*(1+2*ratio)+2*margin, pad.height*(1+2*ratio)+2*margin
            radius = min(max(0, pad.radius+margin), width/2, height/2)
            display_pad = Pad(pad.number or "opening", pad.x, pad.y, width, height, pad.angle, radius,
                              pad_shape=pad.pad_shape if pad.pad_shape in ("circle", "oval") else "auto")
            if paste_visible and not mask_visible:
                display_pad = world_paste_shapes(pad)[0]
        vertices = self._pad_vertices(display_pad, sketch, pad_outline(display_pad))
        if isinstance(pad, CustomPad) and not copper_visible and mask_visible and pad.mask_margin:
            from shapely import Polygon
            buffered = Polygon(pad.outline).buffer(pad.mask_margin)
            if not buffered.is_empty and buffered.geom_type == "Polygon":
                vertices = self._pad_vertices(pad, sketch, list(buffered.exterior.coords))
        options = {
            "fill": BLUE if preview else TEAL,
            "outline": BLUE if preview else TEAL,
            "width": 2,
            "stipple": "gray25" if preview else "gray50",
            "tags": (("preview", f"preview-pad-{index}") if index is not None else ("preview",))
                    if preview else ("pad", f"pad-{index}"),
        }
        if preview:
            options["dash"] = (5, 3)
        if copper_visible or mask_visible or not isinstance(pad, CustomPad):
            self.create_polygon(self._clipped(vertices), **options)
        if pad.pad_type != "smd" and copper_visible:
            hole = Pad("hole", pad.x, pad.y, pad.drill_width, pad.drill_height,
                       angle=pad.angle, pad_shape="oval")
            opening = self._pad_vertices(hole, sketch, pad_outline(hole))
            self.create_polygon(self._clipped(opening), fill=WORKSPACE, outline=INK, tags=("drill",))
        if isinstance(pad, CustomPad) and paste_visible:
            for aperture in world_paste_shapes(pad):
                opening = self._pad_vertices(aperture, sketch, pad_outline(aperture))
                self.create_polygon(self._clipped(opening), fill="", outline="#b56e00", dash=(3, 3),
                                    tags=("paste-opening",))
        cx, cy = self.image_to_canvas(sketch.mm_to_image((pad.x, pad.y)))
        if isinstance(pad, CustomPad):
            self.create_line(cx - 4, cy, cx + 4, cy, fill=BLUE, tags=("connection-point",))
            self.create_line(cx, cy - 4, cx, cy + 4, fill=BLUE, tags=("connection-point",))
        if 0 <= cx <= self.winfo_width() and 0 <= cy <= self.winfo_height():
            label_x, label_y, anchor = self._number_label_position(cx, cy, pad.number)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                self.create_text(label_x + dx, label_y + dy, text=pad.number, fill=INK,
                                 font=self._number_font, anchor=anchor, tags=("pad-number-outline",))
            number_tag = "pad-number-preview" if preview else "pad-number"
            if index is not None:
                number_tag += f"-{index}"
            self.create_text(label_x, label_y, text=pad.number, fill="#ffffff", font=self._number_font,
                             anchor=anchor,
                             tags=("pad-number", number_tag))

    def _number_label_position(self, cx: float, cy: float, text: str) -> tuple[float, float, str]:
        width, height = self.winfo_width(), self.winfo_height()
        text_width = self._number_font.measure(text) + 2
        text_height = self._number_font.metrics("linespace") + 2
        gap = 10
        candidates = []
        for right, above, anchor in ((True, True, "sw"), (False, True, "se"),
                                     (True, False, "nw"), (False, False, "ne")):
            x, y = cx + (gap if right else -gap), cy + (-gap if above else gap)
            left = x if right else x - text_width
            top = y - text_height if above else y
            bounds = (left, top, left + text_width, top + text_height)
            candidates.append(((x, y, anchor), bounds))
            if left >= 2 and top >= 2 and bounds[2] <= width - 2 and bounds[3] <= height - 2:
                return x, y, anchor
        # Long identifiers may not fit in any quadrant. Keep the cross clear
        # and choose the quadrant with the most visible label area.
        def visible_area(candidate):
            left, top, right, bottom = candidate[1]
            return max(0, min(width, right) - max(0, left)) * max(0, min(height, bottom) - max(0, top))
        return max(candidates, key=visible_area)[0]

    def _draw_selection(
        self,
        pad: Pad,
        sketch: Sketch,
        *,
        index: int | None = None,
        handles: bool = True,
    ) -> None:
        corners = [
            (-pad.width / 2, -pad.height / 2),
            (pad.width / 2, -pad.height / 2),
            (pad.width / 2, pad.height / 2),
            (-pad.width / 2, pad.height / 2),
        ]
        if isinstance(pad, CustomPad):
            left, right = min(x for x, _ in pad.outline), max(x for x, _ in pad.outline)
            top, bottom = min(y for _, y in pad.outline), max(y for _, y in pad.outline)
            corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
            handles = False
        vertices = self._pad_vertices(pad, sketch, corners)
        self.create_polygon(
            self._clipped(vertices),
            fill="",
            outline=BLUE,
            width=2,
            tags=("selection", "selection-outline", f"selected-pad-{index}")
                 if index is not None else ("selection", "selection-outline"),
        )
        if not handles:
            return
        for index, (x, y) in enumerate(vertices):
            if not (-5 <= x <= self.winfo_width() + 5 and -5 <= y <= self.winfo_height() + 5):
                continue
            self.create_rectangle(
                x - 4,
                y - 4,
                x + 4,
                y + 4,
                fill=SURFACE,
                outline=BLUE,
                width=2,
                tags=("selection", "selection-handle", f"selection-handle-{index}"),
            )


def pad_outline(pad: Pad) -> list[Point]:
    if isinstance(pad, Graphic):
        return graphic_outline(pad)
    if isinstance(pad, CustomPad):
        return list(pad.outline)
    left, right = -pad.width / 2, pad.width / 2
    top, bottom = -pad.height / 2, pad.height / 2
    radius = min(pad.width, pad.height)/2 if pad.pad_shape in ("circle", "oval") else pad.radius
    if radius == 0:
        return [(left, top), (right, top), (right, bottom), (left, bottom)]

    corners = (
        (right - radius, top + radius, -90),
        (right - radius, bottom - radius, 0),
        (left + radius, bottom - radius, 90),
        (left + radius, top + radius, 180),
    )
    outline = []
    for x, y, start in corners:
        for step in range(9):
            angle = math.radians(start + step * 90 / 8)
            outline.append((x + radius * math.cos(angle), y + radius * math.sin(angle)))
    return outline
