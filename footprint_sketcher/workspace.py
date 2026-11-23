"""Document, drawing, transform and persistent-constraint controls."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image

from .model import Pad, Calibration, Sketch, FRONT_LAYERS, BACK_LAYERS
from .graphics import Graphic, graphic_from_points
from .editing import (Snapshot, copy_pads_between_points, point_symmetric_copies,
                      line_symmetric_copies, delete_selected_pads)
from .constraints import Constraint, solve_constraints
from .layers import locked_content


KINDS = {
    "中心一致": "coincidence", "水平": "horizontal", "垂直": "vertical",
    "点対称": "symmetric", "水平軸対称": "symmetry_x", "垂直軸対称": "symmetry_y",
    "等幅": "equal_width", "等長": "equal_width", "等高": "equal_height", "等寸法": "equal_size", "固定": "fixed",
    "X位置": "x", "Y位置": "y", "幅": "width", "高さ": "height", "角度": "angle", "R": "radius",
    "中心間距離": "distance", "X間隔": "spacing_x", "Y間隔": "spacing_y",
}


class EditorTools:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.window = None
        self.panel = None
        self._drawing_points = []
        self._point_fields = None
        self.saved = Snapshot.capture(app.sketch)
        self.saved_name = app.footprint_name.get()
        self.saved_image = self._image_digest()
        self.project_path = None
        self.last_constraint_error = ""
        self.state_label = tk.StringVar(self.root, "未拘束")
        defaults = dict(x="0", y="0", pad_type="smd", shape="auto", side="F", drill_w="0", drill_h="0",
                        mask_margin="0", paste_margin="0", paste_ratio="0", layer="F.SilkS", stroke="0.15",
                        graphic="rect", graphic_radius="0.1", ax="0", ay="0", bx="0", by="0", cx="0", cy="0", value="0",
                        constraint="水平", reference="REF**", doc_value="", split_x="2", split_y="1", split_gap="0.1", bridge_width="0.3")
        self.fields = {key: tk.StringVar(self.root, value) for key, value in defaults.items()}
        self.mask = tk.BooleanVar(self.root, True)
        self.paste = tk.BooleanVar(self.root, True)
        self.filled = tk.BooleanVar(self.root, False)
        self.keep_numbers = tk.BooleanVar(self.root, False)
        self.layer_visible = {layer: tk.BooleanVar(self.root, True) for layer in FRONT_LAYERS + BACK_LAYERS}
        self.layer_locked = {layer: tk.BooleanVar(self.root, False) for layer in FRONT_LAYERS + BACK_LAYERS}
        self._menu()
        self.root.bind("<Delete>", lambda event: app._shortcut(event, self.delete))
        self.root.bind("<Control-s>", lambda event: app._shortcut(event, self.save))
        self.root.bind("<Return>", self._finish_key, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _menu(self):
        menu = tk.Menu(self.root)
        files = tk.Menu(menu, tearoff=False)
        for name, command in (("新規", self.new), ("プロジェクトを開く…", self.open_project),
                              ("プロジェクトを保存…", self.save), ("KiCadフットプリントを開く…", self.open_footprint),
                              ("図形を残して下絵を追加・差替え…", self.attach_reference),
                              ("PDFのページ／範囲を下絵に…", self.open_pdf),
                              ("KiCadへ書き出す…", self.app.export)):
            files.add_command(label=name, command=command)
        menu.add_cascade(label="ファイル", menu=files)
        edit = tk.Menu(menu, tearoff=False)
        edit.add_command(label="選択を削除", command=self.delete)
        edit.add_command(label="基準点コピー・対称…", command=lambda: self.open_panel(2))
        menu.add_cascade(label="編集", menu=edit)
        menu.add_command(label="属性・レイヤー・図形…", command=lambda: self.open_panel(0))
        menu.add_command(label="拘束…", command=lambda: self.open_panel(3))
        self.root.configure(menu=menu)

    def _num(self, name):
        value = float(self.fields[name].get())
        if not math.isfinite(value):
            raise ValueError("数値は有限値で指定してください。")
        return value

    def _entry(self, frame, label, name, row, column=0, *, values=None):
        ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", padx=5, pady=3)
        if values:
            widget = ttk.Combobox(frame, values=values, textvariable=self.fields[name], state="readonly", width=20)
        else:
            widget = ttk.Entry(frame, textvariable=self.fields[name], width=20)
        widget.grid(row=row, column=column+1, sticky="ew", padx=5, pady=3)
        if name in ("reference", "doc_value"):
            widget.bind("<Return>", lambda _event: self.apply_document_fields())
        elif name == "value":
            widget.bind("<Return>", lambda _event: self.update_constraint() if hasattr(self, "constraint_list") and self.constraint_list.curselection() else self.add_constraint())
        elif name in ("split_x", "split_y", "split_gap"):
            widget.bind("<Return>", lambda _event: self.split_paste())
        elif name in ("ax", "ay", "bx", "by", "cx", "cy", "stroke", "bridge_width", "graphic_radius"):
            widget.bind("<Return>", lambda _event, key=name: self.validate_setting(key))
        return widget

    def validate_setting(self, name):
        try:
            value = self._num(name)
            if name == "stroke" and value <= 0:
                raise ValueError("線幅には正のmm値を指定してください。")
            if name == "bridge_width" and value < 0:
                raise ValueError("銅箔接続の幅は0以上を指定してください。")
            if name == "graphic_radius" and value <= 0:
                raise ValueError("角丸図形のRは正のmm値を指定してください。")
            self.app._message("入力値を反映しました。次の作図・変換で使用します。")
        except ValueError as error:
            self.app._message(str(error), error=True)

    def _ready(self):
        if self.app._pending_calibration_blocks_edit(): return False
        if self.app.mode != "idle" or self.app._drag is not None:
            self.app._message("選択/移動に切り替えてから、対象を編集してください。", error=True)
            return False
        return True

    def open_panel(self, tab=0):
        if self.window is not None and self.window.winfo_exists():
            self.panel.select(tab)
            self.window.deiconify()
            self.window.lift()
            return
        self.window = tk.Toplevel(self.root)
        self.window.title("Footprint Sketcher — 属性・図形・拘束")
        self.window.geometry("900x900+40+40")
        self.panel = ttk.Notebook(self.window)
        self.panel.pack(fill="both", expand=True, padx=10, pady=10)
        tabs = []
        for title in ("属性・穴・開口", "図形・レイヤー", "コピー・接続", "拘束", "文書"):
            frame = ttk.Frame(self.panel, padding=12)
            frame.columnconfigure(1, weight=1)
            self.panel.add(frame, text=title)
            tabs.append(frame)
        f = tabs[0]
        for row, (label, name, values) in enumerate((
            ("位置X (mm)", "x", None), ("位置Y (mm)", "y", None),
            ("種別", "pad_type", ("smd", "thru_hole", "np_thru_hole")),
            ("形状", "shape", ("auto", "rect", "roundrect", "circle", "oval")),
            ("面", "side", ("F", "B")), ("穴の幅 (mm)", "drill_w", None), ("穴の高さ (mm)", "drill_h", None),
            ("Mask片側拡縮 (mm)", "mask_margin", None), ("Paste片側拡縮 (mm)", "paste_margin", None),
            ("Paste片側比率 (0.1=10%)", "paste_ratio", None))):
            widget = self._entry(f, label, name, row, values=values)
            widget.bind("<Return>", lambda _event: self.apply_attributes())
        ttk.Checkbutton(f, text="Maskを出力", variable=self.mask).grid(row=10, column=0, sticky="w")
        ttk.Checkbutton(f, text="Pasteを出力", variable=self.paste).grid(row=10, column=1, sticky="w")
        ttk.Button(f, text="選択へ位置・穴・面・開口を適用", command=self.apply_attributes).grid(row=11, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(f, text="幅・高さ・角度・Rは本画面の右欄で入力します。\n未選択時の種別・穴・面・開口設定は、次に配置するパッドへ使います。\nNPTHは非電気・非めっき穴で、端子番号は空欄になります。\n穴なしSMDの穴寸法は0にします。", wraplength=650).grid(row=12, column=0, columnspan=2, sticky="w")
        f = tabs[1]
        self._entry(f, "図形", "graphic", 0, values=("rect", "roundrect", "line", "circle", "arc", "polygon"))
        self._entry(f, "作図レイヤー", "layer", 1, values=FRONT_LAYERS+BACK_LAYERS)
        self._entry(f, "線幅 (mm)", "stroke", 2)
        self._entry(f, "作図する角丸図形のR (mm)", "graphic_radius", 3)
        ttk.Checkbutton(f, text="塗りつぶす（独立開口は有効にする）", variable=self.filled).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Button(f, text="キャンバスで作図", command=self.begin_graphic).grid(row=5, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Button(f, text="多角形を確定", command=self.finish_polygon).grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Label(f, text="線・四角・円はドラッグ。円弧は始点→中間点→終点。\n多角形は頂点をクリックし「確定」またはEnter。Escで取消し。\nMask/Pasteの独立開口は該当レイヤーで塗りつぶして作図します。", wraplength=650).grid(row=7, column=0, columnspan=2, sticky="w", pady=5)
        for row, layer in enumerate(FRONT_LAYERS+BACK_LAYERS, 8):
            ttk.Checkbutton(f, text=layer, variable=self.layer_visible[layer], command=self.layers_changed).grid(row=row, column=0, sticky="w")
            ttk.Checkbutton(f, text="編集ロック", variable=self.layer_locked[layer], command=self.layers_changed).grid(row=row, column=1, sticky="w")
        f = tabs[2]
        for row, (label, name) in enumerate((("点A / 軸始点 X", "ax"), ("点A / 軸始点 Y", "ay"),
                                           ("点B / 軸終点 X", "bx"), ("点B / 軸終点 Y", "by"),
                                           ("対称中心 X", "cx"), ("対称中心 Y", "cy"))):
            self._entry(f, label+" (mm)", name, row)
        for row, (text, pair) in enumerate((("点Aを選ぶ", ("ax", "ay")), ("点Bを選ぶ", ("bx", "by")), ("中心を選ぶ", ("cx", "cy"))), 6):
            ttk.Button(f, text=text, command=lambda p=pair: self.pick_point(p)).grid(row=row, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(f, text="コピー先でも元の端子番号を維持する", variable=self.keep_numbers).grid(row=9, column=0, columnspan=2, sticky="w", pady=5)
        for row, (text, mode) in enumerate((("点A→点Bへコピー", "reference"), ("指定中心で点対称コピー", "point"), ("点A–点Bの軸で線対称コピー", "line")), 10):
            ttk.Button(f, text=text, command=lambda m=mode: self.transform(m)).grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
        self._entry(f, "銅箔接続の幅 (mm、0は重なる部分のみ)", "bridge_width", 13)
        ttk.Button(f, text="選択した同一ネット端子を銅箔接続", command=self.connect).grid(row=14, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(f, text="元端子番号を残し、一覧順の中心間を指定幅のCuで結びます。\n同一ネット用です。別ネット用Net-tieは作成しません。", wraplength=650).grid(row=15, column=0, columnspan=2, sticky="w")
        for row, (label, name) in enumerate((("Paste分割 横数", "split_x"), ("Paste分割 縦数", "split_y"), ("窓間隔 (mm)", "split_gap")), 16):
            self._entry(f, label, name, row)
        ttk.Button(f, text="選択パッドのPasteを分割", command=self.split_paste).grid(row=19, column=0, columnspan=2, sticky="ew", pady=5)
        f = tabs[3]
        self._constraint_combo = self._entry(f, "拘束", "constraint", 0, values=tuple(KINDS))
        self._entry(f, "寸法値 (mm／度)", "value", 1)
        ttk.Label(f, text="対象はキャンバス／一覧の選択順ではなく一覧順。\n関係拘束は中心（Customは接続点）の関係です。形状の反転には対称コピーを使います。\n2対象の対称はコピー・接続タブの中心を使用します。\n寸法・固定は1対象、関係拘束は2対象を選択します。", wraplength=650).grid(row=2, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Button(f, text="選択対象へ拘束を追加", command=self.add_constraint).grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Label(f, textvariable=self.state_label).grid(row=4, column=0, columnspan=2, sticky="w", pady=5)
        self.constraint_list = tk.Listbox(f, selectmode="extended", height=12, exportselection=False)
        self.constraint_list.grid(row=5, column=0, columnspan=2, sticky="nsew")
        ttk.Button(f, text="選んだ拘束を入力値へ変更", command=self.update_constraint).grid(row=6, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(f, text="選んだ拘束を解除", command=self.remove_constraints).grid(row=7, column=0, columnspan=2, sticky="ew")
        f = tabs[4]
        self._entry(f, "Reference", "reference", 0)
        self._entry(f, "Value", "doc_value", 1)
        ttk.Button(f, text="Reference / Valueを反映", command=self.apply_document_fields).grid(row=2, column=0, columnspan=2, sticky="ew")
        for row, (text, command) in enumerate((("プロジェクトを保存…", self.save), ("プロジェクトを開く…", self.open_project),
                                              ("KiCadフットプリントを開く…", self.open_footprint), ("PDFのページ／範囲を開く…", self.open_pdf)), 3):
            ttk.Button(f, text=text, command=command).grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        self.panel.select(tab)
        self.refresh()

    def on_selection(self, pad):
        self.fields["x"].set(str(pad.x)); self.fields["y"].set(str(pad.y))
        if isinstance(pad, Graphic):
            for key, value in (("graphic", pad.graphic_kind), ("layer", pad.layer), ("stroke", pad.stroke)):
                self.fields[key].set(str(value))
            self.filled.set(pad.filled)
            if pad.graphic_kind == "roundrect": self.fields["graphic_radius"].set(str(pad.radius))
            return
        for key, value in (("pad_type", pad.pad_type), ("shape", pad.pad_shape),
                           ("drill_w", pad.drill_width), ("drill_h", pad.drill_height),
                           ("mask_margin", pad.mask_margin), ("paste_margin", pad.paste_margin), ("paste_ratio", pad.paste_ratio)):
            self.fields[key].set(str(value))
        self.fields["side"].set("B" if "B.Cu" in pad.layers else "F")
        self.mask.set(any(layer.endswith("Mask") for layer in pad.layers))
        self.paste.set(any(layer.endswith("Paste") for layer in pad.layers))

    def pad_options(self):
        kind, side = self.fields["pad_type"].get(), self.fields["side"].get()
        if kind == "smd":
            layers = (side+".Cu",) + ((side+".Paste",) if self.paste.get() else ()) + ((side+".Mask",) if self.mask.get() else ())
        else:
            layers = ("*.Cu",) + (("*.Mask",) if self.mask.get() else ())
        return dict(pad_type=kind, pad_shape=self.fields["shape"].get(), layers=layers,
                    drill_width=self._num("drill_w"), drill_height=self._num("drill_h"),
                    mask_margin=self._num("mask_margin"), paste_margin=self._num("paste_margin"), paste_ratio=self._num("paste_ratio"))

    def apply_attributes(self):
        app = self.app
        if app.mode in ("pad", "draw"):
            try:
                app._pad_at((0, 0))
                app._message("穴・面・開口の入力を確認しました。次に配置するパッドへ使用します。")
            except ValueError as error: app._message(str(error), error=True)
            return
        if not self._ready() or not app.selected_indices:
            return
        before = Snapshot.capture(app.sketch)
        try:
            replacements = {}
            used_numbers = {pad.number for pad in app.sketch.pads}
            next_number = 1
            for index in sorted(app.selected_indices):
                pad = app.sketch.pads[index]
                changes = {"x": self._num("x"), "y": self._num("y")} if len(app.selected_indices) == 1 else {}
                if isinstance(pad, Graphic):
                    changes.update(layer=self.fields["layer"].get(), stroke=self._num("stroke"), filled=self.filled.get())
                else:
                    changes.update(self.pad_options())
                    from .compound import CustomPad
                    if isinstance(pad, CustomPad) and changes["pad_shape"] != pad.pad_shape:
                        raise ValueError("合体形状の種別変更には対応していません。Undoで合体前へ戻して編集してください。")
                    if changes["pad_shape"] in ("rect", "circle", "oval"):
                        changes["radius"] = 0
                    elif changes["pad_shape"] == "roundrect" and pad.pad_shape != "roundrect":
                        radius = app._read_number(app.pad_radius, "角のR")
                        if radius <= 0:
                            raise ValueError("角丸形状には、メイン画面の角のRへ正のmm値を入力してください。")
                        changes["radius"] = radius
                    if pad.pad_type == "np_thru_hole" and changes["pad_type"] != "np_thru_hole":
                        while str(next_number) in used_numbers:
                            next_number += 1
                        changes["number"] = str(next_number)
                        used_numbers.add(str(next_number))
                replacements[index] = replace(pad, **changes)
            for index, pad in replacements.items():
                app.sketch.pads[index] = pad
            self._changed(before, "位置・穴・面・開口を更新しました。"+(" NPTHは非電気の穴なので端子番号は空欄です。" if self.fields["pad_type"].get() == "np_thru_hole" else ""))
        except ValueError as error:
            before.restore(app.sketch); app._message(str(error), error=True)

    def _changed(self, before, message):
        app = self.app
        if app._record_change(before) is False:
            return False
        app._refresh_pad_list()
        if app.selected_index is not None:
            app._fill_pad_fields(app.sketch.pads[app.selected_index])
        app._refresh(); app._message(message)
        return True

    def delete(self):
        app = self.app
        if not self._ready() or not app.selected_indices:
            return
        selected = set(app.selected_indices)
        affected = [c for c in app.sketch.constraints if selected.intersection(c.targets)]
        if affected and not messagebox.askyesno("拘束も解除", "削除対象を参照する拘束も解除します。\n"+"\n".join(c.id+": "+c.kind for c in affected), parent=self.root):
            return
        before = Snapshot.capture(app.sketch)
        mapping = {old: new for new, old in enumerate(i for i in range(len(app.sketch.pads)) if i not in selected)}
        app.sketch.pads[:] = delete_selected_pads(app.sketch.pads, sorted(selected))
        app.sketch.constraints[:] = [replace(c, targets=tuple(mapping[i] for i in c.targets)) for c in app.sketch.constraints if c not in affected]
        app.selected_index = None
        self._changed(before, "選択を削除しました。残りの端子番号は保持しています。")

    def pick_point(self, fields):
        if self.app.sketch.calibration is None or self.app.sketch.origin is None:
            self.app._message("基準点をキャンバスで選ぶ前に、下絵を校正して原点を指定してください。", error=True)
            return
        self.app.select_tool(); self._point_fields = fields; self.app.mode = "point"
        self.app._message("基準点をキャンバスでクリックしてください。")
        self.root.lift(); self.app.canvas.focus_set()

    def transform(self, mode):
        app = self.app
        if not self._ready() or not app.selected_indices: return
        before = Snapshot.capture(app.sketch)
        try:
            pads = [app.sketch.pads[i] for i in sorted(app.selected_indices)]
            a, b, center = (self._num("ax"), self._num("ay")), (self._num("bx"), self._num("by")), (self._num("cx"), self._num("cy"))
            options = dict(keep_numbers=self.keep_numbers.get())
            if mode == "reference": copies = copy_pads_between_points(pads, app.sketch.pads, a, b, **options)
            elif mode == "point": copies = point_symmetric_copies(pads, app.sketch.pads, center, **options)
            else: copies = line_symmetric_copies(pads, app.sketch.pads, a, b, **options)
            first = len(app.sketch.pads); app.sketch.pads.extend(copies)
            app._set_selection(range(first, len(app.sketch.pads)))
            self._changed(before, "基準点・対称コピーを作成しました。元図形と所属レイヤーを保持しています。")
        except ValueError as error:
            before.restore(app.sketch); app._message(str(error), error=True)

    def connect(self):
        from .compound import connect_terminals
        app = self.app
        if not self._ready() or len(app.selected_indices) < 2: return
        before = Snapshot.capture(app.sketch)
        try:
            sources = [app.sketch.pads[i] for i in sorted(app.selected_indices)]
            bridge = connect_terminals(sources, sources[0].number, bridge_width=self._num("bridge_width"))
            app.sketch.pads.append(bridge); app.selected_index = len(app.sketch.pads)-1
            self._changed(before, "元端子番号を残して同一ネット用Cu接続を追加しました。別ネットでは使わないでください。")
        except ValueError as error: app._message(str(error), error=True)

    def split_paste(self):
        from .compound import split_paste_apertures
        app = self.app
        if not self._ready(): return
        if len(app.selected_indices) != 1 or isinstance(app.sketch.pads[app.selected_index], Graphic):
            app._message("分割するSMDパッドを1個選択してください。", error=True); return
        before = Snapshot.capture(app.sketch)
        try:
            app.sketch.pads[app.selected_index] = split_paste_apertures(app.sketch.pads[app.selected_index], int(self.fields["split_x"].get()), int(self.fields["split_y"].get()), self._num("split_gap"))
            self._changed(before, "Cuを保持してPaste開口を分割しました。")
        except ValueError as error: app._message(str(error), error=True)

    def _new_constraint(self, identifier, targets=None):
        app = self.app
        targets = tuple(sorted(app.selected_indices)) if targets is None else targets
        kind = KINDS[self.fields["constraint"].get()]
        value = self._num("value") if kind in ("x", "y", "width", "height", "angle", "radius", "distance", "spacing_x", "spacing_y") else None
        values = tuple(getattr(app.sketch.pads[targets[0]], key) for key in ("x", "y", "width", "height", "angle", "radius")) if kind == "fixed" and targets else ()
        return Constraint(identifier, kind, targets, value=value, point=(self._num("cx"), self._num("cy")), values=values)

    def add_constraint(self):
        app = self.app
        if not self._ready(): return
        before = Snapshot.capture(app.sketch)
        try:
            used = {c.id for c in app.sketch.constraints}; n = 1
            while f"C{n}" in used: n += 1
            app.sketch.constraints.append(self._new_constraint(f"C{n}"))
            self._changed(before, "拘束を追加しました。関係は以後の編集でも保持します。")
        except ValueError as error:
            before.restore(app.sketch); app._message(str(error), error=True)

    def update_constraint(self):
        if not self._ready(): return
        if not hasattr(self, "constraint_list") or len(self.constraint_list.curselection()) != 1: return
        index = self.constraint_list.curselection()[0]; app = self.app
        before = Snapshot.capture(app.sketch)
        try:
            old = app.sketch.constraints[index]
            # Selecting a relation in the list never silently changes its targets.
            app.sketch.constraints[index] = self._new_constraint(old.id, old.targets)
            self._changed(before, "拘束を変更しました。")
        except ValueError as error:
            before.restore(app.sketch); app._message(str(error), error=True)

    def remove_constraints(self):
        if not self._ready(): return
        if not hasattr(self, "constraint_list"): return
        selected = set(self.constraint_list.curselection())
        if not selected: return
        before = Snapshot.capture(self.app.sketch)
        self.app.sketch.constraints[:] = [c for i, c in enumerate(self.app.sketch.constraints) if i not in selected]
        self._changed(before, "選択した拘束を解除しました。")

    def constrain(self, before):
        app = self.app
        # Layer locks also protect edits initiated from numeric fields/list.
        locked_before = locked_content(before.pads, app.canvas.locked_layers)
        if locked_before != locked_content(app.sketch.pads, app.canvas.locked_layers):
            raise ValueError("編集ロック中のレイヤーを変更できません。")
        if not app.sketch.constraints: return
        driving = {}
        if before.origin != app.sketch.origin and before.origin is not None and app.sketch.origin is not None:
            calibration = app.sketch.calibration
            dx = (app.sketch.origin[0]-before.origin[0])*calibration.mm_per_pixel_x
            dy = (app.sketch.origin[1]-before.origin[1])*calibration.mm_per_pixel_y
            shifted = []
            for c in app.sketch.constraints:
                updates = dict(point=(c.point[0]-dx, c.point[1]-dy))
                if c.kind in ("x", "y"): updates["value"] = c.value-(dx if c.kind == "x" else dy)
                if c.kind == "fixed": updates["values"] = (c.values[0]-dx, c.values[1]-dy, *c.values[2:])
                shifted.append(replace(c, **updates))
            app.sketch.constraints[:] = shifted
        elif tuple(app.sketch.constraints) == before.constraints and len(app.sketch.pads) == len(before.pads):
            for i, (old, new) in enumerate(zip(before.pads, app.sketch.pads)):
                changes = {key: getattr(new, key) for key in ("x", "y", "width", "height", "angle", "radius") if getattr(new, key) != getattr(old, key)}
                if changes: driving[i] = changes
        result = solve_constraints(app.sketch.pads, app.sketch.constraints, driving=driving)
        if locked_before != locked_content(result.pads, app.canvas.locked_layers):
            raise ValueError("拘束の追従で編集ロック中の図形が変わるため、操作を取り消しました。")
        app.sketch.pads[:] = result.pads
        self.last_constraint_error = ""

    def refresh(self):
        app = self.app
        if self.window is not None and not self.window.winfo_exists(): self.window = None
        if hasattr(self, "constraint_list") and self.window is not None:
            count = len(app.selected_indices)
            scalar = {"x", "y", "width", "height", "angle", "radius", "fixed"}
            if count == 1:
                pad = app.sketch.pads[app.selected_index]
                kind = getattr(pad, "graphic_kind", pad.pad_shape)
                from .compound import CustomPad
                if isinstance(pad, CustomPad): scalar = {"x", "y", "angle", "fixed"}
                elif kind == "line": scalar = {"x", "y", "width", "angle", "fixed"}
                elif kind in ("rect", "oval", "arc", "polygon"): scalar.discard("radius")
                available = [label for label, value in KINDS.items() if value in scalar]
            elif count == 2:
                available = [label for label, value in KINDS.items() if value not in scalar]
            else: available = list(KINDS)
            self._constraint_combo.configure(values=available)
            if self.fields["constraint"].get() not in available:
                self.fields["constraint"].set(available[0])
            selected = self.constraint_list.curselection(); self.constraint_list.delete(0, "end")
            for c in app.sketch.constraints:
                targets = ",".join(app.sketch.pads[i].number for i in c.targets if i < len(app.sketch.pads))
                self.constraint_list.insert("end", f"{c.id}  {c.kind} [{targets}]  {c.value if c.value is not None else ''}")
            for i in selected:
                if i < self.constraint_list.size(): self.constraint_list.selection_set(i)
        try:
            if app.sketch.constraints:
                result = solve_constraints(app.sketch.pads, app.sketch.constraints)
                self.state_label.set(("完全拘束" if result.state == "fully_constrained" else "未拘束")+f" / 残り自由度 {result.dof}")
            else: self.state_label.set("未拘束 / 拘束なし")
        except ValueError as error: self.state_label.set("矛盾・解けない: "+str(error))

    def layers_changed(self):
        self.app.canvas.hidden_layers = {l for l, v in self.layer_visible.items() if not v.get()}
        self.app.canvas.locked_layers = {l for l, v in self.layer_locked.items() if v.get()}
        self.app._redraw()

    def begin_graphic(self):
        if self.app.sketch.origin is None:
            if self.app.canvas.image is not None:
                self.app._message("下絵を校正し、原点を指定してから作図してください。", error=True)
                return
            self.new_blank()
        settings = {key: self.fields[key].get() for key in ("graphic", "layer", "stroke", "graphic_radius")}
        filled, radius = self.filled.get(), self.app.pad_radius.get()
        self.app.select_tool(); self.app.selected_index = None
        for key, value in settings.items(): self.fields[key].set(value)
        self.filled.set(filled); self.app.pad_radius.set(radius)
        self.app.mode = "graphic"; self._drawing_points = []
        self.app._message("図形を作図します。線・四角・円はドラッグ、円弧・多角形は点をクリック。Escで取消します。")
        self.root.lift(); self.app.canvas.focus_set()

    def _graphic(self, points):
        kind = self.fields["graphic"].get()
        props = dict(layer=self.fields["layer"].get(), stroke=self._num("stroke"), filled=self.filled.get())
        used = {p.number for p in self.app.sketch.pads}; n = 1
        while f"G{n}" in used: n += 1
        if kind in ("line", "arc", "polygon"):
            return graphic_from_points(f"G{n}", kind, points, **props)
        a, b = points
        width, height = abs(b[0]-a[0]), abs(b[1]-a[1])
        if kind == "circle": width = height = min(width, height)
        radius = min(self._num("graphic_radius"), width/2, height/2) if kind == "roundrect" else 0
        if kind == "roundrect" and radius <= 0: raise ValueError("角丸図形のRは正のmm値を指定してください。")
        return Graphic(f"G{n}", (a[0]+b[0])/2, (a[1]+b[1])/2, width, height,
                       radius=radius, graphic_kind=kind, **props)

    def press(self, event):
        app = self.app
        if app.mode == "point":
            point = app._event_mm(event)
            for name, value in zip(self._point_fields, point): self.fields[name].set(str(value))
            self._point_fields = None; app.select_tool(); return True
        if app.mode != "graphic": return False
        point = app._event_mm(event); kind = self.fields["graphic"].get()
        if kind in ("polygon", "arc"):
            self._drawing_points.append(point)
            if kind == "arc" and len(self._drawing_points) == 3: self._commit_graphic()
        else: self._drawing_points = [point]
        app._refresh()
        return True

    def motion(self, event):
        if self.app.mode != "graphic": return False
        if self._drawing_points and self.fields["graphic"].get() not in ("arc", "polygon"):
            try: self.app._preview_pad = self._graphic([self._drawing_points[0], self.app._event_mm(event)])
            except ValueError: self.app._preview_pad = None
            self.app._redraw()
        return True

    def release(self, event):
        if self.app.mode != "graphic": return False
        if self._drawing_points and self.fields["graphic"].get() not in ("arc", "polygon"):
            self._drawing_points = [self._drawing_points[0], self.app._event_mm(event)]; self._commit_graphic()
        return True

    def _commit_graphic(self):
        before = Snapshot.capture(self.app.sketch)
        try:
            self.app.sketch.pads.append(self._graphic(self._drawing_points))
            self.app.selected_index = len(self.app.sketch.pads)-1
            self.app._preview_pad = None; self._drawing_points = []
            self._changed(before, "図形を作成しました。続けて作図できます。")
        except ValueError as error:
            before.restore(self.app.sketch); self._drawing_points = []; self.app._message(str(error), error=True)

    def finish_polygon(self):
        if self.app.mode == "graphic" and self.fields["graphic"].get() == "polygon": self._commit_graphic()

    def _finish_key(self, event):
        if event.widget == self.app.canvas and self.app.mode == "graphic":
            self.finish_polygon(); return "break"

    def apply_document_fields(self):
        if not self._ready(): return
        before = Snapshot.capture(self.app.sketch)
        self.app.sketch.reference = self.fields["reference"].get(); self.app.sketch.value = self.fields["doc_value"].get()
        self._changed(before, "Reference / Valueを更新しました。")

    def _image_digest(self):
        image = self.app.canvas.image
        return None if image is None else (image.size, image.mode, hashlib.sha256(image.tobytes()).hexdigest())

    def _mark_saved(self):
        self.saved = Snapshot.capture(self.app.sketch)
        self.saved_name = self.app.footprint_name.get()
        self.saved_image = self._image_digest()

    def _confirm_discard(self):
        clean = (Snapshot.capture(self.app.sketch) == self.saved
                 and self.app.footprint_name.get() == self.saved_name and self._image_digest() == self.saved_image)
        return clean or messagebox.askyesno("未保存の変更", "編集用プロジェクトへ未保存の変更があります。破棄して続けますか？", parent=self.root)

    def new_blank(self):
        self.app.sketch = Sketch(); self.app.sketch.set_calibration((0, 0), (100, 0), 1); self.app.sketch.set_origin((1000, 700))
        self.app.canvas.set_image(Image.new("RGBA", (2000, 1400), "white"))
        self.app.image_name.set("下絵なし — mm座標で編集")

    def _loaded(self, name, sketch, image):
        self.app.select_tool(); self.app.sketch = sketch
        if image is None:
            image = Image.new("RGBA", (2000, 1400), "white")
            if sketch.calibration is None:
                left = min((p.x-p.width-p.height for p in sketch.pads), default=-5)
                right = max((p.x+p.width+p.height for p in sketch.pads), default=5)
                top = min((p.y-p.width-p.height for p in sketch.pads), default=-5)
                bottom = max((p.y+p.width+p.height for p in sketch.pads), default=5)
                scale = max((right-left)/1800, (bottom-top)/1200, 0.001)
                sketch.set_calibration((0, 0), (1, 0), scale)
                if sketch.origin is None:
                    sketch.origin = (1000-(left+right)/2/scale, 700-(top+bottom)/2/scale)
            if sketch.origin is None: sketch.origin = (1000, 700)
        self.app.canvas.set_image(image); self.app.history.clear(); self.app.selected_index = None
        self.app.calibration_points.clear(); self.app.footprint_name.set(name)
        self.fields["reference"].set(sketch.reference); self.fields["doc_value"].set(sketch.value)
        self._mark_saved()
        self.app._refresh_pad_list(); self.app._refresh()

    def new(self):
        if not self._confirm_discard(): return
        self.new_blank(); self.app.history.clear(); self.app.selected_index = None; self.app.calibration_points.clear()
        self.app.select_tool(); self.app.footprint_name.set(""); self.project_path = None
        self._mark_saved(); self.app._refresh_pad_list(); self.app._refresh()

    def open_footprint(self):
        path = filedialog.askopenfilename(parent=self.root, title="KiCadフットプリントを開く", filetypes=[("KiCad", "*.kicad_mod")])
        if not path or not self._confirm_discard(): return
        try:
            from .persistence import load_footprint
            name, sketch, classification = load_footprint(Path(path))
            self._loaded(name, sketch, None); self.app.image_name.set(Path(path).name+" — 既存形状")
            self.app._message("読み込みました。"+" / ".join(classification)); self.project_path = None
            if classification: messagebox.showinfo("読込要素の分類", "\n".join(classification), parent=self.root)
        except (ValueError, OSError) as error: self.app._message("読込失敗: "+str(error), error=True)

    def open_project(self):
        path = filedialog.askopenfilename(parent=self.root, title="編集用プロジェクトを開く", filetypes=[("Footprint Sketcher", "*.kfsproj")])
        if not path or not self._confirm_discard(): return
        try:
            from .persistence import load_project
            name, sketch, image = load_project(Path(path)); self._loaded(name, sketch, image)
            self.project_path = Path(path); self.app.image_name.set(Path(path).name)
            self.app._message("下絵・校正・図形・拘束を復元しました。")
        except (ValueError, OSError) as error: self.app._message("プロジェクト読込失敗: "+str(error), error=True)

    def save(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="編集用プロジェクトを保存", defaultextension=".kfsproj", filetypes=[("Footprint Sketcher", "*.kfsproj")])
        if not path: return
        try:
            from .persistence import save_project
            save_project(Path(path), self.app.sketch, self.app.canvas.image, self.app.footprint_name.get())
            self.project_path = Path(path); self._mark_saved()
            self.app._message("編集用データを保存しました。 "+path)
        except (ValueError, OSError) as error: self.app._message("保存失敗: "+str(error), error=True)

    def attach_reference(self):
        path = filedialog.askopenfilename(parent=self.root, title="図形を残して下絵を追加", filetypes=[("PNG / JPEG / PDF", ("*.png", "*.jpg", "*.jpeg", "*.pdf"))])
        if not path: return
        if Path(path).suffix.lower() == ".pdf":
            self.open_pdf(keep_geometry=True, path=path)
            return
        try:
            with Image.open(path, formats=("PNG", "JPEG")) as source:
                image = source.convert("RGBA")
            self._attach_image(image, Path(path).name)
        except (ValueError, OSError, Image.DecompressionBombError) as error:
            self.app._message("下絵読込失敗: "+str(error), error=True)

    def _attach_image(self, image, name):
        app = self.app
        app.select_tool(); app.selected_index = None; app.history.clear()
        app.sketch.calibration = None; app.sketch.origin = None
        app.canvas.set_image(image); app.image_name.set(name)
        app._refresh_pad_list(); app._refresh()
        app._message("図形・拘束のmm座標を保持して下絵を追加しました。新しい下絵を校正し、原点を指定してください。")

    def open_pdf(self, *, keep_geometry=False, path=None):
        from .reference import pdf_page_count, render_pdf_page
        path = path or filedialog.askopenfilename(parent=self.root, title="PDF下絵を開く", filetypes=[("PDF", "*.pdf")])
        if not path: return
        try:
            pages = pdf_page_count(Path(path))
            page = simpledialog.askinteger("PDFページ", f"1〜{pages}ページ", minvalue=1, maxvalue=pages, parent=self.root)
            if page is None: return
            image = render_pdf_page(Path(path), page-1)
            text = simpledialog.askstring("PDF範囲", f"画像 {image.width}×{image.height} px。範囲はX,Y,幅,高さpx（空欄は全体）", initialvalue="", parent=self.root)
            if text is None: return
            if text.strip(): image = render_pdf_page(Path(path), page-1, crop=tuple(int(v.strip()) for v in text.split(",")))
            if keep_geometry:
                self._attach_image(image, f"{Path(path).name} — {page}ページ")
                return
            if not self._confirm_discard(): return
            self._loaded(self.app.footprint_name.get(), Sketch(), image); self.app.image_name.set(f"{Path(path).name} — {page}ページ")
            self.app._message("PDFを下絵として表示しました。校正と原点を指定してください。")
        except (ValueError, OSError, RuntimeError) as error: self.app._message("PDF読込失敗: "+str(error), error=True)

    def close(self):
        if self._confirm_discard(): self.root.destroy()
