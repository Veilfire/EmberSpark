"""In-process stub chat model for tests and demos.

Implements just enough of the LangChain `BaseChatModel` contract to run the
Spark engine end-to-end without a real LLM. The stub replies with a canned
JSON payload matching the tool-call or reflection schema the runtime expects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StubMessage:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    # Duck-type the bits LangGraph's message shim reads.
    additional_kwargs: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:
        return "ai"


@dataclass
class StubChatModel:
    """A scripted chat model.

    `script` is a list of replies the model hands out in order. Each reply is
    either:
      - a plain string (returned as message content)
      - a dict with optional `content` and `tool_calls` keys
    """

    script: list[Any]
    _cursor: int = 0

    async def ainvoke(self, _messages: Any, **_kwargs: Any) -> StubMessage:
        if self._cursor >= len(self.script):
            return StubMessage(content="done")
        item = self.script[self._cursor]
        self._cursor += 1
        if isinstance(item, str):
            return StubMessage(content=item)
        if isinstance(item, dict):
            return StubMessage(
                content=item.get("content", ""),
                tool_calls=item.get("tool_calls", []),
            )
        return StubMessage(content=json.dumps(item))

    def invoke(self, messages: Any, **kwargs: Any) -> StubMessage:
        import asyncio

        return asyncio.get_event_loop().run_until_complete(self.ainvoke(messages, **kwargs))

    def with_structured_output(self, _schema: Any) -> "StubChatModel":
        return self
