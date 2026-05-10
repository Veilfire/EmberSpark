"""Home Assistant plugin — view + (opt-in) control HA + HomeKit devices.

Spark agents talk to Home Assistant over its REST API with a Bearer
long-lived access token. HA's built-in HomeKit Controller integration
bridges Apple HomeKit devices through the same surface, so this single
plugin covers both ecosystems for v1. A direct HomeKit-on-Linux path
is out of scope (would need ``aiohomekit`` + mDNS pairing UX).

Five guard rails:

1. **Read-only by default** — the plugin's ``HomeAssistantConfig``
   ships with ``read_only=true``. ``call_service`` refuses with
   ``SparkError(PERMISSION_MISSING, missing_toggle="read_only")`` until
   the operator flips it.
2. **Domain allowlist** — every action checks the entity's domain
   against ``allowed_domains``. Defaults exclude ``lock``,
   ``alarm_control_panel``, ``camera``, ``device_tracker``,
   ``person``, ``vacuum``.
3. **Service allowlist** — even with ``read_only=false``, a service
   call requires the (domain, service) pair to be in
   ``allowed_services``. The empty default refuses everything.
4. **Entity exclude globs** — defense in depth on top of the domain
   allowlist for narrowing visible entities (e.g. exclude only
   ``device_tracker.spouse``).
5. **Sensitivity = MODERATE + filter_output_before_model = True** so
   responses flow through the existing redaction chain; HA state
   attributes carrying ``latitude`` / ``longitude`` are caught by the
   data-class layer before reaching the model.

The plugin also ships a read-only ``discover()`` helper that hits
HA's ``/api/config``, ``/api/services``, and ``/api/states`` endpoints
and returns a structured snapshot. The Plugins page's custom editor
(see ``HomeAssistantConfigEditor.tsx``) calls this through
``POST /api/plugin-config/home_assistant/discover`` to drive its
checkbox grids — the operator picks domains / services / entities
from live data instead of typing strings.
"""

from __future__ import annotations

import fnmatch
from typing import Any, ClassVar, Literal
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError


# ---------------------------------------------------------------------------
# Risk classification — drives editor chips + audit-severity decisions.
# Not user-tunable; ships as a constant table in the plugin module.
# ---------------------------------------------------------------------------

_DANGER_DOMAINS = frozenset(
    {
        "lock",
        "alarm_control_panel",
        "camera",
        "device_tracker",
        "person",
        "vacuum",
    }
)

_ELEVATED_DOMAINS = frozenset(
    {
        "cover",
        "script",
        "automation",
        "media_player",
    }
)

#: Per-service danger overrides keyed by ``"<domain>.<service>"`` or
#: just ``"<service>"`` for service names that are dangerous on every
#: domain (very rare).
_DANGER_SERVICES = frozenset(
    {
        # Door/lock disarm-style operations.
        "lock.unlock",
        "alarm_control_panel.disarm",
        "alarm_control_panel.disarm_away",
        "alarm_control_panel.disarm_home",
        "alarm_control_panel.disarm_night",
        # Movement that affects physical state of doors / shutters.
        "cover.open_cover",
        "cover.open_cover_tilt",
        # Triggering automations / scripts is tantamount to remote
        # code execution by the agent's standards.
        "automation.trigger",
        "script.execute",
        # Restart / shutdown of HA itself.
        "homeassistant.restart",
        "homeassistant.stop",
        # Vacuum start (physical movement).
        "vacuum.start",
    }
)

_ELEVATED_SERVICES = frozenset(
    {
        # Mutating media playback.
        "media_player.play_media",
        "media_player.media_pause",
        "media_player.media_play",
        "media_player.media_stop",
        "media_player.volume_set",
        # Toggles on otherwise-safe domains can still surprise.
        "light.toggle",
        "switch.toggle",
        "input_boolean.toggle",
        # Any cover close is mutating but less risky than open.
        "cover.close_cover",
        "cover.stop_cover",
        # Scenes execute multiple things at once — elevated.
        "scene.turn_on",
    }
)

