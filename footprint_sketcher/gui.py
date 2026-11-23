"""The Stage 1 desktop editor."""

from __future__ import annotations

import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk
from typing import Literal

from PIL import Image, ImageTk

from .model import PAD_LAYERS, Calibration, Pad, Point, Sketch, export_footprint


SURFACE = "#ffffff"
WORKSPACE = "#edf1f6"
INK = "#192636"
MUTED = "#506276"
LINE = "#c8d3df"
TEAL = "#08695e"
BLUE = "#234f9a"
ERROR = "#9b3324"

Mode = Literal["idle", "calibration", "origin", "pad"]


def display_number(value: float) -> str:
    # Display rounding leaves the model and export values untouched.
    return format(value, ".9g")


def ensure_drawable(scale: float, pads: list[Pad]) -> None:
    extent = max(
        (max(abs(pad.x), abs(pad.y)) + pad.width + pad.height for pad in pads),
        default=1.0,
    )
    if not math.isfinite(max(1.0, extent) / scale):
        raise ValueError("この縮尺ではパッドの位置・寸法を表示できません。校正の実距離を確認してください。")


class SketchCanvas(tk.Canvas):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, background=WORKSPACE, highlightthickness=0)
        self.image: Image.Image | None = None
        self.scale = 1.0
        self.offset: Point = (0.0, 0.0)
        self._photo: ImageTk.PhotoImage | None = None
        self._photo_size: tuple[int, int] | None = None

    def set_image(self, image: Image.Image) -> None:
        self.image = image
        self._photo_size = None

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

    def render(self, sketch: Sketch, candidate_points: list[Point]) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        image = self.image
        if image is None:
            self.create_text(
                width / 2,
                height / 2,
                text="PNG または JPEG の下絵を開いてください",
                fill=MUTED,
                font="TkDefaultFont",
            )
            return

        margin = 32
        self.scale = min(
            1.0,
            max(1, width - 2 * margin) / image.width,
            max(1, height - 2 * margin) / image.height,
        )
        self.offset = (
            round((width - image.width * self.scale) / 2),
            round((height - image.height * self.scale) / 2),
        )
        size = (
            max(1, round(image.width * self.scale)),
            max(1, round(image.height * self.scale)),
        )
        if size != self._photo_size:
            resized = image.resize(size, Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(resized, master=self)
            self._photo_size = size
        left, top = self.offset
        self.create_rectangle(
            left - 1, top - 1, left + size[0] + 1, top + size[1] + 1,
            fill=SURFACE, outline=LINE,
        )
        self.create_image(left, top, image=self._photo, anchor="nw")

        calibration = sketch.calibration
        points = candidate_points
        if not points and calibration is not None:
            points = [calibration.a, calibration.b]
        if sketch.origin is not None:
            for pad in sketch.pads:
                self._draw_pad(pad, sketch)
            self._draw_origin(sketch.origin)
        self._draw_calibration(points)

    def _draw_calibration(self, points: list[Point]) -> None:
        screen_points = [self.image_to_canvas(point) for point in points]
        if len(screen_points) == 2:
            self.create_line(*screen_points[0], *screen_points[1], fill=BLUE, dash=(5, 3))
        for label, (x, y) in zip(("A", "B"), screen_points):
            self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=SURFACE, outline=BLUE, width=2)
            self.create_text(x + 8, y - 8, text=label, fill=BLUE, anchor="sw")

    def _draw_origin(self, point: Point) -> None:
        x, y = self.image_to_canvas(point)
        self.create_line(x - 6, y, x + 19, y, fill=BLUE, arrow="last", width=2)
        self.create_line(x, y - 6, x, y + 19, fill=BLUE, arrow="last", width=2)
        self.create_text(x + 22, y, text="X", fill=BLUE, anchor="w")
        self.create_text(x, y + 22, text="Y", fill=BLUE, anchor="n")
        self.create_text(x - 6, y - 6, text="0", fill=BLUE, anchor="se")

    def _draw_pad(self, pad: Pad, sketch: Sketch) -> None:
        calibration = sketch.calibration
        assert calibration is not None
        pixels_per_mm = self.scale / calibration.mm_per_pixel
        cx, cy = self.image_to_canvas(sketch.mm_to_image((pad.x, pad.y)))
        angle = math.radians(pad.angle % 360)
        cos_angle, sin_angle = math.cos(angle), math.sin(angle)
        vertices = [
            (
                cx + (x * cos_angle + y * sin_angle) * pixels_per_mm,
                cy + (y * cos_angle - x * sin_angle) * pixels_per_mm,
            )
            for x, y in pad_outline(pad)
        ]
        # Keep drawing coordinates within the range used by the platform canvas.
        clipped = [
            (max(-30_000, min(x, 30_000)), max(-30_000, min(y, 30_000)))
            for x, y in vertices
        ]
        self.create_polygon(clipped, fill=TEAL, outline=TEAL, width=2, stipple="gray50")
        if 0 <= cx <= self.winfo_width() and 0 <= cy <= self.winfo_height():
            self.create_text(cx, cy, text=pad.number, fill=INK)


