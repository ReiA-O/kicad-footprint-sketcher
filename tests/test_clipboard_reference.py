import io
from pathlib import Path
import struct
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch
import zlib

from PIL import Image, ImageGrab

from footprint_sketcher.clipboard_reference import read_clipboard_reference


class ClipboardReferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.root = Mock()
        self.root.clipboard_get.side_effect = RuntimeError("no text")

    def read(self, content):
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", return_value=content):
            return read_clipboard_reference(self.root)

    def read_windows(self, format, payload):
        # Exercise Pillow's Windows decoder while keeping the OS clipboard out
        # of the test, including on Linux hosts without this native function.
        with patch.object(ImageGrab.sys, "platform", "win32"), patch.object(
                Image.core, "grabclipboard_win32", create=True, return_value=(format, payload)):
            return read_clipboard_reference(self.root)

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
        self.assertIsNone(result.pdf_path)
        with self.assertRaises(ValueError):
            original.getpixel((0, 0))
        self.root.clipboard_get.assert_not_called()

    def test_oversized_bitmap_is_rejected_before_conversion_and_released(self):
        original = Image.new("RGB", (2, 2))
        with patch("footprint_sketcher.clipboard_reference.MAX_REFERENCE_PIXELS", 3), patch.object(original, "convert") as convert:
            with self.assertRaisesRegex(ValueError, "大きすぎ"):
                self.read(original)
            convert.assert_not_called()
        with self.assertRaises(ValueError):
            original.getpixel((0, 0))

    def test_paletted_png_transparency_survives_metadata_removal(self):
        path = self.path / "palette.png"
        source = Image.new("P", (2, 1), 0)
        source.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        source.putpixel((1, 0), 1)
        source.save(path, transparency=0)
        source.close()
        result = self.read([str(path)])
        self.addCleanup(result.image.close)
        self.assertEqual(result.image.getpixel((0, 0))[3], 0)
        self.assertEqual(result.image.getpixel((1, 0)), (255, 0, 0, 255))
        self.assertEqual(result.image.info, {})

    def test_single_copied_image_file_is_decoded_and_file_can_be_removed(self):
        path = self.path / "sample.PNG"
        Image.new("RGBA", (3, 2), (12, 34, 56, 78)).save(path)
        result = self.read([str(path)])
        self.addCleanup(result.image.close)
        path.unlink()
        self.assertEqual(result.image.size, (3, 2))
        self.assertEqual(result.image.getpixel((0, 0)), (12, 34, 56, 78))
        self.assertEqual(result.name, "クリップボード画像")
        self.root.clipboard_get.assert_not_called()

    def test_quoted_text_file_path_with_trailing_line_ending_is_supported(self):
        path = self.path / "copied image.jpg"
        Image.new("RGB", (4, 2), "red").save(path)
        self.root.clipboard_get.side_effect = None
        self.root.clipboard_get.return_value = '"' + str(path) + '"\r\n'
        result = self.read(None)
        self.addCleanup(result.image.close)
        self.assertEqual(result.image.size, (4, 2))
        self.assertGreater(result.image.getpixel((1, 1))[0], 240)
        self.root.clipboard_get.assert_called_once_with()

    def test_pdf_file_is_returned_to_viewer_without_rendering_or_path_in_label(self):
        path = self.path / "document.PDF"
        # Rendering and validation belong to the PDF viewer, not this adapter.
        path.write_bytes(b"%PDF-placeholder")
        result = self.read([str(path)])
        self.assertIsNone(result.image)
        self.assertEqual(result.pdf_path, path)
        self.assertEqual(result.name, "クリップボードのPDF")
        self.assertNotIn(str(path), result.name)
        self.root.clipboard_get.assert_not_called()

    def test_text_path_fallback_when_bitmap_provider_is_unavailable(self):
        path = self.path / "local.pdf"
        path.write_bytes(b"%PDF-placeholder")
        self.root.clipboard_get.side_effect = None
        self.root.clipboard_get.return_value = str(path)
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", side_effect=NotImplementedError):
            result = read_clipboard_reference(self.root)
        self.assertEqual(result.pdf_path, path)

    def test_ambiguous_file_list_never_falls_back_to_unrelated_text(self):
        for files in ([], ["first.png", "second.pdf"], [1]):
            with self.subTest(files=files), self.assertRaisesRegex(ValueError, "1つ"):
                self.read(files)
        self.root.clipboard_get.assert_not_called()

    def test_urls_and_multiple_text_paths_are_rejected_without_filesystem_access(self):
        self.root.clipboard_get.side_effect = None
        for value in ("https://example.invalid/datasheet.pdf", "file:///sample.pdf", "data:image/png;base64,not-an-image", "first.pdf\nsecond.pdf", "sample.pdf\0"):
            self.root.clipboard_get.return_value = value
            with self.subTest(value=value), patch("footprint_sketcher.clipboard_reference.Path.is_file") as is_file:
                with self.assertRaises(ValueError):
                    self.read(None)
                is_file.assert_not_called()

    def test_missing_directory_unknown_and_invalid_image_are_rejected(self):
        missing = self.path / "missing.png"
        directory = self.path / "directory.pdf"
        directory.mkdir()
        other = self.path / "notes.txt"
        other.write_text("not an image")
        invalid = self.path / "invalid.png"
        invalid.write_bytes(b"not a png")
        for path in (missing, directory, other, invalid):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                self.read([str(path)])

    def test_clipboard_busy_and_empty_errors_do_not_reveal_payloads(self):
        marker = "private clipboard detail"
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", side_effect=OSError(marker)):
            with self.assertRaises(ValueError) as caught:
                read_clipboard_reference(self.root)
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn(marker, "".join(traceback.format_exception(caught.exception)))
        self.root.clipboard_get.assert_not_called()
        self.root.clipboard_get.side_effect = RuntimeError(marker)
        with self.assertRaises(ValueError) as caught:
            self.read(None)
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn(marker, "".join(traceback.format_exception(caught.exception)))

    def test_decompression_warning_is_rejected_by_bitmap_reader(self):
        with patch("footprint_sketcher.clipboard_reference.ImageGrab.grabclipboard", side_effect=Image.DecompressionBombWarning("too large")):
            with self.assertRaisesRegex(ValueError, "読み取れません"):
                read_clipboard_reference(self.root)
        self.root.clipboard_get.assert_not_called()

    def test_malformed_windows_payloads_are_reported_without_decoder_details(self):
        for format, payload in (("png", b"private invalid PNG"), ("file", b"x"),
                                ("file", b"\0" * 4)):
            with self.subTest(format=format, payload=payload):
                with self.assertRaisesRegex(ValueError, "読み取れません") as caught:
                    self.read_windows(format, payload)
                self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))
        self.root.clipboard_get.assert_not_called()

    def test_corrupt_png_metadata_is_rejected_after_opening_for_bitmap_and_file(self):
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
            with self.subTest(kind=kind, source="file"):
                path = self.path / "private.png"
                path.write_bytes(png)
                with self.assertRaisesRegex(ValueError, "開けません") as caught:
                    self.read([str(path)])
                self.assertNotIn("private", "".join(traceback.format_exception(caught.exception)))
                path.unlink()
        self.root.clipboard_get.assert_not_called()

    def test_unsupported_bitmap_payload_and_empty_text_are_rejected(self):
        with self.assertRaises(ValueError):
            self.read(b"unsupported")
        self.root.clipboard_get.side_effect = None
        for text in ("", " \r\n", None):
            self.root.clipboard_get.return_value = text
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(None)


if __name__ == "__main__":
    unittest.main()
