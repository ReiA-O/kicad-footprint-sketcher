"""Preview a PDF and copy a visually selected raster region as a reference."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from PIL import Image

from .reference import pdf_page_count, render_pdf_page

try:
    import tkinter as tk
    from tkinter import ttk
    from PIL import ImageTk
except ImportError:  # Pure coordinate checks remain usable without Tcl/Tk.
    tk = ttk = ImageTk = None


_PDF_ERROR = "PDFを開けません。破損・パスワード保護やファイルの状態を確認してください。"
_PAGE_SIZE_ERROR = "PDFページが大きすぎます。表示倍率を下げてください。"
_VIEWER_PAGE_SIZE_ERROR = "PDFページが大きすぎるため表示できません。小さいページのPDFを選んでください。"


def _count_pages(path):
    try:
        return pdf_page_count(path)
    except (ValueError, OSError, RuntimeError):
        raise ValueError(_PDF_ERROR) from None


def _render_page(path, page_index):
    try:
        return render_pdf_page(path, page_index)
    except (ValueError, OSError, RuntimeError) as error:
        if isinstance(error, ValueError) and str(error) == _PAGE_SIZE_ERROR:
            raise ValueError(_VIEWER_PAGE_SIZE_ERROR) from None
        raise ValueError(_PDF_ERROR) from None


def selection_crop(bounds: tuple[float, float, float, float],
                   size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Clamp a drag to the source raster, including partially selected pixels."""
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise ValueError("切り取り範囲の座標が不正です。")
    if min(size) <= 0:
        raise ValueError("画像の大きさが不正です。")
    x1, x2 = sorted((bounds[0], bounds[2]))
    y1, y2 = sorted((bounds[1], bounds[3]))
    x1, x2 = (min(size[0], max(0.0, value)) for value in (x1, x2))
    y1, y2 = (min(size[1], max(0.0, value)) for value in (y1, y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("幅と高さのある範囲をドラッグしてください。")

    def stable(value):
        nearest = round(value)
        return nearest if math.isclose(value, nearest, abs_tol=1e-7, rel_tol=0) else value

    left, top = math.floor(stable(x1)), math.floor(stable(y1))
    right, bottom = math.ceil(stable(x2)), math.ceil(stable(y2))
    if right <= left or bottom <= top:
        raise ValueError("幅と高さのある範囲をドラッグしてください。")
    return left, top, right-left, bottom-top


@dataclass(frozen=True)
class PdfReferenceSelection:
    """The caller owns the independent image returned by a completed dialog."""

    image: Image.Image
    page_index: int
    crop: tuple[int, int, int, int] | None
    source_name: str

    @property
    def label(self) -> str:
        suffix = "選択範囲" if self.crop is not None else "ページ全体"
        return f"{self.source_name} — {self.page_index+1}ページ（{suffix}）"


_DialogBase = tk.Toplevel if tk is not None else object


class PdfCropDialog(_DialogBase):
    """Modal selection UI; it never changes its parent document or source PDF."""

    MIN_ZOOM = .02
    MAX_ZOOM = 8.0
    BACKGROUND = "#e7edf3"

    def __init__(self, parent, path: Path):
        if tk is None:
            raise RuntimeError("PDFビューアーにはTcl/Tkを含むPythonが必要です。")
        self.path = Path(path)
        self.page_count = _count_pages(self.path)
        if not self.page_count:
            raise ValueError("PDFに表示できるページがありません。")
        image = _render_page(self.path, 0)
        self._page_image: Image.Image | None = image
        self.result: PdfReferenceSelection | None = None
        self.page_index = 0
        self.selection: tuple[float, float, float, float] | None = None
        self.scale = 1.0
        self.offset = (0.0, 0.0)
        self._fit_mode = True
        self._drag_start = None
        self._photo = None
        self._redraw_id = None
        self._closing = False
        self._previous_focus = None
        try:
            self._previous_focus = parent.focus_get()
            super().__init__(parent)
            self.title("PDFから下絵を切り取る — " + self.path.name)
            self.transient(parent)
            self.protocol("WM_DELETE_WINDOW", self.cancel)
            screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
            width, height = min(1000, max(640, screen_w-100)), min(760, max(420, screen_h-120))
            self.geometry(f"{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//2)}")
            self.minsize(min(640, screen_w), min(420, screen_h))
            self._build()
            self.bind("<Escape>", lambda _event: self.cancel())
            self._queue_draw()
        except Exception:
            image.close()
            self._page_image = None
            if hasattr(self, "tk"):
                try:
                    super().destroy()
                except tk.TclError:
                    pass
            raise

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)
        pages = ttk.Frame(toolbar)
        pages.grid(row=0, column=0, sticky="w")
        self.previous_button = ttk.Button(pages, text="前", width=4, command=lambda: self.show_page(self.page_index-1))
        self.previous_button.pack(side="left", padx=(0, 4))
        self.next_button = ttk.Button(pages, text="次", width=4, command=lambda: self.show_page(self.page_index+1))
        self.next_button.pack(side="left", padx=(0, 8))
        self.page_number = tk.StringVar(self, "1")
        entry = ttk.Entry(pages, textvariable=self.page_number, width=5, justify="right")
        entry.pack(side="left")
        entry.bind("<Return>", lambda _event: self.go_to_page())
        ttk.Label(pages, text=f" / {self.page_count}").pack(side="left", padx=4)
        ttk.Button(pages, text="ページを表示", command=self.go_to_page).pack(side="left", padx=4)
        zoom = ttk.Frame(toolbar)
        zoom.grid(row=0, column=2, sticky="e")
        ttk.Button(zoom, text="−", width=3, command=lambda: self.zoom_at(1/1.25)).pack(side="left")
        self.zoom_label = tk.StringVar(self, "100%")
        ttk.Label(zoom, textvariable=self.zoom_label, width=7, anchor="center").pack(side="left")
        ttk.Button(zoom, text="＋", width=3, command=lambda: self.zoom_at(1.25)).pack(side="left")
        ttk.Button(zoom, text="全体表示", command=self.fit_to_window).pack(side="left", padx=(6, 0))

        viewport = ttk.Frame(self)
        viewport.grid(row=1, column=0, sticky="nsew", padx=10)
        viewport.rowconfigure(0, weight=1)
        viewport.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(viewport, background=self.BACKGROUND, highlightthickness=0,
                                cursor="crosshair", xscrollincrement=1, yscrollincrement=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        horizontal = ttk.Scrollbar(viewport, orient="horizontal", command=self._scroll_x)
        vertical = ttk.Scrollbar(viewport, orient="vertical", command=self._scroll_y)
        horizontal.grid(row=1, column=0, sticky="ew")
        vertical.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.bind("<Configure>", self._configure)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda event: self.zoom_at(1.25, (event.x, event.y)))
        self.canvas.bind("<Button-5>", lambda event: self.zoom_at(1/1.25, (event.x, event.y)))

        footer = ttk.Frame(self, padding=10)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar(self, "左ドラッグで範囲を選択。ホイールで拡大・縮小、中ボタンドラッグで移動できます。")
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=600)
        status_label.grid(row=0, column=0, sticky="w", pady=(0, 8))
        footer.bind("<Configure>", lambda event: status_label.configure(wraplength=max(180, event.width-20)))
        buttons = ttk.Frame(footer)
        buttons.grid(row=1, column=0, sticky="e")
        ttk.Button(buttons, text="選択をクリア", command=self.clear_selection).pack(side="left", padx=4)
        self.import_button = ttk.Button(buttons, text="選択範囲を取り込む", command=self.import_selection, state="disabled")
        self.import_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="ページ全体を取り込む", command=self.import_page).pack(side="left", padx=4)
        ttk.Button(buttons, text="キャンセル", command=self.cancel).pack(side="left", padx=(12, 0))
        self._update_page_buttons()

    def _configure(self, _event=None):
        if self._fit_mode:
            self.fit_to_window()
        else:
            self._queue_draw()

    def _fit_scale(self):
        image = self._page_image
        if image is None:
            return 1.0
        return min(1.0, max(1, self.canvas.winfo_width()-24)/image.width,
                   max(1, self.canvas.winfo_height()-24)/image.height)

    def fit_to_window(self):
        if self._closing or self._page_image is None:
            return
        self._fit_mode = True
        self.scale = self._fit_scale()
        self._geometry()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._queue_draw()

    def _geometry(self):
        image = self._page_image
        if image is None:
            return
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        image_w, image_h = image.width*self.scale, image.height*self.scale
        self.offset = (max(0.0, (width-image_w)/2), max(0.0, (height-image_h)/2))
        self._region = (max(width, image_w), max(height, image_h))
        self.canvas.configure(scrollregion=(0, 0, *self._region))
        self.zoom_label.set(f"{self.scale*100:.0f}%")

    def canvas_to_image(self, x, y):
        return ((self.canvas.canvasx(x)-self.offset[0])/self.scale,
                (self.canvas.canvasy(y)-self.offset[1])/self.scale)

    def image_to_canvas(self, x, y):
        """Return viewport-relative coordinates, useful for direct event tests."""
        return (self.offset[0]+x*self.scale-self.canvas.canvasx(0),
                self.offset[1]+y*self.scale-self.canvas.canvasy(0))

    def zoom_at(self, factor, anchor=None):
        if self._closing or self._page_image is None:
            return
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("表示倍率が不正です。")
        anchor = anchor or (self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)
        point = self.canvas_to_image(*anchor)
        self.scale = min(self.MAX_ZOOM, max(min(self.MIN_ZOOM, self._fit_scale()), self.scale*factor))
        self._fit_mode = False
        self._geometry()
        self.canvas.xview_moveto((self.offset[0]+point[0]*self.scale-anchor[0])/self._region[0])
        self.canvas.yview_moveto((self.offset[1]+point[1]*self.scale-anchor[1])/self._region[1])
        self._queue_draw()

    def _wheel(self, event):
        if event.delta:
            self.zoom_at(1.25 if event.delta > 0 else 1/1.25, (event.x, event.y))
        return "break"

    def _scroll_x(self, *args):
        self.canvas.xview(*args)
        self._queue_draw()

    def _scroll_y(self, *args):
        self.canvas.yview(*args)
        self._queue_draw()

    def _pan_start(self, event):
        self.canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self._queue_draw()

    def _press(self, event):
        self._drag_start = self.canvas_to_image(event.x, event.y)
        self.selection = (*self._drag_start, *self._drag_start)
        self._update_selection()

    def _motion(self, event):
        if self._drag_start is not None:
            self.selection = (*self._drag_start, *self.canvas_to_image(event.x, event.y))
            self._update_selection()

    def _release(self, event):
        self._motion(event)
        self._drag_start = None
        crop = self.selected_crop
        if crop is None:
            self.selection = None
            self.status.set("幅と高さのある範囲をドラッグしてください。")
        else:
            x, y, width, height = crop
            self.selection = (x, y, x+width, y+height)
        self._update_selection()

    @property
    def selected_crop(self):
        if self.selection is None or self._page_image is None:
            return None
        try:
            return selection_crop(self.selection, self._page_image.size)
        except ValueError:
            return None

    def _update_selection(self):
        crop = self.selected_crop
        self.import_button.configure(state="normal" if crop is not None else "disabled")
        if crop is not None:
            self.status.set(f"選択範囲：{crop[2]} × {crop[3]} px。取り込み後に実寸を校正してください。")
        self._queue_draw()

    def clear_selection(self):
        self.selection = self._drag_start = None
        self.status.set("範囲をクリアしました。左ドラッグで選び直せます。")
        self._update_selection()

    def _queue_draw(self):
        if not self._closing and self._redraw_id is None:
            self._redraw_id = self.after_idle(self._draw)

    def _draw(self):
        self._redraw_id = None
        image = self._page_image
        if self._closing or image is None:
            return
        self._geometry()
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        origin = (self.canvas.canvasx(0), self.canvas.canvasy(0))
        # Render only the viewport; high display zoom never builds a huge bitmap.
        tile = image.transform((width, height), Image.Transform.AFFINE,
                               (1/self.scale, 0, (origin[0]-self.offset[0])/self.scale,
                                0, 1/self.scale, (origin[1]-self.offset[1])/self.scale),
                               Image.Resampling.BICUBIC, fillcolor=self.BACKGROUND)
        try:
            self._photo = ImageTk.PhotoImage(tile, master=self.canvas)
        finally:
            tile.close()
        self.canvas.delete("all")
        self.canvas.create_image(*origin, anchor="nw", image=self._photo)
        crop = self.selected_crop
        if crop is not None:
            x, y, width, height = crop
            self.canvas.create_rectangle(self.offset[0]+x*self.scale, self.offset[1]+y*self.scale,
                                         self.offset[0]+(x+width)*self.scale, self.offset[1]+(y+height)*self.scale,
                                         outline="#124ca0", width=2, dash=(6, 3))

    def _update_page_buttons(self):
        self.previous_button.configure(state="normal" if self.page_index > 0 else "disabled")
        self.next_button.configure(state="normal" if self.page_index+1 < self.page_count else "disabled")
        self.page_number.set(str(self.page_index+1))

    def go_to_page(self):
        try:
            number = int(self.page_number.get())
            if not 1 <= number <= self.page_count:
                raise ValueError
        except ValueError:
            self.page_number.set(str(self.page_index+1))
            self.status.set(f"ページは1〜{self.page_count}の整数で指定してください。")
            return
        self.show_page(number-1)

    def show_page(self, page_index):
        if not 0 <= page_index < self.page_count:
            return
        if page_index == self.page_index:
            self.page_number.set(str(self.page_index+1))
            return
        try:
            image = _render_page(self.path, page_index)
        except (ValueError, OSError, RuntimeError) as error:
            self.page_number.set(str(self.page_index+1))
            self.status.set("PDFページを表示できませんでした："+str(error))
            return
        old_image = self._page_image
        self._page_image = image
        self.page_index = page_index
        self.clear_selection()
        self._update_page_buttons()
        self.fit_to_window()
        if old_image is not None:
            old_image.close()

    def import_selection(self):
        crop = self.selected_crop
        if crop is None or self._page_image is None:
            self.status.set("先に範囲をドラッグして選択してください。")
            return
        x, y, width, height = crop
        image = self._page_image.crop((x, y, x+width, y+height))
        self.result = PdfReferenceSelection(image, self.page_index, crop, self.path.name)
        self.destroy()

    def import_page(self):
        if self._page_image is not None:
            self.result = PdfReferenceSelection(self._page_image.copy(), self.page_index, None, self.path.name)
            self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def show(self):
        try:
            if not self.winfo_viewable():
                self.wait_visibility()
            if self._closing:
                return self.result
            self.grab_set()
            self.focus_force()
            self.canvas.focus_set()
            self.wait_window()
            return self.result
        except tk.TclError:
            if not self._closing:
                self.cancel()
            return self.result

    def destroy(self):
        if self._closing:
            return
        self._closing = True
        if self._redraw_id is not None:
            try:
                self.after_cancel(self._redraw_id)
            except tk.TclError:
                pass
            self._redraw_id = None
        if self._page_image is not None:
            self._page_image.close()
            self._page_image = None
        self._photo = None
        try:
            if self.grab_current() == self:
                self.grab_release()
            super().destroy()
            previous = self._previous_focus
            if previous is not None and previous.winfo_exists():
                previous.focus_set()
            elif self.master.winfo_exists():
                self.master.focus_set()
        except tk.TclError:
            pass


def select_pdf_reference(parent, path: Path) -> PdfReferenceSelection | None:
    """Return a copied image after explicit import, or None after cancellation."""
    return PdfCropDialog(parent, path).show()
