import hashlib
from pathlib import Path

from app.rag.models import DocumentChunk


def _document_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("Install the 'docs' extra to read DOCX") from exc
        return "\n".join(p.text for p in Document(path).paragraphs if p.text.strip())
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install the 'docs' extra to read PDF") from exc
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    raise ValueError(f"Unsupported knowledge file: {path.name}")


def load_document(path: Path, *, approved: bool = False) -> tuple[str, dict]:
    text = load_text(path)
    metadata = {
        "document_id": _document_id(path),
        "document_name": path.name,
        "source": str(path),
        "version": "unreviewed",
        "effective_date": None,
        "is_approved": approved,
    }
    return text, metadata


def split_document(text: str, metadata: dict, *, chunk_size: int = 500, overlap: int = 80) -> list[DocumentChunk]:
    if chunk_size <= overlap or overlap < 0:
        raise ValueError("chunk_size must be greater than overlap")
    clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    chunks: list[DocumentChunk] = []
    start = 0
    index = 0
    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        piece = clean[start:end]
        chunks.append(
            DocumentChunk(
                **metadata,
                chunk_id=f"{metadata['document_id']}-{index:04d}",
                page=None,
                text=piece,
            )
        )
        if end == len(clean):
            break
        start = end - overlap
        index += 1
    return chunks

