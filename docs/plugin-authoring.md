# Writing an EmberSpark Plugin

An EmberSpark plugin is a Python class that satisfies the `ToolPlugin` protocol in [`spark/plugins/base.py`](../spark/plugins/base.py). Plugins run inside the sandbox child process, so they do not need to implement OS isolation themselves — but they must be explicit about inputs, outputs, permissions, secrets, operator-configurable settings, and sensitivity.

This document is the reference for plugin authors. See [plugin-config.md](plugin-config.md) for the operator-facing reference and [tools-and-permissions.md](tools-and-permissions.md) for a deep dive on how plugins, permissions, grants, and operator config fit together.

---

## The contract

```python
from pathlib import Path
from typing import Any, ClassVar, Literal
from pydantic import BaseModel, Field, ConfigDict
from spark.config.enums import Permission, Sensitivity


# 1. Operator-editable config schema. Exposed in the Plugins page of the UI.
class MyPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_targets: list[str] = Field(default_factory=list)
    read_only: bool = False
    timeout_seconds: float = 5.0


# 2. Per-call input schema. What the model is allowed to send.
class MyPluginArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    action: Literal["read", "write"]
    payload: str | None = None


# 3. Output schema. Strictly validated before results reach the model.
class MyPluginResult(BaseModel):
    target: str
    ok: bool
    bytes_written: int = 0


# 4. The plugin class.
class MyPlugin:
    name: ClassVar[str] = "my_plugin"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = "Short operator-facing description."
    input_schema: ClassVar[type[BaseModel]] = MyPluginArgs
    output_schema: ClassVar[type[BaseModel]] = MyPluginResult
    config_schema: ClassVar[type[BaseModel]] = MyPluginConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.FS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: MyPluginArgs, ctx: Any) -> MyPluginResult:
        # Read operator-only knobs from ctx.plugin_config.
        cfg = getattr(ctx, "plugin_config", {}) or {}
        if cfg.get("read_only") and args.action == "write":
            raise PermissionError("my_plugin is configured read_only")
        ...
```

---

## Contract checklist

- [ ] `name` is stable, lowercase, unique across plugins.
- [ ] `input_schema` and `output_schema` are strict Pydantic models (`extra="forbid"`).
- [ ] `config_schema` captures every operator-editable knob. Fields that appear in both `input_schema` *and* `config_schema` are treated as operator-overridable at call time — the operator value **wins** on overlap. See [plugin-config.md](plugin-config.md).
- [ ] `required_permissions` lists exactly the `Permission` values you need. Missing one → runtime denies the call.
- [ ] `required_secrets` lists secret names only. Values are injected at runtime into `ctx.secrets`. If you don't declare a secret here, you can't read it.
- [ ] `sensitivity` reflects the most sensitive output the plugin can produce. Use `HIGH` or `RESTRICTED` for anything that could contain user PII, credentials, or private keys. This controls what the privacy pipeline does with the output before the model sees it.
- [ ] `filter_output_before_model` should stay `True` unless you have already applied your own structured redaction and are sure the output is safe to hand to the model verbatim.
- [ ] `needs_network` declares whether the plugin requires network access in the sandbox. Default is `False` — that means the sandbox `unshare --net` for the child process and your plugin cannot open any socket.

---

## `ctx` — what the plugin sees at runtime

The context passed to `execute(args, ctx)` exposes three attributes:

| Attribute | Type | What it contains |
|---|---|---|
| `ctx.secrets` | `dict[str, str]` | Only the secrets you declared in `required_secrets`, already resolved. |
| `ctx.plugin_config` | `dict[str, Any]` | The full operator-configured `config_schema` values as a dict. Use this for operator-only knobs like `read_only`, `allowed_targets`, etc. |
| `ctx.privacy_mode` | `str` | The current privacy mode (`strict` / `balanced` / `regex_only`). You probably don't need this — the runtime handles filtering. |

---

## Merge semantics — why the operator always wins

Let's walk through a concrete example. You're writing an HTTP plugin. The operator configures it with:

```json
{
  "allow_hosts": ["api.github.com"],
  "allowed_methods": ["GET"],
  "user_agent": "my-org/1.0"
}
```

The model tries to call it with:

```json
{
  "url": "https://api.github.com/repos/foo",
  "method": "GET",
  "allow_hosts": ["evil.example"]
}
```

The tool runtime's `merge_config_and_args` walks the operator config. For any key that also exists in the plugin's `input_schema`, the operator's value overrides the model's. So the merged args become:

```json
{
  "url": "https://api.github.com/repos/foo",
  "method": "GET",
  "allow_hosts": ["api.github.com"]
}
```

The model cannot widen `allow_hosts`. It also cannot remove fields that the plugin expects (Pydantic validation would refuse).

