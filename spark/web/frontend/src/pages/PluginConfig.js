import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Info, ExternalLink } from "lucide-react";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
// ---------------------------------------------------------------------------
// Plugin-specific help hints. Keyed as "plugin_name.field_name". These fill
// in the gaps where a Pydantic schema alone can't capture "what does this
// actually look like?" — especially for complex list-of-object fields like
// http_tool.rules. Add entries here whenever a field needs more context than
// its description provides. Becomes the gold-standard reference operators
// see when configuring a plugin.
// ---------------------------------------------------------------------------
const FIELD_HELP = {
    "http_tool.rules": {
        hint: "List of per-host rules. Each rule pins one hostname and declares " +
            "which HTTP methods are allowed, size/timeout overrides, and whether " +
            "GET HTML responses should be stripped to readable-main-content.",
        example: JSON.stringify([
            {
                host: "api.github.com",
                allowed_methods: ["GET"],
                allow_http: false,
                max_response_bytes: 5_000_000,
                note: "read-only GitHub API lookups",
            },
            {
                host: "example.com",
                allowed_methods: ["GET"],
                extract_main_content: true,
            },
        ], null, 2),
        docsHref: "/wiki/Plugin-Reference-HTTP-Tool",
    },
    "http_tool.default_max_response_bytes": {
        hint: "Cap applied when a host rule does not set its own max_response_bytes.",
    },
    "http_tool.default_connect_timeout_seconds": {
        hint: "TCP connect timeout in seconds. 5s is usually enough for well-behaved APIs.",
    },
    "http_tool.default_read_timeout_seconds": {
        hint: "Read timeout in seconds. Increase for APIs that stream large responses; " +
            "decrease to fail fast on slow endpoints.",
    },
    "http_tool.user_agent": {
        hint: "User-Agent header sent with every request.",
    },
    "http_client.allow_hosts": {
        hint: "One hostname per line. Exact-match only; no wildcards.",
        example: "api.github.com\nwww.wikipedia.org",
    },
    "filesystem.allow_paths": {
        hint: "Absolute paths the plugin may read under. Each path is checked with " +
            "symlink resolution; traversal outside the list is refused.",
        example: "/data/spark-volume/deliverables\n/data/spark-volume/scratch",
    },
    "filesystem.deny_paths": {
        hint: "Deny-list applied after allow_paths. Useful for carving out subdirectories.",
    },
    "shell.allowed_commands": {
        hint: "Argv-first tokens this plugin may invoke. Matched strictly (no shell " +
            "interpolation, no PATH lookup — binaries must be pre-installed).",
        example: "ls\ngit\njq",
    },
    "web_search.provider": {
        hint: "Which search backend to use. Requires the matching secret.",
    },
    "image_gen.provider": {
        hint: "Which image-gen backend to use. Requires the matching secret.",
    },
    "email_sender.smtp_host": {
        hint: "SMTP server hostname. TLS is required; plaintext SMTP is refused.",
    },
};
function specsFromSchema(schema) {
    const props = (schema.properties ?? {});
    const required = new Set(schema.required ?? []);
    const out = [];
    for (const [name, spec] of Object.entries(props)) {
        const t = spec.type || "string";
        const field = {
            name,
            type: t,
            default: spec.default,
            required: required.has(name),
        };
        if (spec.description)
            field.description = String(spec.description);
        if (typeof spec.minimum === "number")
            field.minimum = spec.minimum;
        if (typeof spec.maximum === "number")
            field.maximum = spec.maximum;
        if (typeof spec.exclusiveMinimum === "number")
            field.exclusiveMinimum = spec.exclusiveMinimum;
        if (typeof spec.exclusiveMaximum === "number")
            field.exclusiveMaximum = spec.exclusiveMaximum;
        if (typeof spec.minLength === "number")
            field.minLength = spec.minLength;
        if (typeof spec.maxLength === "number")
            field.maxLength = spec.maxLength;
        if (typeof spec.pattern === "string")
            field.pattern = spec.pattern;
        if (Array.isArray(spec.examples))
            field.examples = spec.examples;
        if (Array.isArray(spec.enum)) {
            field.type = "enum";
            field.enumValues = spec.enum.map(String);
        }
        if (t === "array") {
            const items = spec.items;
            if (items) {
                const itype = items.type || "object";
                field.itemsType = itype;
                field.itemsSchema = items;
            }
        }
        out.push(field);
    }
    return out;
}
function typeLabel(field) {
    if (field.type === "enum")
        return "enum";
    if (field.type === "array") {
        return field.itemsType === "object" ? "list[object]" : `list[${field.itemsType ?? "string"}]`;
    }
    return field.type;
}
function constraintHints(field) {
    const hints = [];
    if (field.minimum !== undefined && field.maximum !== undefined) {
        hints.push(`${field.minimum.toLocaleString()} ≤ n ≤ ${field.maximum.toLocaleString()}`);
    }
    else if (field.minimum !== undefined) {
        hints.push(`n ≥ ${field.minimum.toLocaleString()}`);
    }
    else if (field.maximum !== undefined) {
        hints.push(`n ≤ ${field.maximum.toLocaleString()}`);
    }
    if (field.exclusiveMinimum !== undefined) {
        hints.push(`n > ${field.exclusiveMinimum.toLocaleString()}`);
    }
    if (field.exclusiveMaximum !== undefined) {
        hints.push(`n < ${field.exclusiveMaximum.toLocaleString()}`);
    }
    if (field.minLength !== undefined && field.maxLength !== undefined) {
        hints.push(`${field.minLength}–${field.maxLength} chars`);
    }
    else if (field.maxLength !== undefined) {
        hints.push(`max ${field.maxLength} chars`);
    }
    else if (field.minLength !== undefined) {
        hints.push(`min ${field.minLength} chars`);
    }
    if (field.pattern) {
        hints.push(`pattern /${field.pattern}/`);
    }
    return hints;
}
function formatDefault(value) {
    if (value === undefined)
        return "";
    if (value === null)
        return "null";
    if (Array.isArray(value))
        return value.length === 0 ? "[]" : JSON.stringify(value);
    if (typeof value === "object")
        return JSON.stringify(value);
    return String(value);
}
export default function PluginConfigPage() {
    const plugins = useQuery({
        queryKey: ["plugins"],
        queryFn: () => api.get("/api/plugin-config/"),
    });
    const [selected, setSelected] = useState(null);
    const active = useMemo(() => {
        if (!plugins.data)
            return null;
        if (selected === null && plugins.data.length > 0)
            return plugins.data[0];
        return plugins.data.find((p) => p.plugin_name === selected) ?? null;
    }, [plugins.data, selected]);
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Plugins" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Configure built-in plugins without editing YAML. Operator-edited values override the agent YAML on overlapping fields. Every save is audited." })] }), _jsxs("div", { className: "flex gap-4", children: [_jsx("div", { className: "panel p-2 w-56 shrink-0", children: (plugins.data ?? []).map((p) => (_jsxs("button", { onClick: () => setSelected(p.plugin_name), className: `block w-full text-left px-2 py-1.5 rounded-md text-sm ${active?.plugin_name === p.plugin_name
                                ? "bg-spark-border text-spark-text"
                                : "text-spark-muted hover:bg-spark-border/50"}`, children: [_jsx("div", { className: "font-mono", children: p.plugin_name }), _jsx("div", { className: "text-xs", children: p.version })] }, p.plugin_name))) }), _jsx("div", { className: "flex-1 min-w-0", children: active && (
                        // ``key`` forces a fresh component instance per plugin so
                        // the inner ``useState(info.config)`` re-initializes from
                        // the new plugin's config. Without this, switching plugins
                        // in the sidebar leaves ``draft`` holding the previous
                        // plugin's fields — Save then sends the wrong shape and
                        // the backend 422s with ``extra_forbidden`` on every field.
                        _jsx(PluginEditor, { info: active }, active.plugin_name)) })] })] }));
}
function PluginEditor({ info }) {
    const client = useQueryClient();
    const fields = useMemo(() => specsFromSchema(info.schema), [info.schema]);
    const [draft, setDraft] = useState(info.config);
    const [reason, setReason] = useState("");
    const [error, setError] = useState(null);
    const save = useMutation({
        mutationFn: () => {
            // Strip ``null`` / ``undefined`` keys before sending. Pydantic
            // refuses ``null`` for non-optional fields; omitting the key lets
            // the schema's default kick in instead. This protects against
            // stray nulls from any field renderer (cleared inputs, NaN
            // parses) and is a no-op on healthy drafts.
            const sanitized = Object.fromEntries(Object.entries(draft).filter(([, v]) => v !== null && v !== undefined));
            return api.put(`/api/plugin-config/${encodeURIComponent(info.plugin_name)}`, {
                config: sanitized,
                reason,
            });
        },
        onSuccess: () => {
            client.invalidateQueries({ queryKey: ["plugins"] });
            setError(null);
            setReason("");
            toast.success(`${info.plugin_name} saved`);
        },
        onError: (e) => {
            const err = e;
            // Surface the field path + reason from FastAPI's 422 detail block
            // so operators see "max_results: input should be a valid integer"
            // instead of an opaque "422 Unprocessable Entity".
            let msg = err.message;
            const detail = err.detail;
            const errs = detail?.errors
                ?? detail?.detail;
            if (Array.isArray(errs) && errs.length > 0) {
                msg = errs
                    .map((e) => `${(e.loc ?? []).slice(1).join(".") || "config"}: ${e.msg}`)
                    .join("; ");
            }
            setError(msg);
            toast.error(`Save failed: ${msg}`);
        },
    });
    const reset = useMutation({
        mutationFn: () => api.post(`/api/plugin-config/${encodeURIComponent(info.plugin_name)}/reset`),
        onSuccess: () => {
            client.invalidateQueries({ queryKey: ["plugins"] });
            toast.success(`${info.plugin_name} reset to defaults`);
        },
    });
    async function handleReset() {
        const ok = await confirmDialog({
            title: `Reset ${info.plugin_name} to defaults?`,
            description: "This wipes your operator overrides and falls back to whatever the agent YAML specifies. The change is audited.",
            tone: "danger",
            confirmLabel: "Reset to defaults",
        });
        if (ok)
            reset.mutate();
    }
    const updateField = (name, value) => setDraft((d) => ({ ...d, [name]: value }));
    const dirty = JSON.stringify(draft) !== JSON.stringify(info.config);
    return (_jsxs("div", { className: "panel p-4 space-y-5", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("h3", { className: "font-bold text-lg font-mono", children: info.plugin_name }), _jsxs("span", { className: "chip text-xs", children: ["v", info.version] }), info.fresh && (_jsx("span", { className: "chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30", children: "operator-edited" }))] }), _jsx("p", { className: "text-sm text-spark-muted mt-1", children: info.description })] }), _jsxs("div", { className: "space-y-4", children: [fields.map((f) => (_jsx(FieldRenderer, { pluginName: info.plugin_name, field: f, value: draft[f.name], onChange: (v) => updateField(f.name, v) }, f.name))), fields.length === 0 && (_jsx("p", { className: "text-sm text-spark-muted", children: "This plugin has no operator-configurable fields." }))] }), _jsxs("div", { className: "pt-3 border-t border-spark-border space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Reason (audited)" }), _jsx("input", { className: "input w-full", value: reason, onChange: (e) => setReason(e.target.value), placeholder: "why are you changing this?" }), _jsx("span", { className: "text-xs text-spark-muted mt-1 block", children: "Recorded in the audit log alongside the diff. Keep it short and specific \u2014 future you will thank present you." })] }), error && _jsx("div", { className: "text-spark-danger text-sm", children: error }), _jsxs("div", { className: "flex justify-between items-center", children: [_jsx("div", { className: "text-xs text-spark-muted", children: dirty ? "Unsaved changes" : "In sync with stored config" }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { className: "btn btn-danger", onClick: handleReset, children: "Reset to defaults" }), _jsx("button", { className: "btn btn-primary", onClick: () => save.mutate(), disabled: !dirty || save.isPending, children: save.isPending ? "Saving…" : "Save" })] })] })] })] }));
}
function FieldHeader({ field, help, }) {
    const hints = constraintHints(field);
    return (_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: "font-mono text-sm", children: field.name }), _jsx("span", { className: "chip text-[10px] uppercase tracking-wide", children: typeLabel(field) }), field.required && (_jsx("span", { className: "chip text-[10px] bg-spark-danger/15 text-spark-danger border border-spark-danger/30 uppercase tracking-wide", children: "required" })), hints.length > 0 && (_jsx("span", { className: "text-[11px] text-spark-muted font-mono", children: hints.join(" · ") }))] }), (field.description || help?.hint) && (_jsxs("div", { className: "flex items-start gap-1.5 text-xs text-spark-muted mt-1", children: [_jsx(Info, { className: "w-3 h-3 mt-0.5 shrink-0" }), _jsxs("span", { children: [field.description, field.description && help?.hint ? " " : "", help?.hint] })] })), help?.docsHref && (_jsxs("a", { href: help.docsHref, target: "_blank", rel: "noopener noreferrer", className: "text-xs text-spark-accent inline-flex items-center gap-1 mt-1", children: ["Reference ", _jsx(ExternalLink, { className: "w-3 h-3" })] }))] }));
}
function DefaultHint({ field }) {
    if (field.default === undefined)
        return null;
    return (_jsxs("div", { className: "text-[11px] text-spark-muted mt-1 font-mono", children: ["default: ", formatDefault(field.default)] }));
}
function FieldRenderer({ pluginName, field, value, onChange, }) {
    const help = FIELD_HELP[`${pluginName}.${field.name}`];
    if (field.type === "boolean") {
        return (_jsxs("div", { className: "space-y-1", children: [_jsx(FieldHeader, { field: field, help: help }), _jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer mt-1", children: [_jsx("input", { type: "checkbox", checked: !!value, onChange: (e) => onChange(e.target.checked) }), _jsx("span", { children: value ? "enabled" : "disabled" })] }), _jsx(DefaultHint, { field: field })] }));
    }
    if (field.type === "enum") {
        return (_jsxs("div", { className: "space-y-1", children: [_jsx(FieldHeader, { field: field, help: help }), _jsx("select", { className: "input w-full mt-1", value: String(value ?? ""), onChange: (e) => onChange(e.target.value), children: field.enumValues?.map((v) => (_jsx("option", { value: v, children: v }, v))) }), _jsx(DefaultHint, { field: field })] }));
    }
    if (field.type === "integer" || field.type === "number") {
        return (_jsxs("div", { className: "space-y-1", children: [_jsx(FieldHeader, { field: field, help: help }), _jsx("input", { className: "input w-full mt-1", type: "number", value: String(value ?? ""), min: field.minimum, max: field.maximum, onChange: (e) => {
                        const raw = e.target.value;
                        // An empty input must NOT serialize as JSON ``null`` —
                        // most plugin-config number fields are non-optional and
                        // Pydantic refuses ``null`` for them. Fall back to the
                        // schema default when known, else preserve the prior
                        // value rather than poisoning the draft. The user can
                        // always overwrite with a fresh number.
                        if (raw === "") {
                            if (field.default !== undefined && field.default !== null) {
                                onChange(field.default);
                            }
                            return;
                        }
                        const parsed = field.type === "integer" ? parseInt(raw, 10) : parseFloat(raw);
                        // NaN guard — Number.parse* yields NaN for "1e" mid-typing.
                        if (Number.isNaN(parsed))
                            return;
                        onChange(parsed);
                    }, placeholder: field.default !== undefined ? String(field.default) : undefined }), _jsx(DefaultHint, { field: field })] }));
    }
    if (field.type === "array") {
        // list[object] → JSON editor with schema hint. list[string|int] → lines.
        if (field.itemsType === "object") {
            return _jsx(ComplexArrayEditor, { field: field, help: help, value: value, onChange: onChange });
        }
        const asText = Array.isArray(value)
            ? value.map((v) => String(v)).join("\n")
            : "";
        return (_jsxs("div", { className: "space-y-1", children: [_jsx(FieldHeader, { field: field, help: help }), _jsx("textarea", { className: "input w-full h-20 font-mono text-xs mt-1", value: asText, placeholder: help?.example ?? "one value per line", onChange: (e) => {
                        const items = e.target.value
                            .split("\n")
                            .map((s) => s.trim())
                            .filter(Boolean)
                            .map((s) => {
                            if (field.itemsType === "integer" || field.itemsType === "number") {
                                const n = Number(s);
                                // Drop unparseable rows rather than emitting NaN —
                                // NaN serializes to JSON null and Pydantic 422s.
                                return Number.isNaN(n) ? undefined : n;
                            }
                            return s;
                        })
                            .filter((v) => v !== undefined);
                        onChange(items);
                    } }), _jsx("div", { className: "text-[11px] text-spark-muted", children: "One per line. Empty lines are stripped." }), _jsx(DefaultHint, { field: field })] }));
    }
    // fallback: string / object
    const isSecretRef = field.name.endsWith("_secret");
    const stringValue = String(value ?? "");
    // ``*_secret`` fields hold the *name* of a vault entry, not the
    // credential itself. If the value doesn't match the slug pattern
    // (letters, digits, ``.``, ``_``, ``-``) and is suspiciously long,
    // it's almost certainly a pasted credential — a real footgun that
    // both poisons the agent (lookup misses) and persists cleartext to
    // disk. Surface a loud inline warning + a deep-link to /secrets.
    const looksLikeCredential = isSecretRef &&
        stringValue.length >= 24 &&
        !/^[a-zA-Z0-9._-]+$/.test(stringValue);
    return (_jsxs("div", { className: "space-y-1", children: [_jsx(FieldHeader, { field: field, help: help }), _jsx("input", { className: "input w-full mt-1", value: stringValue, type: isSecretRef ? "text" : "text", autoComplete: isSecretRef ? "off" : undefined, spellCheck: isSecretRef ? false : undefined, placeholder: field.examples && field.examples.length > 0
                    ? String(field.examples[0])
                    : help?.example, onChange: (e) => onChange(e.target.value) }), isSecretRef && (_jsxs("div", { className: "text-[11px] text-spark-muted", children: ["Holds the ", _jsx("em", { children: "name" }), " of a vault entry, not the credential itself. Manage entries in", " ", _jsx("a", { className: "text-spark-link hover:underline", href: "/secrets", children: "Secure \u2192 Secrets" }), "."] })), looksLikeCredential && (_jsxs("div", { className: "text-[11px] text-spark-danger border border-spark-danger/40 rounded p-2 mt-1", children: ["\u26A0 This value looks like a credential, not a name. Pasting a raw API key here persists it cleartext on disk. Add it to the vault under a name (e.g. ", _jsxs("code", { children: [field.name.replace("_secret", ""), "_key"] }), ") at", " ", _jsx("a", { className: "text-spark-link hover:underline", href: "/secrets", children: "Secure \u2192 Secrets" }), " ", "and put the ", _jsx("em", { children: "name" }), " here instead."] })), _jsx(DefaultHint, { field: field })] }));
}
function ComplexArrayEditor({ field, help, value, onChange, }) {
    const initial = useMemo(() => (Array.isArray(value) ? JSON.stringify(value, null, 2) : "[]"), [value]);
    const [text, setText] = useState(initial);
    const [jsonError, setJsonError] = useState(null);
    function commit(raw) {
        setText(raw);
        if (raw.trim() === "") {
            onChange([]);
            setJsonError(null);
            return;
        }
        try {
            const parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) {
                setJsonError("Expected a JSON array");
                return;
            }
            setJsonError(null);
            onChange(parsed);
        }
        catch (err) {
            setJsonError(err.message);
        }
    }
    const itemFields = (field.itemsSchema?.properties ?? {});
    const itemRequired = new Set(field.itemsSchema?.required ?? []);
    return (_jsxs("div", { className: "space-y-2", children: [_jsx(FieldHeader, { field: field, help: help }), Object.keys(itemFields).length > 0 && (_jsxs("details", { className: "border border-spark-border rounded bg-spark-bg/40", children: [_jsxs("summary", { className: "px-3 py-2 text-xs cursor-pointer text-spark-muted", children: ["Item schema (", Object.keys(itemFields).length, " field", Object.keys(itemFields).length === 1 ? "" : "s", ")"] }), _jsx("div", { className: "px-3 py-2 border-t border-spark-border text-xs font-mono space-y-1", children: Object.entries(itemFields).map(([k, v]) => {
                            const t = v.type || (Array.isArray(v.enum) ? "enum" : "string");
                            const desc = v.description || "";
                            return (_jsxs("div", { className: "flex gap-2 items-baseline flex-wrap", children: [_jsx("span", { className: "text-spark-text", children: k }), _jsxs("span", { className: "text-spark-muted", children: [": ", t] }), itemRequired.has(k) && (_jsx("span", { className: "text-spark-danger", children: "required" })), v.default !== undefined && (_jsxs("span", { className: "text-spark-muted", children: ["default ", JSON.stringify(v.default)] })), desc && (_jsxs("span", { className: "text-spark-muted font-sans", children: ["\u2014 ", desc] }))] }, k));
                        }) })] })), _jsx("textarea", { className: "input w-full h-56 font-mono text-xs", value: text, placeholder: help?.example ?? "[]", onChange: (e) => commit(e.target.value), spellCheck: false }), jsonError ? (_jsxs("div", { className: "text-xs text-spark-danger", children: ["JSON error: ", jsonError] })) : (_jsx("div", { className: "text-[11px] text-spark-muted", children: "Edit as JSON. Must be a JSON array. Invalid JSON is not saved." })), help?.example && (_jsxs("details", { className: "border border-spark-border rounded bg-spark-bg/40", children: [_jsx("summary", { className: "px-3 py-2 text-xs cursor-pointer text-spark-muted", children: "Example" }), _jsx("pre", { className: "px-3 py-2 border-t border-spark-border text-xs font-mono overflow-auto", children: help.example }), _jsx("div", { className: "px-3 pb-2", children: _jsx("button", { type: "button", className: "btn text-xs", onClick: () => commit(help.example), children: "Paste example" }) })] })), _jsx(DefaultHint, { field: field })] }));
}
