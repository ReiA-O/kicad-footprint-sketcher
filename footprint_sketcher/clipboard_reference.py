"""Read an explicitly requested clipboard image.

This module does not monitor or write to the clipboard, read text or files, or
fetch URLs. The caller owns the returned image and decides when to change the
current document.
"""
from dataclasses import dataclass
import struct
import warnings

from PIL import Image, ImageGrab


MAX_REFERENCE_PIXELS = 50_000_000


@dataclass(frozen=True)
class ClipboardReference:
    image: Image.Image | None = None
    name: str = "クリップボード画像"


def _copy_image(source: Image.Image) -> Image.Image:
    if source.width <= 0 or source.height <= 0 or source.width * source.height > MAX_REFERENCE_PIXELS:
        raise ValueError("クリップボード画像が大きすぎます。範囲を小さくしてコピーしてください。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = source.convert("RGBA")
    except (OSError, RuntimeError, ValueError, SyntaxError, IndexError, struct.error, MemoryError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("クリップボード画像を読み取れません。範囲を小さくしてコピーし直してください。") from None
    # Reference projects need pixels, not metadata from a copied file.
    image.info.clear()
    return image


def read_clipboard_reference() -> ClipboardReference:
    """Read once after a user action, without exposing clipboard values in errors.

    Pillow may return a filename list or no image. Only image pixels are
    accepted; text, copied file paths and URLs are never read or followed.
    There is no clipboard mutation, background polling or PDF rendering here.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            content = ImageGrab.grabclipboard()
    except NotImplementedError:
        raise ValueError("この環境ではクリップボード画像を読み取れません。画像を保存して下絵ファイルとして開いてください。") from None
    except (OSError, RuntimeError, ValueError, SyntaxError, IndexError, struct.error, MemoryError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("クリップボードを読み取れません。ほかの操作が終わってからコピーし直してください。") from None

    if isinstance(content, Image.Image):
        try:
            return ClipboardReference(image=_copy_image(content))
        finally:
            content.close()
    raise ValueError("クリップボードに画像がありません。スクリーンショットなどの画像をコピーしてください。")
