"""Tuning catalogue — turn an :class:`SparkError` into actionable options.

The Failure Inspector's whole UX rests on this module: given an error,
produce a small ordered list of :class:`TuningOption` records, each
naming the change the operator would otherwise type themselves, the
exact deep-link to land them on the right config page with the form
pre-filled, and a one-line risk statement.

Design notes:

* The catalogue is **pure** — no I/O, no DB. It reads ``error.code`` +
  ``error.detail`` and emits a deterministic list. That keeps it cheap
  to call inside ``SparkError.to_dict()`` (every serialized error pays
  it, including ones the model never sees).
* ``TuningOption.prefill`` is a dict whose shape is consumed by the
  matching frontend page. The shared schema lives in
  ``spark/web/frontend/src/lib/prefill.ts`` (TypeScript discriminated
  union) — keep both ends in sync. The Python side just emits JSON; the
  TS side validates on read.
* When the raise site doesn't pass a structured ``detail`` (legacy
  ``UrlDenied(f"Host {host!r} ...")`` etc.), the catalogue falls back
  to advice-only options. Operators still see WHICH gate fired and a
  human-readable description; they just don't get a one-click prefill.
* ``deep_link`` is always relative (``/security?...``) so it works in
  any deployment mode.

Adding a new code? Add a branch + at least one option, then add a row
to ``tests/unit/test_remediation_catalogue.py`` (parameterized over
every ``ErrorCode``).
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from spark.errors.codes import ErrorCode

Severity = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class TuningOption:
    """One actionable suggestion for the operator.

    Fields:
        label: imperative, ≤60 chars. ("Add /etc to allow_paths")
        description: 1–2 sentence explanation of what this does.
        risk: 1-line statement of what the operator gives up. "None" when
            the option is purely advice (use a workspace path, retry,
            wait for budget reset).
        severity: ``low`` / ``medium`` / ``high`` / ``critical``. Drives
            the chip color and the order ("low" first → safest first).
        deep_link: relative URL with optional ``?prefill=<base64>`` query
            param. ``None`` for advice-only options.
        prefill: dict that the target page reads via the shared TS
            schema in ``lib/prefill.ts``. ``None`` for advice-only.
        audit_kind: the audit ``kind`` the target page will write when
            the operator clicks Save. Used by the inspector's "related
            audit entries" link. ``None`` if the option doesn't mutate
            (advice-only) or doesn't audit.
    """

    label: str
    description: str
    risk: str
    severity: Severity
    deep_link: str | None = None
    prefill: dict[str, Any] | None = None
    audit_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_prefill(payload: dict[str, Any]) -> str:
    """Encode a prefill dict into a URL-safe base64 string.

    Decoded back by ``decodePrefill`` in ``lib/prefill.ts``. Padding is
    stripped — the TS decoder pads on read.
    """
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _link(path: str, prefill: dict[str, Any] | None = None, **params: str) -> str:
    """Build a relative URL with optional ?prefill= and other params."""
    qs: list[str] = []
    for k, v in params.items():
        if v:
            qs.append(f"{k}={v}")
    if prefill:
        qs.append(f"prefill={_encode_prefill(prefill)}")
    return path if not qs else f"{path}?{'&'.join(qs)}"


def _agent(detail: dict[str, Any]) -> str | None:
    """Best-effort agent-name extraction. Many raise sites pass `agent`."""
    val = detail.get("agent") or detail.get("agent_name")
    return str(val) if val else None


# ---------------------------------------------------------------------------
# Per-code option builders
# ---------------------------------------------------------------------------


def _opts_plugin_not_allowed(d: dict[str, Any]) -> list[TuningOption]:
    plugin = d.get("plugin")
    agent = _agent(d)
    # Convention: safest-first. Advice option leads; mutating option follows.
    out: list[TuningOption] = [
        TuningOption(
            label="Use a different plugin already on the allowlist",
            description=(
                "If the agent has another plugin that can do the job, "
                "rephrase the task to use it instead."
            ),
            risk="None — keeps the existing surface intact.",
            severity="low",
        )
    ]
    if plugin and agent:
        out.append(
            TuningOption(
                label=f"Add {plugin} to {agent}'s allowlist",
                description=(
                    f"Allows {agent} to invoke the {plugin} plugin. "
                    "Per-tool permission grants still apply."
                ),
                risk=(
                    "Agent gains the ability to call this plugin's tools. "
                    "Each tool still gates on its declared permissions."
                ),
                severity="medium",
                deep_link=_link(
                    "/security",
                    prefill={"kind": "plugin_allow", "agent": agent, "plugin": plugin},
                    tab="plugins",
                ),
                prefill={"kind": "plugin_allow", "agent": agent, "plugin": plugin},
                audit_kind="security.plugins.patch",
            )
        )
    return out


def _opts_permission_missing(d: dict[str, Any]) -> list[TuningOption]:
    plugin = d.get("plugin")
    missing = d.get("missing") or []
    if isinstance(missing, str):
        missing = [missing]
    agent = _agent(d)

    # Plugin-specific shapes — the home_assistant plugin emits richer
    # detail keys so the inspector deep-links into the live-config
    # editor with the matching checkbox / toggle pre-ticked + flashed.
    if plugin == "home_assistant":
        return _opts_home_assistant_permission(d)

    # Generic allowlist-grant shape — used by every plugin that
    # implements live introspection (calendar, imap_reader, slack,
    # cloud_drive, …). Plugin emits:
    #
    #   detail = {
    #     "plugin": "<plugin_name>",
    #     "missing_allowlist_item": "<id>",        # the unallowed item
    #     "field": "<config_field_name>",          # e.g. allowed_calendars
    #     "missing_toggle": "<bool_field_name>",   # optional, e.g. read_only
    #     "risk": "safe"|"elevated"|"danger",      # optional, drives chip
    #   }
    if plugin and (d.get("missing_allowlist_item") or d.get("missing_toggle")):
        return _opts_plugin_allowlist_grant(d)

    out: list[TuningOption] = [
        TuningOption(
            label="Re-scope the task so this permission isn't needed",
            description=(
                "If the agent doesn't actually need this capability, "
                "tighten the prompt or split the work between agents."
            ),
            risk="None — keeps the existing safety boundary intact.",
            severity="low",
        )
    ]
    if plugin and agent and missing:
        out.append(
            TuningOption(
                label=f"Grant {', '.join(missing)} to {agent}",
                description=(
                    f"Adds the missing permission(s) to {agent}'s grants for {plugin}. "
                    "The plugin still operates within the agent's allow_hosts / allow_paths."
                ),
                risk=(
                    f"{agent} can perform {', '.join(missing)} actions through {plugin}. "
                    "Combine with narrow host/path allowlists for defense-in-depth."
                ),
                severity="medium",
                deep_link=_link(
                    "/security",
                    prefill={
                        "kind": "permission_grant",
                        "agent": agent,
                        "plugin": plugin,
                        "permissions": list(missing),
                    },
                    tab="plugins",
                ),
                prefill={
                    "kind": "permission_grant",
                    "agent": agent,
                    "plugin": plugin,
                    "permissions": list(missing),
                },
                audit_kind="security.permissions.grant",
            )
        )
    return out


def _opts_plugin_allowlist_grant(d: dict[str, Any]) -> list[TuningOption]:
    """Generic allowlist-refusal options for any plugin.

    Plugins that ship a live-introspection editor emit one of two
    shapes:

    - ``missing_allowlist_item: "<id>"`` + ``field: "<config_field>"``
      — operator should tick this item in the editor's checkbox grid.
    - ``missing_toggle: "<field>"`` — operator should flip a boolean.

    The catalogue routes both into the same deep-link target
    (``/plugins?plugin=<name>&prefill=...``) carrying the new
    ``plugin_allowlist_grant`` prefill kind. The plugin's custom
    editor reads the prefill on mount and flashes / ticks / pre-flips
    the matching control with an amber ring — exactly like the HA
    flow, but parametric over plugin name + field name.

    ``risk`` (``safe``/``elevated``/``danger``) in detail drives the
    chip color. Danger items still gate behind the typed-confirm
    modal inside the editor.
    """
    plugin = d.get("plugin") or "?"
    risk = d.get("risk", "elevated")
    out: list[TuningOption] = [
        TuningOption(
            label="Re-scope the task to avoid this surface",
            description=(
                "If the agent doesn't actually need this calendar / "
                "mailbox / channel / remote, tighten the prompt or "
                "hand the work to a different agent."
            ),
            risk="None — preserves the existing allowlist.",
            severity="low",
        )
    ]
    if d.get("missing_toggle"):
        toggle = str(d["missing_toggle"])
        prefill = {
            "kind": "plugin_allowlist_grant",
            "plugin": plugin,
            "toggle": toggle,
        }
        out.append(
            TuningOption(
                label=f"Flip {toggle!r} on {plugin}",
                description=(
                    f"Lets the plugin perform the gated operation. "
                    "Other allowlists still apply."
                ),
                risk=(
                    f"Agent gains broader access through the {plugin} plugin. "
                    "Pair with tight item-level allowlists."
                ),
                severity="high",
                deep_link=_link("/plugins", prefill=prefill, plugin=plugin),
                prefill=prefill,
                audit_kind="security.plugin_config.update",
            )
        )
        return out

    item = d.get("missing_allowlist_item")
    field = d.get("field")
    if item and field:
        sev: Severity = (
            "critical" if risk == "danger" else "high" if risk == "elevated" else "medium"
        )
        prefill = {
            "kind": "plugin_allowlist_grant",
            "plugin": plugin,
            "add_item": str(item),
            "field": str(field),
        }
        out.append(
            TuningOption(
                label=f"Allow `{item}` on {plugin}",
                description=(
                    f"Adds {str(item)!r} to the {plugin} plugin's "
                    f"`{field}` allowlist. Other items stay refused. "
                    "Danger items still require typed-confirm on the "
                    "editor before they activate."
                ),
                risk=(
                    f"Agent gains access to `{item}` through {plugin} "
                    f"({risk}). The rest of the allowlist is unchanged."
                ),
                severity=sev,
                deep_link=_link("/plugins", prefill=prefill, plugin=plugin),
                prefill=prefill,
                audit_kind="security.plugin_config.update",
            )
        )
    return out


def _opts_home_assistant_permission(d: dict[str, Any]) -> list[TuningOption]:
    """``home_assistant``-specific tuning options.

    Three shapes the plugin emits:

    - ``missing_domain``: domain not in ``allowed_domains``.
    - ``missing_service``: ``"<domain>.<service>"`` not in
      ``allowed_services[domain]``.
    - ``missing_toggle: "read_only"``: ``read_only=true`` blocked
      ``call_service``.

    All three deep-link into the live-config editor on `/plugins` with
    the matching checkbox / toggle pre-ticked + flashed via the
    ``home_assistant_grant`` prefill kind.
    """
    out: list[TuningOption] = [
        TuningOption(
            label="Re-scope the task to avoid this domain / service",
            description=(
                "If the agent doesn't actually need to touch this part "
                "of Home Assistant, tighten the prompt or hand the work "
                "to a different agent."
            ),
            risk="None — preserves the existing allowlist.",
            severity="low",
        )
    ]
    missing_domain = d.get("missing_domain")
    missing_service = d.get("missing_service")
    missing_toggle = d.get("missing_toggle")

    if missing_toggle == "read_only":
        prefill = {"kind": "home_assistant_grant", "toggle": "read_only"}
        out.append(
            TuningOption(
                label="Disable read_only on home_assistant",
                description=(
                    "Lets the plugin call services. Service-level allowlist "
                    "still applies — only services in `allowed_services` fire."
                ),
                risk=(
                    "Agent gains the ability to mutate HA state for any service "
                    "you've allowlisted. Pair with a tight per-domain service list."
                ),
                severity="high",
                deep_link=_link("/plugins", prefill=prefill, plugin="home_assistant"),
                prefill=prefill,
                audit_kind="security.plugin_config.update",
            )
        )
        return out

    if missing_service and isinstance(missing_service, str):
        # Severity: danger services → critical chip, others → high.
        from spark.plugins.builtins.home_assistant import service_risk  # noqa: PLC0415

        try:
            dom, svc = missing_service.split(".", 1)
        except ValueError:
            dom, svc = "", missing_service
        risk = service_risk(dom, svc) if dom and svc else "elevated"
        sev: Severity = "critical" if risk == "danger" else "high"
        prefill = {"kind": "home_assistant_grant", "add_service": missing_service}
        out.append(
            TuningOption(
                label=f"Allow `{missing_service}` on home_assistant",
                description=(
                    f"Adds {missing_service!r} to the per-domain "
                    "service allowlist. The agent can call only this "
                    "service in this domain — other services stay refused."
                ),
                risk=(
                    f"Agent gains the ability to call `{missing_service}` "
                    f"({risk}). Other services in the same domain remain refused."
                ),
                severity=sev,
                deep_link=_link("/plugins", prefill=prefill, plugin="home_assistant"),
                prefill=prefill,
                audit_kind="security.plugin_config.update",
            )
        )
        return out

    if missing_domain and isinstance(missing_domain, str):
        from spark.plugins.builtins.home_assistant import domain_risk  # noqa: PLC0415

        risk = domain_risk(missing_domain)
        sev = "critical" if risk == "danger" else "medium" if risk == "elevated" else "medium"
        prefill = {"kind": "home_assistant_grant", "add_domain": missing_domain}
        out.append(
            TuningOption(
                label=f"Allow `{missing_domain}` on home_assistant",
                description=(
                    f"Adds {missing_domain!r} to the home_assistant "
                    "plugin's `allowed_domains`. The agent can read "
                    "states for entities in this domain (and call "
                    "services if both `read_only=false` AND a matching "
                    "service is in the per-domain allowlist)."
                ),
                risk=(
                    f"Agent gains visibility (and potentially control) over "
                    f"every entity in the `{missing_domain}` domain "
                    f"({risk}). High-risk domains (lock / camera / "
                    "device_tracker / person / alarm_control_panel / "
                    "vacuum) require typed-confirm on the editor before "
                    "they activate."
                ),
                severity=sev,
                deep_link=_link("/plugins", prefill=prefill, plugin="home_assistant"),
                prefill=prefill,
                audit_kind="security.plugin_config.update",
            )
        )
        return out

    # Fall through to the generic shape if the detail didn't match any
    # of the home_assistant-specific keys.
    return out


def _opts_budget_exceeded(d: dict[str, Any], code: ErrorCode) -> list[TuningOption]:
    used = d.get("used")
    limit = d.get("limit")
    agent = _agent(d)
    field_map = {
        ErrorCode.BUDGET_ITER_EXCEEDED: ("max_iterations", "iteration"),
        ErrorCode.BUDGET_MODEL_EXCEEDED: ("max_model_calls", "model call"),
        ErrorCode.BUDGET_TOOL_EXCEEDED: ("max_tool_calls", "tool call"),
        ErrorCode.BUDGET_TOKEN_EXCEEDED: ("max_tokens_per_run", "token"),
        ErrorCode.BUDGET_WALL_CLOCK_EXCEEDED: ("max_runtime_seconds", "runtime second"),
    }
    yaml_field, unit = field_map.get(code, ("budget", "unit"))
    suggested = None
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        suggested = int(limit * 1.5)
    out: list[TuningOption] = [
        TuningOption(
            label="Investigate why the planner hit the cap",
            description=(
                "A correctly-bounded task usually doesn't exhaust the budget. "
                "Check the run's trace for tool retry loops or planner regressions."
            ),
            risk="None — diagnostic step.",
            severity="low",
        )
    ]
    if agent:
        prefill = {
            "kind": "runtime_budget",
            "agent": agent,
            "field": yaml_field,
            "current": limit,
            "suggested": suggested,
        }
        out.append(
            TuningOption(
                label=(
                    f"Raise {yaml_field} to {suggested}"
                    if suggested
                    else f"Raise {yaml_field}"
                ),
                description=(
                    f"Lifts the per-run {unit} cap for {agent}. "
                    "Existing observed behavior probably needs ~50% headroom; tune as needed."
                ),
                risk=(
                    f"Higher {unit} ceiling means a runaway planner can spend more before "
                    "hitting the next gate. Pair with a cost budget for hard money cap."
                ),
                severity="medium",
                deep_link=_link(
                    "/agents/{agent_name}".replace("{agent_name}", agent),
                    prefill=prefill,
                ),
                prefill=prefill,
                audit_kind="agent.runtime.patch",
            )
        )
    return out


def _opts_budget_cost_hard_stop(d: dict[str, Any]) -> list[TuningOption]:
    agent = _agent(d)
    return [
        TuningOption(
            label="Wait for the budget period to reset",
            description=(
                "Daily budgets reset at midnight UTC, weekly on Monday, monthly on the 1st. "
                "The Cost page shows the next reset time."
            ),
            risk="None — passive, no action needed.",
            severity="low",
        ),
        TuningOption(
            label="Raise the agent's cost budget",
            description=(
                "Adds headroom for the current period. The new ceiling re-applies to "
                "every run inside the period, not just the next one."
            ),
            risk=(
                "Higher money ceiling per period. Set a soft alert below the hard "
                "stop so you see the climb before the next halt."
            ),
            severity="medium",
            deep_link=_link("/cost", prefill={"kind": "cost_budget", "agent": agent}),
            prefill={"kind": "cost_budget", "agent": agent} if agent else None,
            audit_kind="cost.budget.patch",
        ),
    ]


def _opts_url_denied(d: dict[str, Any], code: ErrorCode) -> list[TuningOption]:
    host = d.get("host") or d.get("hostname") or _extract_host_from_message(d)
    agent = _agent(d)
    if code is ErrorCode.URL_METADATA_BLOCKED:
        return [
            TuningOption(
                label="Cloud metadata is never reachable",
                description=(
                    "169.254.169.254 (AWS/GCP/Azure metadata) and the IPv6 equivalent "
                    "are hard-blocked. There is no operator override."
                ),
                risk="N/A — this gate has no tuning by design (SSRF defense).",
                severity="critical",
            ),
        ]
    if code is ErrorCode.URL_PRIVATE_IP:
        out: list[TuningOption] = [
            TuningOption(
                label="Use the public DNS name instead",
                description=(
                    "If a public alias exists for the same service, use it. "
                    "Public DNS resolves outside the SSRF block."
                ),
                risk="None — preserves the SSRF safety boundary.",
                severity="low",
            )
        ]
        if agent:
            prefill = {"kind": "internal_ip_grant", "agent": agent, "host": host}
            out.append(
                TuningOption(
                    label="Request a time-limited internal-IP grant",
                    description=(
                        f"Lets {agent} reach a private/RFC1918 IP for a bounded "
                        "TTL (default 7 days). Requires typed-name confirmation."
                    ),
                    risk=(
                        "Agent can reach internal services. Audited at critical "
                        "severity. Use a narrow CIDR and a short TTL."
                    ),
                    severity="critical",
                    deep_link=_link("/security", prefill=prefill, tab="network"),
                    prefill=prefill,
                    audit_kind="security.network.internal_grant",
                )
            )
        return out
    if code is ErrorCode.URL_IDN_INVALID:
        return [
            TuningOption(
                label="Use the punycode form of the hostname",
                description=(
                    "Internationalized domain names must arrive as ASCII "
                    "(``xn--…``). The resolver refuses non-ASCII to stop "
                    "homograph attacks."
                ),
                risk="None — fix is on the caller.",
                severity="low",
            )
        ]
    # Generic URL_DENIED
    out = [
        TuningOption(
            label="Use a host already on the allowlist",
            description=(
                "If the data is available from an already-allowed host (e.g. a "
                "different docs mirror), prefer that."
            ),
            risk="None — keeps the existing surface intact.",
            severity="low",
        )
    ]
    if host and agent:
        prefill = {"kind": "network_allow_host", "agent": agent, "host": host}
        out.append(
            TuningOption(
                label=f"Add {host} to {agent}'s allow_hosts",
                description=(
                    f"Lets {agent} reach {host} via HTTP/HTTPS. "
                    "Per-host method rules still apply."
                ),
                risk=(
                    f"Agent gains outbound reach to {host}. Combine with method "
                    "allowlists (GET/POST only) to narrow the surface."
                ),
                severity="medium",
                deep_link=_link("/security", prefill=prefill, tab="network"),
                prefill=prefill,
                audit_kind="security.network.patch",
            )
        )
    return out


def _opts_method_not_allowed(d: dict[str, Any]) -> list[TuningOption]:
    method = d.get("method")
    host = d.get("host")
    agent = _agent(d)
    out: list[TuningOption] = [
        TuningOption(
            label="Rephrase the operation as an allowed method",
            description=(
                "If a GET-style endpoint exists for the same data, use it. "
                "Many APIs expose read endpoints alongside their write counterparts."
            ),
            risk="None — keeps the method allowlist intact.",
            severity="low",
        )
    ]
    if method and host and agent:
        prefill = {
            "kind": "network_allow_method",
            "agent": agent,
            "host": host,
            "method": method,
        }
        out.append(
            TuningOption(
                label=f"Allow {method} on {host}",
                description=(
                    f"Adds {method} to the per-host rule for {host}. "
                    "Other hosts and methods stay restricted."
                ),
                risk=(
                    f"Agent can issue {method} requests against {host}. "
                    "Mutating methods (POST/PUT/DELETE) deserve extra scrutiny."
                ),
                severity="medium" if method.upper() in {"GET", "HEAD"} else "high",
                deep_link=_link("/security", prefill=prefill, tab="network"),
                prefill=prefill,
                audit_kind="security.network.patch",
            )
        )
    return out


def _opts_response_too_large(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Paginate or narrow the request",
            description=(
                "Most APIs accept a ``limit`` / ``per_page`` parameter. "
                "Smaller pages stay under the cap and are cheaper to scan."
            ),
            risk="None — purely a request shape change.",
            severity="low",
        ),
        TuningOption(
            label="Raise the plugin's max_response_bytes",
            description=(
                "Lifts the response cap in the network plugin's config. "
                "Affects every host the plugin reaches."
            ),
            risk=(
                "Larger responses consume more memory + tokens. Pair with a token "
                "budget so a runaway response can't blow the prompt window."
            ),
            severity="medium",
            deep_link="/plugins",
            prefill=None,
            audit_kind="plugin.config.patch",
        ),
    ]


def _opts_path_denied(d: dict[str, Any]) -> list[TuningOption]:
    path = d.get("path") or _extract_path_from_message(d)
    agent = _agent(d)
    plugin = d.get("plugin")
    provider = d.get("provider")

    # cloud_drive's PATH_DENIED carries provider + path, and the
    # tuning surface lives inside that provider's card in the plugin
    # editor — not in the Security Center filesystem tab.
    if plugin == "cloud_drive" and provider and path:
        suggested = _suggest_allow_root(path) if path else path
        prefill = {
            "kind": "plugin_allowlist_grant",
            "plugin": "cloud_drive",
            "field": "allowed_paths",
            "provider": str(provider),
            "add_item": str(suggested or path),
        }
        return [
            TuningOption(
                label="Re-scope the task to a path already allowed",
                description=(
                    "If the agent doesn't need this path, point it at one of "
                    f"the existing allowed_paths on {provider!r}."
                ),
                risk="None — preserves the existing allowlist.",
                severity="low",
            ),
            TuningOption(
                label=f"Add `{suggested or path}` to {provider} allowed_paths",
                description=(
                    f"Lets the agent read/write under `{suggested or path}` on "
                    f"the {provider!r} provider. Other paths stay refused."
                ),
                risk=(
                    f"Agent gains access to everything under "
                    f"`{suggested or path}` on {provider!r}."
                ),
                severity="high",
                deep_link=_link("/plugins", prefill=prefill, plugin="cloud_drive"),
                prefill=prefill,
                audit_kind="security.plugin_config.update",
            ),
        ]

    out: list[TuningOption] = [
        TuningOption(
            label="Use a workspace-relative path",
            description=(
                "The agent's scratch dir is already on the allowlist. Stage "
                "files there and copy in/out via an explicit step."
            ),
            risk="None — preserves the allow_paths boundary.",
            severity="low",
        )
    ]
    if path and agent:
        # If the operator wants to allow it, suggest the parent directory
        # rather than a single-file allow — far more common shape.
        suggested = _suggest_allow_root(path)
        prefill = {"kind": "fs_allow_path", "agent": agent, "path": suggested}
        out.append(
            TuningOption(
                label=f"Add {suggested} to allow_paths",
                description=(
                    f"Lets {agent}'s filesystem plugin read/write under {suggested}. "
                    "Symlinks still refused at the kernel boundary."
                ),
                risk=(
                    f"Agent gains access to everything under {suggested}. "
                    "Prefer narrower paths when the task allows."
                ),
                severity="high" if _looks_sensitive(suggested) else "medium",
                deep_link=_link("/security", prefill=prefill, tab="filesystem"),
                prefill=prefill,
                audit_kind="security.filesystem.patch",
            )
        )
    return out


def _opts_path_traversal(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Use absolute paths inside the allowlist",
            description=(
                "Or stage files inside the agent's workspace; the workspace is "
                "already canonicalized so traversal can't escape it."
            ),
            risk="None.",
            severity="low",
        ),
        TuningOption(
            label="Path traversal patterns are hard-blocked",
            description=(
                "Paths containing ``..`` or symlink escapes are refused regardless "
                "of allow_paths. There is no operator override — this is a kernel-"
                "level safety boundary."
            ),
            risk="N/A — this gate has no tuning by design.",
            severity="critical",
        ),
    ]


def _opts_path_symlink_refused(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Read or write the resolved real path instead",
            description=(
                "Resolve the symlink at task-prep time, then ask the agent to "
                "operate on the real path."
            ),
            risk="None.",
            severity="low",
        ),
        TuningOption(
            label="Symlinks are refused by design",
            description=(
                "Bind-mount escape via crafted symlinks is a known sandbox-bypass. "
                "Spark refuses all symlink follows."
            ),
            risk="N/A — this gate has no tuning.",
            severity="critical",
        ),
    ]


def _opts_file_too_large(d: dict[str, Any]) -> list[TuningOption]:
    size = d.get("size")
    limit = d.get("max_bytes")
    suggested = None
    if isinstance(size, int) and isinstance(limit, int) and size > limit:
        suggested = max(size, int(limit * 2))
    out: list[TuningOption] = [
        TuningOption(
            label="Read in chunks",
            description=(
                "Most filesystem plugins accept ``offset`` + ``length`` for "
                "ranged reads. Process the file a chunk at a time."
            ),
            risk="None — preserves the read cap.",
            severity="low",
        ),
        TuningOption(
            label=(
                f"Raise max_read_bytes to {suggested}"
                if suggested
                else "Raise max_read_bytes"
            ),
            description=(
                "Lifts the cap in the filesystem plugin's config. Affects every "
                "read this agent does."
            ),
            risk=(
                "Larger reads consume more memory and tokens. A pathological "
                "task can fill the prompt window with one file."
            ),
            severity="medium",
            deep_link="/plugins",
            prefill={"kind": "fs_max_read_bytes", "suggested": suggested},
            audit_kind="plugin.config.patch",
        ),
    ]
    return out


def _opts_file_type_denied(d: dict[str, Any]) -> list[TuningOption]:
    """``cloud_drive.file_type_allowlist`` refusal.

    The plugin emits ``{plugin, extension, field}``. Inspector
    deep-links to the cloud_drive editor with the matching extension
    flashed in the file-type bucket picker.
    """
    plugin = d.get("plugin") or "cloud_drive"
    ext = str(d.get("extension") or "").lstrip(".")
    out: list[TuningOption] = [
        TuningOption(
            label="Stage a file with an allowed extension",
            description=(
                "Convert the deliverable to one of the operator's allowed "
                "types (pdf / txt / docx / xlsx / png / jpeg by default) "
                "and retry."
            ),
            risk="None — preserves the file-type allowlist.",
            severity="low",
        )
    ]
    if ext:
        prefill = {
            "kind": "plugin_allowlist_grant",
            "plugin": plugin,
            "field": "file_type_allowlist",
            "add_item": ext,
        }
        out.append(
            TuningOption(
                label=f"Add `.{ext}` to file_type_allowlist",
                description=(
                    f"Lets the plugin transfer `.{ext}` files. Other "
                    "extensions stay refused."
                ),
                risk=(
                    f"Files of type `.{ext}` can flow through {plugin}. "
                    "Pair with tight `allowed_paths` for defense in depth."
                ),
                severity="medium",
                deep_link=_link("/plugins", prefill=prefill, plugin=plugin),
                prefill=prefill,
                audit_kind="security.plugin_config.update",
            )
        )
    return out


def _opts_file_not_found(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Check the path",
            description=(
                "The file may not exist yet, or the agent may have used a "
                "stale path. Verify with a directory listing first."
            ),
            risk="None — diagnostic.",
            severity="low",
        ),
        TuningOption(
            label="Have the task create the file as a prior step",
            description=(
                "If the task assumed the file existed, add an explicit "
                "create step or an early check-and-bail."
            ),
            risk="None.",
            severity="low",
        ),
    ]


def _opts_sandbox_unavailable(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Run sandbox self-test",
            description=(
                "Confirm the sandbox backend is reachable. Self-test surfaces "
                "the failure mode (missing binary, kernel feature, etc.)."
            ),
            risk="None — read-only check.",
            severity="low",
            deep_link="/security?tab=sandbox",
            audit_kind="security.sandbox.self_test",
        ),
        TuningOption(
            label="Install the sandbox backend",
            description=(
                "Linux: ``apt install bubblewrap`` (Debian) / ``dnf install bubblewrap`` "
                "(Fedora). macOS: sandbox-exec ships with the OS; a missing binary "
                "indicates a broken install."
            ),
            risk=(
                "If the sandbox is unavailable, plugins refuse to run by design — "
                "no fallback to unsandboxed execution exists."
            ),
            severity="high",
        ),
    ]


def _opts_sandbox_timeout(d: dict[str, Any]) -> list[TuningOption]:
    agent = _agent(d)
    prefill = {"kind": "sandbox_timeout", "agent": agent} if agent else None
    return [
        TuningOption(
            label="Profile the operation",
            description=(
                "A correctly-bounded task usually doesn't trip the sandbox "
                "timeout. Inspect the run's trace for slow tools or runaway loops."
            ),
            risk="None — diagnostic.",
            severity="low",
        ),
        TuningOption(
            label="Raise sandbox timeout_seconds",
            description=(
                "Lifts the per-invocation wall-clock cap. Tools that legitimately "
                "take longer (large reads, long-running shells) need this."
            ),
            risk=(
                "Longer-running tools tie up sandbox workers and can mask "
                "infinite-loop bugs. Profile first."
            ),
            severity="medium",
            deep_link=_link("/security", prefill=prefill, tab="sandbox"),
            prefill=prefill,
            audit_kind="security.sandbox.patch",
        ),
    ]


def _opts_sandbox_exec_failed(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Retry the operation",
            description=(
                "Sandbox exec failures are often transient (worker crash, "
                "OOM kill). Retrying picks up a fresh worker."
            ),
            risk="None — operation already failed.",
            severity="low",
        ),
        TuningOption(
            label="Check sandbox logs",
            description=(
                "If the failure persists across retries, the sandbox worker is "
                "crashing on this input. The Ops page's live log tail surfaces "
                "the worker's stderr."
            ),
            risk="None — diagnostic.",
            severity="low",
            deep_link="/ops",
        ),
    ]


def _opts_data_class_blocked(d: dict[str, Any]) -> list[TuningOption]:
    classes = d.get("classes") or []
    if isinstance(classes, str):
        classes = [classes]
    primary = classes[0] if classes else None
    agent = _agent(d)
    scope = d.get("scope")
    out: list[TuningOption] = [
        TuningOption(
            label="Re-scope the task to avoid the class",
            description=(
                "If the agent doesn't actually need the matched content "
                "(e.g. wanted a summary, not the original), tighten the prompt."
            ),
            risk="None — preserves the existing policy.",
            severity="low",
        )
    ]
    if primary:
        prefill_lower = {
            "kind": "data_class_level",
            "data_class": primary,
            "level": "redact",
            "scope": scope,
        }
        out.append(
            TuningOption(
                label=f"Lower {primary} from block to redact",
                description=(
                    "Redact still scrubs the matched span via the chosen mask "
                    "style; the operation continues instead of aborting."
                ),
                risk=(
                    "The model sees a redacted placeholder. Acceptable for most "
                    "categories; for credentials.* a redacted placeholder is still safer "
                    "than a leak but the model may try harder to recover the original."
                ),
                severity="medium",
                deep_link=_link("/filtering", prefill=prefill_lower),
                prefill=prefill_lower,
                audit_kind="security.filtering.category.update",
            )
        )
        if agent:
            prefill_grant = {
                "kind": "data_class_grant",
                "data_class": primary,
                "agent": agent,
                "scope": scope,
            }
            out.append(
                TuningOption(
                    label=f"Grant {agent} an unlimited {primary} carve-out",
                    description=(
                        "Time-bounded grant (default 7 days) that lets the named agent "
                        "handle this class while every other agent stays blocked. "
                        "Opens the Grants drawer on the Filtering page pre-filled."
                    ),
                    risk=(
                        "Audited at critical severity. Pair with the shortest plausible "
                        "TTL and a typed reason. Permanent grants need a danger-tone "
                        "confirm step."
                    ),
                    severity="critical",
                    # Grants live on /filtering now — the dedicated Data
                    # Classes tab on Security Center is being removed.
                    deep_link=_link("/filtering", prefill=prefill_grant),
                    prefill=prefill_grant,
                    audit_kind="security.data_class.grant",
                )
            )
    return out


def _opts_data_class_grant_required(d: dict[str, Any]) -> list[TuningOption]:
    return _opts_data_class_blocked(d)


def _opts_input_schema_invalid(d: dict[str, Any]) -> list[TuningOption]:
    plugin = d.get("plugin")
    return [
        TuningOption(
            label="Send only fields in the plugin's input_schema",
            description=(
                f"The {plugin or 'plugin'}'s schema rejected unknown / mistyped "
                "fields. Spark logs the full validation error operator-side; the "
                "model sees the field count only (to avoid leaking schema details)."
            ),
            risk="None — caller-side fix.",
            severity="low",
        )
    ]


def _opts_output_schema_invalid(d: dict[str, Any]) -> list[TuningOption]:
    plugin = d.get("plugin")
    return [
        TuningOption(
            label="Plugin bug — file an issue",
            description=(
                f"{plugin or 'A plugin'} returned data that doesn't match its "
                "declared output_schema. This is not an operator-tunable error; "
                "it's a plugin bug."
            ),
            risk="None — caller-side.",
            severity="low",
        )
    ]


def _opts_operator_override_refused(d: dict[str, Any]) -> list[TuningOption]:
    field_name = d.get("field")
    return [
        TuningOption(
            label=(
                f"Field {field_name!r} is locked by operator config"
                if field_name
                else "Field is locked by operator config"
            ),
            description=(
                "The operator pinned this value in plugin config; the agent can't "
                "override it. If this is wrong, ask the operator to unlock."
            ),
            risk="N/A — by design.",
            severity="low",
            deep_link="/plugins",
        )
    ]


def _opts_secret_not_found(d: dict[str, Any]) -> list[TuningOption]:
    name = d.get("secret_name") or d.get("name")
    return [
        TuningOption(
            label=(
                f"Populate the secret {name!r}"
                if name
                else "Populate the secret"
            ),
            description=(
                "Run ``spark secrets set <name>`` on the host. The age vault stores "
                "it encrypted; only the runtime can read it."
            ),
            risk=(
                "Stored secrets become readable by every agent that's been granted "
                "the matching SECRETS_READ permission for this name."
            ),
            severity="medium",
            deep_link="/secrets",
            audit_kind="secret.set",
        )
    ]


def _opts_secret_provider_unavailable(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Initialize the age vault",
            description=(
                "Run ``spark secrets init-age-vault`` on the host. The vault is "
                "the only secret backend; without it, no secret reads succeed."
            ),
            risk=(
                "First-time init creates an age identity; lose it and existing "
                "encrypted secrets become unrecoverable."
            ),
            severity="high",
            deep_link="/secrets",
        )
    ]


def _opts_frozen(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Wait for the operator to unfreeze",
            description=(
                f"The runtime is frozen by operator action ({d.get('reason') or 'no reason given'}). "
                "All non-essential operations are refused until unfreeze."
            ),
            risk="None — passive.",
            severity="low",
        ),
        TuningOption(
            label="Unfreeze the runtime",
            description=(
                "Operator-only action. Unfreeze re-enables every gated operation; "
                "a typed-confirm step prevents accidental clicks."
            ),
            risk=(
                "Whatever incident triggered the freeze becomes live again. "
                "Investigate before unfreezing."
            ),
            severity="critical",
            deep_link="/security?tab=global",
            audit_kind="security.posture.unfreeze",
        ),
    ]


def _opts_approval_required(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Approve the task in the Scheduler",
            description=(
                "The task is paused awaiting operator approval. Approve to "
                "release the run; reject to terminate it."
            ),
            risk=(
                "Approving runs the task with the requested permissions and budget. "
                "Inspect the trigger payload and prior runs first."
            ),
            severity="medium",
            deep_link="/scheduler",
            audit_kind="task.approval.decide",
        )
    ]


def _opts_run_window_closed(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Wait for the next run window",
            description=(
                "The task is configured to run only inside a specific window. "
                "The Scheduler shows the next eligible time."
            ),
            risk="None — passive.",
            severity="low",
            deep_link="/scheduler",
        ),
        TuningOption(
            label="Widen the run_window",
            description=(
                "Edit the task's ``run_window`` in YAML. Wider windows trade "
                "predictability for flexibility."
            ),
            risk="More opportunities for the task to fire — make sure that's intended.",
            severity="medium",
            deep_link="/scheduler",
            audit_kind="task.spec.patch",
        ),
    ]


def _opts_dlq_unacked(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Ack the dead-letter queue entry",
            description=(
                "The task hit its retry ceiling. Inspect the trace, decide if the "
                "underlying issue is fixed, then ack to allow re-runs."
            ),
            risk=(
                "Ack-ing without fixing the cause means the task will fail again "
                "on the next trigger and re-enter DLQ."
            ),
            severity="medium",
            deep_link="/scheduler",
            audit_kind="task.dlq.ack",
        )
    ]


def _opts_plugin_not_registered(d: dict[str, Any]) -> list[TuningOption]:
    plugin = d.get("plugin")
    return [
        TuningOption(
            label=(
                f"Install the {plugin} plugin"
                if plugin
                else "Install the missing plugin"
            ),
            description=(
                "The agent's allowlist references a plugin that isn't installed. "
                "Add it to the runtime's plugin set and restart."
            ),
            risk=(
                "Plugins ship with permissions; review the plugin's declared "
                "capabilities before installing."
            ),
            severity="medium",
            deep_link="/ops",
        )
    ]


def _opts_plugin_raised(d: dict[str, Any]) -> list[TuningOption]:
    return [
        TuningOption(
            label="Inspect the plugin's logs",
            description=(
                "An unhandled exception inside the plugin. Plugin logs surface "
                "the original traceback; the model only sees the error class."
            ),
            risk="None — diagnostic.",
            severity="low",
            deep_link="/ops",
        ),
        TuningOption(
            label="Retry the operation",
            description=(
                "Some plugin failures are transient (connection reset, rate "
                "limit, race). Retrying after a backoff often succeeds."
            ),
            risk="None — operation already failed.",
            severity="low",
        ),
    ]


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


_DISPATCH: dict[ErrorCode, Any] = {
    ErrorCode.PLUGIN_NOT_ALLOWED: _opts_plugin_not_allowed,
    ErrorCode.PLUGIN_NOT_REGISTERED: _opts_plugin_not_registered,
    ErrorCode.PERMISSION_MISSING: _opts_permission_missing,
    ErrorCode.BUDGET_ITER_EXCEEDED: lambda d: _opts_budget_exceeded(d, ErrorCode.BUDGET_ITER_EXCEEDED),
    ErrorCode.BUDGET_MODEL_EXCEEDED: lambda d: _opts_budget_exceeded(d, ErrorCode.BUDGET_MODEL_EXCEEDED),
    ErrorCode.BUDGET_TOOL_EXCEEDED: lambda d: _opts_budget_exceeded(d, ErrorCode.BUDGET_TOOL_EXCEEDED),
    ErrorCode.BUDGET_TOKEN_EXCEEDED: lambda d: _opts_budget_exceeded(d, ErrorCode.BUDGET_TOKEN_EXCEEDED),
    ErrorCode.BUDGET_WALL_CLOCK_EXCEEDED: lambda d: _opts_budget_exceeded(d, ErrorCode.BUDGET_WALL_CLOCK_EXCEEDED),
    ErrorCode.BUDGET_COST_HARD_STOP: _opts_budget_cost_hard_stop,
    ErrorCode.INPUT_SCHEMA_INVALID: _opts_input_schema_invalid,
    ErrorCode.OUTPUT_SCHEMA_INVALID: _opts_output_schema_invalid,
    ErrorCode.OPERATOR_OVERRIDE_REFUSED: _opts_operator_override_refused,
    ErrorCode.SANDBOX_UNAVAILABLE: _opts_sandbox_unavailable,
    ErrorCode.SANDBOX_TIMEOUT: _opts_sandbox_timeout,
    ErrorCode.SANDBOX_EXEC_FAILED: _opts_sandbox_exec_failed,
    ErrorCode.URL_DENIED: lambda d: _opts_url_denied(d, ErrorCode.URL_DENIED),
    ErrorCode.URL_METADATA_BLOCKED: lambda d: _opts_url_denied(d, ErrorCode.URL_METADATA_BLOCKED),
    ErrorCode.URL_PRIVATE_IP: lambda d: _opts_url_denied(d, ErrorCode.URL_PRIVATE_IP),
    ErrorCode.URL_IDN_INVALID: lambda d: _opts_url_denied(d, ErrorCode.URL_IDN_INVALID),
    ErrorCode.METHOD_NOT_ALLOWED: _opts_method_not_allowed,
    ErrorCode.RESPONSE_TOO_LARGE: _opts_response_too_large,
    ErrorCode.PATH_DENIED: _opts_path_denied,
    ErrorCode.PATH_TRAVERSAL: _opts_path_traversal,
    ErrorCode.PATH_SYMLINK_REFUSED: _opts_path_symlink_refused,
    ErrorCode.FILE_NOT_FOUND: _opts_file_not_found,
    ErrorCode.FILE_TOO_LARGE: _opts_file_too_large,
    ErrorCode.FILE_TYPE_DENIED: _opts_file_type_denied,
    ErrorCode.SECRET_NOT_FOUND: _opts_secret_not_found,
    ErrorCode.SECRET_PROVIDER_UNAVAILABLE: _opts_secret_provider_unavailable,
    ErrorCode.FROZEN: _opts_frozen,
    ErrorCode.APPROVAL_REQUIRED: _opts_approval_required,
    ErrorCode.RUN_WINDOW_CLOSED: _opts_run_window_closed,
    ErrorCode.DLQ_UNACKED: _opts_dlq_unacked,
    ErrorCode.DATA_CLASS_BLOCKED: _opts_data_class_blocked,
    ErrorCode.DATA_CLASS_GRANT_REQUIRED: _opts_data_class_grant_required,
    ErrorCode.PLUGIN_RAISED: _opts_plugin_raised,
}


def options_for(code: ErrorCode, detail: dict[str, Any] | None) -> list[TuningOption]:
    """Return ordered tuning options for ``(code, detail)``.

    Always returns at least one option; falls back to a generic
    "no operator-tunable knob" option for unknown codes.
    """
    builder = _DISPATCH.get(code)
    if builder is None:
        return [
            TuningOption(
                label="No operator-tunable knob for this code",
                description=(
                    f"{code.value} doesn't currently surface a remediation "
                    "option in the catalogue. File an issue if this is a "
                    "common gate so it can be added."
                ),
                risk="N/A.",
                severity="low",
            )
        ]
    return builder(detail or {})


# ---------------------------------------------------------------------------
# Heuristic helpers — extract context from legacy raise sites that don't
# pass a structured detail dict yet.
# ---------------------------------------------------------------------------


def _extract_host_from_message(d: dict[str, Any]) -> str | None:
    msg = str(d.get("_message") or "")
    # Match "Host 'foo' is not in the allowlist" / "host 'foo' failed ..."
    import re  # noqa: PLC0415

    m = re.search(r"[Hh]ost\s+['\"]([^'\"]+)['\"]", msg)
    return m.group(1) if m else None


def _extract_path_from_message(d: dict[str, Any]) -> str | None:
    msg = str(d.get("_message") or "")
    import re  # noqa: PLC0415

    m = re.search(r"Path\s+(\S+?)\s+is", msg)
    return m.group(1) if m else None


def _suggest_allow_root(path: str) -> str:
    """Pick a sensible allow-list root for a denied path.

    For ``/etc/passwd`` → ``/etc``. For ``/Users/x/proj/file.txt`` →
    ``/Users/x/proj``. The point: nudging operators toward "the directory"
    rather than per-file allows; everyone reaches for the parent anyway.
    """
    import os  # noqa: PLC0415

    if not path:
        return path
    parent = os.path.dirname(path.rstrip("/"))
    return parent or path


def _looks_sensitive(path: str) -> bool:
    """Hint that the suggested allow-list addition crosses a sensitive surface.

    Pure heuristic — drives the option's severity chip from medium → high
    so operators see the warning before clicking through.
    """
    p = path.lower()
    sensitive = ("/etc", "/root", "/var/log", "/.ssh", "/.aws", "/.gnupg")
    return any(s in p for s in sensitive)
