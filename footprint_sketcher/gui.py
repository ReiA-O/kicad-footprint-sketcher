"""The Stage 1 desktop editor."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk
from typing import Literal

from PIL import Image

from .canvas import SketchCanvas, pad_outline
from .interaction import hit_pad, pad_corners, resize_pad
from .model import PAD_LAYERS, AxisCalibration, Calibration, Pad, Point, Sketch, export_footprint


SURFACE = "#ffffff"
WORKSPACE = "#edf1f6"
INK = "#192636"
MUTED = "#506276"
LINE = "#c8d3df"
TEAL = "#08695e"
BLUE = "#234f9a"
ERROR = "#9b3324"

Mode = Literal["idle", "calibration", "origin", "pad", "draw"]
AXIS_METHOD = "横2点＋縦2点（標準）"
UNIFORM_METHOD = "2点・共通縮尺（補助）"


def minimum_scale(calibration: Calibration | AxisCalibration) -> float:
    return min(calibration.mm_per_pixel_x, calibration.mm_per_pixel_y)


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


class SketcherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.sketch = Sketch()
        self.mode: Mode = "idle"
        self.calibration_points: list[Point] = []
        self.selected_index: int | None = None
        self._drag: dict | None = None
        self._preview_pad: Pad | None = None
        self._pan_start: Point | None = None
        self._draw_shape = "rect"
        self.tool = tk.StringVar(root, "select")
        self.zoom_label = tk.StringVar(root, "100%")

        self.image_name = tk.StringVar(root, "下絵未選択")
        self.distance = tk.StringVar(root)
        self.vertical_distance = tk.StringVar(root)
        self.calibration_method = tk.StringVar(root, AXIS_METHOD)
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
        style.configure("TRadiobutton", background=SURFACE, foreground=INK)
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
        work.rowconfigure(2, weight=1)
        ttk.Label(work, textvariable=self.image_name, width=1).grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        self._build_tools(work)
        self.canvas = SketchCanvas(work)
        self.canvas.grid(row=2, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        self.canvas.bind("<Button-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda event: self._zoom_at(1.2, (event.x, event.y)))
        self.canvas.bind("<Button-5>", lambda event: self._zoom_at(1 / 1.2, (event.x, event.y)))
        self.canvas.bind("<Button-2>", self._begin_pan)
        self.canvas.bind("<B2-Motion>", self._pan)
        self.canvas.bind("<ButtonRelease-2>", self._end_pan)
        self._build_pad_list(work)

        self._build_sidebar(shell)

        footer = ttk.Frame(shell, padding=(10, 6))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._status_label = ttk.Label(footer, textvariable=self.status)
        self._status_label.pack(fill="x")
        self.root.bind("<Escape>", self._cancel_action)

    def _build_tools(self, parent: ttk.Frame) -> None:
        tools = ttk.Frame(parent)
        tools.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Radiobutton(tools, text="選択/移動", variable=self.tool, value="select",
                        command=self.select_tool).pack(side="left")
        self._draw_buttons = []
        for text, shape in (("矩形パッド", "rect"), ("角丸パッド", "roundrect")):
            button = ttk.Radiobutton(tools, text=text, variable=self.tool, value=shape,
                                     command=lambda value=shape: self.begin_draw(value))
            button.pack(side="left", padx=(5, 0))
            self._draw_buttons.append(button)
        ttk.Button(tools, text="全体表示", command=self._fit_view).pack(side="right")
        ttk.Button(tools, text="＋", width=3, command=lambda: self._zoom_at(1.2)).pack(side="right")
        ttk.Label(tools, textvariable=self.zoom_label, width=6, anchor="center").pack(side="right")
        ttk.Button(tools, text="−", width=3, command=lambda: self._zoom_at(1 / 1.2)).pack(side="right")

    def _zoom_at(self, factor: float, anchor: Point | None = None) -> str:
        if anchor is None:
            anchor = (self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2)
        self.canvas.zoom_at(factor, anchor)
        self._redraw()
        return "break"

    def _on_wheel(self, event: tk.Event) -> str:
        if event.delta:
            self._zoom_at(1.2 ** max(-5, min(5, event.delta / 120)), (event.x, event.y))
        return "break"

    def _fit_view(self) -> None:
        self.canvas.fit_to_window()
        self._redraw()

    def _begin_pan(self, event: tk.Event) -> str:
        self._pan_start = (event.x, event.y)
        return "break"

    def _pan(self, event: tk.Event) -> str:
        if self._pan_start is not None:
            self.canvas.pan_by(event.x - self._pan_start[0], event.y - self._pan_start[1])
            self._pan_start = (event.x, event.y)
            self._redraw()
        return "break"

    def _end_pan(self, _event: tk.Event) -> str:
        self._pan_start = None
        return "break"

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
        frame.grid(row=3, column=0, sticky="ew", pady=(9, 0))
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
        self.pad_table.bind("<<TreeviewSelect>>", self._on_table_select)

    def _build_controls(self, panel: ttk.Frame) -> None:
        ttk.Button(panel, text="下絵を開く…", command=self.open_image).grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        self._section(panel, "校正と原点", 1)
        self._method_combo = ttk.Combobox(panel, textvariable=self.calibration_method,
                                         values=(AXIS_METHOD, UNIFORM_METHOD), state="readonly", width=24)
        self._method_combo.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self._method_combo.bind("<<ComboboxSelected>>", self._calibration_mode_changed)
        self._calibration_button = ttk.Button(
            panel, text="横2点＋縦2点を選ぶ", command=self.begin_calibration
        )
        self._calibration_button.grid(row=4, column=0, columnspan=2, sticky="ew")
        self._distance_entry = self._field(panel, "横／共通の実距離 (mm)", self.distance, 5, 0)
        self._vertical_entry = self._field(panel, "縦の実距離 (mm)", self.vertical_distance, 5, 1)
        self._distance_entry.bind("<Return>", lambda _event: self.apply_calibration())
        self._vertical_entry.bind("<Return>", lambda _event: self.apply_calibration())
        self._apply_button = ttk.Button(panel, text="校正を適用", command=self.apply_calibration)
        self._apply_button.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Label(panel, textvariable=self.calibration_label, style="Hint.TLabel").grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(5, 5)
        )
        self._origin_button = ttk.Button(panel, text="原点を選ぶ", command=self.begin_origin)
        self._center_button = ttk.Button(panel, text="画像の中央を原点に", command=self.center_origin)
        self._origin_button.grid(row=9, column=0, sticky="ew", padx=(0, 3))
        self._center_button.grid(row=9, column=1, sticky="ew", padx=(3, 0))

        self._section(panel, "表面SMDパッド", 10)
        ttk.Label(panel, text=" / ".join(PAD_LAYERS), style="Hint.TLabel").grid(
            row=12, column=0, columnspan=2, sticky="w"
        )
        self._field(panel, "幅 (mm)", self.pad_width, 13, 0)
        self._field(panel, "高さ (mm)", self.pad_height, 13, 1)
        self._field(panel, "角度 (°・反時計回り)", self.pad_angle, 15, 0)
        self._field(panel, "角のR (mm)", self.pad_radius, 15, 1)
        self._number_entry = self._field(panel, "パッド番号", self.pad_number, 17, 0)
        self._placement_button = ttk.Button(panel, text="パッドを配置", command=self.begin_placement)
        self._placement_button.grid(row=18, column=1, sticky="ew", padx=(3, 0))
        self._selection_button = ttk.Button(panel, text="選択パッドに適用", command=self.apply_selected)
        self._selection_button.grid(row=19, column=0, columnspan=2, sticky="ew", pady=(7, 0))

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
            (self._apply_button, len(self.calibration_points) == (4 if self._axis_method() else 2)),
            (self._vertical_entry, self._axis_method()),
            (self._origin_button, calibration is not None),
            (self._center_button, calibration is not None),
            (self._placement_button, can_place),
            (self._export_button, bool(self.sketch.pads)),
            (self._selection_button,
             self.mode == "idle" and self._drag is None and self.selected_index is not None),
            *[(button, can_place) for button in self._draw_buttons],
        ):
            widget.state(["!disabled"] if enabled else ["disabled"])
        self._calibration_button.configure(text="横2点＋縦2点を選ぶ" if self._axis_method() else "校正の2点を選ぶ")
        if calibration is None:
            self.calibration_label.set("未校正")
        elif isinstance(calibration, AxisCalibration):
            self.calibration_label.set(f"X: {display_number(calibration.mm_per_pixel_x)} mm/px  ・  {display_number(calibration.x_distance)} mm\n"
                                       f"Y: {display_number(calibration.mm_per_pixel_y)} mm/px  ・  {display_number(calibration.y_distance)} mm")
        else:
            self.calibration_label.set(f"{display_number(calibration.mm_per_pixel)} mm/px  ・  {display_number(calibration.distance)} mm")
        self.canvas.configure(cursor="crosshair" if self.mode != "idle" else "arrow")
        self._redraw()

    def _redraw(self) -> None:
        drawing_new = self._drag is not None and self._drag["kind"] == "draw"
        self.canvas.render(self.sketch, self.calibration_points,
                           selected_index=None if drawing_new else self.selected_index,
                           preview_pad=self._preview_pad,
                           calibration_axes=self._axis_method())
        self.zoom_label.set(f"{self.canvas.scale * 100:.0f}%")

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
        if self.selected_index is not None and self.selected_index < len(self.sketch.pads):
            self.pad_table.selection_set(self.pad_table.get_children()[self.selected_index])

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
        self.selected_index = None
        self._drag = None
        self._preview_pad = None
        self.tool.set("select")
        self.calibration_points.clear()
        self.mode = "idle"
        self.distance.set("")
        self.vertical_distance.set("")
        self.canvas.set_image(image)
        self.image_name.set(f"{path.name}  ·  {image.width} × {image.height} px")
        self._message("下絵を表示しました。校正方式を選び、基準となる点を指定してください。")
        self._refresh_pad_list()
        self._refresh()

    def _axis_method(self) -> bool:
        return self.calibration_method.get() == AXIS_METHOD

    def _calibration_mode_changed(self, _event: tk.Event | None = None) -> None:
        self._drag = None
        self._preview_pad = None
        self.calibration_points.clear()
        self.mode = "idle"
        self.tool.set("select")
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._message("校正方式を切り替えました。新しい基準点を選んで適用してください。")
        self._refresh()

    def begin_calibration(self) -> None:
        self._drag = None
        self._preview_pad = None
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self.calibration_points.clear()
        self.mode = "calibration"
        self.tool.set("")
        self._message("横寸法の始点 X1 をクリックしてください。" if self._axis_method() else
                      "寸法が分かっている区間の始点 A をクリックしてください。")
        self._refresh()

    def apply_calibration(self) -> None:
        if len(self.calibration_points) != (4 if self._axis_method() else 2):
            return
        try:
            distance = self._read_number(self.distance, "実距離")
            if self._axis_method():
                vertical = self._read_number(self.vertical_distance, "縦の実距離")
                a, b, c, d = self.calibration_points
                candidate = AxisCalibration(a, b, distance, c, d, vertical)
                ensure_drawable(minimum_scale(candidate), self.sketch.pads)
                self.sketch.set_axis_calibration(a, b, distance, c, d, vertical)
            else:
                candidate = Calibration(*self.calibration_points, distance)
                ensure_drawable(minimum_scale(candidate), self.sketch.pads)
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
        self._drag = None
        self._preview_pad = None
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self.mode = "origin"
        self.tool.set("")
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
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
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
        self.tool.set("")
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

    def select_tool(self) -> None:
        self.mode = "idle"
        self.tool.set("select")
        self._drag = None
        self._preview_pad = None
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._message("パッドを選択してドラッグで移動、四隅のハンドルでサイズ変更できます。")
        self._refresh()

    def _next_pad_number(self, after: str | None = None) -> str:
        used = {pad.number for pad in self.sketch.pads}
        try:
            number = int(after) + 1 if after is not None and after.isdecimal() else 1
        except ValueError:
            # Very long numeric identifiers can exceed Python's int limit.
            number = 1
        while str(number) in used:
            number += 1
        return str(number)

    def begin_draw(self, shape: str) -> None:
        if self.sketch.calibration is None or self.sketch.origin is None:
            return
        self._drag = None
        self._preview_pad = None
        self.selected_index = None
        selection = self.pad_table.selection()
        if selection:
            self.pad_table.selection_remove(selection)
        self._draw_shape = shape
        self.mode = "draw"
        self.tool.set(shape)
        number = self._next_pad_number()
        self.pad_number.set(number)
        self.pad_angle.set("0")
        if shape == "rect":
            self.pad_radius.set("0")
        else:
            try:
                radius = self._read_number(self.pad_radius, "角のR")
            except ValueError:
                radius = 0
            if radius <= 0:
                self.pad_radius.set("0.1")
        self._message(f"パッド {number} の対角をドラッグしてください。番号・角度・Rは右の欄で指定できます。")
        self._refresh()

    def _fill_pad_fields(self, pad: Pad) -> None:
        for variable, value in ((self.pad_number, pad.number), (self.pad_width, pad.width),
                                (self.pad_height, pad.height), (self.pad_angle, pad.angle),
                                (self.pad_radius, pad.radius)):
            variable.set(str(value))

    def _on_table_select(self, _event: tk.Event) -> None:
        selection = self.pad_table.selection()
        if selection and self.mode == "idle" and self._drag is None:
            children = self.pad_table.get_children()
            if selection[0] in children:
                self.selected_index = children.index(selection[0])
                self._fill_pad_fields(self.sketch.pads[self.selected_index])
                self._refresh()

    def apply_selected(self) -> None:
        if self.mode != "idle" or self._drag is not None or self.selected_index is None:
            return
        original = self.sketch.pads[self.selected_index]
        try:
            candidate = self._pad_at((original.x, original.y))
            ensure_drawable(minimum_scale(self.sketch.calibration), [candidate])
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self.sketch.pads[self.selected_index] = candidate
        self._refresh_pad_list()
        self._refresh()
        self._message(f"パッド {candidate.number} の寸法・属性を更新しました。")

    def _event_mm(self, event: tk.Event) -> Point:
        return self.sketch.image_to_mm(self.canvas.canvas_to_image((event.x, event.y)))

    def _on_canvas_press(self, event: tk.Event) -> None:
        if self.mode in ("calibration", "origin", "pad"):
            self._on_canvas_click(event)
            return
        if self.sketch.calibration is None or self.sketch.origin is None:
            return
        point = self._event_mm(event)
        if self.mode == "draw":
            self._drag = {"kind": "draw", "start": point, "screen_start": (event.x, event.y)}
            return
        if self.selected_index is not None:
            pad = self.sketch.pads[self.selected_index]
            for corner, position in enumerate(pad_corners(pad)):
                screen = self.canvas.image_to_canvas(self.sketch.mm_to_image(position))
                if math.dist(screen, (event.x, event.y)) <= 8:
                    self._drag = {"kind": "resize", "start": point, "original": pad, "corner": corner,
                                  "screen_start": (event.x, event.y)}
                    return
        self.selected_index = next((i for i in range(len(self.sketch.pads) - 1, -1, -1)
                                    if hit_pad(self.sketch.pads[i], point)), None)
        if self.selected_index is not None:
            pad = self.sketch.pads[self.selected_index]
            self._fill_pad_fields(pad)
            self._drag = {"kind": "move", "start": point, "original": pad,
                          "screen_start": (event.x, event.y)}
            self._message(f"パッド {pad.number} を選択しました。ドラッグで移動、四隅でサイズ変更できます。")
        else:
            self._drag = None
            selection = self.pad_table.selection()
            if selection:
                self.pad_table.selection_remove(selection)
        self._refresh()

    def _drag_candidate(self, point: Point) -> Pad:
        drag = self._drag
        assert drag is not None
        if drag["kind"] == "move":
            pad = drag["original"]
            return replace(pad, x=pad.x + (point[0] - drag["start"][0]),
                           y=pad.y + (point[1] - drag["start"][1]))
        if drag["kind"] == "resize":
            corner = pad_corners(drag["original"])[drag["corner"]]
            target = (corner[0] + (point[0] - drag["start"][0]),
                      corner[1] + (point[1] - drag["start"][1]))
            return resize_pad(drag["original"], drag["corner"], target)
        start = drag["start"]
        angle = self._read_number(self.pad_angle, "角度")
        radians = math.radians(angle % 360)
        dx, dy = point[0] - start[0], point[1] - start[1]
        width = abs(dx * math.cos(radians) - dy * math.sin(radians))
        height = abs(dx * math.sin(radians) + dy * math.cos(radians))
        radius = 0.0 if self._draw_shape == "rect" else self._read_number(self.pad_radius, "角のR")
        if radius < 0 or (self._draw_shape == "roundrect" and radius == 0):
            raise ValueError("角丸パッドのRには正の値を指定してください。")
        return Pad(self.pad_number.get(), (start[0] + point[0]) / 2, (start[1] + point[1]) / 2,
                   width, height, angle, min(radius, width / 2, height / 2))

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        try:
            candidate = self._drag_candidate(self._event_mm(event))
            ensure_drawable(minimum_scale(self.sketch.calibration), [candidate])
            self._preview_pad = candidate
        except ValueError:
            self._preview_pad = None
        self._redraw()

    def _on_canvas_release(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        drag = self._drag
        try:
            if drag["kind"] == "draw" and math.dist((event.x, event.y), drag["screen_start"]) < 3:
                raise ValueError("対角にドラッグしてパッドの幅と高さを指定してください。")
            if drag["kind"] != "draw" and (event.x, event.y) == drag["screen_start"]:
                candidate = drag["original"]
            else:
                candidate = self._drag_candidate(self._event_mm(event))
            ensure_drawable(minimum_scale(self.sketch.calibration), [candidate])
        except ValueError as error:
            self._message(str(error), error=True)
        else:
            if drag["kind"] == "draw":
                self.sketch.add_pad(candidate)
                self.selected_index = len(self.sketch.pads) - 1
                self._fill_pad_fields(candidate)
                number = self._next_pad_number(candidate.number)
                self.pad_number.set(number)
                self.mode = "draw"
                self.tool.set(self._draw_shape)
                self._message(
                    f"パッド {candidate.number} を配置しました。続けてパッド {number} の対角をドラッグできます。"
                    " Esc で選択/移動へ戻ります。"
                )
            else:
                self.sketch.pads[self.selected_index] = candidate
                self.mode = "idle"
                self.tool.set("select")
                self._fill_pad_fields(candidate)
                if candidate == drag["original"]:
                    self._message(f"パッド {candidate.number} を選択しました。四隅でサイズ変更できます。")
                else:
                    self._message(f"パッド {candidate.number} を更新しました。寸法とRは右の欄で確認できます。")
        finally:
            self._drag = None
            self._preview_pad = None
            self._refresh_pad_list()
            self._refresh()

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
        count = len(self.calibration_points)
        if self._axis_method() and count == 1:
            point = (point[0], self.calibration_points[0][1])
        elif self._axis_method() and count == 3:
            point = (self.calibration_points[2][0], point[1])
        if not (0 <= point[0] <= image.width and 0 <= point[1] <= image.height):
            self._message("校正点は下絵の内側をクリックしてください。", error=True)
            return
        if count in (1, 3) and self.calibration_points[-1] == point:
            self._message("同じ位置では校正できません。固定された軸上の異なる終点を選んでください。", error=True)
            return
        self.calibration_points.append(point)
        total = 4 if self._axis_method() else 2
        if len(self.calibration_points) == total:
            self.mode = "idle"
            self._distance_entry.focus_set()
            self._message("横・縦それぞれの実距離をmmで入力して「校正を適用」を押してください。" if self._axis_method() else
                          "2点間の実距離を入力して「校正を適用」を押してください。")
        elif self._axis_method():
            self._message({1: "横寸法の終点 X2 を選んでください。X1から水平に固定されます。",
                           2: "縦寸法の始点 Y1 をクリックしてください。",
                           3: "縦寸法の終点 Y2 を選んでください。Y1から垂直に固定されます。"}[len(self.calibration_points)])
        else:
            self._message("区間の終点 B をクリックしてください。")
        self._refresh()

    def _place_pad(self, point: Point) -> None:
        try:
            pad = self._pad_at(self.sketch.image_to_mm(point))
            calibration = self.sketch.calibration
            assert calibration is not None
            ensure_drawable(minimum_scale(calibration), [pad])
            self.sketch.add_pad(pad)
            self.selected_index = None
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self.pad_number.set("")
        self._number_entry.focus_set()
        self._message(f"パッド {pad.number} を配置しました。続けて配置する場合は次の番号を入力してください。")
        self._refresh_pad_list()
        self._refresh()

    def _cancel_action(self, _event: tk.Event) -> None:
        if self.mode != "idle" or self.calibration_points or self._drag:
            self._drag = None
            self._preview_pad = None
            self.tool.set("select")
            self.mode = "idle"
            self.calibration_points.clear()
            if self.selected_index is not None:
                self._fill_pad_fields(self.sketch.pads[self.selected_index])
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
