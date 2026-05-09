"""LangGraph adapter — backward-compatible single-node wrapper.

Spark's engine now uses LangGraph natively per-run (see
:mod:`spark.runtime.engine`'s ``_compile_graph``). This module is kept
for callers that historically used ``build_graph(engine)`` to obtain a
top-level graph object — typically for orchestration tooling that wants
to drive a single ``ainvoke`` rather than calling ``engine.run()``
directly.

The wrapper exposes one node, ``run``, that delegates to
``engine.run()``. The engine still spins up its real per-run state
machine internally, so the safety + observability invariants are
identical whether you call the engine directly or via this graph.
"""

from __future__ import annotations

from typing import Any


def build_graph(engine: Any) -> Any:
    """Return a single-node compiled StateGraph that runs the engine.

    Falls back to returning the engine unchanged if LangGraph is not
    importable — the engine's own execution path doesn't require this
    helper.
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:  # pragma: no cover
        return engine

    graph: Any = StateGraph(dict)

    async def run_node(_: dict[str, Any]) -> dict[str, Any]:
        result = await engine.run()
        return {
            "result": result.result,
            "status": result.state.value,
            "run_id": result.run_id,
        }

    graph.add_node("run", run_node)
    graph.set_entry_point("run")
    graph.add_edge("run", END)
    return graph.compile()