#: Built-in safe defaults shipped pre-checked in the editor.
_DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = (
    "light",
    "switch",
    "sensor",
    "binary_sensor",
    "media_player",
    "climate",
    "weather",
    "fan",
    "scene",
    "input_boolean",
    "cover",
    "script",
)


def domain_risk(domain: str) -> Literal["safe", "elevated", "danger"]:
    if domain in _DANGER_DOMAINS:
        return "danger"
    if domain in _ELEVATED_DOMAINS:
        return "elevated"
    return "safe"


def service_risk(domain: str, service: str) -> Literal["safe", "elevated", "danger"]:
    full = f"{domain}.{service}"
    if full in _DANGER_SERVICES or service in _DANGER_SERVICES:
        return "danger"
    if full in _ELEVATED_SERVICES:
        return "elevated"
    # Mutating fallback for unknown services on danger domains.
    if domain in _DANGER_DOMAINS:
        return "danger"
    if domain in _ELEVATED_DOMAINS:
        return "elevated"
    return "safe"


def domain_label(domain: str) -> str:
    """Operator-friendly group label for the editor."""
    pretty = {
        "light": "Lights",
        "switch": "Switches",
        "sensor": "Sensors",
        "binary_sensor": "Binary sensors",
        "media_player": "Media players",
        "climate": "Climate",
        "weather": "Weather",
        "fan": "Fans",
        "scene": "Scenes",
        "input_boolean": "Input toggles",
        "cover": "Covers",
        "script": "Scripts",
        "lock": "Locks",
        "alarm_control_panel": "Alarm panels",
        "camera": "Cameras",
        "device_tracker": "Device trackers",
        "person": "People",
        "vacuum": "Vacuums",
        "automation": "Automations",
    }
    return pretty.get(domain, domain.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Operator config
# ---------------------------------------------------------------------------


class HomeAssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default="",
        max_length=512,
        description=(
            "Home Assistant base URL, e.g. http://ha.lan:8123. "
            "Almost always RFC1918 — operator must add the host to the "
            "agent's allow_hosts and issue an internal-IP grant."
        ),
    )
    token_secret: str = Field(
        default="home_assistant_token",
        max_length=128,
        description=(
            "Vault key holding the long-lived access token. "
            "Operator: `spark secrets set home_assistant_token`."
        ),
    )
    read_only: bool = Field(
        default=True,
        description="Refuses call_service when true. Default true.",
    )
    allowed_domains: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_ALLOWED_DOMAINS),
        description=(
            "Whitelist of HA domains the agent can read / act on. "
            "Excludes lock / alarm_control_panel / camera / "
            "device_tracker / person / vacuum by default."
        ),
    )
    allowed_services: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Per-domain service allowlist for call_service. Two-key "
            "gate: domain in allowed_domains AND service in this map."
        ),
    )
    entity_filter_glob: list[str] = Field(
        default_factory=list,
        description=(
            "Optional excludes applied to list_states. Glob patterns "
            "supported (e.g. `device_tracker.*`)."
        ),
    )
    verify_ssl: bool = Field(default=True)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_response_bytes: int = Field(default=1_048_576, gt=0)
    max_states_returned: int = Field(default=200, gt=0, le=2000)


# ---------------------------------------------------------------------------
# Action surface — discriminated union on `action`
# ---------------------------------------------------------------------------


class _ListStatesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list_states"] = "list_states"
    domain: str | None = Field(
        default=None,
        max_length=64,
        description="Optional: restrict to one domain (e.g. 'light').",
    )
    verbose: bool = Field(
        default=False,
        description="Include full attribute blobs (default: trimmed).",
    )


class _GetStateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["get_state"] = "get_state"
    entity_id: str = Field(min_length=1, max_length=255)


class _CallServiceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["call_service"] = "call_service"
    domain: str = Field(min_length=1, max_length=64)
    service: str = Field(min_length=1, max_length=64)
    entity_id: str | list[str] | None = Field(
        default=None,
        description="Target entity_id (string or list).",
    )
    data: dict[str, Any] | None = Field(
        default=None,
        description="Service kwargs (e.g. brightness, color_name).",
    )


