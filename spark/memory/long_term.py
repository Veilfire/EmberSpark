"""Long-term memory backed by Chroma, one collection per namespace.

This module holds only the storage-side operations. The promotion pipeline
(`spark.memory.promotion`) performs classification, redaction, dedup, and
calls into here for the actual writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spark.config.enums import MemoryType, RetentionClass, Sensitivity, SourceType
from spark.memory.embeddings import EmbeddingProvider
from spark.utils.time import isoformat, utcnow


@dataclass
class MemoryRecord:
    memory_id: str
    agent_id: str
    namespace: str
    content_summary: str
    canonical_text: str
    memory_type: MemoryType
    source_type: SourceType
    sensitivity: Sensitivity
    retention_class: RetentionClass
    confidence: float
    tags: list[str] = field(default_factory=list)
    task_id: str | None = None
    session_id: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "agent_id": self.agent_id,
            "namespace": self.namespace,
            "content_summary": self.content_summary,
            "memory_type": self.memory_type.value,
            "source_type": self.source_type.value,
            "sensitivity": self.sensitivity.value,
            "retention_class": self.retention_class.value,
            "confidence": self.confidence,
            "tags": ",".join(self.tags),
            "task_id": self.task_id or "",
            "session_id": self.session_id or "",
            "created_at": isoformat(utcnow()),
        }


class LongTermMemory:
    def __init__(
        self,
        *,
        namespace: str,
        collection_name: str,
        persist_path: Path,
        embedder: EmbeddingProvider,
    ) -> None:
        self.namespace = namespace
        self.collection_name = collection_name
        self.persist_path = persist_path.expanduser().resolve()
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._client: Any | None = None
        self._collection: Any | None = None

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        import chromadb

        # Neutralize chromadb's trust_remote_code embedding-function sink
        # (CVE-2026-45829) before touching any collection. Idempotent.
        from spark.memory._chroma_hardening import harden_chromadb

        harden_chromadb()

        self._client = chromadb.PersistentClient(path=str(self.persist_path))
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"namespace": self.namespace, "spark_version": "0.1.0"},
        )
        return self._collection

    def upsert(self, record: MemoryRecord) -> None:
        collection = self._get_collection()
        embedding = self.embedder.embed(record.canonical_text)
        collection.upsert(
            ids=[record.memory_id],
            embeddings=[embedding],
            metadatas=[record.to_metadata()],
            documents=[record.canonical_text],
        )
        # Invalidate the BM25 sidecar for this namespace so the next
        # retrieval rebuilds with the new document included.
        try:
            from spark.memory import bm25 as _bm25  # noqa: PLC0415

            _bm25.invalidate(self.namespace)
        except Exception:  # pragma: no cover — best effort
            pass

    def query(
        self,
        query_text: str,
        *,
        top_k: int = 6,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._get_collection()
        embedding = self.embedder.embed(query_text)
        raw = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=where,
        )
        hits: list[dict[str, Any]] = []
        ids = raw.get("ids", [[]])[0]
        metadatas = raw.get("metadatas", [[]])[0]
        documents = raw.get("documents", [[]])[0]
        distances = raw.get("distances", [[]])[0]
        for i, memory_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            hits.append(
                {
                    "memory_id": memory_id,
                    "document": documents[i] if i < len(documents) else "",
                    "metadata": meta,
                    "distance": distances[i] if i < len(distances) else 0.0,
                }
            )
        return hits

    def delete(self, memory_id: str) -> None:
        collection = self._get_collection()
        collection.delete(ids=[memory_id])
        try:
            from spark.memory import bm25 as _bm25  # noqa: PLC0415

            _bm25.invalidate(self.namespace)
        except Exception:  # pragma: no cover
            pass
