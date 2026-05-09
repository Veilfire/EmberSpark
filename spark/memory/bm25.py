"""BM25 sidecar index for hybrid retrieval (T1.1).

Maintains an in-memory BM25 index per (namespace, collection). Invalidated
on write via `update_namespace()`, rebuilt lazily on query. Falls back
gracefully if `rank_bm25` is not installed.

Design notes:
- We rebuild the whole namespace on any change rather than maintain
  incremental state. BM25 indexes are cheap enough for 10k-doc
  namespaces; if we ever exceed that this moves to a proper sidecar DB.
- Tokens are lowercase alphanumeric, min length 2. Good enough for
  operator-facing text; can be replaced with a proper tokenizer later.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")
_lock = threading.Lock()


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


@dataclass
class _Index:
    ids: list[str]
    bm25: Any  # rank_bm25.BM25Okapi instance


# Key = namespace
_cache: dict[str, _Index] = {}


def _available() -> bool:
    try:
        import rank_bm25  # noqa: F401, PLC0415

        return True
    except ImportError:
        return False


def invalidate(namespace: str) -> None:
    with _lock:
        _cache.pop(namespace, None)


def invalidate_all() -> None:
    with _lock:
        _cache.clear()


async def _load_namespace(namespace: str) -> _Index | None:
    """Load a namespace's docs from the SQLite index and build BM25."""
    if not _available():
        return None
    from rank_bm25 import BM25Okapi  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    from spark.persistence.db import session_scope  # noqa: PLC0415
    from spark.persistence.models import LongTermMemoryIndexRow  # noqa: PLC0415

    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow).where(
            LongTermMemoryIndexRow.namespace == namespace
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    if not rows:
        return None

    ids: list[str] = []
    corpus: list[list[str]] = []
    for r in rows:
        toks = tokenize(r.content_summary)
        if not toks:
            continue
        ids.append(r.memory_id)
        corpus.append(toks)

    if not corpus:
        return None
    return _Index(ids=ids, bm25=BM25Okapi(corpus))


async def search(namespace: str, query: str, top_k: int = 20) -> list[tuple[str, float]]:
    """Return (memory_id, bm25_score) pairs, highest first."""
    if not _available() or not query.strip():
        return []
    with _lock:
        idx = _cache.get(namespace)
    if idx is None:
        fresh = await _load_namespace(namespace)
        if fresh is None:
            return []
        with _lock:
            _cache[namespace] = fresh
        idx = fresh

    tokens = tokenize(query)
    if not tokens:
        return []
    scores = idx.bm25.get_scores(tokens)
    pairs = [
        (idx.ids[i], float(scores[i]))
        for i in range(len(idx.ids))
        if scores[i] > 0
    ]
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs[:top_k]
