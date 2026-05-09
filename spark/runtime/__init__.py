"""Runtime engine, lifecycle, and optional LangGraph adapter."""

from __future__ import annotations

# Suppress a noisy LangGraph deprecation warning from a path we never use.
#
# langgraph >= 1.1.10 emits ``LangChainPendingDeprecationWarning`` from its
# internal lazy import of ``JsonPlusSerializer`` at:
#
#     langgraph/cache/base/__init__.py:8
#     ↳ from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
#
# The warning asks callers to pass ``allowed_objects='messages'`` or
# ``'core'`` to the serializer constructor. EmberSpark uses LangGraph
# strictly via ``StateGraph`` + ``END`` (see ``engine.py`` and ``graph.py``)
# — no ``MemorySaver`` / ``SqliteSaver`` / checkpointing — so we never
# touch the serializer ourselves and there is no public API on
# ``StateGraph`` to forward this kwarg in 1.1.x.
#
# When LangGraph bumps the default to ``'core'`` (planned per the
# deprecation message), this filter becomes a no-op and can be removed.
# Tracking signal: the warning disappears on a stock LangGraph install
# without this filter.
import warnings as _warnings

try:
    from langchain_core._api.deprecation import (  # noqa: PLC0415
        LangChainPendingDeprecationWarning as _LCPendingDeprecation,
    )

    # Match by message text rather than module path — the warning's
    # ``stacklevel`` makes its call-site module *the importer of*
    # ``JsonPlusSerializer`` (e.g. ``langgraph.cache.base``), not the
    # module that defines the deprecation. A ``message=`` regex is the
    # robust handle.
    _warnings.filterwarnings(
        "ignore",
        category=_LCPendingDeprecation,
        message=r".*allowed_objects.*will change.*",
    )
except Exception:  # pragma: no cover — defensive against version drift
    pass

from spark.runtime.bootstrap import (
    bootstrap,
    effective_chroma_path,
    effective_sqlite_path,
    get_secret_manager,
    set_secret_manager,
)
from spark.runtime.engine import EngineResult, RuntimeEngine
from spark.runtime.lifecycle import Lifecycle
from spark.runtime.state import RunState

__all__ = [
    "EngineResult",
    "Lifecycle",
    "RunState",
    "RuntimeEngine",
    "bootstrap",
    "effective_chroma_path",
    "effective_sqlite_path",
    "get_secret_manager",
    "set_secret_manager",
]