def pad_outline(pad: Pad) -> list[Point]:
    left, right = -pad.width / 2, pad.width / 2
    top, bottom = -pad.height / 2, pad.height / 2
    radius = pad.radius
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


class SketcherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.sketch = Sketch()
        self.mode: Mode = "idle"
        self.calibration_points: list[Point] = []

        self.image_name = tk.StringVar(root, "下絵未選択")
        self.distance = tk.StringVar(root)
        self.calibration_label = tk.StringVar(root, "未校正")
        self.pad_number = tk.StringVar(root)
        self.pad_width = tk.StringVar(root)
        self.pad_height = tk.StringVar(root)
        self.pad_angle = tk.StringVar(root, "0")
        self.pad_radius = tk.StringVar(root, "0")
        self.footprint_name = tk.StringVar(root)
        self.status = tk.StringVar(root, "下絵を開いて作業を始めてください。")

        root.title("KiCad Footprint Sketcher")
        root.geometry("1180x760")
        root.minsize(880, 680)
        self._configure_style()
        self._build_layout()
        self._refresh()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        default_family = font.nametofont("TkDefaultFont").actual("family")
        self._font = (default_family, 10)
        style.configure(".", font=self._font)
        style.configure("TFrame", background=SURFACE)
        style.configure("Workspace.TFrame", background=WORKSPACE)
        style.configure("TLabel", background=SURFACE, foreground=INK)
        style.configure("Title.TLabel", font=(default_family, 19, "bold"))
        style.configure("Section.TLabel", font=(default_family, 11, "bold"))
        style.configure("Hint.TLabel", foreground=MUTED)
        style.configure("Status.TLabel", foreground=INK)
        style.configure("Error.TLabel", foreground=ERROR)
        style.configure("TButton", padding=(9, 5), background=SURFACE, foreground=INK)
        style.map("TButton", background=[("pressed", "#dbe5ed"), ("active", "#eef3f6")])
        style.configure("Primary.TButton", padding=(9, 7), background=TEAL, foreground=SURFACE)
        style.map(
            "Primary.TButton",
            background=[("disabled", "#e0e7ec"), ("pressed", "#064a42"), ("active", "#087f70")],
            foreground=[("disabled", MUTED), ("!disabled", SURFACE)],
        )
        style.configure("TEntry", padding=(5, 2), foreground=INK, fieldbackground=SURFACE)
        style.configure("Treeview", rowheight=24, background=SURFACE, foreground=INK)
        style.configure("Treeview.Heading", background=WORKSPACE, foreground=INK)
        style.configure("TSeparator", background=LINE)

    def _build_layout(self) -> None:
        shell = ttk.Frame(self.root, padding=(14, 12), style="Workspace.TFrame")
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell, padding=(14, 9))
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(header, text="Footprint Sketcher", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="KiCad 10.0", style="Hint.TLabel").pack(side="right")

        work = ttk.Frame(shell, padding=10)
        work.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        work.columnconfigure(0, weight=1)
        work.rowconfigure(1, weight=1)
        ttk.Label(work, textvariable=self.image_name, width=1).grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        self.canvas = SketchCanvas(work)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self._build_pad_list(work)

        self._build_sidebar(shell)

        footer = ttk.Frame(shell, padding=(10, 6))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._status_label = ttk.Label(footer, textvariable=self.status)
        self._status_label.pack(fill="x")
        self.root.bind("<Escape>", self._cancel_action)

    def _build_sidebar(self, shell: ttk.Frame) -> None:
        sidebar = ttk.Frame(shell)
        sidebar.grid(row=1, column=1, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(0, weight=1)

        viewport = tk.Canvas(sidebar, background=SURFACE, highlightthickness=0, height=1, width=1)
        viewport.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(sidebar, orient="vertical", command=viewport.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=(10, 0))
        viewport.configure(yscrollcommand=scroll.set)
        panel = ttk.Frame(viewport, padding=(12, 10, 7, 9))
        panel.columnconfigure((0, 1), weight=1)
        window = viewport.create_window(0, 0, anchor="nw", window=panel)
        self._build_controls(panel)

        def fit_panel(_event: tk.Event) -> None:
            viewport.configure(
                width=panel.winfo_reqwidth(),
                scrollregion=(0, 0, panel.winfo_width(), panel.winfo_reqheight()),
            )

        def scroll_controls(event: tk.Event) -> None:
            if event.delta and str(event.widget).startswith(str(viewport)):
                steps = max(1, abs(event.delta) // 120)
                viewport.yview_scroll(-steps if event.delta > 0 else steps, "units")

        def reveal_control(event: tk.Event) -> None:
            widget = event.widget
            if not str(widget).startswith(f"{panel}."):
                return
            top = widget.winfo_rooty() - panel.winfo_rooty()
            bottom = top + widget.winfo_height()
            visible_top = viewport.canvasy(0)
            height = viewport.winfo_height()
            if top < visible_top:
                viewport.yview_moveto(top / panel.winfo_reqheight())
            elif bottom > visible_top + height:
                viewport.yview_moveto((bottom - height) / panel.winfo_reqheight())

        panel.bind("<Configure>", fit_panel)
        viewport.bind("<Configure>", lambda event: viewport.itemconfigure(window, width=event.width))
        self.root.bind("<MouseWheel>", scroll_controls)
        self.root.bind("<FocusIn>", reveal_control)

        export_panel = ttk.Frame(sidebar, padding=(12, 0, 12, 10))
        export_panel.grid(row=1, column=0, columnspan=2, sticky="ew")
        export_panel.columnconfigure((0, 1), weight=1)
        self._section(export_panel, "書き出し", 0)
        self._field(export_panel, "フットプリント名", self.footprint_name, 2, 0, colspan=2)
        self._export_button = ttk.Button(
            export_panel, text=".kicad_mod を書き出す…", command=self.export, style="Primary.TButton"
        )
        self._export_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(7, 0))

    def _build_pad_list(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        frame.columnconfigure(0, weight=1)
        columns = {
            "number": "番号",
            "x": "X (mm)",
            "y": "Y (mm)",
            "width": "幅 (mm)",
            "height": "高さ (mm)",
            "angle": "角度 (°)",
            "radius": "R (mm)",
        }
        self.pad_table = ttk.Treeview(frame, columns=tuple(columns), show="headings", height=3)
        for key, label in columns.items():
            self.pad_table.heading(key, text=label, anchor="center")
            self.pad_table.column(key, width=64, minwidth=45, anchor="center")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.pad_table.yview)
        self.pad_table.configure(yscrollcommand=scroll.set)
        self.pad_table.grid(row=0, column=0, sticky="ew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.pad_table.xview)

        def sync_horizontal(first: str, last: str) -> None:
            horizontal.set(first, last)
            if float(first) > 0 or float(last) < 1:
                horizontal.grid(row=1, column=0, sticky="ew")
            else:
                horizontal.grid_remove()

        self.pad_table.configure(xscrollcommand=sync_horizontal)

    def _build_controls(self, panel: ttk.Frame) -> None:
        ttk.Button(panel, text="下絵を開く…", command=self.open_image).grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        self._section(panel, "校正と原点", 1)
        self._calibration_button = ttk.Button(
            panel, text="校正の2点を選ぶ", command=self.begin_calibration
        )
        self._calibration_button.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._distance_entry = self._field(panel, "実距離 (mm)", self.distance, 4, 0)
        self._distance_entry.bind("<Return>", lambda _event: self.apply_calibration())
        self._apply_button = ttk.Button(panel, text="校正を適用", command=self.apply_calibration)
        self._apply_button.grid(row=5, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(panel, textvariable=self.calibration_label, style="Hint.TLabel").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(5, 5)
        )
        self._origin_button = ttk.Button(panel, text="原点を選ぶ", command=self.begin_origin)
        self._center_button = ttk.Button(panel, text="画像の中央を原点に", command=self.center_origin)
        self._origin_button.grid(row=7, column=0, sticky="ew", padx=(0, 3))
        self._center_button.grid(row=7, column=1, sticky="ew", padx=(3, 0))

        self._section(panel, "表面SMDパッド", 8)
        ttk.Label(panel, text=" / ".join(PAD_LAYERS), style="Hint.TLabel").grid(
            row=10, column=0, columnspan=2, sticky="w"
        )
        self._field(panel, "幅 (mm)", self.pad_width, 11, 0)
        self._field(panel, "高さ (mm)", self.pad_height, 11, 1)
        self._field(panel, "角度 (°・反時計回り)", self.pad_angle, 13, 0)
        self._field(panel, "角のR (mm)", self.pad_radius, 13, 1)
        self._number_entry = self._field(panel, "パッド番号", self.pad_number, 15, 0)
        self._placement_button = ttk.Button(panel, text="パッドを配置", command=self.begin_placement)
        self._placement_button.grid(row=16, column=1, sticky="ew", padx=(3, 0))

    @staticmethod
    def _section(panel: ttk.Frame, title: str, row: int) -> None:
        ttk.Separator(panel).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(9, 6))
        ttk.Label(panel, text=title, style="Section.TLabel").grid(
            row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 5)
        )

    @staticmethod
    def _field(
        panel: ttk.Frame, label: str, variable: tk.StringVar, row: int, column: int, *,
        colspan: int = 1,
    ) -> ttk.Entry:
        padx = (0, 0) if colspan == 2 else ((0, 3) if column == 0 else (3, 0))
        ttk.Label(panel, text=label).grid(
            row=row, column=column, columnspan=colspan, sticky="w", padx=padx, pady=(3, 1)
        )
        entry = ttk.Entry(panel, textvariable=variable, width=15)
        entry.grid(row=row + 1, column=column, columnspan=colspan, sticky="ew", padx=padx)
        return entry

    def _message(self, text: str, *, error: bool = False) -> None:
        self.status.set(text)
        self._status_label.configure(style="Error.TLabel" if error else "Status.TLabel")

    def _refresh(self) -> None:
        calibration = self.sketch.calibration
        can_place = calibration is not None and self.sketch.origin is not None
        for widget, enabled in (
            (self._calibration_button, self.canvas.image is not None),
            (self._apply_button, len(self.calibration_points) == 2),
            (self._origin_button, calibration is not None),
            (self._center_button, calibration is not None),
            (self._placement_button, can_place),
            (self._export_button, bool(self.sketch.pads)),
        ):
            widget.state(["!disabled"] if enabled else ["disabled"])
        self.calibration_label.set(
            "未校正" if calibration is None else
            f"{display_number(calibration.mm_per_pixel)} mm/px  ・  {display_number(calibration.distance)} mm"
        )
        self.canvas.configure(cursor="crosshair" if self.mode != "idle" else "arrow")
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.render(self.sketch, self.calibration_points)

    def _refresh_pad_list(self) -> None:
        rows = self.pad_table.get_children()
        if rows:
            self.pad_table.delete(*rows)
        for pad in self.sketch.pads:
            values = (pad.x, pad.y, pad.width, pad.height, pad.angle, pad.radius)
            item = self.pad_table.insert(
                "", "end", values=(pad.number, *(display_number(value) for value in values))
            )
        if self.sketch.pads:
            self.pad_table.see(item)

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="下絵を開く",
            filetypes=[("PNG / JPEG", ("*.png", "*.jpg", "*.jpeg")), ("すべてのファイル", "*")],
        )
        if path:
            self.load_image(Path(path))

    def load_image(self, path: Path) -> None:
        try:
            with Image.open(path, formats=("PNG", "JPEG")) as source:
                image = source.convert("RGBA")
        except (OSError, ValueError, Image.DecompressionBombError):
            self._message(f"下絵を開けませんでした。PNG・JPEGファイルを確認してください。 {path.name}", error=True)
            return
        if (self.sketch.calibration is not None or self.calibration_points) and not messagebox.askyesno(
            "下絵を切り替え",
            "下絵を切り替えると、現在の校正・原点・パッド配置が消去されます。続けますか？",
            parent=self.root,
        ):
            return

        self.sketch = Sketch()
        self.calibration_points.clear()
        self.mode = "idle"
        self.distance.set("")
        self.canvas.set_image(image)
        self.image_name.set(f"{path.name}  ·  {image.width} × {image.height} px")
        self._message("下絵を表示しました。校正に使う2点を選んでください。")
        self._refresh_pad_list()
        self._refresh()

    def begin_calibration(self) -> None:
        self.calibration_points.clear()
        self.mode = "calibration"
        self._message("寸法が分かっている区間の始点 A をクリックしてください。")
        self._refresh()

    def apply_calibration(self) -> None:
        if len(self.calibration_points) != 2:
            return
        try:
            distance = self._read_number(self.distance, "実距離")
            candidate = Calibration(*self.calibration_points, distance)
            ensure_drawable(candidate.mm_per_pixel, self.sketch.pads)
            self.sketch.set_calibration(*self.calibration_points, distance)
        except ValueError as error:
            self._message(str(error), error=True)
            return

        self.calibration_points.clear()
        self.mode = "idle"
        if self.sketch.origin is None:
            self._message("校正しました。フットプリントの原点を選んでください。")
        else:
            self._message("縮尺を更新しました。配置済みパッドの座標と寸法を保持しています。")
        self._refresh()

    def begin_origin(self) -> None:
        self.mode = "origin"
        self._message("原点にする位置をキャンバスでクリックしてください。")
        self._refresh()

    def center_origin(self) -> None:
        image = self.canvas.image
        if image is not None:
            self._set_origin((image.width / 2, image.height / 2))

    def _set_origin(self, point: Point) -> None:
        try:
            self.sketch.set_origin(point)
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self.mode = "idle"
        self._message("原点を設定しました。パッドの寸法を入力し、配置を始めてください。")
        self._refresh_pad_list()
        self._refresh()

    def begin_placement(self) -> None:
        try:
            self._pad_at((0.0, 0.0))
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self.mode = "pad"
        self._message("パッドの中心をキャンバスでクリックしてください。 Esc で配置を終了します。")
        self._refresh()

    def _pad_at(self, coordinates: Point) -> Pad:
        return Pad(
            number=self.pad_number.get(), x=coordinates[0], y=coordinates[1],
            width=self._read_number(self.pad_width, "幅"),
            height=self._read_number(self.pad_height, "高さ"),
            angle=self._read_number(self.pad_angle, "角度"),
            radius=self._read_number(self.pad_radius, "角のR"),
        )

    @staticmethod
    def _read_number(field: tk.StringVar, label: str) -> float:
        try:
            number = float(field.get())
        except ValueError:
            raise ValueError(f"{label}に数値を入力してください。") from None
        if not math.isfinite(number):
            raise ValueError(f"{label}に有限の数値を入力してください。")
        return number

    def _on_canvas_click(self, event: tk.Event) -> None:
        image = self.canvas.image
        if image is None or self.mode == "idle":
            return
        point = self.canvas.canvas_to_image((event.x, event.y))
        if self.mode == "calibration":
            self._pick_calibration_point(point, image)
        elif self.mode == "origin":
            self._set_origin(point)
        elif self.mode == "pad":
            self._place_pad(point)

    def _pick_calibration_point(self, point: Point, image: Image.Image) -> None:
        if not (0 <= point[0] <= image.width and 0 <= point[1] <= image.height):
            self._message("校正点は下絵の内側をクリックしてください。", error=True)
            return
        if self.calibration_points and self.calibration_points[0] == point:
            self._message("始点と異なる終点 B をクリックしてください。", error=True)
            return
        self.calibration_points.append(point)
        if len(self.calibration_points) == 2:
            self.mode = "idle"
            self._distance_entry.focus_set()
            self._message("2点間の実距離を入力して「校正を適用」を押してください。")
        else:
            self._message("区間の終点 B をクリックしてください。")
        self._refresh()

    def _place_pad(self, point: Point) -> None:
        try:
            pad = self._pad_at(self.sketch.image_to_mm(point))
            calibration = self.sketch.calibration
            assert calibration is not None
            ensure_drawable(calibration.mm_per_pixel, [pad])
            self.sketch.add_pad(pad)
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self.pad_number.set("")
        self._number_entry.focus_set()
        self._message(f"パッド {pad.number} を配置しました。続けて配置する場合は次の番号を入力してください。")
        self._refresh_pad_list()
        self._refresh()

    def _cancel_action(self, _event: tk.Event) -> None:
        if self.mode != "idle" or self.calibration_points:
            self.mode = "idle"
            self.calibration_points.clear()
            self._message("操作を取り消しました。")
            self._refresh()

    def export(self) -> None:
        name = self.footprint_name.get()
        if not name.strip():
            self._message("フットプリント名を入力してください。", error=True)
            return
        directory = filedialog.askdirectory(parent=self.root, title="書き出し先フォルダーを選択")
        if not directory:
            return
        target = Path(directory)
        try:
            try:
                path = export_footprint(target, name, self.sketch.pads)
            except FileExistsError:
                if not messagebox.askyesno(
                    "書き出し先に同名ファイルがあります",
                    f"{target / (name + '.kicad_mod')} を置き換えますか？",
                    parent=self.root,
                ):
                    return
                path = export_footprint(target, name, self.sketch.pads, overwrite=True)
        except (ValueError, OSError) as error:
            self._message(f"書き出しに失敗しました。 {error}", error=True)
            return
        self._message(f"書き出しました。 {path}")


def main() -> None:
    root = tk.Tk()
    SketcherApp(root)
    root.mainloop()
