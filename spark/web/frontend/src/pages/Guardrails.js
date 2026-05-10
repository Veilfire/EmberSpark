import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
export default function GuardrailsPage() {
    const data = useQuery({
        queryKey: ["guardrails"],
        queryFn: () => api.get("/api/guardrails/?hours=24"),
    });
    if (!data.data)
        return _jsx("div", { className: "text-spark-muted", children: "Loading\u2026" });
    const g = data.data;
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Guardrails" }), _jsxs("p", { className: "text-spark-muted text-sm", children: ["Last ", g.window_hours, "h. Click any category to jump into the filtered audit log; expand for the top offenders."] })] }), _jsxs("section", { className: "grid grid-cols-3 gap-4", children: [_jsx(SeverityCard, { label: "Critical", count: g.critical, tone: "danger" }), _jsx(SeverityCard, { label: "Elevated", count: g.elevated, tone: "warn" }), _jsx(SeverityCard, { label: "Info", count: g.info, tone: "neutral" })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Categories" }), _jsx("ul", { className: "divide-y divide-spark-border", children: Object.entries(g.categories).map(([cat, count]) => (_jsx(CategoryRow, { category: cat, count: count, auditKind: g.category_kinds?.[cat] }, cat))) })] })] }));
}
function CategoryRow({ category, count, auditKind, }) {
    const [open, setOpen] = useState(false);
    const linkTo = auditKind ? `/audit?kind=${encodeURIComponent(auditKind)}` : "/audit";
    return (_jsxs("li", { className: "py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 min-w-0 flex-1", children: [count > 0 && (_jsx("button", { className: "btn-icon p-0.5", onClick: () => setOpen((o) => !o), "aria-label": open ? "Hide offenders" : "Show offenders", children: open ? (_jsx(ChevronDown, { size: 14 })) : (_jsx(ChevronRight, { size: 14 })) })), _jsx(Link, { to: linkTo, className: "font-mono text-sm text-spark-text hover:underline truncate", children: category })] }), _jsx("span", { className: `chip ${count > 0 ? "chip-warn" : ""}`, children: count })] }), open && count > 0 && auditKind && (_jsx(CategoryOffenders, { kind: auditKind }))] }));
}
function CategoryOffenders({ kind }) {
    const data = useQuery({
        queryKey: ["guardrails-offenders", kind],
        queryFn: () => api.get(`/api/guardrails/offenders?kind=${encodeURIComponent(kind)}&limit=5`),
    });
    if (!data.data) {
        return (_jsx("div", { className: "mt-2 ml-7 text-xs text-spark-muted", children: "Loading offenders\u2026" }));
    }
    const r = data.data;
    return (_jsxs("div", { className: "mt-2 ml-7 grid grid-cols-2 gap-3 text-xs", children: [_jsx(OffenderTable, { label: "Top actors", rows: r.top_actors, kind: kind }), _jsx(OffenderTable, { label: "Top targets", rows: r.top_targets, kind: kind })] }));
}
function OffenderTable({ label, rows, kind, }) {
    if (rows.length === 0) {
        return (_jsxs("div", { children: [_jsx("div", { className: "label mb-1", children: label }), _jsx("div", { className: "text-spark-muted", children: "\u2014" })] }));
    }
    return (_jsxs("div", { children: [_jsx("div", { className: "label mb-1", children: label }), _jsx("table", { className: "w-full", children: _jsx("tbody", { children: rows.map((r, i) => (_jsxs("tr", { className: "border-t border-spark-border/50 first:border-0", children: [_jsx("td", { className: "py-1 truncate", children: _jsx(Link, { to: `/audit?kind=${encodeURIComponent(kind)}`, className: "text-spark-text hover:underline", children: r.name }) }), _jsx("td", { className: "py-1 text-right tabular-nums w-12 text-spark-muted", children: r.count })] }, i))) }) })] }));
}
function SeverityCard({ label, count, tone, }) {
    const color = tone === "danger"
        ? "text-spark-danger"
        : tone === "warn"
            ? "text-spark-accent"
            : "text-spark-muted";
    return (_jsxs("div", { className: "panel p-4", children: [_jsx("div", { className: "label", children: label }), _jsx("div", { className: `text-3xl font-bold mt-1 ${color}`, children: count })] }));
}