**Operator-only fields** (fields in `config_schema` that aren't in `input_schema`, like `user_agent`) go through `ctx.plugin_config` instead. Your plugin reads them from there:

```python
async def execute(self, args, ctx):
    cfg = getattr(ctx, "plugin_config", {}) or {}
    ua = cfg.get("user_agent", "spark-runtime/0.1")
    ...
```

---

## Common pitfalls

### 1. Don't `open()` without path checking

Use [`spark.utils.paths.PathPolicy.check`](../spark/utils/paths.py) to resolve and validate the path. Then use `os.open(..., os.O_NOFOLLOW | os.O_CLOEXEC)` for the final open. Never call `mkdir -p` from inside a plugin — operators lay out the workspace in advance.

```python
from spark.utils.paths import PathPolicy

policy = PathPolicy.from_strings(allow_paths, deny_paths)
target = policy.check(Path(args.path))
# target is now the resolved, verified absolute path
```

### 2. Don't fetch URLs directly — use `spark.utils.net.validate_url` + `pin_dns`

Even if your plugin has its own allowlist, the [`validate_url`](../spark/utils/net.py) helper gives you:

- IDN normalization (punycode fail-closed for homoglyphs)
- Private / loopback / link-local / multicast / cloud-metadata IP rejection
- IPv4-mapped IPv6 unwrapping
- A pre-validated IP for DNS pinning (defeats DNS rebinding without breaking TLS)

```python
from spark.utils.net import HostPolicy, pin_dns, validate_url

policy = HostPolicy.from_list(args.allow_hosts, allow_http=False)
target = validate_url(args.url, policy)
# target.host, target.ip, target.scheme, target.port

with pin_dns(target):
    async with httpx.AsyncClient(verify=True, trust_env=False) as client:
        response = await client.request(args.method, args.url, headers=headers)
```

Pass the **original URL** (with the hostname) to httpx — TLS uses the hostname for SNI and cert verification. Inside the `pin_dns(target)` context, `socket.getaddrinfo` resolutions of `target.host` return the pre-validated IP, so the TCP connection still goes to the IP that passed the SSRF gauntlet. Don't rebuild the URL with the IP literal — that breaks cert verification (the cert won't match the IP) and you'll get `CERTIFICATE_VERIFY_FAILED: IP address mismatch`.

### 3. Don't print secrets

`ctx.secrets` is a plain dict of string values inside the sandbox child. **Do not** include any of them in the returned result. The parent-side structlog scrub processor will redact known secret values from logs, but the right place to not leak secrets is the plugin itself — never put them in `output_schema` fields, never concatenate them into error messages, never log them.

### 4. Don't swallow validation errors

Let Pydantic raise. The tool runtime catches `ValidationError` at the parent side, logs the full details to operator logs, and returns a sanitized `PermissionDenied` to the model (with no field names). If you catch and reformat validation errors yourself, you risk leaking schema internals to the model.

### 5. Respect `needs_network`

If `needs_network = False`, the sandbox strips the child's network namespace. Any `socket.connect` will fail with `EPERM` / `ENETUNREACH`. If you declare `needs_network = False` but your plugin secretly calls out, it will break at runtime — which is the intended outcome.

### 6. Don't use `subprocess.run` with `shell=True`

Ever. The shell plugin uses `create_subprocess_exec` with an argv list and rejects shell metacharacters in every positional argument. Do the same in your own plugin.

### 7. Keep `execute` idempotent when possible

The engine retries failed tool calls on transient errors (in F5, under the retry policy). Your plugin should handle being called twice with the same arguments without corrupting state.

---

## Registering your plugin

### Option 1 — Entry points in `pyproject.toml`

```toml
[project.entry-points."spark.plugins"]
my_plugin = "my_package.my_plugin:MyPlugin"
```

After `pip install -e .`, EmberSpark's `default_registry` will auto-discover it on startup. But it is **not usable** until an agent includes it in `plugins.allow` and an operator populates its config via the UI.

### Option 2 — Runtime registration

```python
from spark.plugins.registry import default_registry
reg = default_registry()
reg.register_class(MyPlugin)
```

Useful for tests and custom runners.

---

## Testing your plugin

### Unit tests

Test `execute` directly with a stub `ctx`:

```python
class _Ctx:
    def __init__(self, config: dict, secrets: dict | None = None) -> None:
        self.secrets = secrets or {}
        self.privacy_mode = "strict"
        self.plugin_config = config

@pytest.mark.asyncio
async def test_read_only_rejects_writes():
    plugin = MyPlugin()
    ctx = _Ctx(config={"read_only": True})
    with pytest.raises(PermissionError, match="read_only"):
        await plugin.execute(MyPluginArgs(target="x", action="write"), ctx)
```

### Integration tests

For integration tests that should not spawn a real sandboxed subprocess, patch `spark.plugins.tool_runtime.run_sandboxed` to return a canned `ResponseFrame`:

```python
from unittest.mock import AsyncMock, patch
from spark.sandbox.ipc import ResponseFrame

fake_response = ResponseFrame(ok=True, result={"target": "x", "ok": True})
with patch(
    "spark.plugins.tool_runtime.run_sandboxed",
    AsyncMock(return_value=fake_response),
):
    outcome = await executor.call("my_plugin", {"target": "x", "action": "read"})
```

The real sandbox is covered separately by `tests/integration/test_sandbox_real.py`, which is auto-skipped on hosts without a backend.

---

## Example plugins in the repo

Start by reading these before writing your own — they're the reference implementations:

- **[filesystem](../spark/plugins/builtins/filesystem.py)** — `PathPolicy` + `O_NOFOLLOW` + operator `read_only` gate
- **[http_client](../spark/plugins/builtins/http_client.py)** — `validate_url` + pinned-IP transport + `allowed_methods` gate
- **[markdown_writer](../spark/plugins/builtins/markdown_writer.py)** — thin wrapper, `.md` extension check, `allow_append`/`allow_overwrite` operator gates
- **[shell](../spark/plugins/builtins/shell.py)** — argv-only command allowlist with per-command flag allowlists
- **[sqlite](../spark/plugins/builtins/sqlite.py)** — `sqlglot` pre-parse gate, per-database mode (`read` | `read_write`), banned-keyword prefilter