class _RenderTemplateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["render_template"] = "render_template"
    template: str = Field(min_length=1, max_length=4096)


class _GetHistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["get_history"] = "get_history"
    entity_id: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Required filter — HA returns the world without it. "
            "Single entity per call."
        ),
    )
    hours: int = Field(default=24, gt=0, le=24)


class _HomeAssistantArgsWrapper(BaseModel):
    """Discriminated-union dispatch on ``action``.

    Mirrors the telegram pattern: a permissive wrapper with all the
    optional fields, validated at the top with one of the inner
    ``_*Args`` models in ``execute``.
    """

    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "list_states",
        "get_state",
        "call_service",
        "render_template",
        "get_history",
    ] = Field(
        description=(
            "Which HA call to make: 'list_states' (filtered by allowed_domains), "
            "'get_state' (one entity), 'call_service' (write — refused while "
            "read_only), 'render_template' (Jinja2 aggregation), 'get_history' "
            "(per-entity 24h replay)."
        ),
    )
    domain: str | None = None
    service: str | None = None
    entity_id: str | list[str] | None = None
    data: dict[str, Any] | None = None
    template: str | None = None
    hours: int | None = None
    verbose: bool | None = None


class HomeAssistantHit(BaseModel):
    """Trimmed entity record for ``list_states`` responses."""

    model_config = ConfigDict(extra="forbid")
    entity_id: str
    state: str | None = None
    friendly_name: str | None = None
    last_changed: str | None = None
    attributes: dict[str, Any] | None = None  # populated when verbose=true


class HomeAssistantResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    states: list[HomeAssistantHit] | None = None
    state: HomeAssistantHit | None = None
    rendered: str | None = None
    history: list[list[dict[str, Any]]] | None = None
    truncated: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class HomeAssistantDomainEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    risk: Literal["safe", "elevated", "danger"]
    entity_count: int


class HomeAssistantServiceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    risk: Literal["safe", "elevated", "danger"]
    description: str | None = None


class HomeAssistantEntityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    domain: str
    friendly_name: str | None = None
    state: str | None = None


class HomeAssistantDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    error: str | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    domains: list[HomeAssistantDomainEntry] = Field(default_factory=list)
    services_by_domain: dict[str, list[HomeAssistantServiceEntry]] = Field(
        default_factory=dict
    )
    entities: list[HomeAssistantEntityEntry] = Field(default_factory=list)
    instance_url: str | None = None
    instance_version: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def _looks_private(host: str) -> bool:
    """Heuristic: is the URL host a private / loopback / link-local IP.

    Used to map ``httpx.ConnectError`` to ``URL_PRIVATE_IP`` vs
    ``URL_DENIED`` when we can't resolve. Real validation happens in
    ``spark.utils.net.HostPolicy``; this is just for error-mapping.
    """
    import ipaddress  # noqa: PLC0415

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_token(cfg: dict[str, Any], ctx: Any) -> str:
    secret_name = (cfg.get("token_secret") or "home_assistant_token").strip()
    secrets = getattr(ctx, "secrets", {}) or {}
    token = secrets.get(secret_name) if isinstance(secrets, dict) else None
    if not token:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"home_assistant: secret {secret_name!r} not injected",
            detail={
                "plugin": "home_assistant",
                "secret_name": secret_name,
            },
        )
    return str(token)


def _hostname(url: str) -> str:
    from urllib.parse import urlparse  # noqa: PLC0415

    return (urlparse(url).hostname or "").strip()


