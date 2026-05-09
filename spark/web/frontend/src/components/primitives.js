import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Link } from "react-router-dom";
export function StatCard({ label, value, sub, trend, tone = "default", className = "", }) {
    const toneClass = tone === "good"
        ? "text-spark-good"
        : tone === "warn"
            ? "text-spark-accent"
            : tone === "danger"
                ? "text-spark-danger"
                : "text-spark-text";
    return (_jsxs("div", { className: `panel p-4 shadow-sm hover:shadow-md transition-shadow ${className}`, children: [_jsx("div", { className: "text-xs uppercase tracking-wide text-spark-muted", children: label }), _jsx("div", { className: `text-2xl font-bold mt-1 tabular-nums ${toneClass}`, children: value }), sub && (_jsx("div", { className: "text-xs text-spark-muted mt-1 truncate", children: sub })), trend && trend.length > 1 && (_jsx("div", { className: "mt-2", children: _jsx(Sparkline, { data: trend, tone: tone }) }))] }));
}
// ---------------------------------------------------------------------------
// Sparkline — inline SVG
// ---------------------------------------------------------------------------
export function Sparkline({ data, tone = "default", height = 24, width = 80, }) {
    if (data.length < 2)
        return null;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const color = tone === "good"
        ? "#3fb950"
        : tone === "warn"
            ? "#f59e0b"
            : tone === "danger"
                ? "#f85149"
                : "#f59e0b";
    const points = data
        .map((v, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = height - ((v - min) / range) * height;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
        .join(" ");
    return (_jsx("svg", { width: width, height: height, className: "overflow-visible block", children: _jsx("polyline", { points: points, fill: "none", stroke: color, strokeWidth: "1.5", strokeLinecap: "round", strokeLinejoin: "round" }) }));
}
export function EmptyState({ icon, title, description, action, }) {
    return (_jsxs("div", { className: "panel p-8 text-center flex flex-col items-center gap-2", children: [icon && (_jsx("div", { className: "text-spark-muted w-10 h-10 flex items-center justify-center", children: icon })), _jsx("div", { className: "font-semibold text-spark-text", children: title }), description && (_jsx("p", { className: "text-sm text-spark-muted max-w-md", children: description })), action && (_jsx("div", { className: "mt-2", children: action.to ? (_jsx(Link, { to: action.to, className: "btn btn-primary", children: action.label })) : (_jsx("button", { className: "btn btn-primary", onClick: action.onClick, children: action.label })) }))] }));
}
// ---------------------------------------------------------------------------
// Skeleton — shimmering placeholder
// ---------------------------------------------------------------------------
export function Skeleton({ className = "" }) {
    return (_jsx("div", { className: `animate-pulse bg-spark-border/40 rounded-md ${className}` }));
}
export function SkeletonRow({ cols = 4 }) {
    return (_jsx("tr", { className: "border-t border-spark-border", children: Array.from({ length: cols }).map((_, i) => (_jsx("td", { className: "py-2 pr-4", children: _jsx(Skeleton, { className: "h-4 w-full max-w-[12rem]" }) }, i))) }));
}
export function SkeletonCard() {
    return (_jsxs("div", { className: "panel p-4 space-y-3", children: [_jsx(Skeleton, { className: "h-5 w-1/3" }), _jsx(Skeleton, { className: "h-3 w-full" }), _jsx(Skeleton, { className: "h-3 w-4/5" })] }));
}
// ---------------------------------------------------------------------------
// HealthDot — colored status indicator
// ---------------------------------------------------------------------------
export function HealthDot({ ok, size = "sm", pulse, }) {
    const dim = size === "md" ? "w-3 h-3" : "w-2 h-2";
    const color = ok === null
        ? "bg-spark-muted"
        : ok
            ? "bg-spark-good"
            : "bg-spark-danger";
    return (_jsxs("span", { className: "relative inline-flex items-center justify-center", children: [pulse && ok && (_jsx("span", { className: `absolute inline-flex rounded-full ${color} opacity-60 animate-ping ${dim}` })), _jsx("span", { className: `relative inline-flex rounded-full ${color} ${dim}` })] }));
}
// ---------------------------------------------------------------------------
// Divider — soft gradient
// ---------------------------------------------------------------------------
export function Divider({ label }) {
    if (!label) {
        return (_jsx("div", { className: "h-px bg-gradient-to-r from-transparent via-spark-border to-transparent my-6" }));
    }
    return (_jsxs("div", { className: "flex items-center gap-3 my-6", children: [_jsx("div", { className: "flex-1 h-px bg-gradient-to-r from-transparent to-spark-border" }), _jsx("span", { className: "text-xs uppercase tracking-wide text-spark-muted", children: label }), _jsx("div", { className: "flex-1 h-px bg-gradient-to-l from-transparent to-spark-border" })] }));
}
// ---------------------------------------------------------------------------
// Section — a titled panel with optional collapse
// ---------------------------------------------------------------------------
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
export function Section({ title, icon, children, actions, collapsible, defaultOpen = true, }) {
    const [open, setOpen] = useState(defaultOpen);
    return (_jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsxs("h3", { className: `font-semibold flex items-center gap-2 ${collapsible ? "cursor-pointer select-none" : ""}`, onClick: collapsible ? () => setOpen(!open) : undefined, children: [collapsible &&
                                (open ? (_jsx(ChevronDown, { className: "w-4 h-4" })) : (_jsx(ChevronRight, { className: "w-4 h-4" }))), icon && _jsx("span", { className: "text-spark-accent", children: icon }), title] }), actions && _jsx("div", { className: "flex gap-2", children: actions })] }), (!collapsible || open) && _jsx("div", { children: children })] }));
}
