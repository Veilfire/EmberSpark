import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Failure Inspector — render a SparkError.to_dict() payload as a
 * "what gate, what element, how to tune, what risk" panel.
 *
 * Two variants:
 *   - "inline" — used beneath a failed turn in Chat or a span in
 *     Replay. Full layout: gate header chip, element table, tuning
 *     option list, risk tooltips.
 *   - "compact" — used in narrow rows (Audit log expand,
 *     NotificationBell drawer). One-row layout with the first
 *     tuning option as a button.
 *
 * The component is data-driven from the backend `tuning` field; the
 * frontend has no per-code switch statements.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, ChevronRight, Info, ShieldAlert, } from "lucide-react";
// ---------------------------------------------------------------------------
// Code → human family + element-table copy.
// ---------------------------------------------------------------------------
const FAMILY_BY_PREFIX = [
    // [code prefixes, family label, family icon-bg color hint]
    [["SPK_E_PLUGIN_NOT_ALLOWED", "SPK_E_PERMISSION_MISSING"], "Permission", "warn"],
    [["SPK_E_BUDGET_"], "Budget", "warn"],
    [["SPK_E_PATH_", "SPK_E_FILE_"], "Filesystem", "info"],
    [["SPK_E_URL_", "SPK_E_METHOD_NOT_ALLOWED", "SPK_E_RESPONSE_TOO_LARGE"], "Network", "info"],
    [["SPK_E_SANDBOX_"], "Sandbox", "warn"],
    [["SPK_E_DATA_CLASS_"], "Data class", "danger"],
    [["SPK_E_FROZEN", "SPK_E_APPROVAL_", "SPK_E_RUN_WINDOW_", "SPK_E_DLQ_"], "Lifecycle", "warn"],
    [["SPK_E_INPUT_SCHEMA_", "SPK_E_OUTPUT_SCHEMA_", "SPK_E_OPERATOR_OVERRIDE_"], "Validation", "info"],
    [["SPK_E_SECRET_"], "Secrets", "warn"],
    [["SPK_E_PLUGIN_RAISED"], "Plugin internal", "info"],
];
function familyForCode(code) {
    for (const [prefixes, label, tone] of FAMILY_BY_PREFIX) {
        if (prefixes.some((p) => code.startsWith(p) || code === p)) {
            return { label, tone };
        }
    }
    return { label: "Failure", tone: "info" };
}
const SEVERITY_CHIP = {
    low: "chip-good",
    medium: "chip-warn",
    high: "chip-danger",
    critical: "chip-danger",
};
const SEVERITY_LABEL = {
    low: "low risk",
    medium: "medium risk",
    high: "high risk",
    critical: "critical",
};
// Detail keys we hide from the operator (internal markers).
const INTERNAL_KEYS = new Set(["_message"]);
// ---------------------------------------------------------------------------
// Inline variant
// ---------------------------------------------------------------------------
function FailureInspectorInline({ error, context }) {
    const family = familyForCode(error.code);
    const tuning = error.tuning ?? [];
    const detailEntries = Object.entries(error.detail ?? {}).filter(([k]) => !INTERNAL_KEYS.has(k));
    return (_jsxs("div", { className: "border border-spark-border rounded-md bg-spark-panel/40 mt-2 overflow-hidden", children: [_jsxs("div", { className: "flex items-center gap-2 px-3 py-2 border-b border-spark-border bg-spark-panel", children: [_jsx(ShieldAlert, { size: 14, className: family.tone === "danger"
                            ? "text-spark-danger"
                            : family.tone === "warn"
                                ? "text-spark-accent"
                                : "text-spark-muted" }), _jsx("span", { className: "text-xs font-semibold uppercase tracking-wide", children: family.label }), _jsx("code", { className: "ml-auto font-mono text-[10px] text-spark-muted", children: error.code })] }), _jsxs("div", { className: "px-3 py-2 space-y-3 text-sm", children: [_jsx("p", { className: "text-spark-text", children: error.message }), (detailEntries.length > 0 || context) && (_jsx(ElementTable, { entries: detailEntries, context: context ?? null })), tuning.length > 0 && (_jsxs("div", { children: [_jsx("div", { className: "label mb-1.5", children: "Tune" }), _jsx("div", { className: "space-y-2", children: tuning.map((opt, i) => (_jsx(TuningOptionCard, { option: opt }, i))) })] }))] })] }));
}
// ---------------------------------------------------------------------------
// Compact variant — one row, first action only
// ---------------------------------------------------------------------------
function FailureInspectorCompact({ error }) {
    const family = familyForCode(error.code);
    const firstAction = (error.tuning ?? []).find((o) => o.deep_link);
    return (_jsxs("div", { className: "flex items-center gap-3 text-xs", children: [_jsx("span", { className: `chip ${family.tone === "danger"
                    ? "chip-danger"
                    : family.tone === "warn"
                        ? "chip-warn"
                        : "chip-info"}`, children: family.label }), _jsx("code", { className: "font-mono text-[10px] text-spark-muted", children: error.code }), _jsx("span", { className: "text-spark-text truncate flex-1", children: error.message }), firstAction && firstAction.deep_link && (_jsxs(Link, { to: firstAction.deep_link, className: "btn btn-ghost text-xs whitespace-nowrap", children: [firstAction.label, " ", _jsx(ChevronRight, { size: 12, className: "ml-1 inline" })] }))] }));
}
// ---------------------------------------------------------------------------
// Element table — what triggered this gate
// ---------------------------------------------------------------------------
function ElementTable({ entries, context, }) {
    const ctxRows = [];
    if (context?.agent_name)
        ctxRows.push(["agent", context.agent_name]);
    if (context?.plugin)
        ctxRows.push(["plugin", context.plugin]);
    if (ctxRows.length === 0 && entries.length === 0)
        return null;
    return (_jsxs("div", { children: [_jsx("div", { className: "label mb-1", children: "Element" }), _jsx("table", { className: "text-xs w-full", children: _jsxs("tbody", { className: "divide-y divide-spark-border/50", children: [ctxRows.map(([k, v]) => (_jsxs("tr", { children: [_jsx("td", { className: "py-1 pr-3 font-mono text-spark-muted w-32 align-top", children: k }), _jsx("td", { className: "py-1 break-all", children: v })] }, k))), entries.map(([k, v]) => (_jsxs("tr", { children: [_jsx("td", { className: "py-1 pr-3 font-mono text-spark-muted w-32 align-top", children: k }), _jsx("td", { className: "py-1 break-all", children: Array.isArray(v) ? (v.length === 0 ? (_jsx("span", { className: "text-spark-muted", children: "(empty)" })) : (v.map((x) => String(x)).join(", "))) : v === null || v === undefined ? (_jsx("span", { className: "text-spark-muted", children: "\u2014" })) : typeof v === "object" ? (_jsx("code", { className: "font-mono text-[10px]", children: JSON.stringify(v) })) : (String(v)) })] }, k)))] }) })] }));
}
// ---------------------------------------------------------------------------
// TuningOptionCard — one row per option
// ---------------------------------------------------------------------------
function TuningOptionCard({ option }) {
    const [showRisk, setShowRisk] = useState(false);
    const isAdvice = option.deep_link === null;
    return (_jsx("div", { className: `p-2.5 border rounded-md ${isAdvice
            ? "border-spark-border/60 bg-spark-bg/40"
            : "border-spark-border hover:border-spark-accent/40 transition-colors"}`, children: _jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: isAdvice ? "text-spark-muted" : "font-medium", children: option.label }), _jsx("span", { className: `chip ${SEVERITY_CHIP[option.severity]} text-[10px]`, children: SEVERITY_LABEL[option.severity] })] }), _jsx("p", { className: "text-xs text-spark-muted mt-1", children: option.description }), _jsxs("div", { className: "mt-1.5 flex items-center gap-1.5 text-xs", children: [_jsx("button", { onClick: () => setShowRisk((s) => !s), className: "btn-ghost btn-icon", "aria-label": showRisk ? "Hide risk" : "Show risk", title: option.risk, children: _jsx(Info, { size: 12 }) }), _jsxs("span", { className: "text-spark-muted", children: [_jsx("strong", { children: "Risk:" }), " ", showRisk ? option.risk : option.risk.split(".")[0] + "."] })] })] }), !isAdvice && option.deep_link && (_jsxs(Link, { to: option.deep_link, className: "btn btn-primary text-xs whitespace-nowrap", children: ["Open ", _jsx(ChevronRight, { size: 12, className: "ml-1 inline" })] }))] }) }));
}
// ---------------------------------------------------------------------------
// Public dispatcher
// ---------------------------------------------------------------------------
export function FailureInspector(props) {
    if (!props.error || !props.error.code) {
        // Defensive: don't render an empty inspector if the WS frame had no
        // structured error. Caller is expected to feature-detect.
        return null;
    }
    if (props.variant === "compact") {
        return _jsx(FailureInspectorCompact, { error: props.error, context: props.context });
    }
    return _jsx(FailureInspectorInline, { error: props.error, context: props.context });
}
/** Best-effort SparkError feature detection — used by call sites that
 * receive `error` as `string | object`. */
export function isSparkError(value) {
    if (!value || typeof value !== "object")
        return false;
    const v = value;
    return typeof v.code === "string" && typeof v.message === "string" && v.code.startsWith("SPK_E_");
}
/** A small "Why?" toggle button for inline use beneath a thin error line. */
export function WhyToggle({ open, onClick, }) {
    return (_jsxs("button", { onClick: onClick, className: "btn-ghost text-[11px] inline-flex items-center gap-1 px-1.5 py-0.5", "aria-expanded": open, children: [_jsx(AlertCircle, { size: 11 }), open ? "Hide" : "Why?"] }));
}
