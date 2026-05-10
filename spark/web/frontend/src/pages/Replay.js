import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { MarkdownView } from "../components/MarkdownView";
import { FailureInspector, isSparkError, } from "../components/FailureInspector";
export default function Replay() {
    const { run_id } = useParams();
    const data = useQuery({
        queryKey: ["replay", run_id],
        queryFn: () => api.get(`/api/replay/${encodeURIComponent(run_id ?? "")}`),
        enabled: !!run_id,
    });
    if (!data.data)
        return _jsx("div", { className: "text-spark-muted", children: "Loading\u2026" });
    const r = data.data;
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Run replay" }), _jsx("p", { className: "text-spark-muted text-sm font-mono", children: r.run_id }), _jsxs("div", { className: "mt-2 flex gap-4 text-sm flex-wrap", children: [_jsx("span", { className: `chip ${r.state === "completed" ? "chip-good" : r.state === "failed" ? "chip-danger" : "chip-warn"}`, children: r.state }), _jsxs("span", { children: ["task: ", r.task_name] }), _jsxs("span", { children: ["iters: ", r.iterations] }), _jsxs("span", { children: ["model calls: ", r.model_calls] }), _jsxs("span", { children: ["tool calls: ", r.tool_calls] }), r.cost && r.cost.call_count > 0 && (_jsxs("span", { title: costSourceTitle(r.cost), children: ["cost: $", r.cost.total_usd.toFixed(4)] })), r.triggered_by && (_jsxs("span", { className: "text-spark-muted", children: ["via ", r.triggered_by] }))] })] }), r.error && (_jsxs("section", { className: "panel p-4 border-spark-danger/40", children: [_jsx("h3", { className: "font-semibold mb-2 text-spark-danger", children: "Error" }), isSparkError(r.error_payload) ? (_jsx(FailureInspector, { error: r.error_payload, context: { agent_name: r.agent_name, run_id: r.run_id }, variant: "inline" })) : (_jsx("pre", { className: "text-sm whitespace-pre-wrap text-spark-text", children: r.error }))] })), r.result_text && (_jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Final response" }), _jsx(MarkdownView, { content: r.result_text, className: "text-spark-text text-sm" })] })), r.summary && (_jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-2 text-spark-muted text-xs uppercase tracking-wide", children: "Reflection summary" }), _jsx(MarkdownView, { content: r.summary, className: "text-spark-text text-sm" })] })), r.deliverables.length > 0 && (_jsxs("section", { className: "panel p-4", children: [_jsxs("h3", { className: "font-semibold mb-3", children: ["Deliverables (", r.deliverables.length, ")"] }), _jsx("ul", { className: "space-y-1 text-sm", children: r.deliverables.map((d) => (_jsxs("li", { className: "flex items-center justify-between border-b border-spark-border last:border-0 py-1", children: [_jsx("a", { href: `/api/deliverables/${encodeURI(d.relative_path)}`, className: "font-mono text-spark-link hover:underline", children: d.relative_path }), _jsxs("span", { className: "text-xs text-spark-muted", children: [formatBytes(d.size_bytes), " \u00B7 ", d.kind] })] }, d.id))) })] })), r.trigger_payload_json && _jsx(TriggerPayloadPanel, { raw: r.trigger_payload_json }), r.model_call_events && r.model_call_events.length > 0 && (_jsx(ModelCallsPanel, { calls: r.model_call_events })), _jsxs("section", { className: "panel p-4", children: [_jsxs("h3", { className: "font-semibold mb-3", children: ["Flame graph (", r.spans.length, " spans)"] }), _jsx(FlameGraph, { spans: r.spans })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Timeline" }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "depth" }), _jsx("th", { className: "text-left", children: "span" }), _jsx("th", { className: "text-left", children: "duration" }), _jsx("th", { className: "text-left", children: "error" })] }) }), _jsx("tbody", { children: r.spans.map((s) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 text-spark-muted", children: s.parent_span_id ? "  ↳" : "●" }), _jsx("td", { className: "font-mono", children: s.name }), _jsx("td", { children: s.duration_ms ? `${s.duration_ms.toFixed(1)} ms` : "—" }), _jsx("td", { className: "text-spark-danger text-xs", children: s.error_class ?? "" })] }, s.id))) })] })] })] }));
}
function formatBytes(n) {
    if (n < 1024)
        return `${n} B`;
    if (n < 1024 * 1024)
        return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function costSourceTitle(cost) {
    const parts = Object.entries(cost.source_mix)
        .map(([k, v]) => `${v} ${k}`)
        .join(", ");
    return `${cost.call_count} model calls (${parts || "no detail"})`;
}
function openrouterDeepLink(req) {
    if (!req || !req.startsWith("gen-"))
        return null;
    return `https://openrouter.ai/activity?gen=${encodeURIComponent(req)}`;
}
function ModelCallsPanel({ calls }) {
    const sorted = [...calls].sort((a, b) => a.sequence - b.sequence);
    const totalReported = sorted.filter((c) => c.cost_source === "reported").length;
    const totalComputed = sorted.length - totalReported;
    return (_jsxs("section", { className: "panel p-4", children: [_jsxs("h3", { className: "font-semibold mb-3", children: ["Model calls (", sorted.length, ")", _jsxs("span", { className: "text-spark-muted text-xs ml-2 font-normal", children: [totalReported, " reported \u00B7 ", totalComputed, " computed"] })] }), _jsx("div", { className: "overflow-x-auto", children: _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left py-1", children: "#" }), _jsx("th", { className: "text-left py-1", children: "model" }), _jsx("th", { className: "text-right py-1", children: "in" }), _jsx("th", { className: "text-right py-1", children: "out" }), _jsx("th", { className: "text-right py-1", children: "cache" }), _jsx("th", { className: "text-right py-1", children: "latency" }), _jsx("th", { className: "text-right py-1", children: "cost" }), _jsx("th", { className: "text-left py-1 pl-3", children: "request" })] }) }), _jsx("tbody", { children: sorted.map((c) => {
                                const cacheTotal = c.cached_input_tokens + c.cache_creation_tokens;
                                const link = c.provider === "openrouter" ? openrouterDeepLink(c.request_id) : null;
                                return (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono text-spark-muted", children: c.sequence }), _jsxs("td", { className: "py-1 font-mono", children: [c.provider, "/", c.model] }), _jsx("td", { className: "py-1 text-right tabular-nums", children: c.input_tokens.toLocaleString() }), _jsxs("td", { className: "py-1 text-right tabular-nums", children: [c.output_tokens.toLocaleString(), c.reasoning_tokens > 0 && (_jsxs("span", { className: "text-spark-muted text-xs", children: [" (", c.reasoning_tokens, " r)"] }))] }), _jsx("td", { className: "py-1 text-right tabular-nums", children: cacheTotal > 0 ? (_jsx("span", { title: `cache_read=${c.cached_input_tokens}, cache_creation=${c.cache_creation_tokens}`, children: cacheTotal.toLocaleString() })) : (_jsx("span", { className: "text-spark-muted", children: "\u2014" })) }), _jsxs("td", { className: "py-1 text-right tabular-nums", children: [c.latency_ms, " ms"] }), _jsx("td", { className: "py-1 text-right tabular-nums", children: c.cost_usd != null ? (_jsxs(_Fragment, { children: ["$", c.cost_usd.toFixed(5), _jsx("span", { className: `ml-1 text-xs ${c.cost_source === "reported" ? "text-spark-good" : "text-spark-muted"}`, title: c.cost_source === "reported" ? "Provider-authoritative cost" : "Computed from local price table", children: c.cost_source === "reported" ? "✓" : "≈" })] })) : (_jsx("span", { className: "text-spark-muted", children: "\u2014" })) }), _jsx("td", { className: "py-1 pl-3 font-mono text-xs", children: link ? (_jsx("a", { className: "text-spark-link hover:underline", href: link, target: "_blank", rel: "noreferrer", children: c.request_id })) : (_jsx("span", { className: "text-spark-muted", children: c.request_id ?? "—" })) })] }, c.id));
                            }) })] }) })] }));
}
function TriggerPayloadPanel({ raw }) {
    const [expanded, setExpanded] = useState(false);
    let pretty = raw;
    try {
        pretty = JSON.stringify(JSON.parse(raw), null, 2);
    }
    catch {
        /* leave raw */
    }
    return (_jsxs("section", { className: "panel p-4", children: [_jsxs("button", { type: "button", className: "font-semibold text-left w-full flex items-center justify-between", onClick: () => setExpanded((v) => !v), children: [_jsx("span", { children: "Trigger payload" }), _jsx("span", { className: "text-spark-muted text-xs", children: expanded ? "hide" : "show" })] }), expanded && (_jsx("pre", { className: "mt-3 text-xs font-mono bg-spark-bg p-3 rounded overflow-x-auto whitespace-pre-wrap", children: pretty }))] }));
}
function FlameGraph({ spans }) {
    const { rows, total } = useMemo(() => {
        if (spans.length === 0)
            return { rows: [], total: 1 };
        const earliest = Math.min(...spans.map((s) => new Date(s.started_at).getTime()));
        const latest = Math.max(...spans.map((s) => s.finished_at ? new Date(s.finished_at).getTime() : new Date(s.started_at).getTime()));
        const total = Math.max(latest - earliest, 1);
        // Build depth buckets
        const depthById = new Map();
        function depth(s) {
            if (s.parent_span_id == null)
                return 0;
            if (depthById.has(s.id))
                return depthById.get(s.id);
            const parent = spans.find((x) => x.id === s.parent_span_id);
            const d = parent ? depth(parent) + 1 : 0;
            depthById.set(s.id, d);
            return d;
        }
        const rows = spans.map((s) => {
            const start = new Date(s.started_at).getTime() - earliest;
            const end = s.finished_at
                ? new Date(s.finished_at).getTime() - earliest
                : start + (s.duration_ms ?? 0);
            return {
                span: s,
                depth: depth(s),
                start,
                width: Math.max(end - start, 1),
            };
        });
        return { rows, total };
    }, [spans]);
    const maxDepth = rows.reduce((m, r) => Math.max(m, r.depth), 0);
    const rowHeight = 18;
    const height = (maxDepth + 1) * (rowHeight + 2) + 4;
    return (_jsx("div", { className: "bg-spark-bg border border-spark-border rounded overflow-x-auto", children: _jsx("svg", { width: "100%", height: height, viewBox: `0 0 1000 ${height}`, preserveAspectRatio: "none", children: rows.map((r) => {
                const x = (r.start / total) * 1000;
                const w = (r.width / total) * 1000;
                const y = r.depth * (rowHeight + 2) + 2;
                const fill = r.span.error_class ? "#f85149" : "#f59e0b";
                return (_jsxs("g", { children: [_jsx("rect", { x: x, y: y, width: Math.max(w, 1), height: rowHeight, fill: fill, opacity: 0.75, rx: 2 }), _jsxs("title", { children: [r.span.name, " \u2014 ", r.span.duration_ms?.toFixed(1) ?? "?", " ms"] }), w > 40 && (_jsx("text", { x: x + 4, y: y + rowHeight - 4, fontSize: 10, fill: "#14181d", fontFamily: "monospace", children: r.span.name }))] }, r.span.id));
            }) }) }));
}
