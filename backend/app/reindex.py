"""Re-index command (spec section 4): re-embed every active chunk with the current
embedding model and rebuild the vector store. Required after changing models.embedding.

Stop the API server first, then:
    cd backend; .venv\\Scripts\\python -m app.reindex

Inactive versions lose their vectors; they are re-embedded if they are rolled back.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from .config import Config, load_config
from .ollama_client import OllamaClient
from .store.sqlite import Database, open_database
from .store.vectors import VectorStore, chunk_metadata, open_vector_store


async def reindex(config: Config, db: Database, vectors: VectorStore, embedder, *, document_id: int | None = None,
                  log=print) -> dict[str, int]:
    """Re-embed active chunks. With document_id, only that document's active version
    (POST /documents/{id}/reindex) and the rest of the store is left alone."""
    if document_id is None:
        rows = db.active_chunks()
        vectors.reset()
    else:
        active = db.active_version(document_id)
        if active is None:
            raise ValueError(f"document {document_id} has no active version")
        rows = db.chunks_for_version(active["id"])
        vectors.delete_version(active["id"])
    size = config.ingestion.embed_batch_size
    start = time.perf_counter()
    for i in range(0, len(rows), size):
        batch = rows[i : i + size]
        vecs = await embedder.embed_documents([r["text"] for r in batch])
        vectors.add([r["id"] for r in batch], vecs, [chunk_metadata(r) for r in batch], [r["text"] for r in batch])
        log(f"  embedded {min(i + size, len(rows))} of {len(rows)}")
    with db.transaction() as conn:
        if document_id is None:
            # The store was rebuilt: inactive versions have no vectors any more.
            conn.execute("UPDATE versions SET embedding_model = NULL WHERE status != 'active'")
            conn.execute("UPDATE versions SET embedding_model = ? WHERE status = 'active'", (config.models.embedding,))
            db.set_meta("embedding_model", config.models.embedding, conn)
        else:
            conn.execute("UPDATE versions SET embedding_model = ? WHERE document_id = ? AND status = 'active'",
                         (config.models.embedding, document_id))
        db.bump_generation(conn)
    return {"chunks": len(rows), "seconds": int(time.perf_counter() - start)}


async def _main(config: Config) -> dict[str, int]:
    db = open_database(config)
    vectors = VectorStore(open_vector_store(config))
    try:
        async with OllamaClient(config) as ollama:
            return await reindex(config, db, vectors, ollama)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-embed every active chunk and rebuild the vector store.")
    parser.add_argument("--config", help="path to config.yaml (default: repository root)")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    print(f"Re-indexing with embedding model {config.models.embedding} ...")
    result = asyncio.run(_main(config))
    print(f"Done: {result['chunks']} active chunks re-embedded in {result['seconds']} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
