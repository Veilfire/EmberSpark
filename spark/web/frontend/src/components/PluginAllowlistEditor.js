import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, RotateCcw, Save, ShieldAlert, Wifi, X, } from "lucide-react";
import { Modal } from "./Modal";
import { FailureInspector, } from "./FailureInspector";
import { useSuggestedPrefill } from "../lib/prefill";
// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------
const RISK_CHIP = {
    safe: "chip-good",
    elevated: "chip-warn",
    danger: "chip-danger",
};
const RISK_LABEL = {
    safe: "safe",
    elevated: "elevated",
    danger: "danger",
};
export function PluginAllowlistEditor(props) {
    const { pluginName, config, onSave, discover, toggles = [], connectionPanel, } = props;
    const [draft, setDraft] = useState(() => ({ ...config }));
    const [reason, setReason] = useState("");
    const [envelope, setEnvelope] = useState(null);
    const [discovering, setDiscovering] = useState(false);
    const [confirmFor, setConfirmFor] = useState(null);
    const [saving, setSaving] = useState(false);
    const flashedRef = useRef({});
    // Prefill handler — `plugin_allowlist_grant` shape from the
    // Failure Inspector deep-link. Only acts when `plugin` matches.
    const [prefill, discardPrefill] = useSuggestedPrefill("plugin_allowlist_grant");
    const prefillMatchesUs = prefill && prefill.plugin === pluginName;
    useEffect(() => {
        if (!prefillMatchesUs || !prefill)
            return;
        if (prefill.toggle) {
            // Flip the matching boolean on.
            setDraft((d) => ({ ...d, [prefill.toggle]: false }));
            flashedRef.current[`toggle:${prefill.toggle}`] = true;
            return;
        }
        if (prefill.add_item && prefill.field) {
            const field = prefill.field;
            setDraft((d) => {
                const cur = new Set(Array.isArray(d[field]) ? d[field] : []);
                cur.add(prefill.add_item);
                return { ...d, [field]: Array.from(cur) };
            });
            flashedRef.current[`item:${prefill.field}:${prefill.add_item}`] = true;
        }
    }, [prefillMatchesUs, prefill]);
    // Auto-discover on mount when we have anything to introspect.
    useEffect(() => {
        runDiscover().catch(() => undefined);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    async function runDiscover() {
        setDiscovering(true);
        try {
            const r = await discover();
            setEnvelope(r);
        }
        finally {
            setDiscovering(false);
        }
    }
    const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(config), [draft, config]);
    async function handleSave() {
        setSaving(true);
        try {
            await onSave(draft, reason);
            setReason("");
            discardPrefill();
            runDiscover().catch(() => undefined);
        }
        finally {
            setSaving(false);
        }
    }
    function toggleItem(field, item) {
        const cur = new Set(Array.isArray(draft[field]) ? draft[field] : []);
        if (cur.has(item.id)) {
            cur.delete(item.id);
            setDraft((d) => ({ ...d, [field]: Array.from(cur) }));
            return;
        }
        if (item.risk === "danger") {
            setConfirmFor({ field, item });
            return;
        }
        cur.add(item.id);
        setDraft((d) => ({ ...d, [field]: Array.from(cur) }));
    }
    function toggleBool(field) {
        setDraft((d) => ({ ...d, [field]: !d[field] }));
    }
    const sparkErr = envelope && !envelope.ok && envelope.error_code
        ? {
            code: envelope.error_code,
            message: envelope.error || "Discovery failed",
            detail: envelope.error_detail ?? {},
            remediation: null,
            tuning: null,
        }
        : null;
    return (_jsxs("div", { className: "panel p-4 space-y-5", children: [connectionPanel && _jsx("section", { children: connectionPanel }), _jsxs("section", { className: "flex items-center gap-3 flex-wrap", children: [_jsxs("button", { className: "btn btn-ghost text-xs", disabled: discovering, onClick: () => runDiscover(), children: [discovering ? (_jsx(Loader2, { size: 12, className: "animate-spin mr-1.5 inline" })) : (_jsx(RefreshCw, { size: 12, className: "mr-1.5 inline" })), envelope ? "Re-discover" : "Test connection & discover"] }), envelope?.ok && envelope.badges && (_jsxs("div", { className: "flex items-center gap-2 text-xs text-spark-good flex-wrap", children: [_jsx(CheckCircle2, { size: 14 }), envelope.badges.map((b, i) => (_jsxs("span", { children: [b.label, ": ", _jsx("code", { children: b.value })] }, i)))] })), sparkErr && (_jsx("div", { className: "w-full", children: _jsx(FailureInspector, { error: sparkErr, variant: "compact" }) }))] }), prefillMatchesUs && prefill && (_jsxs("div", { className: "panel p-3 border-amber-400/60 bg-amber-400/5 flex items-start gap-3", children: [_jsx(AlertTriangle, { size: 16, className: "text-amber-400 shrink-0 mt-0.5" }), _jsxs("div", { className: "flex-1 text-sm", children: [_jsx("strong", { children: "Suggested by failure inspector." }), " ", prefill.toggle ? (_jsxs(_Fragment, { children: [_jsx("code", { children: prefill.toggle }), " staged to flip. Review and Save."] })) : prefill.add_item && prefill.field ? (_jsxs(_Fragment, { children: [_jsx("code", { children: prefill.add_item }), " staged for", " ", _jsx("code", { children: prefill.field }), ". Review the highlighted checkbox and Save."] })) : null] }), _jsx("button", { className: "btn btn-ghost text-xs", onClick: () => {
                            discardPrefill();
                            setDraft({ ...config });
                        }, children: "Discard" })] })), toggles.length > 0 && (_jsx("section", { className: "space-y-2", children: toggles.map((t) => {
                    const flashed = flashedRef.current[`toggle:${t.field}`];
                    const checked = Boolean(draft[t.field]);
                    return (_jsxs("label", { className: `flex items-start gap-3 p-2 rounded-md ${flashed ? "ring-2 ring-amber-400/70" : ""}`, children: [_jsx("input", { type: "checkbox", checked: checked, onChange: () => toggleBool(t.field), className: "mt-1" }), _jsxs("span", { className: "text-sm", children: [_jsx("strong", { children: t.label }), " ", checked ? (_jsx("span", { className: "chip chip-good text-[10px] ml-1", children: t.on_label ?? "on" })) : (_jsx("span", { className: "chip chip-warn text-[10px] ml-1", children: t.off_label ?? "off" })), t.description && (_jsx("span", { className: "block text-xs text-spark-muted mt-0.5", children: t.description }))] })] }, t.field));
                }) })), envelope?.ok &&
                envelope.sections.map((sec) => (_jsxs("section", { children: [_jsxs("div", { className: "label mb-1", children: [sec.title, " ", _jsxs("span", { className: "text-spark-muted text-[11px] normal-case font-normal", children: ["(", (Array.isArray(draft[sec.field])
                                            ? draft[sec.field]
                                            : []).length, "/", sec.items.length, " selected)"] })] }), sec.description && (_jsx("p", { className: "text-xs text-spark-muted mb-2", children: sec.description })), _jsx("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-1.5", children: sec.items.map((item) => {
                                const checked = Array.isArray(draft[sec.field])
                                    ? draft[sec.field].includes(item.id)
                                    : false;
                                const flashKey = `item:${sec.field}:${item.id}`;
                                const flashed = flashedRef.current[flashKey];
                                return (_jsxs("label", { className: `flex items-center gap-2 px-2 py-1.5 border rounded-md text-sm cursor-pointer hover:border-spark-accent/40 transition-colors ${checked
                                        ? "border-spark-border bg-spark-bg/30"
                                        : "border-spark-border"} ${flashed ? "ring-2 ring-amber-400/70" : ""}`, title: item.hint ?? undefined, children: [_jsx("input", { type: "checkbox", checked: checked, onChange: () => toggleItem(sec.field, item) }), _jsxs("span", { className: "flex-1 min-w-0 truncate", children: [_jsx("span", { className: "text-sm", children: item.label }), item.hint && (_jsx("span", { className: "block text-[10px] text-spark-muted truncate", children: item.hint }))] }), item.risk !== "safe" && (_jsx("span", { className: `chip ${RISK_CHIP[item.risk]} text-[9px]`, children: RISK_LABEL[item.risk] }))] }, item.id));
                            }) })] }, sec.field))), _jsxs("div", { className: "pt-3 border-t border-spark-border space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Reason (audited)" }), _jsx("input", { className: "input w-full", placeholder: "why are you changing this?", value: reason, onChange: (e) => setReason(e.target.value) })] }), _jsxs("div", { className: "flex items-center justify-between", children: [_jsx("div", { className: "text-xs text-spark-muted", children: dirty ? "Unsaved changes" : "In sync with stored config" }), _jsxs("div", { className: "flex gap-2", children: [_jsxs("button", { className: "btn", disabled: !dirty, onClick: () => {
                                            setDraft({ ...config });
                                            setReason("");
                                            discardPrefill();
                                        }, children: [_jsx(RotateCcw, { size: 13, className: "mr-1.5 inline" }), "Discard"] }), _jsxs("button", { className: "btn btn-primary", disabled: !dirty || saving, onClick: handleSave, children: [_jsx(Save, { size: 13, className: "mr-1.5 inline" }), saving ? "Saving…" : "Save"] })] })] })] }), confirmFor && (_jsx(Modal, { open: true, onClose: () => setConfirmFor(null), children: _jsx(DangerConfirm, { target: confirmFor, onCancel: () => setConfirmFor(null), onConfirm: () => {
                        const { field, item } = confirmFor;
                        setDraft((d) => {
                            const cur = new Set(Array.isArray(d[field]) ? d[field] : []);
                            cur.add(item.id);
                            return { ...d, [field]: Array.from(cur) };
                        });
                        setConfirmFor(null);
                    } }) }))] }));
}
function DangerConfirm({ target, onCancel, onConfirm, }) {
    const [typed, setTyped] = useState("");
    const matches = typed === target.item.id || typed === target.item.label;
    return (_jsxs("div", { className: "panel p-5 max-w-md", children: [_jsxs("div", { className: "flex items-start gap-3", children: [_jsx(ShieldAlert, { size: 20, className: "text-spark-danger shrink-0 mt-0.5" }), _jsxs("div", { className: "flex-1", children: [_jsxs("h4", { className: "font-bold", children: ["Allow `", target.item.label, "`?"] }), _jsx("p", { className: "text-sm text-spark-muted mt-1", children: "This is a high-risk item. Allowing it lets the agent interact with it through the plugin." }), _jsxs("p", { className: "text-xs text-spark-muted mt-3", children: ["Type ", _jsx("code", { className: "font-mono", children: target.item.id }), " or", " ", _jsx("code", { className: "font-mono", children: target.item.label }), " to confirm:"] }), _jsx("input", { className: "input w-full mt-2 font-mono text-sm", autoFocus: true, value: typed, onChange: (e) => setTyped(e.target.value), onKeyDown: (e) => {
                                    if (e.key === "Enter" && matches)
                                        onConfirm();
                                } })] })] }), _jsxs("div", { className: "flex justify-end gap-2 mt-4", children: [_jsx("button", { className: "btn", onClick: onCancel, children: "Cancel" }), _jsx("button", { className: "btn btn-danger", disabled: !matches, onClick: onConfirm, children: "Allow" })] })] }));
}
// Suppress unused-import warning for X (chips render their own remove
// buttons in per-plugin wrappers, not in the shared component).
void X;
void Wifi;
