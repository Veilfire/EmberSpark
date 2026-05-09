"""Pluggable embedding provider interface + sentence-transformers default."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from spark.logging import get_logger

log = get_logger("spark.memory.embeddings")


class EmbeddingProvider(Protocol):
    provider: str
    model_name: str
    dimension: int | None

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformersProvider:
    """Local embedding provider using the `sentence-transformers` library.

    Lazy-loads the model on first use so import cost is deferred.
    """

    provider: str = "sentence_transformers"

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model_name = model_name
        self._model: object | None = None
        self._dim: int | None = None

    @property
    def dimension(self) -> int | None:
        return self._dim

    def _load(self) -> object:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            # ``get_sentence_embedding_dimension`` was renamed to
            # ``get_embedding_dimension`` in newer sentence-transformers
            # releases (and emits a FutureWarning under the old name).
            # Prefer the new name when present, fall back to the old one
            # so this code keeps working on both versions.
            try:
                m = self._model
                accessor = getattr(
                    m,
                    "get_embedding_dimension",
                    getattr(m, "get_sentence_embedding_dimension", None),
                )
                self._dim = int(accessor()) if accessor is not None else None
            except Exception:  # pragma: no cover
                self._dim = None
        return self._model

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [list(map(float, v)) for v in vectors]


# ---------------------------------------------------------------------------
# Startup preload — pay the embedding-model cold-start at container boot,
# not on the first user-facing request.
# ---------------------------------------------------------------------------


def preload(model_name: str) -> None:
    """Load the named sentence-transformers model into memory + disk cache.

    Idempotent — sentence-transformers caches weights under
    ``~/.cache/huggingface/hub`` after the first download, so subsequent
    constructions of the same model name are fast and reuse those bytes.
    The provider's lazy ``_load()`` is what we actually invoke; calling
    it here just shifts the work earlier in the process lifecycle.

    Failures are logged and swallowed: preload is best-effort. If HF is
    unreachable at boot, the first request will fail with the same
    error and the operator can investigate then.
    """
    started = time.monotonic()
    log.info("embeddings.preload_started", model=model_name)
    try:
        provider = SentenceTransformersProvider(model_name)
        provider._load()
        duration_ms = (time.monotonic() - started) * 1000
        log.info(
            "embeddings.preload_complete",
            model=model_name,
            duration_ms=round(duration_ms, 1),
            dimension=provider.dimension,
        )
    except Exception as exc:  # pragma: no cover — best-effort
        duration_ms = (time.monotonic() - started) * 1000
        log.warning(
            "embeddings.preload_failed",
            model=model_name,
            duration_ms=round(duration_ms, 1),
            error=str(exc),
        )


def discover_required_models() -> set[str]:
    """Walk ``~/.spark/agents/*.yaml`` and collect every embedder model name
    referenced by an agent that has long-term memory enabled.

    Returns the unique set so the caller can preload each exactly once,
    no matter how many agents share the same model.
    """
    from spark.config.loader import load_agent  # noqa: PLC0415 — local for fast import

    agents_dir = Path("~/.spark/agents").expanduser()
    out: set[str] = set()
    if not agents_dir.is_dir():
        return out
    for yaml_path in sorted(agents_dir.glob("*.yaml")):
        try:
            agent = load_agent(yaml_path)
        except Exception:
            # Mirrors the scheduler's discover loop — bad YAML
            # shouldn't take down preload. The agent will fail on its
            # own load path with a clear error when next used.
            continue
        ltm = agent.spec.memory.long_term_memory
        if ltm is None or not ltm.enabled:
            continue
        out.add(ltm.embedder.model)
    return out


def preload_all() -> int:
    """Discover + preload every embedding model needed by the active agents.

    Returns the count of models loaded (0 when no agents need embeddings).
    Designed to run inside ``asyncio.to_thread`` from the web app's
    startup hook so the synchronous ``SentenceTransformer(...)``
    constructor doesn't stall the event loop.
    """
    models = discover_required_models()
    if not models:
        log.info("embeddings.preload_skipped", reason="no_models_required")
        return 0
    for name in models:
        preload(name)
    return len(models)
