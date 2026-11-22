"""Ingestion errors carry a stable code (for the UI and logs) and a user-facing message."""

from __future__ import annotations

SCANNED_PDF_MESSAGE = (
    "This PDF appears to be scanned and has no text layer. "
    "Upload a text-based PDF, or enable OCR in config (slow)."
)


class IngestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
