"""
LifeOps documents: check, store and read uploaded files. Every upload is untrusted until checked.

- Allowlist: PDF, JPEG, PNG, HEIC/HEIF only, recognised from the file's first bytes (its "signature"),
  never from the file name or the browser's claim. Max MAX_FILE_BYTES; PDFs max MAX_PDF_PAGES pages.
- Stored privately in <data>/files/ under a random name; the original name is kept only as text.
- Photos: location and camera details (JPEG EXIF / PNG text chunks) are removed before storing.
- PDFs: text read with pypdf, page by page (pages are kept so evidence can say "page 2").
- Photos aren't read yet (no OCR): that's Claude's job in M5. Their review starts empty.
"""

from __future__ import annotations

import hashlib
import io
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_TEXT_CHARS = 60_000  # the extractor never gets more than this
MAX_NAME_CHARS = 200


class DocumentError(Exception):
    """A problem with an upload the user can understand (shown as-is)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Kind:
    media_type: str
    extension: str
    label: str


PDF = Kind("application/pdf", ".pdf", "PDF")
JPEG = Kind("image/jpeg", ".jpg", "JPEG photo")
PNG = Kind("image/png", ".png", "PNG image")
HEIC = Kind("image/heic", ".heic", "HEIC photo")
KINDS = {k.media_type: k for k in (PDF, JPEG, PNG, HEIC)}


def sniff(data: bytes) -> Kind:
    """What the file really is, from its first bytes. Anything else is refused."""
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"mif1", b"msf1", b"heis"}:
        return HEIC
    raise DocumentError("Only PDF, JPEG, PNG and HEIC files can be uploaded (this file is something else).", 415)


def clean_name(name: str | None) -> str:
    """The original file name, kept only as display text (never used as a path)."""
    base = (name or "document").replace("\\", "/").split("/")[-1]
    printable = "".join(ch for ch in base if ch.isprintable()).strip()
    return printable[:MAX_NAME_CHARS] or "document"


# ---- removing photo metadata ------------------------------------------------------------------
def strip_jpeg_metadata(data: bytes) -> bytes:
    """Drop APP1 (EXIF/XMP: GPS, camera) and COM segments; keep everything needed to show the photo."""
    out = bytearray(data[:2])
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xDA:  # start of the image data: copy the rest as it is
            out += data[i:]
            return bytes(out)
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        segment = data[i : i + 2 + length]
        if marker not in (0xE1, 0xFE):  # APP1 = EXIF/XMP, FE = comment
            out += segment
        i += 2 + length
    out += data[i:]
    return bytes(out)


def strip_png_metadata(data: bytes) -> bytes:
    """Drop text and EXIF chunks (tEXt, zTXt, iTXt, eXIf, tIME); keep the image chunks."""
    out = bytearray(data[:8])
    i = 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        chunk_type = data[i + 4 : i + 8]
        chunk = data[i : i + 12 + length]
        if chunk_type not in {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}:
            out += chunk
        i += 12 + length
        if chunk_type == b"IEND":
            break
    return bytes(out)


# ---- reading text -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Extracted:
    pages: list[str]  # text per page (empty list for photos)

    @property
    def text(self) -> str:
        return "\n".join(self.pages)


def read_pdf(data: bytes) -> Extracted:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("This PDF is password-protected. Save an unprotected copy and upload that.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentError(f"This PDF has {len(reader.pages)} pages; the limit is {MAX_PDF_PAGES}.")
        pages, total = [], 0
        for page in reader.pages:
            text = (page.extract_text() or "")[: MAX_TEXT_CHARS - total]
            pages.append(text)
            total += len(text)
        return Extracted(pages=pages)
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, struct.error) as error:
        raise DocumentError("This PDF couldn't be read. It may be damaged.") from error


# ---- storing --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Stored:
    stored_name: str
    original_name: str
    media_type: str
    size: int
    sha256: str
    pages: int | None
    extracted: Extracted


def check_and_store(data: bytes, original_name: str | None, folder: Path) -> Stored:
    """Check an upload, clean it, extract its text (PDFs) and save it under a random name."""
    if not data:
        raise DocumentError("That file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"Files can be up to {MAX_FILE_BYTES // (1024 * 1024)} MB.", 413)
    kind = sniff(data)
    if kind is JPEG:
        data = strip_jpeg_metadata(data)
    elif kind is PNG:
        data = strip_png_metadata(data)
    extracted = read_pdf(data) if kind is PDF else Extracted(pages=[])
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = secrets.token_hex(16) + kind.extension
    (folder / stored_name).write_bytes(data)
    return Stored(
        stored_name=stored_name,
        original_name=clean_name(original_name),
        media_type=kind.media_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        pages=len(extracted.pages) if kind is PDF else None,
        extracted=extracted,
    )


def heic_to_jpeg(data: bytes) -> bytes:
    """Convert an iPhone HEIC photo to JPEG with macOS's built-in `sips`, then remove its metadata."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder) / "in.heic", Path(folder) / "out.jpg"
        source.write_bytes(data)
        try:
            subprocess.run(
                ["/usr/bin/sips", "-s", "format", "jpeg", str(source), "--out", str(target)],
                check=True, capture_output=True, timeout=60,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            raise DocumentError("This HEIC photo couldn't be converted for reading. Try a JPEG instead.") from None
        return strip_jpeg_metadata(target.read_bytes())


def for_claude(source):
    """What is sent to Claude: PDFs and JPEG/PNG as stored (already without photo metadata), HEIC converted."""
    if source.media_type in ("image/heic", "image/heif") and source.data is not None:
        source.data = heic_to_jpeg(source.data)
        source.media_type = "image/jpeg"
    return source


def remove_stored(folder: Path, stored_name: str) -> None:
    """Delete a stored file (only ever a name we generated, inside our folder)."""
    path = (folder / stored_name).resolve()
    if path.parent == folder.resolve() and path.is_file():
        path.unlink()
