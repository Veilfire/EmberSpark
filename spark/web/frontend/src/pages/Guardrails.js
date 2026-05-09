import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
export default function GuardrailsPage() {
    const data = useQuery({
        queryKey: ["guardrails"],
        queryFn: () => api.get("/api/guardrails/?hours=24"),
    });
    if (!data.data)
        return _jsx("div", { className: "text-spark-muted", children: "Loading\u2026" });
    const g = data.data;
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Guardrails" }), _jsxs("p", { className: "text-spark-muted text-sm", children: ["Last ", g.window_hours, "h. Click any category to jump into the filtered audit log."] })] }), _jsxs("section", { className: "grid grid-cols-3 gap-4", children: [_jsx(SeverityCard, { label: "Critical", count: g.critical, tone: "danger" }), _jsx(SeverityCard, { label: "Elevated", count: g.elevated, tone: "warn" }), _jsx(SeverityCard, { label: "Info", count: g.info, tone: "neutral" })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Categories" }), _jsx("ul", { className: "divide-y divide-spark-border", children: Object.entries(g.categories).map(([cat, count]) => (_jsxs("li", { className: "flex items-center justify-between py-2", children: [_jsx(Link, { to: "/audit", className: "font-mono text-sm text-spark-text hover:underline", children: cat }), _jsx("span", { className: `chip ${count > 0 ? "chip-warn" : ""}`, children: count })] }, cat))) })] })] }));
}
function SeverityCard({ label, count, tone, }) {
    const color = tone === "danger"
        ? "text-spark-danger"
        : tone === "warn"
            ? "text-spark-accent"
            : "text-spark-muted";
    return (_jsxs("div", { className: "panel p-4", children: [_jsx("div", { className: "label", children: label }), _jsx("div", { className: `text-3xl font-bold mt-1 ${color}`, children: count })] }));
}
