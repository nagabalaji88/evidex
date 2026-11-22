"""Step 1 of the pipeline: validate the file (spec section 4)."""

from __future__ import annotations

from pathlib import PurePath

from ..config import Config
from .errors import IngestError

SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "md", ".xlsx": "xlsx", ".csv": "csv", ".json": "json"}


def clean_filename(filename: str) -> str:
    """Strip any client-supplied directory part (C:\\Users\\x\\a.pdf -> a.pdf)."""
    return PurePath((filename or "").replace("\\", "/")).name.strip()


def file_type(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        allowed = ", ".join(e.lstrip(".").upper() for e in SUPPORTED)
        raise IngestError("unsupported_type", f"File type '{ext or 'none'}' is not supported. Upload one of: {allowed}.")
    return SUPPORTED[ext]


def validate(filename: str, data: bytes, config: Config) -> str:
    """Return the file type, or raise IngestError. The scanned-PDF check runs during extraction."""
    if not filename:
        raise IngestError("bad_name", "The file has no name.")
    kind = file_type(filename)
    if len(data) == 0 or not data.strip():
        raise IngestError("empty_file", f"{filename} is empty.")
    limit = config.ingestion.max_file_mb
    if len(data) > limit * 1024 * 1024:
        raise IngestError("too_large", f"{filename} is larger than the {limit:g} MB limit.")
    return kind
