from contextlib import contextmanager
import io
from pathlib import Path
import struct
import traceback
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, ImageGrab

from footprint_sketcher.clipboard_reference import MAX_REFERENCE_PIXELS, read_clipboard_reference


class ClipboardReferenceTests(unittest.TestCase):
    def read(self, content):
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", return_value=content) as grab:
            try:
                return read_clipboard_reference()
            finally:
                grab.assert_called_once_with()

    def read_windows(self, format, payload):
        # Exercise Pillow's Windows decoder while keeping the OS clipboard out
        # of the test, including on Linux hosts without this native function.
        with patch.object(ImageGrab.sys, "platform", "win32"), patch.object(
                Image.core, "grabclipboard_win32", create=True, return_value=(format, payload)):
            return read_clipboard_reference()

    @contextmanager
    def without_file_access(self):
        with patch("builtins.open") as file_open, patch.object(Path, "open") as path_open, \
             patch.object(Path, "is_file") as is_file, \
             patch("footprint_sketcher.clipboard_reference.Image.open") as image_open:
            yield
            for access in (file_open, path_open, is_file, image_open):
                access.assert_not_called()

    def png_with_trailing_chunk(self, kind, payload):
        with Image.new("RGB", (1, 1), "red") as source, io.BytesIO() as output:
            source.save(output, format="PNG")
            png = output.getvalue()
        chunk = (struct.pack(">I", len(payload)) + kind + payload
                 + struct.pack(">I", zlib.crc32(kind + payload)))
        return png[:-12] + chunk + png[-12:]

    def test_bitmap_is_independent_rgba_and_preserves_alpha_without_metadata(self):
        original = Image.new("RGBA", (4, 3), (30, 70, 120, 90))
        original.info["comment"] = "not part of the reference pixels"
        result = self.read(original)
        self.addCleanup(result.image.close)
        self.assertIsNot(result.image, original)
        self.assertEqual(result.image.mode, "RGBA")
        self.assertEqual(result.image.getpixel((2, 1)), (30, 70, 120, 90))
        self.assertEqual(result.image.info, {})
        self.assertEqual(result.name, "クリップボード画像")
        self.assertEqual(set(vars(result)), {"image", "name"})
        with self.assertRaises(ValueError):
            original.getpixel((0, 0))

    def test_oversized_bitmap_is_rejected_before_conversion_and_released(self):
        self.assertEqual(MAX_REFERENCE_PIXELS, 50_000_000)
        original = Image.new("RGB", (2, 2))
        with patch("footprint_sketcher.clipboard_reference.MAX_REFERENCE_PIXELS", 3), patch.object(original, "convert") as convert:
            with self.assertRaisesRegex(ValueError, "大きすぎ"):
                self.read(original)
            convert.assert_not_called()
        with self.assertRaises(ValueError):
            original.getpixel((0, 0))

    def test_paletted_windows_png_transparency_survives_metadata_removal(self):
        with Image.new("P", (2, 1), 0) as source, io.BytesIO() as output:
            source.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
            source.putpixel((1, 0), 1)
            source.save(output, format="PNG", transparency=0)
            payload = output.getvalue()
        result = self.read_windows("png", payload)
        self.addCleanup(result.image.close)
        self.assertEqual(result.image.getpixel((0, 0))[3], 0)
        self.assertEqual(result.image.getpixel((1, 0)), (255, 0, 0, 255))
        self.assertEqual(result.image.info, {})

    def test_windows_png_pixels_are_detached_from_decoder(self):
        with Image.new("RGBA", (3, 2), (12, 34, 56, 78)) as source, io.BytesIO() as output:
            source.save(output, format="PNG")
            result = self.read_windows("png", output.getvalue())
        self.addCleanup(result.image.close)
        self.assertEqual(result.image.size, (3, 2))
        self.assertEqual(result.image.getpixel((0, 0)), (12, 34, 56, 78))
        self.assertEqual(result.name, "クリップボード画像")

    def test_copied_image_and_pdf_files_are_rejected_without_file_access(self):
        for suffix in ("png", "jpg", "jpeg", "PDF"):
            path = Path("private clipboard") / ("sample." + suffix)
            for content in ([str(path)], [path], (str(path),)):
                with self.subTest(content=content), self.without_file_access():
                    with self.assertRaisesRegex(ValueError, "画像をコピー") as caught:
                        self.read(content)
                    self.assertNotIn(str(path), str(caught.exception))

    def test_windows_cf_hdrop_files_are_rejected_without_file_access(self):
        for filename in ("private image.png", "private image.jpg", "private document.pdf"):
            payload = struct.pack("<IiiII", 20, 0, 0, 0, 1) + (filename + "\0\0").encode("utf-16le")
            with self.subTest(filename=filename), self.without_file_access():
                with self.assertRaisesRegex(ValueError, "画像をコピー") as caught:
                    self.read_windows("file", payload)
                self.assertNotIn(filename, str(caught.exception))

    def test_text_paths_urls_and_unknown_payloads_are_rejected_without_file_access(self):
        for content in ("private.png", '"private image.jpg"\r\n', "private.pdf",
                        "https://example.invalid/private.pdf", "file:///private.pdf",
                        "data:image/png;base64,private", "first.pdf\nsecond.pdf", "private.pdf\0",
                        b"unsupported", 1, [], ["first.png", "second.pdf"], [1], None):
            with self.subTest(content=content), self.without_file_access():
                with self.assertRaisesRegex(ValueError, "画像をコピー"):
                    self.read(content)

    def test_unavailable_bitmap_provider_has_no_text_or_file_fallback(self):
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", side_effect=NotImplementedError("private detail")), \
             self.without_file_access():
            with self.assertRaisesRegex(ValueError, "この環境") as caught:
                read_clipboard_reference()
        self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))

    def test_clipboard_busy_and_empty_errors_do_not_reveal_payloads(self):
        marker = "private clipboard detail"
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", side_effect=OSError(marker)):
            with self.assertRaises(ValueError) as caught:
                read_clipboard_reference()
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn(marker, "".join(traceback.format_exception(caught.exception)))
        with self.assertRaises(ValueError) as caught:
            self.read(None)
        self.assertNotIn(marker, "".join(traceback.format_exception(caught.exception)))

    def test_decompression_warning_is_rejected_by_bitmap_reader(self):
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", side_effect=Image.DecompressionBombWarning("private too large")):
            with self.assertRaisesRegex(ValueError, "読み取れません") as caught:
                read_clipboard_reference()
        self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))

    def test_bitmap_conversion_errors_are_normalized_and_source_is_released(self):
        for error in (OSError("private"), RuntimeError("private"), ValueError("private"),
                      MemoryError("private"), Image.DecompressionBombWarning("private")):
            with self.subTest(error=type(error).__name__):
                source = Image.new("RGB", (2, 2))
                with patch.object(source, "convert", side_effect=error):
                    with self.assertRaisesRegex(ValueError, "読み取れません") as caught:
                        self.read(source)
                self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))
                with self.assertRaises(ValueError):
                    source.getpixel((0, 0))

    def test_malformed_windows_payloads_are_reported_without_decoder_details(self):
        for format, payload in (("png", b"private invalid PNG"), ("file", b"x"),
                                ("file", b"\0" * 4)):
            with self.subTest(format=format, payload=payload):
                with self.assertRaisesRegex(ValueError, "読み取れません") as caught:
                    self.read_windows(format, payload)
                self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))

    def test_corrupt_png_metadata_is_rejected_after_opening_and_source_is_closed(self):
        # Pillow parses these chunks lazily while converting pixels; each one
        # raises a different decoder exception after Image.open has succeeded.
        for kind, payload in ((b"tRNS", b"\0"), (b"iCCP", b""),
                              (b"zTXt", b"private\0\1invalid")):
            png = self.png_with_trailing_chunk(kind, payload)
            with self.subTest(kind=kind, source="bitmap"):
                source = Image.open(io.BytesIO(png))
                with self.assertRaisesRegex(ValueError, "読み取れません") as caught:
                    self.read(source)
                self.assertIsNone(source.fp)
                self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))
            with self.subTest(kind=kind, source="windows"):
                with self.assertRaisesRegex(ValueError, "読み取れません") as caught:
                    self.read_windows("png", png)
                self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))


if __name__ == "__main__":
    unittest.main()
