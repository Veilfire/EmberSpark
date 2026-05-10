import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { formatRelative, severityColor } from "../lib/utils";
import { FailureInspector, isSparkError, } from "../components/FailureInspector";
export default function AuditLog() {
    const [params, setParams] = useSearchParams();
    // URL is the source of truth so /audit?kind=security.permission_denied
    // links from Guardrails / NotificationBell hydrate the filter.
    const [kind, setKind] = useState(() => params.get("kind") ?? "");
    const [severity, setSeverity] = useState(() => params.get("severity") ?? "");
    useEffect(() => {
        const next = new URLSearchParams(params);
        if (kind)
            next.set("kind", kind);
        else
            next.delete("kind");
        if (severity)
            next.set("severity", severity);
        else
            next.delete("severity");
        if (next.toString() !== params.toString()) {
            setParams(next, { replace: true });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [kind, severity]);
    const entries = useQuery({
        queryKey: ["audit", kind, severity],
        queryFn: () => {
            const qs = new URLSearchParams();
            qs.set("limit", "300");
            if (kind)
                qs.set("kind", kind);
            if (severity)
                qs.set("min_severity", severity);
            return api.get(`/api/audit/?${qs.toString()}`);
        },
    });
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Audit Log" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Every security-relevant mutation, immutable and searchable." })] }), _jsxs("div", { className: "flex gap-2 items-center flex-wrap", children: [_jsx("input", { className: "input w-72", placeholder: "kind filter (e.g. security.permission_denied)", value: kind, onChange: (e) => setKind(e.target.value) }), _jsxs("select", { className: "input", value: severity, onChange: (e) => setSeverity(e.target.value), children: [_jsx("option", { value: "", children: "All severities" }), _jsx("option", { value: "info", children: "info+" }), _jsx("option", { value: "elevated", children: "elevated+" }), _jsx("option", { value: "critical", children: "critical" })] }), (kind || severity) && (_jsx("button", { className: "btn btn-ghost text-xs", onClick: () => {
                            setKind("");
                            setSeverity("");
                        }, children: "Clear filters" }))] }), _jsx("div", { className: "panel p-0 overflow-hidden", children: _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase bg-spark-bg", children: _jsxs("tr", { children: [_jsx("th", { className: "w-6" }), _jsx("th", { className: "text-left px-3 py-2", children: "when" }), _jsx("th", { className: "text-left", children: "actor" }), _jsx("th", { className: "text-left", children: "kind" }), _jsx("th", { className: "text-left", children: "target" }), _jsx("th", { className: "text-left", children: "severity" }), _jsx("th", { className: "text-left", children: "reason / diff" })] }) }), _jsx("tbody", { children: (entries.data ?? []).map((e, i) => (_jsx(AuditRow, { entry: e }, i))) })] }) })] }));
}
function AuditRow({ entry }) {
    const [open, setOpen] = useState(false);
    const sparkError = parseEmbeddedSparkError(entry.diff);
    const expandable = !!sparkError || isMeaningfulDiff(entry.diff);
    return (_jsxs(_Fragment, { children: [_jsxs("tr", { className: "border-t border-spark-border align-top", children: [_jsx("td", { className: "px-2 py-1", children: expandable && (_jsx("button", { className: "btn-icon p-0.5", onClick: () => setOpen((o) => !o), "aria-label": open ? "Collapse" : "Expand", children: open ? (_jsx(ChevronDown, { size: 14 })) : (_jsx(ChevronRight, { size: 14 })) })) }), _jsx("td", { className: "py-1 px-3 text-xs", children: formatRelative(entry.ts) }), _jsx("td", { children: entry.actor }), _jsx("td", { className: "font-mono text-xs", children: entry.kind }), _jsx("td", { className: "font-mono text-xs", children: entry.target }), _jsx("td", { children: _jsx("span", { className: `chip ${severityColor(entry.severity)}`, children: entry.severity }) }), _jsx("td", { className: "text-xs text-spark-muted max-w-md truncate", children: entry.reason || entry.diff })] }), open && (_jsxs("tr", { className: "border-t border-spark-border/50 bg-spark-bg/40", children: [_jsx("td", {}), _jsx("td", { colSpan: 6, className: "px-3 py-2", children: sparkError ? (_jsx(FailureInspector, { error: sparkError, variant: "inline" })) : (_jsx("pre", { className: "text-xs font-mono text-spark-text whitespace-pre-wrap break-all", children: prettyDiff(entry.diff) })) })] }))] }));
}
/** Try to extract an embedded :class:`SparkError.to_dict()` from the
 * audit diff. Some gate-failure audits will eventually carry the full
 * payload; for now this gracefully degrades when the diff is plain JSON
 * with no SparkError shape. */
function parseEmbeddedSparkError(diff) {
    if (!diff)
        return null;
    try {
        const parsed = JSON.parse(diff);
        if (isSparkError(parsed))
            return parsed;
        // Some entries embed under `error` or `spark_error`.
        if (parsed && typeof parsed === "object") {
            for (const k of ["error", "spark_error", "payload"]) {
                if (isSparkError(parsed[k])) {
                    return parsed[k];
                }
            }
        }
    }
    catch {
        // not JSON; fall through
    }
    return null;
}
function isMeaningfulDiff(diff) {
    if (!diff)
        return false;
    if (diff.length > 80)
        return true;
    return /[{[]/.test(diff);
}
function prettyDiff(diff) {
    if (!diff)
        return "";
    try {
        return JSON.stringify(JSON.parse(diff), null, 2);
    }
    catch {
        return diff;
    }
}
