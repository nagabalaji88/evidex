"""Local persistent Chroma vector store (spec section 4).

Chunk metadata carries doc name, version, page, section, source_location and status.
Every query filters on status = active *and* on the active version ids from SQLite,
so a chunk of an inactive or pending version can never be returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .._compat import chroma_settings
from ..config import Config

COLLECTION = "chunks"


def open_vector_store(config: Config):
    """Open (or create) the persistent Chroma client at storage.vector_store."""
    import chromadb

    path = config.vector_store_path
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path), settings=chroma_settings())


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    similarity: float  # cosine similarity, 1 - cosine distance
    metadata: dict[str, Any]


def chunk_metadata(row) -> dict[str, Any]:
    """Chroma metadata for a chunk row (Chroma does not accept None values)."""
    return {
        "document_id": int(row["document_id"]),
        "version_id": int(row["version_id"]),
        "doc_name": row["doc_name"],
        "version": int(row["version"]),
        "page": int(row["page"]) if row["page"] is not None else -1,
        "section": row["section"] or "",
        "source_location": row["source_location"],
        "status": row["status"],
    }


class VectorStore:
    def __init__(self, client):
        self.client = client
        self.collection = self._collection()

    def _collection(self):
        return self.client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    def heartbeat(self):
        return self.client.heartbeat()

    def reset(self) -> None:
        """Drop and recreate the collection (used by the re-index command)."""
        try:
            self.client.delete_collection(COLLECTION)
        except Exception:
            pass
        self.collection = self._collection()

    def add(self, ids: Sequence[str], embeddings: Sequence[Sequence[float]], metadatas, documents) -> None:
        if ids:
            self.collection.upsert(
                ids=list(ids), embeddings=[list(e) for e in embeddings], metadatas=list(metadatas), documents=list(documents)
            )

    def ids_for_version(self, version_id: int) -> list[str]:
        return self.collection.get(where={"version_id": version_id}, include=[])["ids"]

    def set_status(self, version_id: int, status: str) -> None:
        got = self.collection.get(where={"version_id": version_id}, include=["metadatas"])
        if not got["ids"]:
            return
        metas = [{**m, "status": status} for m in got["metadatas"]]
        self.collection.update(ids=got["ids"], metadatas=metas)

    def status_counts(self, version_id: int) -> dict[str, int]:
        got = self.collection.get(where={"version_id": version_id}, include=["metadatas"])
        counts: dict[str, int] = {}
        for m in got["metadatas"]:
            counts[m["status"]] = counts.get(m["status"], 0) + 1
        return counts

    def delete_version(self, version_id: int) -> None:
        self.collection.delete(where={"version_id": version_id})

    def count(self) -> int:
        return self.collection.count()

    def search(
        self,
        embedding: Sequence[float],
        k: int,
        active_version_ids: Sequence[int],
    ) -> list[VectorHit]:
        """Nearest active chunks. `active_version_ids` comes from SQLite (source of truth)."""
        if not active_version_ids or k <= 0:
            return []
        where = {"$and": [{"status": "active"}, {"version_id": {"$in": [int(v) for v in active_version_ids]}}]}
        res = self.collection.query(
            query_embeddings=[list(embedding)], n_results=k, where=where, include=["metadatas", "distances"]
        )
        return [
            VectorHit(chunk_id=cid, similarity=1.0 - float(dist), metadata=meta)
            for cid, dist, meta in zip(res["ids"][0], res["distances"][0], res["metadatas"][0])
        ]
