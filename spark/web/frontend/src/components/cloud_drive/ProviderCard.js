import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { AlertTriangle, Check, CheckCircle2, ChevronDown, ChevronRight, Cloud, Copy, Loader2, Mail, Plug, Plus, Trash2, X, XCircle, } from "lucide-react";
import { toast } from "sonner";
import { PROVIDER_REGISTRY, } from "./ProviderTypeRegistry";
export function ProviderCard({ config, health, flashed, flashedField, onChange, onRemove, onTest, testing, }) {
    const spec = PROVIDER_REGISTRY[config.auth.kind];
    const [open, setOpen] = useState(false);
    function setEnabled(v) {
        onChange({ ...config, enabled: v });
    }
    function setAuthField(key, value) {
        onChange({ ...config, auth: { ...config.auth, [key]: value } });
    }
    function setAllowedPaths(paths) {
        onChange({ ...config, allowed_paths: paths });
    }
    function setAutoShare(next) {
        onChange({ ...config, auto_share: next });
    }
    const ringClass = flashed ? "ring-2 ring-amber-400/70" : "";
    const healthBadge = healthChip(health);
    return (_jsxs("div", { className: `border rounded-md transition-colors ${config.enabled
            ? "border-spark-accent/40"
            : "border-spark-border"} ${ringClass}`, children: [_jsxs("div", { className: "flex items-center gap-3 px-3 py-2.5", children: [_jsx("input", { type: "checkbox", checked: config.enabled, onChange: (e) => setEnabled(e.target.checked), "aria-label": `Enable ${config.name}` }), _jsx("button", { type: "button", className: "text-spark-muted hover:text-spark-text shrink-0", onClick: () => setOpen((v) => !v), "aria-label": open ? "Collapse" : "Expand", children: open ? _jsx(ChevronDown, { size: 16 }) : _jsx(ChevronRight, { size: 16 }) }), _jsx(Cloud, { size: 16, className: "text-spark-muted shrink-0" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "font-mono text-sm truncate", children: config.name }), _jsxs("div", { className: "text-[11px] text-spark-muted", children: [spec.label, " \u00B7 ", spec.blurb] })] }), healthBadge, _jsx("button", { type: "button", className: "text-spark-muted hover:text-spark-danger", onClick: onRemove, "aria-label": `Remove ${config.name}`, title: "Remove provider", children: _jsx(Trash2, { size: 14 }) })] }), open && (_jsxs("div", { className: "border-t border-spark-border bg-spark-bg/20 px-3 py-3 space-y-4", children: [_jsx(SetupSteps, { kind: config.auth.kind }), _jsx(FieldGroup, { title: "Auth", icon: _jsx(Plug, { size: 12 }), children: spec.fields.map((field) => (_jsx(AuthFieldInput, { field: field, value: config.auth[field.key], onChange: (v) => setAuthField(field.key, v) }, field.key))) }), _jsx(FieldGroup, { title: "Allowed paths", icon: _jsx(CheckCircle2, { size: 12 }), description: "Root paths the agent may touch on this provider. Empty refuses all.", flashed: flashedField === "allowed_paths", children: _jsx(PathListInput, { paths: config.allowed_paths, onChange: setAllowedPaths, providerName: config.name }) }), _jsx(FieldGroup, { title: "Auto-share", icon: _jsx(Mail, { size: 12 }), description: spec.autoShareImplemented
                            ? "On every successful `put`, automatically grant access to these recipients."
                            : `Auto-share isn't wired for ${spec.label} yet — config is preserved for when it lands in v2.`, children: _jsx(AutoShareInput, { spec: config.auto_share, onChange: setAutoShare, warning: !spec.autoShareImplemented }) }), _jsxs("div", { className: "flex items-center justify-between pt-1 border-t border-spark-border", children: [_jsx("div", { className: "text-[11px] text-spark-muted", children: health?.error && (_jsx("span", { className: "text-spark-danger", children: health.error })) }), _jsxs("button", { type: "button", className: "btn btn-ghost text-xs", disabled: testing, onClick: onTest, children: [testing ? (_jsx(Loader2, { size: 12, className: "animate-spin mr-1.5 inline" })) : (_jsx(Plug, { size: 12, className: "mr-1.5 inline" })), "Test connection"] })] })] }))] }));
}
// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
function healthChip(health) {
    if (!health)
        return null;
    if (health.ok) {
        return (_jsxs("span", { className: "chip chip-good text-[10px] flex items-center gap-1", title: health.total_bytes
                ? `${formatBytes(health.free_bytes ?? 0)} free of ${formatBytes(health.total_bytes)}`
                : "Connected", children: [_jsx(CheckCircle2, { size: 10 }), "connected"] }));
    }
    return (_jsxs("span", { className: "chip chip-warn text-[10px] flex items-center gap-1", title: health.error ?? "Failed", children: [_jsx(XCircle, { size: 10 }), "error"] }));
}
function FieldGroup({ title, icon, description, children, flashed, }) {
    return (_jsxs("div", { className: `space-y-2 ${flashed ? "ring-2 ring-amber-400/70 rounded p-2 -m-2" : ""}`, children: [_jsxs("div", { className: "flex items-center gap-1.5 text-xs uppercase tracking-wide text-spark-muted", children: [icon, _jsx("span", { children: title })] }), description && (_jsx("p", { className: "text-[11px] text-spark-muted", children: description })), _jsx("div", { className: "space-y-2", children: children })] }));
}
function AuthFieldInput({ field, value, onChange, }) {
    const str = String(value ?? "");
    // Heuristic: warn if a *_secret field has a value that doesn't look
    // like a vault name (long + non-slug chars).
    const looksLikeCredential = field.type === "secret" &&
        str.length >= 24 &&
        !/^[a-zA-Z0-9._-]+$/.test(str);
    return (_jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs font-mono", children: field.label }), field.type === "enum" ? (_jsx("select", { className: "input w-full mt-1 text-sm", value: str, onChange: (e) => onChange(e.target.value), children: field.options?.map((o) => (_jsx("option", { value: o.value, children: o.label }, o.value))) })) : field.type === "info" ? (_jsx("p", { className: "text-xs text-spark-muted", children: field.hint })) : (_jsx("input", { className: "input w-full mt-1 font-mono text-sm", value: str, placeholder: field.placeholder, autoComplete: field.type === "secret" ? "off" : undefined, spellCheck: field.type === "secret" ? false : undefined, onChange: (e) => onChange(e.target.value) })), field.hint && field.type !== "info" && (_jsx("span", { className: "text-[10px] text-spark-muted block mt-0.5", children: field.hint })), looksLikeCredential && (_jsxs("div", { className: "text-[11px] text-spark-danger border border-spark-danger/40 rounded p-2 mt-1", children: ["\u26A0 This looks like a credential, not a vault name. Add it to the vault under a name (e.g. via", " ", _jsx("a", { className: "text-spark-link hover:underline", href: "/secrets", children: "Secure \u2192 Secrets" }), ") and put the ", _jsx("em", { children: "name" }), " here instead."] }))] }));
}
function PathListInput({ paths, onChange, providerName, }) {
    const [draft, setDraft] = useState("");
    function add() {
        const clean = draft.trim().replace(/^\/+|\/+$/g, "");
        if (!clean)
            return;
        if (paths.includes(clean)) {
            setDraft("");
            return;
        }
        onChange([...paths, clean]);
        setDraft("");
    }
    return (_jsxs("div", { className: "space-y-1.5", children: [paths.length === 0 && (_jsx("div", { className: "text-xs text-spark-danger border border-spark-danger/30 rounded p-2 bg-spark-danger/5", children: "No paths allowed \u2014 the agent will be refused on every action. Add at least one root." })), paths.map((p, i) => (_jsxs("div", { className: "flex items-center gap-2 border border-spark-border rounded px-2 py-1 bg-spark-bg/40", children: [_jsxs("code", { className: "text-xs flex-1 font-mono", children: [providerName, ":", p] }), _jsx("button", { type: "button", className: "text-spark-muted hover:text-spark-danger", onClick: () => onChange(paths.filter((_, j) => j !== i)), "aria-label": "Remove path", children: _jsx(X, { size: 12 }) })] }, i))), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("input", { className: "input flex-1 font-mono text-sm", placeholder: "Spark-agent  (path under the remote root)", value: draft, onChange: (e) => setDraft(e.target.value), onKeyDown: (e) => {
                            if (e.key === "Enter") {
                                e.preventDefault();
                                add();
                            }
                        } }), _jsxs("button", { type: "button", className: "btn text-xs", onClick: add, disabled: !draft.trim(), children: [_jsx(Plus, { size: 12, className: "mr-1 inline" }), "Add"] })] })] }));
}
function AutoShareInput({ spec, onChange, warning, }) {
    const [draft, setDraft] = useState("");
    function addRecipient() {
        const clean = draft.trim().toLowerCase();
        if (!clean)
            return;
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(clean)) {
            toast.error("Looks like an invalid email address");
            return;
        }
        if (spec.recipients.includes(clean)) {
            setDraft("");
            return;
        }
        onChange({ ...spec, recipients: [...spec.recipients, clean] });
        setDraft("");
    }
    return (_jsxs("div", { className: "space-y-2", children: [_jsxs("label", { className: "flex items-center gap-2", children: [_jsx("input", { type: "checkbox", checked: spec.enabled, onChange: (e) => onChange({ ...spec, enabled: e.target.checked }) }), _jsxs("span", { className: "text-sm", children: ["Enable auto-share", warning && spec.enabled && (_jsxs("span", { className: "ml-2 chip chip-warn text-[10px] inline-flex items-center gap-1", children: [_jsx(AlertTriangle, { size: 10 }), "v2 only"] }))] })] }), spec.enabled && (_jsxs(_Fragment, { children: [_jsxs("div", { children: [_jsx("span", { className: "text-xs font-mono", children: "Permission" }), _jsxs("select", { className: "input w-full mt-1 text-sm", value: spec.permission, onChange: (e) => onChange({
                                    ...spec,
                                    permission: e.target.value,
                                }), children: [_jsx("option", { value: "reader", children: "Reader (view only)" }), _jsx("option", { value: "writer", children: "Writer (view + edit)" }), _jsx("option", { value: "commenter", children: "Commenter (view + comment)" })] })] }), _jsxs("div", { children: [_jsx("span", { className: "text-xs font-mono", children: "Recipients" }), _jsxs("div", { className: "space-y-1.5 mt-1", children: [spec.recipients.map((r, i) => (_jsxs("div", { className: "flex items-center gap-2 border border-spark-border rounded px-2 py-1 bg-spark-bg/40", children: [_jsx(Mail, { size: 12, className: "text-spark-muted shrink-0" }), _jsx("code", { className: "text-xs flex-1", children: r }), _jsx("button", { type: "button", className: "text-spark-muted hover:text-spark-danger", onClick: () => onChange({
                                                    ...spec,
                                                    recipients: spec.recipients.filter((_, j) => j !== i),
                                                }), children: _jsx(X, { size: 12 }) })] }, i))), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("input", { className: "input flex-1 text-sm", placeholder: "operator@example.com", value: draft, onChange: (e) => setDraft(e.target.value), onKeyDown: (e) => {
                                                    if (e.key === "Enter") {
                                                        e.preventDefault();
                                                        addRecipient();
                                                    }
                                                } }), _jsxs("button", { type: "button", className: "btn text-xs", onClick: addRecipient, disabled: !draft.trim(), children: [_jsx(Plus, { size: 12, className: "mr-1 inline" }), "Add"] })] })] })] })] }))] }));
}
function SetupSteps({ kind }) {
    const spec = PROVIDER_REGISTRY[kind];
    return (_jsxs("details", { className: "border border-spark-border rounded-md bg-spark-bg/30", children: [_jsxs("summary", { className: "px-3 py-2 text-xs cursor-pointer text-spark-muted select-none", children: ["How to pair ", spec.label] }), _jsx("ol", { className: "px-3 py-2 border-t border-spark-border space-y-2", children: spec.setup.map((step, i) => (_jsxs("li", { className: "flex gap-2", children: [_jsx("span", { className: "shrink-0 w-4 h-4 rounded-full bg-spark-border text-[10px] flex items-center justify-center font-bold mt-0.5", children: i + 1 }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "text-xs font-semibold", children: step.title }), step.cmd && _jsx(CodeLine, { cmd: step.cmd }), step.note && (_jsx("div", { className: "text-[11px] text-spark-muted mt-1", children: step.note }))] })] }, i))) })] }));
}
function CodeLine({ cmd }) {
    const [copied, setCopied] = useState(false);
    async function copy() {
        try {
            await navigator.clipboard.writeText(cmd);
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
        }
        catch {
            toast.error("Clipboard unavailable");
        }
    }
    return (_jsxs("div", { className: "flex items-center gap-2 bg-spark-bg/70 border border-spark-border rounded px-2 py-1 font-mono text-xs mt-1", children: [_jsx("span", { className: "text-spark-muted", children: "$" }), _jsx("code", { className: "flex-1 truncate", children: cmd }), _jsx("button", { type: "button", className: "text-spark-muted hover:text-spark-text shrink-0", onClick: copy, "aria-label": "Copy command", children: copied ? (_jsx(Check, { size: 12, className: "text-spark-good" })) : (_jsx(Copy, { size: 12 })) })] }));
}
function formatBytes(n) {
    if (n < 1024)
        return `${n} B`;
    if (n < 1024 * 1024)
        return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024)
        return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (n < 1024 * 1024 * 1024 * 1024)
        return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    return `${(n / (1024 * 1024 * 1024 * 1024)).toFixed(1)} TB`;
}
