"""JSON query plugin — JMESPath-based structured extraction.

The use case: `http_client` / `http_tool` returns a large JSON body and the
agent only needs specific fields. Instead of pasting the whole JSON into
the model and paying for the tokens, the agent calls this plugin with a
JMESPath expression and gets back just the fields it asked for.

JMESPath is the same language AWS CLI uses for `--query`. It is pure
Python, has no native deps, and has been stable for a decade.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity


class JsonQueryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_input_bytes: int = Field(default=5_000_000, ge=1, le=100_000_000)
    max_output_chars: int = Field(default=50_000, ge=1, le=1_000_000)


class JsonQueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    json_blob: str = Field(
        min_length=1,
        description="JSON document as a string. Typically a previous tool's response body.",
    )
    query: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "JMESPath expression. Examples: 'items[*].name', 'data | [0].id', "
            "'nodes[?status==\\'active\\'].url'."
        ),
    )


class JsonQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    result: Any
    result_text: str
    truncated: bool


class JsonQueryPlugin:
    name: ClassVar[str] = "json_query"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Filter a JSON payload with a JMESPath expression. Lets the agent "
        "extract specific fields instead of feeding the whole blob to the model."
    )
    input_schema: ClassVar[type[BaseModel]] = JsonQueryArgs
    output_schema: ClassVar[type[BaseModel]] = JsonQueryResponse
    config_schema: ClassVar[type[BaseModel]] = JsonQueryConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset()
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: JsonQueryArgs, ctx: Any) -> JsonQueryResponse:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        max_input = int(cfg.get("max_input_bytes") or 5_000_000)
        max_output = int(cfg.get("max_output_chars") or 50_000)

        input_bytes = args.json_blob.encode("utf-8")
        if len(input_bytes) > max_input:
            raise PermissionError(
                f"json_query: input is {len(input_bytes)} bytes (max {max_input})"
            )

        try:
            document = json.loads(args.json_blob)
        except json.JSONDecodeError as exc:
            raise PermissionError(f"json_query: input is not valid JSON: {exc}") from exc

        try:
            import jmespath  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "json_query requires the `jmespath` package. Install with "
                "`pip install jmespath`."
            ) from exc

        try:
            compiled = jmespath.compile(args.query)
        except Exception as exc:
            raise PermissionError(f"json_query: invalid JMESPath {args.query!r}: {exc}") from exc

        try:
            result = compiled.search(document)
        except Exception as exc:
            raise PermissionError(f"json_query: search failed: {exc}") from exc

        try:
            text = json.dumps(result, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = json.dumps(str(result), ensure_ascii=False)
        truncated = False
        if len(text) > max_output:
            text = text[:max_output]
            truncated = True

        return JsonQueryResponse(
            query=args.query,
            result=result,
            result_text=text,
            truncated=truncated,
        )
