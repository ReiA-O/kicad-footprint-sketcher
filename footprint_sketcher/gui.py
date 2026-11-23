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
from .editing import Snapshot, UndoHistory, align_centers, distribute_centers, duplicate_pads, mirrored_copies
from .compound import CustomPad, merge_pads, fillet_pad
from .model import PAD_LAYERS, AxisCalibration, Calibration, Pad, Point, Sketch, export_footprint


SURFACE = "#ffffff"
WORKSPACE = "#edf1f6"
INK = "#192636"
MUTED = "#506276"
LINE = "#c8d3df"
TEAL = "#08695e"
BLUE = "#234f9a"
ERROR = "#9b3324"

Mode = Literal["idle", "calibration", "origin", "pad", "draw", "paste", "fillet"]
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
        self._selected_index: int | None = None
        self.selected_indices: set[int] = set()
        self.history = UndoHistory()
        self.clipboard: tuple[Pad, ...] = ()
        self._drag: dict | None = None
        self._preview_pad: Pad | None = None
        self._preview_pads: dict[int, Pad] = {}
        self._paste_previews: list[Pad] = []
        self._snap_guides: list[tuple[str, float]] = []
        self._number_editor: ttk.Entry | None = None
        self._pan_start: Point | None = None
        self._draw_shape = "rect"
        self.tool = tk.StringVar(root, "select")
        self.zoom_label = tk.StringVar(root, "100%")
        self.center_snap = tk.BooleanVar(root, True)
        self.selection_label = tk.StringVar(root, "選択 0個")
        self.selected_number = tk.StringVar(root)
        self.merge_number = tk.StringVar(root)
        self.fillet_radius = tk.StringVar(root, "0.25")

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

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    @selected_index.setter
    def selected_index(self, index: int | None) -> None:
        chosen = {index} if index is not None else set()
        if chosen != self.selected_indices:
            self.merge_number.set("")
        self._selected_index = index
        self.selected_indices = chosen

    def _set_selection(self, indices, primary: int | None = None) -> None:
        chosen = {index for index in indices if 0 <= index < len(self.sketch.pads)}
        if chosen != self.selected_indices:
            self.merge_number.set("")
        self.selected_indices = chosen
        self._selected_index = (primary if primary in self.selected_indices else
                                max(self.selected_indices) if self.selected_indices else None)

    def _record_change(self, before: Snapshot) -> None:
        if Snapshot.capture(self.sketch) != before:
            self.history.checkpoint(before)

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
        self._undo_button = ttk.Button(header, text="1操作戻す", command=self.undo)
        self._undo_button.pack(side="left", padx=(20, 4))
        self._copy_button = ttk.Button(header, text="コピー", command=self.copy_selected)
        self._copy_button.pack(side="left", padx=4)
        self._paste_button = ttk.Button(header, text="貼り付け", command=self.begin_paste)
        self._paste_button.pack(side="left", padx=4)
        ttk.Label(header, text="選択番号").pack(side="left", padx=(10, 3))
        self._selected_number_entry = ttk.Entry(header, textvariable=self.selected_number, width=7)
        self._selected_number_entry.pack(side="left")
        self._selected_number_entry.bind("<Return>", self.rename_selected)
        self._renumber_button = ttk.Button(header, text="番号を変更", command=self.rename_selected)
        self._renumber_button.pack(side="left", padx=4)
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
        self.canvas.bind("<Button-3>", self._on_secondary_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda event: self._zoom_at(1.2, (event.x, event.y)))
        self.canvas.bind("<Button-5>", lambda event: self._zoom_at(1 / 1.2, (event.x, event.y)))
        self.canvas.bind("<Button-2>", self._begin_pan)
        self.canvas.bind("<B2-Motion>", self._pan)
        self.canvas.bind("<ButtonRelease-2>", self._end_pan)
        self._build_pad_list(work)
        self._build_alignment_tools(work)

        self._build_sidebar(shell)

        footer = ttk.Frame(shell, padding=(10, 6))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._status_label = ttk.Label(footer, textvariable=self.status)
        self._status_label.pack(fill="x")
        self.root.bind("<Escape>", self._cancel_action)
        self.root.bind("<Control-z>", lambda event: self._shortcut(event, self.undo))
        self.root.bind("<Control-c>", lambda event: self._shortcut(event, self.copy_selected))
        self.root.bind("<Control-v>", lambda event: self._shortcut(event, self.begin_paste))

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
        self._numeric_tool = ttk.Radiobutton(tools, text="数値サイズで配置", variable=self.tool,
                                            value="pad", command=self.begin_placement)
        self._numeric_tool.pack(side="left", padx=(5, 0))
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
        frame.grid(row=4, column=0, sticky="ew", pady=(9, 0))
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
        self.pad_table = ttk.Treeview(frame, columns=tuple(columns), show="headings", height=3,
                                     selectmode="extended")
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
        self.pad_table.bind("<Button-1>", self._on_table_press)
        self.pad_table.bind("<Double-Button-1>", self._on_table_double_click)

    def _build_mirror_tools(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=30, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        bar.columnconfigure((0, 1), weight=1)
        self._mirror_buttons = []
        for column, (axis, text) in enumerate((("x", "X軸で反転コピー"), ("y", "Y軸で反転コピー"))):
            button = ttk.Button(bar, text=text, command=lambda value=axis: self.mirror_selected(value))
            button.grid(row=0, column=column, sticky="ew", padx=(0, 3) if column == 0 else (3, 0))
            self._mirror_buttons.append(button)
        ttk.Label(bar, text="原点を通る軸・同じレイヤー", style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))

    def _build_alignment_tools(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self._align_buttons = []
        for column, (text, axis, spacing) in enumerate((
            ("横中心揃え", "y", False), ("縦中心揃え", "x", False),
            ("横等間隔", "x", True), ("縦等間隔", "y", True),
        )):
            button = ttk.Button(bar, text=text, command=lambda a=axis, s=spacing: self.align_selected(a, s))
            button.grid(row=0, column=column, padx=(0, 3))
            self._align_buttons.append((button, 3 if spacing else 2))
        self._snap_check = ttk.Checkbutton(bar, text="中心スナップ", variable=self.center_snap)
        self._snap_check.grid(row=0, column=4, padx=4)
        ttk.Label(bar, textvariable=self.selection_label).grid(row=0, column=5, padx=4)
        def fit(event):
            compact = event.width < 650
            for index, (button, _) in enumerate(self._align_buttons):
                button.grid_configure(row=index // 2 if compact else 0, column=index % 2 if compact else index)
            for offset, widget in enumerate(bar.winfo_children()[4:]):
                widget.grid_configure(row=0, column=(2 if compact else 4) + offset)
        bar.bind("<Configure>", fit)

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
        self._width_entry = self._field(panel, "幅 (mm)", self.pad_width, 13, 0)
        self._height_entry = self._field(panel, "高さ (mm)", self.pad_height, 13, 1)
        self._angle_entry = self._field(panel, "角度 (°・反時計回り)", self.pad_angle, 15, 0)
        self._radius_entry = self._field(panel, "角のR (mm)", self.pad_radius, 15, 1)
        self._number_entry = self._field(panel, "パッド番号", self.pad_number, 17, 0)
        self._placement_button = ttk.Button(panel, text="パッドを配置", command=self.begin_placement)
        self._placement_button.grid(row=18, column=1, sticky="ew", padx=(3, 0))
        self._selection_button = ttk.Button(panel, text="選択パッドに適用", command=self.apply_selected)
        self._selection_button.grid(row=19, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        for entry in (self._width_entry, self._height_entry, self._angle_entry,
                      self._radius_entry, self._number_entry):
            entry.bind("<Return>", self.apply_pad_fields)
        self._section(panel, "形状の合体・フィレット", 20)
        self._merge_entry = self._field(panel, "合体後の端子番号", self.merge_number, 22, 0)
        self._merge_entry.bind("<Return>", lambda _event: self.merge_selected() or "break")
        self._merge_button = ttk.Button(panel, text="選択図形を合体", command=self.merge_selected)
        self._merge_button.grid(row=23, column=1, sticky="ew", padx=(3, 0))
        self._fillet_radius_entry = self._field(panel, "フィレットR (mm)", self.fillet_radius, 24, 0)
        self._fillet_radius_entry.bind("<Return>", self.fillet_radius_enter)
        self._fillet_button = ttk.Button(panel, text="角を選んでフィレット", command=self.begin_fillet)
        self._fillet_button.grid(row=25, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(panel, text="銅箔・Maskは合体、Pasteは元の開口を保持。\n金色の点線はPaste、十字は接続点です。\n合体後の寸法変更はUndoで合体前に戻します。",
                  style="Hint.TLabel").grid(row=26, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._section(panel, "反転コピー", 28)
        self._build_mirror_tools(panel)

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
        has_custom = any(isinstance(self.sketch.pads[index], CustomPad) for index in self.selected_indices)
        for widget, enabled in (
            (self._undo_button, self.history.can_undo or bool(self.calibration_points) or self._drag is not None),
            (self._copy_button, bool(self.selected_indices)),
            (self._renumber_button, len(self.selected_indices) == 1),
            (self._selected_number_entry, len(self.selected_indices) == 1),
            (self._paste_button, can_place and bool(self.clipboard)),
            (self._calibration_button, self.canvas.image is not None),
            (self._apply_button, len(self.calibration_points) == (4 if self._axis_method() else 2)),
            (self._vertical_entry, self._axis_method()),
            (self._origin_button, calibration is not None),
            (self._center_button, calibration is not None),
            (self._placement_button, can_place),
            (self._numeric_tool, can_place),
            (self._export_button, bool(self.sketch.pads)),
            (self._selection_button,
             self.mode == "idle" and self._drag is None and self.selected_index is not None
             and not (has_custom and len(self.selected_indices) > 1)),
            (self._merge_button, self.mode == "idle" and len(self.selected_indices) >= 2),
            (self._fillet_button, self.mode == "idle" and len(self.selected_indices) == 1 and has_custom),
            *[(button, can_place) for button in self._draw_buttons],
            *[(button, self.mode == "idle" and len(self.selected_indices) >= minimum)
              for button, minimum in self._align_buttons],
            *[(button, self.mode == "idle" and bool(self.selected_indices)) for button in self._mirror_buttons],
        ):
            widget.state(["!disabled"] if enabled else ["disabled"])
        self._number_entry.state(["disabled"] if len(self.selected_indices) > 1 else ["!disabled"])
        for entry in (self._width_entry, self._height_entry, self._radius_entry):
            entry.state(["disabled"] if has_custom and self.mode in ("idle", "fillet") else ["!disabled"])
        self.selection_label.set(f"選択 {len(self.selected_indices)}個")
        self.selected_number.set(self.sketch.pads[self.selected_index].number
                                 if len(self.selected_indices) == 1 else "")
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
        drawing_new = (self._drag is not None and self._drag["kind"] == "draw") or self.mode == "paste"
        self.canvas.render(self.sketch, self.calibration_points,
                           selected_index=None if drawing_new else self.selected_index,
                           preview_pad=self._preview_pad,
                           calibration_axes=self._axis_method(),
                           selected_indices=[] if drawing_new else sorted(self.selected_indices),
                           preview_pads=self._preview_pads,
                           new_previews=self._paste_previews, guides=self._snap_guides)
        if self._drag is not None and self._drag["kind"] == "marquee":
            a = self.canvas.image_to_canvas(self.sketch.mm_to_image(self._drag["start"]))
            b = self.canvas.image_to_canvas(self.sketch.mm_to_image(self._drag["end"]))
            self.canvas.create_rectangle(*a, *b, outline=BLUE, width=2, dash=(4, 3),
                                         tags=("selection-box",))
        if self.mode == "fillet" and self.selected_index is not None:
            pad = self.sketch.pads[self.selected_index]
            if isinstance(pad, CustomPad):
                for index, (x, y) in enumerate(self._corner_screens(pad)):
                    self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=SURFACE,
                                            outline=BLUE, width=2, tags=("fillet-corner", f"fillet-corner-{index}"))
        self.zoom_label.set(f"{self.canvas.scale * 100:.0f}%")

    def _refresh_pad_list(self) -> None:
        self._close_number_editor()
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
        children = self.pad_table.get_children()
        if self.selected_indices:
            self.pad_table.selection_set([children[index] for index in sorted(self.selected_indices)])
            if self.selected_index is not None:
                self.pad_table.focus(children[self.selected_index])
                self.pad_table.see(children[self.selected_index])

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
        self.history.clear()
        self.selected_index = None
        self._drag = None
        self._preview_pad = None
        self._preview_pads.clear()
        self._paste_previews.clear()
        self._snap_guides.clear()
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
        self._clear_preview()
        self.calibration_points.clear()
        self.mode = "idle"
        self.tool.set("select")
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._message("校正方式を切り替えました。新しい基準点を選んで適用してください。")
        self._refresh()

    def begin_calibration(self) -> None:
        self._clear_preview()
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
        before = Snapshot.capture(self.sketch)
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

        self._record_change(before)

        self.calibration_points.clear()
        self.mode = "idle"
        if self.sketch.origin is None:
            self._message("校正しました。フットプリントの原点を選んでください。")
        else:
            self._message("縮尺を更新しました。配置済みパッドの座標と寸法を保持しています。")
        self._refresh()

    def begin_origin(self) -> None:
        self._clear_preview()
        self.calibration_points.clear()
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
        before = Snapshot.capture(self.sketch)
        try:
            self.sketch.set_origin(point)
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self._clear_preview()
        self.calibration_points.clear()
        self.mode = "idle"
        self.tool.set("select")
        self._record_change(before)
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._message("原点を設定しました。パッドの寸法を入力し、配置を始めてください。")
        self._refresh_pad_list()
        self._refresh()

    def begin_placement(self) -> None:
        if self.sketch.calibration is None or self.sketch.origin is None:
            self._message("先に校正を適用し、原点を指定してください。", error=True)
            return
        previous_number = self.pad_number.get()
        if not self.pad_number.get() or (self.mode == "idle" and self.selected_index is not None
                                        and self.pad_number.get() == self.sketch.pads[self.selected_index].number):
            self.pad_number.set(self._next_pad_number())
        try:
            self._pad_at((0.0, 0.0))
        except ValueError as error:
            self.pad_number.set(previous_number)
            self._message(str(error), error=True)
            self.tool.set({"draw": self._draw_shape, "pad": "pad", "idle": "select"}.get(self.mode, ""))
            self._width_entry.focus_set()
            return
        self.calibration_points.clear()
        self._clear_preview()
        self.selected_index = None
        selection = self.pad_table.selection()
        if selection:
            self.pad_table.selection_remove(selection)
        self.mode = "pad"
        self.tool.set("pad")
        self._message("入力した幅・高さ・角度・Rで、中心をクリックして連続配置できます。Escで終了します。")
        self._refresh()

    def apply_pad_fields(self, event: tk.Event) -> str:
        if self.mode == "idle" and self.selected_index is not None:
            self.apply_selected()
            return "break"
        if self.mode == "draw" and event.widget not in (self._width_entry, self._height_entry):
            try:
                angle = self._read_number(self.pad_angle, "角度")
                radius = self._read_number(self.pad_radius, "角のR")
                if radius < 0 or (self._draw_shape == "roundrect" and radius == 0):
                    raise ValueError("角丸パッドのRには正の値を指定してください。")
                Pad(self.pad_number.get(), 0, 0, 1, 1, angle, 0)
            except ValueError as error:
                self._message(str(error), error=True)
                return "break"
            self._message("番号・角度・Rの入力値を確認しました。ドラッグ作成を続けられます。")
            self._refresh()
            return "break"
        if self.mode in ("idle", "pad", "draw"):
            self.begin_placement()
            return "break"
        self._message("配置済みパッドの数値を変更する場合は、選択/移動に切り替えて選択してください。")
        return "break"

    def fillet_radius_enter(self, _event: tk.Event) -> str:
        try:
            if self._read_number(self.fillet_radius, "フィレットR") <= 0:
                raise ValueError("フィレットRには正の値をmmで指定してください。")
        except ValueError as error:
            self._message(str(error), error=True)
            return "break"
        if self.selected_index is None or len(self.selected_indices) != 1 or not isinstance(self.sketch.pads[self.selected_index], CustomPad):
            self._message("合体パッドを1個選択してから、丸める角を選んでください。", error=True)
            return "break"
        self.begin_fillet()
        return "break"

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
        self.calibration_points.clear()
        self._clear_preview()
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._message("空白をドラッグで範囲選択。Ctrl+クリックで追加・解除、パッドをドラッグで移動、Shiftで水平/垂直に固定できます。")
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
        self.calibration_points.clear()
        self._clear_preview()
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
        if self.mode == "idle" and self._drag is None:
            children = self.pad_table.get_children()
            indices = {children.index(item) for item in selection if item in children}
            focus = self.pad_table.focus()
            primary = children.index(focus) if focus in selection else max(indices) if indices else None
            if indices != self.selected_indices or primary != self.selected_index:
                self._set_selection(indices, primary)
                if self.selected_index is not None:
                    self._fill_pad_fields(self.sketch.pads[self.selected_index])
                self._refresh()

    def _on_table_press(self, _event: tk.Event) -> None:
        if self.mode != "idle":
            self.select_tool()

    def _on_table_double_click(self, event: tk.Event) -> str | None:
        item = self.pad_table.identify_row(event.y)
        if not item or self.pad_table.identify_column(event.x) != "#1":
            return None
        index = self.pad_table.get_children().index(item)
        self._set_selection([index], index)
        self.pad_table.selection_set(item)
        self.pad_table.focus(item)
        self.begin_renumber()
        return "break"

    def _close_number_editor(self) -> None:
        editor, self._number_editor = self._number_editor, None
        if editor is not None:
            editor.destroy()

    def rename_selected(self, _event: tk.Event | None = None) -> str:
        if len(self.selected_indices) != 1 or self.selected_index is None:
            return "break"
        index = self.selected_index
        original = self.sketch.pads[index]
        try:
            candidate = replace(original, number=self.selected_number.get())
        except ValueError as error:
            self._message(str(error), error=True)
            return "break"
        before = Snapshot.capture(self.sketch)
        self.select_tool()
        self.sketch.pads[index] = candidate
        self._record_change(before)
        self._fill_pad_fields(candidate)
        self._refresh_pad_list()
        self._refresh()
        self.canvas.focus_set()
        self._message(f"パッド番号を {candidate.number} に変更しました。")
        return "break"

    def begin_renumber(self) -> None:
        if len(self.selected_indices) != 1 or self.selected_index is None:
            return
        self._close_number_editor()
        self.select_tool()
        index = self.selected_index
        original = self.sketch.pads[index]
        item = self.pad_table.get_children()[index]
        self.pad_table.see(item)
        self.root.update_idletasks()
        bounds = self.pad_table.bbox(item, "number")
        if not bounds:
            return
        x, y, width, height = bounds
        editor = ttk.Entry(self.pad_table)
        editor.insert(0, original.number)
        editor.place(x=x, y=y, width=width, height=height)
        self._number_editor = editor

        def commit(_event: tk.Event) -> str:
            if index >= len(self.sketch.pads) or self.sketch.pads[index] != original:
                self._close_number_editor()
                return "break"
            try:
                candidate = replace(original, number=editor.get())
            except ValueError as error:
                self._message(str(error), error=True)
                return "break"
            before = Snapshot.capture(self.sketch)
            self.sketch.pads[index] = candidate
            self._close_number_editor()
            self._record_change(before)
            self._fill_pad_fields(candidate)
            self._refresh_pad_list()
            self._refresh()
            self.canvas.focus_set()
            self._message(f"パッド番号を {candidate.number} に変更しました。")
            return "break"

        def cancel(_event: tk.Event) -> str:
            self._close_number_editor()
            self.canvas.focus_set()
            self._message("番号の変更を取り消しました。")
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<Escape>", cancel)
        editor.bind("<FocusOut>", lambda _event: self._close_number_editor())
        editor.focus_set()
        editor.selection_range(0, "end")
        self._message("番号を入力してEnterで確定、Escまたは別の場所をクリックすると取消します。")

    def mirror_selected(self, axis: str) -> None:
        if self.mode != "idle" or not self.selected_indices:
            return
        before = Snapshot.capture(self.sketch)
        try:
            copies = mirrored_copies([self.sketch.pads[index] for index in sorted(self.selected_indices)],
                                     self.sketch.pads, axis)
            ensure_drawable(minimum_scale(self.sketch.calibration), copies)
        except ValueError as error:
            self._message(str(error), error=True)
            return
        first = len(self.sketch.pads)
        self.sketch.pads.extend(copies)
        self._set_selection(range(first, len(self.sketch.pads)))
        self._record_change(before)
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._refresh_pad_list()
        self._refresh()
        self._message(f"原点を通る{axis.upper()}軸で反転コピーしました。元のパッドとレイヤーを保持し、新番号を付けています。")

    def _clear_preview(self) -> None:
        self._drag = None
        self._preview_pad = None
        self._preview_pads.clear()
        self._paste_previews.clear()
        self._snap_guides.clear()

    def _shortcut(self, event: tk.Event, action) -> str | None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text)):
            return None
        action()
        return "break"

    def undo(self) -> None:
        if self._drag is not None:
            self._cancel_action(None)
            return
        if self.calibration_points:
            self.calibration_points.pop()
            self.mode = "calibration"
            self._message("最後の校正点を戻しました。基準点を選び直してください。")
            self._refresh()
            return
        previous = self.history.undo(Snapshot.capture(self.sketch))
        if previous is None:
            return
        active_mode = self.mode
        previous.restore(self.sketch)
        self.selected_index = None
        self._clear_preview()
        calibration = self.sketch.calibration
        if isinstance(calibration, AxisCalibration):
            self.calibration_method.set(AXIS_METHOD)
            self.distance.set(str(calibration.x_distance))
            self.vertical_distance.set(str(calibration.y_distance))
        elif calibration is not None:
            self.calibration_method.set(UNIFORM_METHOD)
            self.distance.set(str(calibration.distance))
        if active_mode in ("draw", "pad", "paste") and calibration is not None and self.sketch.origin is not None:
            self.mode = active_mode
            self.tool.set(self._draw_shape if active_mode == "draw" else "pad" if active_mode == "pad" else "")
            if active_mode != "paste":
                self.pad_number.set(self._next_pad_number())
        else:
            self.mode = "idle"
            self.tool.set("select")
        self._refresh_pad_list()
        self._refresh()
        self._message("1操作戻しました。")

    def copy_selected(self) -> None:
        if not self.selected_indices:
            return
        self.clipboard = tuple(self.sketch.pads[index] for index in sorted(self.selected_indices))
        self._message(f"{len(self.clipboard)}個のパッドをコピーしました。貼り付けで配置先の中心を選んでください。")
        self._refresh()

    def begin_paste(self) -> None:
        if not self.clipboard or self.sketch.calibration is None or self.sketch.origin is None:
            return
        self.calibration_points.clear()
        self._clear_preview()
        self.mode = "paste"
        self.tool.set("")
        self._message("コピーしたパッド群の中心をクリックして貼り付けます。続けて配置でき、Escで終了します。")
        self._refresh()

    def _paste_at(self, point: Point) -> None:
        before = Snapshot.capture(self.sketch)
        try:
            target = self._snap_point(point)
            pasted = duplicate_pads(self.clipboard, self.sketch.pads, target)
            ensure_drawable(minimum_scale(self.sketch.calibration), pasted)
        except ValueError as error:
            self._message(str(error), error=True)
            return
        first = len(self.sketch.pads)
        self.sketch.pads.extend(pasted)
        self._set_selection(range(first, len(self.sketch.pads)))
        self._record_change(before)
        self._paste_previews.clear()
        self._snap_guides.clear()
        self._refresh_pad_list()
        self._refresh()
        self._message(f"{len(pasted)}個を新しい番号で貼り付けました。続けて配置でき、Escで終了します。")

    def align_selected(self, axis: str, spacing: bool = False) -> None:
        if self.mode != "idle":
            return
        before = Snapshot.capture(self.sketch)
        try:
            operation = distribute_centers if spacing else align_centers
            pads = operation(self.sketch.pads, sorted(self.selected_indices), axis)
            ensure_drawable(minimum_scale(self.sketch.calibration), pads)
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self.sketch.pads[:] = pads
        self._record_change(before)
        if self.selected_index is not None:
            self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._refresh_pad_list()
        self._refresh()
        self._message("両端を固定して中心を等間隔に配置しました。" if spacing else "選択したパッドの平均中心線に揃えました。")

    def merge_selected(self) -> None:
        if self.mode != "idle" or len(self.selected_indices) < 2:
            return
        before = Snapshot.capture(self.sketch)
        indices = sorted(self.selected_indices)
        try:
            merged = merge_pads([self.sketch.pads[index] for index in indices], self.merge_number.get())
            ensure_drawable(minimum_scale(self.sketch.calibration), [merged])
        except ValueError as error:
            self._message(str(error), error=True)
            return
        remaining = [pad for index, pad in enumerate(self.sketch.pads) if index not in self.selected_indices]
        remaining.insert(indices[0], merged)
        self.sketch.pads[:] = remaining
        self.selected_index = indices[0]
        self._record_change(before)
        self._fill_pad_fields(merged)
        self._refresh_pad_list()
        self._refresh()
        self._message(f"端子番号 {merged.number} の1個のパッドに合体しました。Pasteは元の各開口を保持しています。")

    def begin_fillet(self) -> None:
        if len(self.selected_indices) != 1 or self.selected_index is None:
            return
        if not isinstance(self.sketch.pads[self.selected_index], CustomPad):
            return
        self._clear_preview()
        self.calibration_points.clear()
        self.mode = "fillet"
        self.tool.set("")
        self._message("フィレットRをmmで入力し、丸めたい角の丸印をクリックしてください。右クリックまたはEscで終了します。")
        self._refresh()

    def _corner_screens(self, pad: CustomPad) -> list[Point]:
        angle = math.radians(pad.angle % 360)
        cosine, sine = math.cos(angle), math.sin(angle)
        return [self.canvas.image_to_canvas(self.sketch.mm_to_image((
            pad.x + x * cosine + y * sine, pad.y + y * cosine - x * sine,
        ))) for x, y in pad.corners]

    def _fillet_at(self, event: tk.Event) -> None:
        if self.selected_index is None:
            return
        original = self.sketch.pads[self.selected_index]
        if not isinstance(original, CustomPad):
            return
        screens = self._corner_screens(original)
        if not screens:
            self._message("フィレットを付けられる直線の角がありません。", error=True)
            return
        index = min(range(len(screens)), key=lambda item: math.dist(screens[item], (event.x, event.y)))
        if math.dist(screens[index], (event.x, event.y)) > 12:
            self._message("丸める角の丸印をクリックしてください。", error=True)
            return
        before = Snapshot.capture(self.sketch)
        try:
            radius = self._read_number(self.fillet_radius, "フィレットR")
            candidate = fillet_pad(original, index, radius)
            ensure_drawable(minimum_scale(self.sketch.calibration), [candidate])
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self.sketch.pads[self.selected_index] = candidate
        self._record_change(before)
        self._fill_pad_fields(candidate)
        self._refresh_pad_list()
        self._refresh()
        self._message(f"選んだ角にR {radius:g} mmを適用しました。Paste開口は保持し、続けて別の角を選べます。")

    def apply_selected(self) -> None:
        if self.mode != "idle" or self._drag is not None or self.selected_index is None:
            return
        original = self.sketch.pads[self.selected_index]
        before = Snapshot.capture(self.sketch)
        try:
            if len(self.selected_indices) > 1 and any(isinstance(self.sketch.pads[index], CustomPad) for index in self.selected_indices):
                raise ValueError("合体形状を含む一括寸法変更には対応していません。合体前にUndoして元の図形を編集してください。")
            candidate = self._pad_at((original.x, original.y))
            if isinstance(original, CustomPad):
                if (candidate.width, candidate.height, candidate.radius) != (original.width, original.height, 0):
                    raise ValueError("合体形状の寸法変更は、Undoで合体前へ戻して元の図形を編集してください。")
                candidate = replace(original, number=candidate.number, angle=candidate.angle)
            replacements = {index: (candidate if len(self.selected_indices) == 1 else
                            replace(self.sketch.pads[index], width=candidate.width, height=candidate.height,
                                    angle=candidate.angle, radius=candidate.radius))
                            for index in self.selected_indices}
            ensure_drawable(minimum_scale(self.sketch.calibration), list(replacements.values()))
        except ValueError as error:
            self._message(str(error), error=True)
            return
        for index, pad in replacements.items():
            self.sketch.pads[index] = pad
        self._record_change(before)
        self._fill_pad_fields(self.sketch.pads[self.selected_index])
        self._refresh_pad_list()
        self._refresh()
        self._message(f"パッド {candidate.number} の寸法・属性を更新しました。")

    def _event_mm(self, event: tk.Event) -> Point:
        return self.sketch.image_to_mm(self.canvas.canvas_to_image((event.x, event.y)))

    def _on_canvas_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        if self.mode == "fillet":
            self._fillet_at(event)
            return
        if self.mode in ("calibration", "origin", "pad"):
            self._on_canvas_click(event)
            return
        if self.sketch.calibration is None or self.sketch.origin is None:
            return
        point = self._event_mm(event)
        if self.mode == "paste":
            self._paste_at(point)
            return
        if self.mode == "draw":
            self._drag = {"kind": "draw", "start": point, "screen_start": (event.x, event.y)}
            return
        if self.calibration_points:
            self._message("校正の実距離を入力して適用するか、選択ツールで校正点の選択を終了してください。")
            return
        hit = next((i for i in range(len(self.sketch.pads) - 1, -1, -1)
                    if hit_pad(self.sketch.pads[i], point)), None)
        if event.state & 0x0004:
            chosen = set(self.selected_indices)
            if hit is not None:
                if hit in chosen:
                    chosen.remove(hit)
                else:
                    chosen.add(hit)
            self._set_selection(chosen, hit)
            if self.selected_index is not None:
                self._fill_pad_fields(self.sketch.pads[self.selected_index])
            self._refresh_pad_list()
            self._refresh()
            return
        if self.selected_index is not None and len(self.selected_indices) == 1 and not isinstance(self.sketch.pads[self.selected_index], CustomPad):
            pad = self.sketch.pads[self.selected_index]
            for corner, position in enumerate(pad_corners(pad)):
                screen = self.canvas.image_to_canvas(self.sketch.mm_to_image(position))
                if math.dist(screen, (event.x, event.y)) <= 8:
                    self._drag = {"kind": "resize", "start": point, "original": pad, "corner": corner,
                                  "before": Snapshot.capture(self.sketch),
                                  "screen_start": (event.x, event.y)}
                    return
        previous_selection = set(self.selected_indices)
        previous_primary = self.selected_index
        self._set_selection(self.selected_indices if hit in self.selected_indices else [hit] if hit is not None else [], hit)
        if self.selected_index is not None:
            pad = self.sketch.pads[self.selected_index]
            self._fill_pad_fields(pad)
            self._drag = {"kind": "move", "start": point, "original": pad,
                          "originals": {index: self.sketch.pads[index] for index in self.selected_indices},
                          "before": Snapshot.capture(self.sketch),
                          "screen_start": (event.x, event.y)}
            self._message(f"{len(self.selected_indices)}個を選択。Ctrl+クリックで追加、Shift+ドラッグで水平/垂直移動できます。")
        else:
            self._drag = {"kind": "marquee", "start": point, "end": point,
                          "screen_start": (event.x, event.y),
                          "previous_selection": previous_selection, "previous_primary": previous_primary}
            selection = self.pad_table.selection()
            if selection:
                self.pad_table.selection_remove(selection)
        self._refresh_pad_list()
        self._refresh()

    def _snap_point(self, point: Point, *, exclude=(), axes=("x", "y")) -> Point:
        self._snap_guides.clear()
        if not self.center_snap.get():
            return point
        excluded = set(exclude)
        references = [pad for index, pad in enumerate(self.sketch.pads) if index not in excluded]
        if not references:
            return point
        screen = self.canvas.image_to_canvas(self.sketch.mm_to_image(point))
        result = list(point)
        for axis in axes:
            coordinate = 0 if axis == "x" else 1
            nearest = min(references, key=lambda pad: abs(
                self.canvas.image_to_canvas(self.sketch.mm_to_image((pad.x, pad.y)))[coordinate] - screen[coordinate]))
            target_screen = self.canvas.image_to_canvas(self.sketch.mm_to_image((nearest.x, nearest.y)))[coordinate]
            if abs(target_screen - screen[coordinate]) <= 8:
                result[coordinate] = getattr(nearest, axis)
                self._snap_guides.append((axis, result[coordinate]))
        return result[0], result[1]

    def _move_candidates(self, point: Point, state: int = 0) -> dict[int, Pad]:
        drag = self._drag
        assert drag is not None
        dx, dy = point[0] - drag["start"][0], point[1] - drag["start"][1]
        axes = ("x", "y")
        locked = None
        if state & 0x0001:
            start_screen = self.canvas.image_to_canvas(self.sketch.mm_to_image(drag["start"]))
            end_screen = self.canvas.image_to_canvas(self.sketch.mm_to_image(point))
            if abs(end_screen[0] - start_screen[0]) >= abs(end_screen[1] - start_screen[1]):
                dy, axes, locked = 0.0, ("x",), ("y", drag["original"].y)
            else:
                dx, axes, locked = 0.0, ("y",), ("x", drag["original"].x)
        original = drag["original"]
        center = self._snap_point((original.x + dx, original.y + dy), exclude=drag["originals"], axes=axes)
        if locked is not None:
            self._snap_guides.append(locked)
        dx, dy = center[0] - original.x, center[1] - original.y
        return {index: replace(pad,
                               x=center[0] if index == self.selected_index else pad.x + dx,
                               y=center[1] if index == self.selected_index else pad.y + dy)
                for index, pad in drag["originals"].items()}

    def _on_canvas_motion(self, event: tk.Event) -> None:
        if self.mode not in ("pad", "paste") or self._drag is not None:
            return
        try:
            target = self._snap_point(self._event_mm(event))
            self._paste_previews = (duplicate_pads(self.clipboard, self.sketch.pads, target)
                                    if self.mode == "paste" else [self._pad_at(target)])
            ensure_drawable(minimum_scale(self.sketch.calibration), self._paste_previews)
        except ValueError:
            self._paste_previews.clear()
        self._redraw()

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
        if self._drag["kind"] == "marquee":
            self._drag["end"] = self._event_mm(event)
            self._redraw()
            return
        try:
            if self._drag["kind"] == "move":
                self._preview_pads = self._move_candidates(self._event_mm(event), event.state)
                candidate = self._preview_pads[self.selected_index]
            else:
                candidate = self._drag_candidate(self._event_mm(event))
            ensure_drawable(minimum_scale(self.sketch.calibration), list(self._preview_pads.values()) or [candidate])
            self._preview_pad = candidate
        except ValueError:
            self._preview_pad = None
            self._preview_pads.clear()
        self._redraw()

    def _on_canvas_release(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        drag = self._drag
        if drag["kind"] == "marquee":
            end = self._event_mm(event)
            if math.dist((event.x, event.y), drag["screen_start"]) >= 3:
                left, right = sorted((drag["start"][0], end[0]))
                top, bottom = sorted((drag["start"][1], end[1]))
                self._set_selection(index for index, pad in enumerate(self.sketch.pads)
                                    if left <= pad.x <= right and top <= pad.y <= bottom)
            self._clear_preview()
            if self.selected_index is not None:
                self._fill_pad_fields(self.sketch.pads[self.selected_index])
            self._refresh_pad_list()
            self._refresh()
            self._message(f"{len(self.selected_indices)}個を範囲選択しました。Ctrl+クリックで追加・解除できます。")
            return
        before = drag.get("before", Snapshot.capture(self.sketch))
        replacements = None
        try:
            if drag["kind"] == "draw" and math.dist((event.x, event.y), drag["screen_start"]) < 3:
                raise ValueError("対角にドラッグしてパッドの幅と高さを指定してください。")
            if drag["kind"] != "draw" and (event.x, event.y) == drag["screen_start"]:
                candidate = drag["original"]
                if drag["kind"] == "move":
                    replacements = drag["originals"]
            elif drag["kind"] == "move":
                replacements = self._move_candidates(self._event_mm(event), event.state)
                candidate = replacements[self.selected_index]
            else:
                candidate = self._drag_candidate(self._event_mm(event))
            ensure_drawable(minimum_scale(self.sketch.calibration), list(replacements.values()) if replacements else [candidate])
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
                for index, pad in (replacements or {self.selected_index: candidate}).items():
                    self.sketch.pads[index] = pad
                self.mode = "idle"
                self.tool.set("select")
                self._fill_pad_fields(candidate)
                if candidate == drag["original"]:
                    self._message(f"パッド {candidate.number} を選択しました。四隅でサイズ変更できます。")
                else:
                    self._message(f"パッド {candidate.number} を更新しました。寸法とRは右の欄で確認できます。")
            self._record_change(before)
        finally:
            self._clear_preview()
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
        before = Snapshot.capture(self.sketch)
        try:
            pad = self._pad_at(self._snap_point(self.sketch.image_to_mm(point)))
            calibration = self.sketch.calibration
            assert calibration is not None
            ensure_drawable(minimum_scale(calibration), [pad])
            self.sketch.add_pad(pad)
            self.selected_index = len(self.sketch.pads) - 1
        except ValueError as error:
            self._message(str(error), error=True)
            return
        self._record_change(before)
        self.pad_number.set(self._next_pad_number(pad.number))
        self._paste_previews.clear()
        self._snap_guides.clear()
        self._message(f"パッド {pad.number} を数値サイズで配置しました。続けて中心をクリックできます。")
        self._refresh_pad_list()
        self._refresh()

    def _cancel_action(self, _event: tk.Event) -> None:
        if self.mode != "idle" or self.calibration_points or self._drag:
            if self._drag is not None and self._drag["kind"] == "marquee":
                self._set_selection(self._drag["previous_selection"], self._drag["previous_primary"])
            self._clear_preview()
            self.tool.set("select")
            self.mode = "idle"
            self.calibration_points.clear()
            if self.selected_index is not None:
                self._fill_pad_fields(self.sketch.pads[self.selected_index])
            self._message("操作を取り消しました。")
            self._refresh_pad_list()
            self._refresh()

    def _on_secondary_click(self, event: tk.Event) -> str | None:
        if self.mode in ("draw", "pad", "paste", "fillet") or (self._drag is not None and self._drag["kind"] == "marquee"):
            self._cancel_action(event)
            return "break"
        return None

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
