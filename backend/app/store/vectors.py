"""Local persistent Chroma vector store (spec section 4). Collections arrive in M2."""

from __future__ import annotations

from .._compat import chroma_settings
from ..config import Config


def open_vector_store(config: Config):
    """Open (or create) the persistent Chroma store at storage.vector_store."""
    import chromadb

    path = config.vector_store_path
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path), settings=chroma_settings())
