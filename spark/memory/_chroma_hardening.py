"""Defense-in-depth guard against chromadb's remote-code embedding-function path.

CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c (critical): chromadb deserializes an
embedding-function configuration out of a collection's stored schema and splats
its ``kwargs`` straight into ``SentenceTransformer(model_name, **kwargs)``. A
config that carries ``trust_remote_code=True`` plus an attacker-chosen
``model_name`` makes Hugging Face download and *execute* arbitrary modeling code
— i.e. remote code execution. The advisory frames this as a Chroma HTTP-server
bug, but the sink actually lives in the shared client-side deserialization
machinery, so it is reachable from the embedded ``PersistentClient`` too: it
fires whenever chroma deserializes a collection schema (e.g. on ``upsert``, via
``Schema.deserialize_from_json`` -> ``known_embedding_functions[...]``
``.build_from_config`` -> ``SentenceTransformer(..., **kwargs)``). chromadb also
keeps a *second*, parallel registry — ``sparse_known_embedding_functions`` (e.g.
``huggingface_sparse`` -> ``SparseEncoder(..., **kwargs)``) — reachable through
the sparse-vector branch of the same schema deserialize, so both registries must
be covered or the guard is trivially bypassed.

EmberSpark never uses chromadb's embedding functions — long-term memory computes
vectors with its own local SentenceTransformersProvider and always passes
precomputed ``embeddings=`` / ``query_embeddings=``. So no legitimate path here
ever needs ``trust_remote_code``. This module strips that kwarg from any
embedding-function config chromadb tries to build, neutralizing the sink even if
a tampered on-disk store were ever loaded.

Why this exists at all: there is no patched chromadb release as of this writing —
the newest version (1.5.9) is still inside the advisory's ``>=1.0.0,<=1.5.9``
range with ``first_patched_version`` = None, and downgrading to the last
pre-1.0 line breaks reading existing 1.x stores and Python 3.13 wheels. So this
guard is the mitigation until upstream ships a fix and we can bump under the
supply-chain cooldown.

The guard is idempotent and fails open with a loud warning: if chromadb's
internals ever change shape we log and continue rather than crash the runtime —
the primary protection is that EmberSpark never loads an attacker-controlled
store; this is belt-and-suspenders on top of that.
"""

from __future__ import annotations

import contextlib
from typing import Any

from spark.logging import get_logger

log = get_logger("spark.memory.chroma_hardening")

# Kwargs that turn model loading into code execution. Stripped from every
# embedding-function config (and nested kwargs) before chromadb builds it.
# ``trust_remote_code`` is the documented RCE trigger; ``code_revision`` is cheap
# insurance (it selects which remote code revision to run).
_DANGEROUS_KEYS = frozenset({"trust_remote_code", "code_revision"})

# Marks an embedding-function class we've already wrapped, so re-entry / aliased
# registry entries don't double-wrap.
_SENTINEL = "_spark_ef_hardened"

_HARDENED = False


def _scrub(value: Any, hit: list[str]) -> Any:
    """Return a copy of ``value`` with every dangerous key removed (recursively).

    Records each stripped key in ``hit`` so the caller can log when an attacker
    (or a tampered store) actually tried to smuggle one through.
    """
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, sub in value.items():
            # Compare on the normalized string value too, so a str subclass with
            # an overridden __eq__/__hash__ can't smuggle a dangerous key past the
            # frozenset membership test.
            norm = str(key) if isinstance(key, str) else key
            if key in _DANGEROUS_KEYS or norm in _DANGEROUS_KEYS:
                hit.append(norm)
                continue
            out[key] = _scrub(sub, hit)
        return out
    if isinstance(value, list | tuple):
        return type(value)(_scrub(item, hit) for item in value)
    return value


def _scrub_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    hit: list[str] = []
    cleaned = _scrub(kwargs, hit)
    if hit:
        log.warning(
            "chroma_hardening.stripped_remote_code_kwarg",
            keys=sorted(set(hit)),
            cve="CVE-2026-45829",
        )
    return cleaned


def _wrap_ef_class(cls: Any) -> bool:
    """Scrub-wrap one embedding-function class's ``build_from_config`` + ``__init__``.

    Returns True if it wrapped the class, False if it was None or already wrapped.
    Generic (signature-agnostic) so it covers every dense/sparse EF uniformly —
    the dangerous kwarg is stripped regardless of which builder is targeted.
    """
    if cls is None or _SENTINEL in cls.__dict__:
        return False

    # build_from_config is the deserialize entrypoint (config_to_embedding_function
    # and the float-list / sparse-vector branches of Schema.deserialize_from_json
    # all dispatch into it). Scrub the config before the function is built.
    build = getattr(cls, "build_from_config", None)
    if callable(build):

        def _safe_build(config: Any, _orig: Any = build) -> Any:
            if isinstance(config, dict):
                config = _scrub_kwargs(config)
            return _orig(config)

        cls.build_from_config = staticmethod(_safe_build)

    # __init__ is the actual sink (it splats **kwargs into the model loader);
    # wrap it too so the dangerous kwarg can't reach the loader via any path.
    init = getattr(cls, "__init__", None)
    if callable(init):

        def _safe_init(self: Any, *args: Any, _orig: Any = init, **kwargs: Any) -> None:
            _orig(self, *args, **_scrub_kwargs(kwargs))

        cls.__init__ = _safe_init

    with contextlib.suppress(Exception):  # pragma: no cover — exotic classes
        setattr(cls, _SENTINEL, True)
    return True


def harden_chromadb() -> None:
    """Neutralize chromadb's remote-code embedding-function sink.

    Wraps every embedding-function class in *both* registries (dense
    ``known_embedding_functions`` and ``sparse_known_embedding_functions``) so a
    tampered collection schema can't smuggle ``trust_remote_code`` into a model
    loader via any builder. Idempotent and cheap to call repeatedly; invoke after
    ``import chromadb`` and before any collection operation (long-term memory does
    this on first collection open).
    """
    global _HARDENED  # noqa: PLW0603 — module-level idempotency flag
    if _HARDENED:
        return
    try:
        import chromadb.utils.embedding_functions as ef

        wrapped = 0
        seen: set[int] = set()
        for attr in ("known_embedding_functions", "sparse_known_embedding_functions"):
            registry = getattr(ef, attr, None)
            if not isinstance(registry, dict):
                continue
            for cls in registry.values():
                if cls is None or id(cls) in seen:
                    continue
                seen.add(id(cls))
                if _wrap_ef_class(cls):
                    wrapped += 1

        _HARDENED = True
        if wrapped:
            log.info("chroma_hardening.applied", cve="CVE-2026-45829", wrapped=wrapped)
        else:  # pragma: no cover — chromadb internals changed
            log.warning("chroma_hardening.no_embedding_functions_wrapped")
    except Exception as exc:  # pragma: no cover — never crash the runtime over this
        log.warning("chroma_hardening.failed", error=str(exc))
