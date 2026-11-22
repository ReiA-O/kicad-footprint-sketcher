"""Render a selected PDF page and image crop without extracting CAD data."""
from pathlib import Path
import math
from PIL import Image


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(path) as document:
        return len(document)


def render_pdf_page(path: Path, page: int, *, scale: float = 2.0,
                    crop: tuple[int, int, int, int] | None = None) -> Image.Image:
    import pypdfium2 as pdfium
    if not math.isfinite(scale) or not 0 < scale <= 8:
        raise ValueError("PDF表示倍率は0より大きく8以下を指定してください。")
    with pdfium.PdfDocument(path) as document:
        if not 0 <= page < len(document):
            raise ValueError("PDFページ番号が範囲外です。")
        selected = document[page]
        try:
            width, height = selected.get_size()
            if width * height * scale * scale > 40_000_000:
                raise ValueError("PDFページが大きすぎます。表示倍率を下げてください。")
            bitmap = selected.render(scale=scale)
            try:
                image = bitmap.to_pil().convert("RGBA").copy()
            finally:
                bitmap.close()
        finally:
            selected.close()
    if crop is not None:
        x, y, width, height = crop
        if min(x, y) < 0 or min(width, height) <= 0 or x+width > image.width or y+height > image.height:
            raise ValueError("PDFの範囲は表示画像内のX,Y,幅,高さpxを指定してください。")
        image = image.crop((x, y, x+width, y+height))
    return image
