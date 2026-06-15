"""Unit tests for the chromadb remote-code embedding-function guard.

Maps to CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c: chromadb deserializes an
embedding-function config out of a (potentially tampered) collection schema and
splats its kwargs into a model loader; a config carrying ``trust_remote_code=True``
+ an attacker-chosen model repo yields RCE. The sink exists in BOTH the dense
registry (``sentence_transformer`` -> ``SentenceTransformer(..., **kwargs)``) and
the sparse registry (``huggingface_sparse`` -> ``SparseEncoder(..., **kwargs)``),
reached via the float-list and sparse-vector branches of the same schema
deserialize. ``spark.memory._chroma_hardening`` strips ``trust_remote_code`` from
every such config across both registries before chromadb builds it. EmberSpark
never uses chromadb embedding functions (it passes precomputed embeddings), so
stripping it is a no-op for legitimate use and a hard stop for the sink.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from spark.memory._chroma_hardening import _scrub_kwargs

_MISSING = object()


# ---- the scrubber removes trust_remote_code anywhere -------------------------


def test_scrub_kwargs_strips_trust_remote_code_recursively() -> None:
    cleaned = _scrub_kwargs(
        {
            "model_name_or_path": "x",
            "device": "cpu",
            "trust_remote_code": True,
            "model_kwargs": {"trust_remote_code": True, "dtype": "float16"},
            "nested": [{"trust_remote_code": True}],
            "code_revision": "deadbeef",
        }
    )
    assert "trust_remote_code" not in cleaned
    assert "trust_remote_code" not in cleaned["model_kwargs"]
    assert "trust_remote_code" not in cleaned["nested"][0]
    assert "code_revision" not in cleaned
    # benign content is preserved untouched
    assert cleaned["model_name_or_path"] == "x"
    assert cleaned["device"] == "cpu"
    assert cleaned["model_kwargs"]["dtype"] == "float16"


def test_scrub_kwargs_leaves_benign_config_unchanged() -> None:
    benign = {"model_name_or_path": "m", "device": "cpu", "normalize_embeddings": True}
    assert _scrub_kwargs(dict(benign)) == benign


def test_scrub_kwargs_strips_str_subclass_keys() -> None:
    """A str subclass with a lying __eq__ must not slip a dangerous key past
    frozenset membership (defensive — not reachable via real JSON schemas)."""

    class _Sneaky(str):
        def __eq__(self, other: object) -> bool:
            return False

        def __hash__(self) -> int:
            return object.__hash__(self)

    cleaned = _scrub_kwargs({_Sneaky("trust_remote_code"): True, "device": "cpu"})
    assert all(str(k) != "trust_remote_code" for k in cleaned)
    assert cleaned["device"] == "cpu"


# ---- end-to-end: the real chromadb deserialize paths are neutralized --------


@contextlib.contextmanager
def _hardened() -> Iterator[Any]:
    """Apply ``harden_chromadb()`` and fully restore every EF class afterwards.

    Snapshots ``build_from_config`` / ``__init__`` (and the hardening sentinel)
    for every class in both registries so the global monkeypatch doesn't leak
    across tests.
    """
    ef = pytest.importorskip("chromadb.utils.embedding_functions")
    from spark.memory import _chroma_hardening as ch

    snaps: list[tuple[Any, Any, Any, bool]] = []
    seen: set[int] = set()
    for attr in ("known_embedding_functions", "sparse_known_embedding_functions"):
        for cls in getattr(ef, attr, {}).values():
            if cls is None or id(cls) in seen:
                continue
            seen.add(id(cls))
            snaps.append(
                (
                    cls,
                    cls.__dict__.get("build_from_config", _MISSING),
                    cls.__dict__.get("__init__", _MISSING),
                    ch._SENTINEL in cls.__dict__,
                )
            )
    flag = ch._HARDENED
    ch._HARDENED = False
    try:
        ch.harden_chromadb()
        yield ef
    finally:
        for cls, build, init, had_sentinel in snaps:
            for name, original in (("build_from_config", build), ("__init__", init)):
                if original is _MISSING:
                    if name in cls.__dict__:
                        delattr(cls, name)
                else:
                    setattr(cls, name, original)
            if not had_sentinel and ch._SENTINEL in cls.__dict__:
                delattr(cls, ch._SENTINEL)
        ch._HARDENED = flag


def _spy(captured: dict[str, object]) -> type:
    class _Spy:
        def __init__(self, **kwargs: object) -> None:
            captured.clear()
            captured.update(kwargs)

    return _Spy


def test_harden_blocks_trust_remote_code_dense_sentence_transformer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", _spy(captured))
    with _hardened() as ef:
        cls = ef.known_embedding_functions["sentence_transformer"]
        cls.models.clear()
        cls.build_from_config(
            {
                "model_name": "attacker/evil-repo",
                "device": "cpu",
                "normalize_embeddings": False,
                "kwargs": {"trust_remote_code": True},
            }
        )
        assert "trust_remote_code" not in captured
        assert captured.get("model_name_or_path") == "attacker/evil-repo"


def test_harden_blocks_trust_remote_code_sparse_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the red-team bypass: the sparse registry must be covered."""
    captured: dict[str, object] = {}
    monkeypatch.setattr("sentence_transformers.SparseEncoder", _spy(captured))
    with _hardened() as ef:
        cls = ef.sparse_known_embedding_functions["huggingface_sparse"]
        cls.models.clear()
        cls.build_from_config(
            {
                "model_name": "attacker/evil-splade",
                "device": "cpu",
                "task": None,
                "query_config": None,
                "kwargs": {"trust_remote_code": True},
            }
        )
        assert "trust_remote_code" not in captured
        assert captured.get("model_name_or_path") == "attacker/evil-splade"


def test_harden_allows_benign_dense_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is surgical — a benign config still constructs normally."""
    captured: dict[str, object] = {}
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", _spy(captured))
    with _hardened() as ef:
        cls = ef.known_embedding_functions["sentence_transformer"]
        cls.models.clear()
        cls.build_from_config(
            {
                "model_name": "sentence-transformers/all-MiniLM-L6-v2",
                "device": "cpu",
                "normalize_embeddings": True,
                "kwargs": {},
            }
        )
        assert captured.get("model_name_or_path") == "sentence-transformers/all-MiniLM-L6-v2"