def _build_client(cfg: dict[str, Any]) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=float(cfg.get("connect_timeout_seconds") or 5.0),
        read=float(cfg.get("read_timeout_seconds") or 15.0),
        write=5.0,
        pool=5.0,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        verify=bool(cfg.get("verify_ssl", True)),
        trust_env=False,
        follow_redirects=False,
    )


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
    max_bytes: int = 1_048_576,
) -> tuple[int, str]:
    """Issue a single HA request, mapping errors to SparkError."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "spark-home-assistant/0.1",
    }
    try:
        if json_body is not None:
            resp = await client.request(method, url, headers=headers, json=json_body)
        else:
            resp = await client.request(method, url, headers=headers)
    except httpx.ConnectError as exc:
        host = _hostname(url)
        code = (
            ErrorCode.URL_PRIVATE_IP
            if _looks_private(host)
            else ErrorCode.URL_DENIED
        )
        raise SparkError(
            code,
            f"home_assistant: cannot reach {host or url}: {exc}",
            detail={"plugin": "home_assistant", "host": host},
        ) from exc
    except httpx.RequestError as exc:
        raise SparkError(
            ErrorCode.PLUGIN_RAISED,
            f"home_assistant: request failed: {exc}",
            detail={"plugin": "home_assistant"},
        ) from exc

    body = resp.text
    if len(body) > max_bytes:
        body = body[:max_bytes]
    if resp.status_code == 401:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            "home_assistant: 401 from HA — long-lived token rejected",
            detail={"plugin": "home_assistant", "secret_name": "home_assistant_token"},
        )
    return resp.status_code, body


def _entity_filtered_out(entity_id: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(entity_id, g) for g in globs)


def _trim_state(state_obj: dict[str, Any], *, verbose: bool) -> HomeAssistantHit:
    attrs = state_obj.get("attributes") or {}
    return HomeAssistantHit(
        entity_id=state_obj.get("entity_id", ""),
        state=state_obj.get("state"),
        friendly_name=attrs.get("friendly_name"),
        last_changed=state_obj.get("last_changed"),
        attributes=attrs if verbose else None,
    )


def _refuse_domain(domain: str) -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        f"home_assistant: domain {domain!r} not in allowed_domains",
        detail={
            "plugin": "home_assistant",
            "missing_domain": domain,
        },
    )


def _refuse_service(domain: str, service: str, *, reason: str) -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        f"home_assistant: {reason}",
        detail={
            "plugin": "home_assistant",
            "missing_service": f"{domain}.{service}",
        },
    )


def _refuse_read_only() -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        "home_assistant: read_only=true blocks call_service",
        detail={
            "plugin": "home_assistant",
            "missing_toggle": "read_only",
        },
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class HomeAssistantPlugin:
    name: ClassVar[str] = "home_assistant"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "View states and (opt-in) call services on a Home Assistant "
        "instance. HA's built-in HomeKit Controller integration "
        "bridges Apple HomeKit devices through the same surface."
    )
    input_schema: ClassVar[type[BaseModel]] = _HomeAssistantArgsWrapper
    output_schema: ClassVar[type[BaseModel]] = HomeAssistantResult
    config_schema: ClassVar[type[BaseModel]] = HomeAssistantConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(
        self, args: _HomeAssistantArgsWrapper, ctx: Any
    ) -> HomeAssistantResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        base_url = (cfg.get("base_url") or "").rstrip("/")
        if not base_url:
            raise SparkError(
                ErrorCode.OPERATOR_OVERRIDE_REFUSED,
                "home_assistant: base_url not set in operator config",
                detail={"plugin": "home_assistant", "field": "base_url"},
            )
        allowed_domains = set(cfg.get("allowed_domains") or _DEFAULT_ALLOWED_DOMAINS)
        allowed_services = {
            d: set(svcs or []) for d, svcs in (cfg.get("allowed_services") or {}).items()
        }
        entity_globs = list(cfg.get("entity_filter_glob") or [])
        read_only = bool(cfg.get("read_only", True))
        max_states = int(cfg.get("max_states_returned") or 200)
        max_bytes = int(cfg.get("max_response_bytes") or 1_048_576)
        token = _resolve_token(cfg, ctx)

        async with _build_client(cfg) as client:
            if args.action == "list_states":
                return await _do_list_states(
                    args, client, base_url, token,
                    allowed_domains=allowed_domains,
                    entity_globs=entity_globs,
                    max_states=max_states,
                    max_bytes=max_bytes,
                )
            if args.action == "get_state":
                return await _do_get_state(
                    args, client, base_url, token,
                    allowed_domains=allowed_domains,
                    entity_globs=entity_globs,
                    max_bytes=max_bytes,
                )
            if args.action == "call_service":
                return await _do_call_service(
                    args, client, base_url, token,
                    allowed_domains=allowed_domains,
                    allowed_services=allowed_services,
                    read_only=read_only,
                    max_bytes=max_bytes,
                )
            if args.action == "render_template":
                return await _do_render_template(
                    args, client, base_url, token, max_bytes=max_bytes,
                )
            if args.action == "get_history":
                return await _do_get_history(
                    args, client, base_url, token,
                    allowed_domains=allowed_domains,
                    entity_globs=entity_globs,
                    max_bytes=max_bytes,
                )
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                f"home_assistant: unknown action {args.action!r}",
                detail={"plugin": "home_assistant", "action": args.action},
            )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


async def _do_list_states(
    args: _HomeAssistantArgsWrapper,
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    allowed_domains: set[str],
    entity_globs: list[str],
    max_states: int,
    max_bytes: int,
) -> HomeAssistantResult:
    import json  # noqa: PLC0415

    status, body = await _request(
        client, "GET", urljoin(base_url + "/", "api/states"), token, max_bytes=max_bytes
    )
    if status >= 400:
        return HomeAssistantResult(action="list_states", ok=False, error=body[:500])
    rows = json.loads(body)
    domain_filter = (args.domain or "").strip() or None
    out: list[HomeAssistantHit] = []
    truncated = False
    for row in rows:
        eid = row.get("entity_id", "")
        d = _domain_of(eid)
        if d not in allowed_domains:
            continue
        if domain_filter and d != domain_filter:
            continue
        if _entity_filtered_out(eid, entity_globs):
            continue
        out.append(_trim_state(row, verbose=bool(args.verbose)))
        if len(out) >= max_states:
            truncated = True
            break
    return HomeAssistantResult(
        action="list_states", ok=True, states=out, truncated=truncated
    )


async def _do_get_state(
    args: _HomeAssistantArgsWrapper,
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    allowed_domains: set[str],
    entity_globs: list[str],
    max_bytes: int,
) -> HomeAssistantResult:
    import json  # noqa: PLC0415

    entity_id = args.entity_id if isinstance(args.entity_id, str) else None
    if not entity_id:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "home_assistant: get_state requires entity_id",
            detail={"plugin": "home_assistant"},
        )
    domain = _domain_of(entity_id)
    if domain not in allowed_domains:
        raise _refuse_domain(domain)
    if _entity_filtered_out(entity_id, entity_globs):
        raise SparkError(
            ErrorCode.PERMISSION_MISSING,
            f"home_assistant: entity {entity_id!r} excluded by entity_filter_glob",
            detail={"plugin": "home_assistant", "missing_domain": domain},
        )

    status, body = await _request(
        client,
        "GET",
        urljoin(base_url + "/", f"api/states/{entity_id}"),
        token,
        max_bytes=max_bytes,
    )
    if status == 404:
        return HomeAssistantResult(
            action="get_state", ok=False, error=f"entity {entity_id!r} not found"
        )
    if status >= 400:
        return HomeAssistantResult(action="get_state", ok=False, error=body[:500])
    return HomeAssistantResult(
        action="get_state", ok=True, state=_trim_state(json.loads(body), verbose=True)
    )


async def _do_call_service(
    args: _HomeAssistantArgsWrapper,
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    allowed_domains: set[str],
    allowed_services: dict[str, set[str]],
    read_only: bool,
    max_bytes: int,
) -> HomeAssistantResult:
    domain = (args.domain or "").strip()
    service = (args.service or "").strip()
    if not domain or not service:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "home_assistant: call_service requires both domain and service",
            detail={"plugin": "home_assistant"},
        )
    if read_only:
        raise _refuse_read_only()
    if domain not in allowed_domains:
        raise _refuse_domain(domain)
    if service not in allowed_services.get(domain, set()):
        raise _refuse_service(
            domain, service,
            reason=f"service {service!r} not in allowed_services[{domain!r}]",
        )
    payload: dict[str, Any] = dict(args.data or {})
    if args.entity_id is not None:
        payload["entity_id"] = args.entity_id

    status, body = await _request(
        client,
        "POST",
        urljoin(base_url + "/", f"api/services/{domain}/{service}"),
        token,
        json_body=payload,
        max_bytes=max_bytes,
    )
    if status >= 400:
        return HomeAssistantResult(action="call_service", ok=False, error=body[:500])
    return HomeAssistantResult(action="call_service", ok=True)


async def _do_render_template(
    args: _HomeAssistantArgsWrapper,
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    max_bytes: int,
) -> HomeAssistantResult:
    template = (args.template or "").strip()
    if not template:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "home_assistant: render_template requires template",
            detail={"plugin": "home_assistant"},
        )
    status, body = await _request(
        client,
        "POST",
        urljoin(base_url + "/", "api/template"),
        token,
        json_body={"template": template},
        max_bytes=max_bytes,
    )
    if status >= 400:
        return HomeAssistantResult(action="render_template", ok=False, error=body[:500])
    return HomeAssistantResult(action="render_template", ok=True, rendered=body)


async def _do_get_history(
    args: _HomeAssistantArgsWrapper,
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    allowed_domains: set[str],
    entity_globs: list[str],
    max_bytes: int,
) -> HomeAssistantResult:
    import json  # noqa: PLC0415
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    entity_id = args.entity_id if isinstance(args.entity_id, str) else None
    if not entity_id:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            (
                "home_assistant: get_history requires entity_id "
                "(unfiltered HA history queries return the world)"
            ),
            detail={"plugin": "home_assistant"},
        )
    domain = _domain_of(entity_id)
    if domain not in allowed_domains:
        raise _refuse_domain(domain)
    if _entity_filtered_out(entity_id, entity_globs):
        raise SparkError(
            ErrorCode.PERMISSION_MISSING,
            f"home_assistant: entity {entity_id!r} excluded by entity_filter_glob",
            detail={"plugin": "home_assistant", "missing_domain": domain},
        )
    hours = int(args.hours or 24)
    if hours < 1 or hours > 24:
        hours = 24
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ts = since.isoformat(timespec="seconds").replace("+00:00", "")
    url = urljoin(
        base_url + "/",
        f"api/history/period/{ts}?filter_entity_id={entity_id}",
    )
    status, body = await _request(client, "GET", url, token, max_bytes=max_bytes)
    if status >= 400:
        return HomeAssistantResult(action="get_history", ok=False, error=body[:500])
    return HomeAssistantResult(
        action="get_history", ok=True, history=json.loads(body)
    )


# ---------------------------------------------------------------------------
# Discovery — driven by the editor's "Test connection & discover" button
# ---------------------------------------------------------------------------


async def discover(cfg: dict[str, Any], ctx: Any) -> HomeAssistantDiscovery:
    """Read-only HA introspection used by the Plugins page editor.

    Mirrors the runtime error mapping: missing token →
    ``SECRET_NOT_FOUND``, RFC1918 connect → ``URL_PRIVATE_IP``, etc.
    The caller (REST endpoint) catches and reflects these as
    ``HomeAssistantDiscovery(ok=false, error_code=...)`` so the editor
    can render the same Failure Inspector compact panel runtime errors
    show.

    Discovery is read-only: hits ``/api/config``, ``/api/services``, and
    ``/api/states`` — never mutates.
    """
    import json  # noqa: PLC0415

    base_url = (cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        return HomeAssistantDiscovery(
            ok=False,
            error="base_url not set",
            error_code=ErrorCode.OPERATOR_OVERRIDE_REFUSED.value,
            error_detail={"plugin": "home_assistant", "field": "base_url"},
        )
    try:
        token = _resolve_token(cfg, ctx)
    except SparkError as exc:
        return HomeAssistantDiscovery(
            ok=False,
            error=exc.message,
            error_code=exc.code.value,
            error_detail=exc.detail,
        )

    max_bytes = int(cfg.get("max_response_bytes") or 1_048_576)
    try:
        async with _build_client(cfg) as client:
            cfg_status, cfg_body = await _request(
                client,
                "GET",
                urljoin(base_url + "/", "api/config"),
                token,
                max_bytes=max_bytes,
            )
            if cfg_status >= 400:
                return HomeAssistantDiscovery(
                    ok=False,
                    error=f"HA /api/config returned {cfg_status}",
                    error_code=ErrorCode.PLUGIN_RAISED.value,
                )
            cfg_obj = json.loads(cfg_body)
            instance_version = cfg_obj.get("version")
            instance_url = base_url

            srv_status, srv_body = await _request(
                client,
                "GET",
                urljoin(base_url + "/", "api/services"),
                token,
                max_bytes=max_bytes,
            )
            if srv_status >= 400:
                return HomeAssistantDiscovery(
                    ok=False,
                    error=f"HA /api/services returned {srv_status}",
                    error_code=ErrorCode.PLUGIN_RAISED.value,
                )
            services_payload = json.loads(srv_body)

            sts_status, sts_body = await _request(
                client,
                "GET",
                urljoin(base_url + "/", "api/states"),
                token,
                max_bytes=max_bytes,
            )
            if sts_status >= 400:
                return HomeAssistantDiscovery(
                    ok=False,
                    error=f"HA /api/states returned {sts_status}",
                    error_code=ErrorCode.PLUGIN_RAISED.value,
                )
            states_payload = json.loads(sts_body)
    except SparkError as exc:
        return HomeAssistantDiscovery(
            ok=False,
            error=exc.message,
            error_code=exc.code.value,
            error_detail=exc.detail,
        )

    # Build entity + per-domain count first so domains can carry an
    # accurate ``entity_count`` even for danger domains the operator
    # may not enable.
    entities: list[HomeAssistantEntityEntry] = []
    domain_counts: dict[str, int] = {}
    for s in states_payload:
        eid = s.get("entity_id") or ""
        d = _domain_of(eid)
        if not d:
            continue
        domain_counts[d] = domain_counts.get(d, 0) + 1
        attrs = s.get("attributes") or {}
        entities.append(
            HomeAssistantEntityEntry(
                entity_id=eid,
                domain=d,
                friendly_name=attrs.get("friendly_name"),
                state=s.get("state"),
            )
        )
    entities.sort(key=lambda e: e.entity_id)

    # Domains: union of (HA's services payload domains) ∪ (entity
    # domains) so the editor sees domains that exist as entities even
    # when no services are registered for them.
    domain_names = sorted(
        set(d.get("domain") for d in services_payload if d.get("domain"))
        | set(domain_counts.keys())
    )
    domains = [
        HomeAssistantDomainEntry(
            name=d,
            label=domain_label(d),
            risk=domain_risk(d),
            entity_count=domain_counts.get(d, 0),
        )
        for d in domain_names
    ]

    services_by_domain: dict[str, list[HomeAssistantServiceEntry]] = {}
    for entry in services_payload:
        dom = entry.get("domain")
        if not dom:
            continue
        svcs = entry.get("services") or {}
        if isinstance(svcs, dict):
            items = []
            for svc_name, svc_meta in svcs.items():
                desc = None
                if isinstance(svc_meta, dict):
                    desc = svc_meta.get("description") or svc_meta.get("name")
                items.append(
                    HomeAssistantServiceEntry(
                        name=svc_name,
                        risk=service_risk(dom, svc_name),
                        description=desc,
                    )
                )
            items.sort(key=lambda s: s.name)
            services_by_domain[dom] = items

    return HomeAssistantDiscovery(
        ok=True,
        domains=domains,
        services_by_domain=services_by_domain,
        entities=entities,
        instance_url=instance_url,
        instance_version=instance_version,
    )
