"""Integration test that actually spawns the sandbox worker.

Skipped unless a working sandbox backend is available on the host. This is the
sandbox "it really runs" check — if this test passes, Spark's mandatory-OS
sandbox path is wired end-to-end.
"""

from __future__ import annotations

import pytest

from spark.sandbox.executor import (
    SandboxUnavailable,
    check_available,
    run_sandboxed,
)
from spark.sandbox.ipc import RequestFrame
from spark.sandbox.policy import SandboxPolicy


@pytest.fixture
def backend_name() -> str:
    try:
        return check_available()
    except SandboxUnavailable:
        pytest.skip("no sandbox backend available on host")


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_sandbox_runs_echo_plugin(backend_name: str):
    from tests.integration.test_engine_end_to_end import EchoPlugin

    module = EchoPlugin.__module__
    class_name = EchoPlugin.__name__

    policy = SandboxPolicy(
        ro_paths=tuple(),
        rw_paths=tuple(),
        allow_network=False,
        allow_hosts=tuple(),
    )
    request = RequestFrame(
        plugin_module=module,
        plugin_class=class_name,
        args={"text": "hi from sandbox"},
        secrets={},
    )
    response = await run_sandboxed(request, policy)
    assert response.ok is True
    assert response.result == {"text": "hi from sandbox"}
