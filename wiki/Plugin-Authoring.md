# Plugin Authoring

Writing an EmberSpark plugin is a small, specific task. This is the operator's guide; for the full contract and source-level reference, see [docs/plugin-authoring.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-authoring.md).

## The minimal plugin

```python
from typing import Any, ClassVar, Literal
from pydantic import BaseModel, ConfigDict, Field
from spark.config.enums import Permission, Sensitivity


class MyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_targets: list[str] = Field(
        default_factory=list,
        description="Operator-locked allowlist of target names.",
    )
    read_only: bool = Field(
        default=False,
        description="When true, refuses any 'write' action regardless of args.",
    )


class MyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(
        description="Name of the target. Must be in the operator's allowed_targets.",
    )
    action: Literal["read", "write"] = Field(
        description="Operation: 'read' returns target state, 'write' updates it.",
    )


class MyResult(BaseModel):
    target: str
    ok: bool


class MyPlugin:
    name: ClassVar[str] = "my_plugin"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Short, specific summary of what the tool does, what it can't do, "
        "and what makes it fail. The planner reads this verbatim from the "
        "system prompt and from the native tool-binding payload, so be "
        "concrete — 'manages X resources via the Y API; refuses writes "
        "without operator allow_writes; fails if target is not in "
        "allowed_targets'."
    )
    input_schema: ClassVar[type[BaseModel]] = MyArgs
    output_schema: ClassVar[type[BaseModel]] = MyResult
    config_schema: ClassVar[type[BaseModel]] = MyConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset({Permission.FS_READ})
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: MyArgs, ctx: Any) -> MyResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        if cfg.get("read_only") and args.action == "write":
            raise PermissionError("my_plugin is configured read_only")
        # your logic here
        return MyResult(target=args.target, ok=True)
```

> **Required: every `input_schema` field must set `description=`.**
> The runtime renders these descriptions into the planner's system
> prompt **and** into the native `bind_tools` payload. A unit test
> ([tests/unit/test_plugin_metadata.py](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_plugin_metadata.py)) fails if any field is undescribed.
> Treat field descriptions like API docs — they directly determine
> whether the planner picks the right argument values.

### Naming `config_schema` fields so the model sees them

The runtime renders each plugin's effective operator config into the
system prompt as an "Operator config" block, so the model knows the
real `allow_paths` / `allow_hosts` / etc. before it picks argument
values. Whether a given field appears there is a name-pattern
heuristic ([tool_spec.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/runtime/tool_spec.py) — `_CONSTRAINT_NAME_PATTERNS`):

- Surfaced by default: any field with `allow`, `deny`, `enabled`, `host`, `path`, `rule`, `provider`, `database`, `repo`, `chat_id`, `model`, or `domain` in its name.
- Skipped: timeouts, user-agent strings, and anything not matching the patterns above.
- Always surfaced: any field whose name overlaps with one in your `input_schema` (those are operator-bound and the model can't widen them — it must know the value).

If your plugin gates behavior by a field name that doesn't match the
heuristic, either rename it (`my_constraint` → `allow_my_constraint`)
or open an issue to extend the pattern list.

## Register via entry point

In your package's `pyproject.toml`:

```toml
[project.entry-points."spark.plugins"]
my_plugin = "my_package.my_plugin:MyPlugin"
```

After `pip install -e .`, EmberSpark's `default_registry` auto-discovers the plugin. It still has to be:

1. Allowlisted in an agent's `spec.plugins.allow`
2. Granted the permissions it declared in `required_permissions`
3. Configured in the Plugins page of the web UI

Before it can actually be called.

## The contract checklist

- [ ] `name` is unique, lowercase, stable
- [ ] **`description` is non-empty** and explains what the tool does, what it can't do, what makes it fail
- [ ] `input_schema`, `output_schema`, and `config_schema` are all Pydantic models with `ConfigDict(extra="forbid")`
- [ ] **Every `Field` in `input_schema` sets `description=`** (a unit test fails the build if it's missing)
- [ ] `required_permissions` is minimal — only what the plugin needs
- [ ] `required_secrets` is declared, not resolved ad-hoc at call time
- [ ] `sensitivity` reflects the worst-case output
- [ ] `filter_output_before_model = True` unless you've proven it's safe to skip
- [ ] `needs_network` is accurate (False by default)
- [ ] `execute` is async, reads operator-only knobs from `ctx.plugin_config`, never prints secrets, never swallows validation errors

## Common pitfalls

1. **Don't `open()` without `PathPolicy.check`** — use `spark.utils.paths.PathPolicy`
2. **Don't fetch URLs directly** — call `validate_url` then run the request inside `pin_dns(target)`. Keep the original hostname in the URL; the context manager pins DNS to the validated IP without breaking SNI / cert verification. See [docs/plugin-authoring.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-authoring.md) for the canonical pattern.
3. **Don't print secrets** — they're in `ctx.secrets` but never belong in `output_schema` or logs
4. **Don't swallow `ValidationError`** — let Pydantic raise, the runtime catches and sanitizes
5. **Don't use `subprocess.run(..., shell=True)`** — argv lists only
6. **Don't create parent directories** — operators lay out the workspace in advance

## Testing

Test `execute` directly with a stub ctx:

```python
class _Ctx:
    def __init__(self, config, secrets=None):
        self.secrets = secrets or {}
        self.privacy_mode = "strict"
        self.plugin_config = config

@pytest.mark.asyncio
async def test_read_only_rejects_writes():
    ctx = _Ctx(config={"read_only": True})
    with pytest.raises(PermissionError, match="read_only"):
        await MyPlugin().execute(MyArgs(target="x", action="write"), ctx)
```

For integration tests that shouldn't spawn a real sandbox child, patch `spark.plugins.tool_runtime.run_sandboxed` with a `ResponseFrame` mock. See [docs/plugin-authoring.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-authoring.md) for the full pattern.

## Further reading

- [docs/plugin-authoring.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-authoring.md) — the deep reference
- [Concepts: Plugins](Concepts-Plugins) — what a plugin is
- [Using Plugins](Using-Plugins) — the operator side
- [Plugin Reference: *](Plugin-Reference-Filesystem) — the built-in references are the best examples
