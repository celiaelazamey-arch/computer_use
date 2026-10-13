import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from cela_sh.memory.document_loader import DocumentLoadError, chunk_text, extract_document


class DocumentLoaderTests(unittest.TestCase):
    def test_extracts_utf8_markdown_with_basename_hash_and_private_source_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            path.write_text("# دليل\n\nمحتوى محلي", encoding="utf-8")

            document = extract_document(path)

        self.assertEqual(document.source_name, "README.md")
        self.assertTrue(document.source_path.startswith("external/"))
        self.assertNotIn(directory, document.source_path)
        self.assertEqual(document.text, "# دليل\n\nمحتوى محلي")
        self.assertEqual(len(document.content_sha256), 64)

    def test_preserves_project_relative_source_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide = root / "docs" / "guide.md"
            guide.parent.mkdir()
            guide.write_text("deployment steps", encoding="utf-8")
            with patch("cela_sh.memory.document_loader.Path.cwd", return_value=root):
                document = extract_document(guide)

        self.assertEqual(document.source_path, "docs/guide.md")

    def test_rejects_unsupported_extensions_invalid_utf8_and_blank_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsupported = root / "notes.docx"
            unsupported.write_bytes(b"data")
            invalid = root / "notes.txt"
            invalid.write_bytes(b"\xff")
            blank = root / "empty.md"
            blank.write_text(" \n ", encoding="utf-8")

            for path in (unsupported, invalid, blank, root / "missing.md"):
                with self.subTest(path=path.name), self.assertRaises(DocumentLoadError):
                    extract_document(path)

    def test_chunks_are_bounded_and_overlap(self):
        text = "".join(str(index % 10) for index in range(2_000))

        chunks = chunk_text(text, size=500, overlap=50)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 500 for chunk in chunks))
        self.assertTrue(all(chunks[index][-50:] == chunks[index + 1][:50] for index in range(len(chunks) - 1)))

    def test_chunk_parameters_are_validated(self):
        with self.assertRaises(ValueError):
            chunk_text("some text", size=0)
        with self.assertRaises(ValueError):
            chunk_text("some text", size=10, overlap=10)

    def test_pdf_extraction_is_local_and_rejects_encrypted_documents(self):
        class Page:
            def extract_text(self):
                return "PDF extracted text"

        class Reader:
            is_encrypted = False
            pages = [Page()]

        pdf_module = ModuleType("pypdf")
        pdf_module.PdfReader = lambda stream, strict=False: Reader()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "architecture.pdf"
            path.write_bytes(b"fake PDF bytes")
            with patch.dict("sys.modules", {"pypdf": pdf_module}):
                self.assertEqual(extract_document(path).text, "PDF extracted text")

                class EncryptedReader(Reader):
                    is_encrypted = True

                pdf_module.PdfReader = lambda stream, strict=False: EncryptedReader()
                with self.assertRaises(DocumentLoadError):
                    extract_document(path)

    def test_pdf_support_explains_the_optional_extra_when_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "architecture.pdf"
            path.write_bytes(b"fake PDF bytes")
            with patch.dict("sys.modules", {"pypdf": None}):
                with self.assertRaisesRegex(DocumentLoadError, "documents"):
                    extract_document(path)


if __name__ == "__main__":
    unittest.main()
