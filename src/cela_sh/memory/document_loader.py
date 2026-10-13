"""Bounded offline extraction and overlap chunking for local knowledge files."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from .sources import normalize_source_path

MAX_FILE_BYTES = 10_000_000
MAX_EXTRACTED_CHARACTERS = 1_000_000
MAX_PDF_PAGES = 500
MAX_CHUNKS = 250
CHUNK_SIZE = 6_000
CHUNK_OVERLAP = 400
SUPPORTED_TEXT_EXTENSIONS = {".md", ".txt"}


class DocumentLoadError(ValueError):
    """Raised when a local knowledge document cannot be safely extracted."""


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    source_name: str
    source_path: str
    content_sha256: str
    text: str


def extract_document(path: str | Path) -> ExtractedDocument:
    """Read UTF-8 Markdown/text or a text-based PDF without network access."""
    if not isinstance(path, (str, Path)) or not str(path).strip():
        raise DocumentLoadError("A local .md, .txt, or .pdf path is required.")
    document_path = Path(path)
    suffix = document_path.suffix.casefold()
    if suffix not in SUPPORTED_TEXT_EXTENSIONS | {".pdf"}:
        raise DocumentLoadError("Supported knowledge formats are .md, .txt, and .pdf.")
    try:
        if not document_path.is_file():
            raise DocumentLoadError("The selected knowledge file does not exist or is not a regular file.")
        raw = document_path.read_bytes()
    except DocumentLoadError:
        raise
    except OSError as exc:
        raise DocumentLoadError("The selected knowledge file could not be read.") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise DocumentLoadError(f"Knowledge files must be no larger than {MAX_FILE_BYTES} bytes.")

    if suffix in SUPPORTED_TEXT_EXTENSIONS:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentLoadError("Markdown and text files must use UTF-8 encoding.") from exc
    else:
        text = _extract_pdf(raw)

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise DocumentLoadError("No extractable text was found in the selected file.")
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise DocumentLoadError(
            f"Extracted text must not exceed {MAX_EXTRACTED_CHARACTERS} characters."
        )
    return ExtractedDocument(
        source_name=document_path.name,
        source_path=_safe_source_path(document_path),
        content_sha256=sha256(raw).hexdigest(),
        text=text,
    )


def _safe_source_path(document_path: Path) -> str:
    """Use a path relative to the working project; never persist an OS path."""
    try:
        resolved = document_path.resolve(strict=True)
        working_root = Path.cwd().resolve()
    except (OSError, RuntimeError) as exc:
        raise DocumentLoadError("The selected knowledge file path could not be resolved.") from exc

    try:
        relative = resolved.relative_to(working_root)
        return normalize_source_path("/".join(quote(part, safe="-._~") for part in relative.parts))
    except ValueError:
        # External files remain importable without writing usernames or host
        # directory names into memory. The opaque directory is stable for this
        # file location on this machine; callers can later provide a project path.
        location_key = sha256(resolved.as_posix().encode("utf-8")).hexdigest()[:16]
        safe_name = quote(resolved.name, safe="-._~")
        return normalize_source_path(f"external/{location_key}/{safe_name}")


def _extract_pdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentLoadError(
            "PDF support is optional; install it with `python -m pip install 'cela-sh[documents]'`."
        ) from exc

    try:
        reader = PdfReader(BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise DocumentLoadError("Encrypted PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentLoadError(f"PDF files may contain at most {MAX_PDF_PAGES} pages.")
        pages = []
        total_characters = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            total_characters += len(page_text)
            if total_characters > MAX_EXTRACTED_CHARACTERS:
                raise DocumentLoadError(
                    f"Extracted text must not exceed {MAX_EXTRACTED_CHARACTERS} characters."
                )
            pages.append(page_text)
    except DocumentLoadError:
        raise
    except Exception as exc:
        raise DocumentLoadError("The PDF could not be parsed safely.") from exc
    return "\n\n".join(pages)


def chunk_text(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split extracted text into bounded overlapping chunks near paragraph boundaries."""
    if not isinstance(text, str) or not text.strip():
        raise DocumentLoadError("Document text must not be blank.")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 8_000:
        raise ValueError("chunk size must be between 1 and 8000 characters")
    if isinstance(overlap, bool) or not isinstance(overlap, int) or not 0 <= overlap < size:
        raise ValueError("chunk overlap must be non-negative and smaller than the chunk size")

    clean_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    chunks = []
    start = 0
    while start < len(clean_text):
        end = min(start + size, len(clean_text))
        if end < len(clean_text):
            earliest_boundary = start + size // 2
            boundary = max(
                clean_text.rfind("\n\n", earliest_boundary, end),
                clean_text.rfind("\n", earliest_boundary, end),
                clean_text.rfind(" ", earliest_boundary, end),
            )
            if boundary > start:
                end = boundary
        chunk = clean_text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if len(chunks) > MAX_CHUNKS:
            raise DocumentLoadError(f"Documents may produce at most {MAX_CHUNKS} knowledge chunks.")
        if end >= len(clean_text):
            break
        start = max(start + 1, end - overlap)
    return chunks
