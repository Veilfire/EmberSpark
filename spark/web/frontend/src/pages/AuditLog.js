import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { formatRelative, severityColor } from "../lib/utils";
export default function AuditLog() {
    const [kind, setKind] = useState("");
    const [severity, setSeverity] = useState("");
    const entries = useQuery({
        queryKey: ["audit", kind, severity],
        queryFn: () => {
            const params = new URLSearchParams();
            params.set("limit", "300");
            if (kind)
                params.set("kind", kind);
            if (severity)
                params.set("min_severity", severity);
            return api.get(`/api/audit/?${params.toString()}`);
        },
    });
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Audit Log" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Every security-relevant mutation, immutable and searchable." })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("input", { className: "input w-48", placeholder: "kind filter", value: kind, onChange: (e) => setKind(e.target.value) }), _jsxs("select", { className: "input", value: severity, onChange: (e) => setSeverity(e.target.value), children: [_jsx("option", { value: "", children: "All severities" }), _jsx("option", { value: "info", children: "info+" }), _jsx("option", { value: "elevated", children: "elevated+" }), _jsx("option", { value: "critical", children: "critical" })] })] }), _jsx("div", { className: "panel p-0 overflow-hidden", children: _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase bg-spark-bg", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left px-3 py-2", children: "when" }), _jsx("th", { className: "text-left", children: "actor" }), _jsx("th", { className: "text-left", children: "kind" }), _jsx("th", { className: "text-left", children: "target" }), _jsx("th", { className: "text-left", children: "severity" }), _jsx("th", { className: "text-left", children: "reason / diff" })] }) }), _jsx("tbody", { children: (entries.data ?? []).map((e, i) => (_jsxs("tr", { className: "border-t border-spark-border align-top", children: [_jsx("td", { className: "py-1 px-3 text-xs", children: formatRelative(e.ts) }), _jsx("td", { children: e.actor }), _jsx("td", { className: "font-mono text-xs", children: e.kind }), _jsx("td", { className: "font-mono text-xs", children: e.target }), _jsx("td", { children: _jsx("span", { className: `chip ${severityColor(e.severity)}`, children: e.severity }) }), _jsx("td", { className: "text-xs text-spark-muted max-w-md truncate", children: e.reason || e.diff })] }, i))) })] }) })] }));
}
