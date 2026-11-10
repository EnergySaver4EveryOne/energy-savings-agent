"""
PDF reading and generation utilities with zero-dependency fallbacks.

Supports PyMuPDF (`fitz`), `pdfplumber`, and `pypdf` when installed, and includes
a built-in pure-Python PDF text stream extractor and PDF generator so sample bills
and tests work out-of-the-box in any Python environment.
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path
from typing import List, Union


def extract_text_from_pdf(pdf_path: Union[str, Path]) -> str:
    """
    Extract plain text from a PDF energy bill.
    Tries PyMuPDF (`fitz`), then `pdfplumber`, then `pypdf`, then pure-Python stream extraction.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF bill not found: {path}")

    # 1. Try PyMuPDF (fitz)
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(path))
        pages = [page.get_text("text") for page in doc]
        doc.close()
        text = "\n".join(pages).strip()
        if text:
            return text
    except ImportError:
        pass
    except Exception:
        pass

    # 2. Try pdfplumber
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(path)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages).strip()
        if text:
            return text
    except ImportError:
        pass
    except Exception:
        pass

    # 3. Try pypdf
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
        if text:
            return text
    except ImportError:
        pass
    except Exception:
        pass

    # 4. Pure-Python PDF content stream parser fallback
    raw_bytes = path.read_bytes()
    return _extract_text_pure_python(raw_bytes)


def _extract_text_pure_python(pdf_bytes: bytes) -> str:
    """Extract text operators (Tj / TJ) from uncompressed or FlateDecode PDF streams."""
    extracted_lines: List[str] = []
    stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)

    for match in stream_pattern.finditer(pdf_bytes):
        stream_data = match.group(1)
        decoded = None
        try:
            decoded = zlib.decompress(stream_data).decode("latin-1", errors="ignore")
        except Exception:
            decoded = stream_data.decode("latin-1", errors="ignore")

        if not decoded:
            continue

        # Match parenthesized strings before Tj
        for tj_match in re.finditer(r"\((?:\\.|[^\\()])*\)\s*Tj", decoded):
            raw_token = tj_match.group(0)
            inner = re.sub(r"\)\s*Tj$", "", raw_token)[1:]
            inner = (
                inner.replace(r"\(", "(")
                .replace(r"\)", ")")
                .replace(r"\\", "\\")
            )
            extracted_lines.append(inner)

    return "\n".join(extracted_lines).strip()


def write_simple_pdf(output_path: Union[str, Path], lines: List[str], title: str = "Energy Bill") -> Path:
    """
    Create a valid, standard single-page PDF document from lines of text using pure Python.
    Compatible with Adobe Acrobat, Preview, PyMuPDF, pdfplumber, and pure-Python extraction.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def escape_pdf_text(text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )

    content_ops = ["BT", "/F1 10 Tf", "45 790 Td", "13 TL"]
    for line in lines:
        safe_line = escape_pdf_text(line)
        content_ops.append(f"({safe_line}) Tj")
        content_ops.append("T*")
    content_ops.append("ET")
    stream_text = "\n".join(content_ops)
    stream_bytes = stream_text.encode("latin-1", errors="replace")

    objects: List[bytes] = []
    # 1: Catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    # 2: Pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    # 3: Page
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    )
    # 4: Font
    objects.append(
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    )
    # 5: Content Stream
    objects.append(
        f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
        + stream_bytes
        + b"\nendstream\nendobj\n"
    )
    # 6: Info
    safe_title = escape_pdf_text(title).encode("latin-1", errors="replace")
    objects.append(b"6 0 obj\n<< /Title (" + safe_title + b") >>\nendobj\n")

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body = bytearray(header)
    offsets: List[int] = []
    for obj in objects:
        offsets.append(len(body))
        body.extend(obj)

    xref_offset = len(body)
    xref_lines = [f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"]
    for off in offsets:
        xref_lines.append(f"{off:010d} 00000 n \n")
    trailer = (
        "".join(xref_lines)
        + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        + f"startxref\n{xref_offset}\n%%EOF\n"
    )
    body.extend(trailer.encode("latin-1"))
    path.write_bytes(bytes(body))
    return path
