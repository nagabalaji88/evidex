"""Small helpers that keep third-party libraries local-only."""

from __future__ import annotations


def chroma_settings():
    # Everything runs locally: switch off Chroma's anonymous telemetry.
    from chromadb.config import Settings

    return Settings(anonymized_telemetry=False, allow_reset=False)
