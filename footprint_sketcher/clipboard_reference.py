"""Read an explicitly requested clipboard image or local reference file.

This module does not monitor the clipboard, write to it, or fetch URLs. The
caller owns the returned image and decides when to change the current document.
"""
from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Protocol
import warnings

from PIL import Image, ImageGrab


MAX_REFERENCE_PIXELS = 50_000_000
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


class ClipboardOwner(Protocol):
    def clipboard_get(self) -> str: ...


@dataclass(frozen=True)
class ClipboardReference:
    image: Image.Image | None = None
    pdf_path: Path | None = None
    name: str = "クリップボード画像"


def _copy_image(source: Image.Image) -> Image.Image:
    if source.width <= 0 or source.height <= 0 or source.width * source.height > MAX_REFERENCE_PIXELS:
        raise ValueError("クリップボード画像が大きすぎます。範囲を小さくしてコピーしてください。")
    image = source.convert("RGBA")
    # Reference projects need pixels, not metadata from a copied file.
    image.info.clear()
    return image


def _file_reference(value: str | Path) -> ClipboardReference:
    try:
        path = Path(value)
        suffix = path.suffix.lower()
        if suffix not in _IMAGE_SUFFIXES | {".pdf"}:
            raise ValueError("PNG・JPEG・PDFファイルを1つコピーしてください。")
        if not path.is_file():
            raise ValueError("コピーしたファイルが見つかりません。ファイルをコピーし直してください。")
        if suffix == ".pdf":
            return ClipboardReference(pdf_path=path, name="クリップボードのPDF")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=("PNG", "JPEG")) as source:
                return ClipboardReference(image=_copy_image(source))
    except (OSError, SyntaxError, IndexError, struct.error, MemoryError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("コピーした画像を開けません。PNG・JPEGの内容と画像サイズを確認してください。") from None


def _text_file_reference(value: str) -> ClipboardReference:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    if not text or any(character in text for character in ("\r", "\n", "\0")):
        raise ValueError("画像、またはPNG・JPEG・PDFファイルのパスを1つコピーしてください。")
    # Accept filesystem paths only; copying a URL must never start a download.
    if "://" in text or text.lower().startswith(("www.", "data:")):
        raise ValueError("URLからは読み込みません。ファイルを保存してコピーするか、画像をコピーしてください。")
    return _file_reference(text)


def read_clipboard_reference(root: ClipboardOwner) -> ClipboardReference:
    """Read once after a user action, without exposing clipboard values in errors.

    Windows may provide an image, a filename list, or no image through Pillow.
    Text is considered only when no image/file payload was supplied. There is no
    clipboard mutation, background polling, URL handling, or PDF rendering here.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            content = ImageGrab.grabclipboard()
    except NotImplementedError:
        # The text path still works when this platform lacks a bitmap provider.
        content = None
    except (OSError, RuntimeError, ValueError, SyntaxError, IndexError, struct.error, MemoryError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("クリップボードを読み取れません。ほかの操作が終わってからコピーし直してください。") from None

    if isinstance(content, Image.Image):
        try:
            return ClipboardReference(image=_copy_image(content))
        except (OSError, RuntimeError, SyntaxError, IndexError, struct.error, MemoryError,
                Image.DecompressionBombError):
            raise ValueError("クリップボード画像を読み取れません。範囲を小さくしてコピーし直してください。") from None
        finally:
            content.close()
    if isinstance(content, (list, tuple)):
        if len(content) != 1 or not isinstance(content[0], (str, Path)):
            raise ValueError("PNG・JPEG・PDFファイルを1つだけコピーしてください。")
        return _file_reference(content[0])
    if content is not None:
        raise ValueError("クリップボードの形式に対応していません。画像かファイルをコピーしてください。")

    try:
        text = root.clipboard_get()
    except Exception:
        # TclError is deliberately not imported: this adapter's pure tests and
        # file decoder can run even when a host has no Tcl/Tk installation.
        raise ValueError("クリップボードに画像やファイルがありません。コピーしてから実行してください。") from None
    if not isinstance(text, str):
        raise ValueError("クリップボードに画像やファイルがありません。コピーしてから実行してください。")
    return _text_file_reference(text)
