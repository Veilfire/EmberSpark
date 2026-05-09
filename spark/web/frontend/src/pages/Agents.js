import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bot, Plus, Search, Zap } from "lucide-react";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, HealthDot, SkeletonCard } from "../components/primitives";
import { Timestamp } from "../components/RelativeTime";
export default function Agents() {
    const [filter, setFilter] = useState("");
    const list = useQuery({
        queryKey: ["agents"],
        queryFn: () => api.get("/api/scheduler/agents"),
    });
    // Fetch detail for each agent in parallel.
    const details = useQueries({
        queries: (list.data ?? []).map((a) => ({
            queryKey: ["agent-detail", a.name],
            queryFn: () => api.get(`/api/scheduler/agents/${encodeURIComponent(a.name)}`),
            staleTime: 30_000,
        })),
    });
    const filtered = useMemo(() => {
        const items = list.data ?? [];
        if (!filter)
            return items;
        const q = filter.toLowerCase();
        return items.filter((a) => a.name.toLowerCase().includes(q) ||
            a.description.toLowerCase().includes(q));
    }, [list.data, filter]);
    return (_jsxs("div", { className: "space-y-6", children: [_jsx(PageHeader, { icon: _jsx(Bot, { className: "w-6 h-6" }), title: "Agents", subtitle: "Installed agents with live health, provider config, and recent activity.", actions: _jsx(_Fragment, { children: _jsxs(Link, { to: "/templates", className: "btn btn-primary", children: [_jsx(Plus, { className: "w-4 h-4 mr-1 inline" }), " New Agent"] }) }) }), list.isLoading && (_jsx("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4", children: Array.from({ length: 3 }).map((_, i) => (_jsx(SkeletonCard, {}, i))) })), list.data && list.data.length === 0 && (_jsx(EmptyState, { icon: _jsx(Bot, { className: "w-10 h-10" }), title: "No agents installed", description: "Install a ready-to-run template or create your own from scratch.", action: { label: "Browse Templates", to: "/templates" } })), list.data && list.data.length > 0 && (_jsxs(_Fragment, { children: [_jsxs("div", { className: "relative max-w-md", children: [_jsx(Search, { className: "w-4 h-4 text-spark-muted absolute left-3 top-1/2 -translate-y-1/2" }), _jsx("input", { className: "input w-full pl-9", placeholder: `Search ${list.data.length} agent${list.data.length === 1 ? "" : "s"}…`, value: filter, onChange: (e) => setFilter(e.target.value) })] }), _jsx("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4", children: filtered.map((a) => {
                            const detailQ = details.find((d) => d.data?.name === a.name);
                            const detail = detailQ?.data;
                            const healthy = !!detail?.health.sandbox_ok &&
                                !!detail?.health.provider_key_available;
                            return (_jsxs(Link, { to: `/agents/${encodeURIComponent(a.name)}`, className: "panel-interactive p-4 block", children: [_jsxs("div", { className: "flex items-start justify-between mb-2", children: [_jsxs("div", { className: "flex items-center gap-2 min-w-0", children: [_jsx(Bot, { className: "w-4 h-4 text-spark-accent shrink-0" }), _jsx("h3", { className: "font-bold truncate", children: a.name })] }), _jsx(HealthDot, { ok: detail ? healthy : null, pulse: detail && healthy })] }), _jsx("p", { className: "text-spark-muted text-xs line-clamp-2 mb-3 min-h-[2.5rem]", children: a.description || "No description" }), detail && (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center gap-1 text-xs", children: [_jsx(Zap, { className: "w-3 h-3 text-spark-muted" }), _jsx("span", { className: "text-spark-muted capitalize", children: detail.provider.type }), _jsx("span", { className: "text-spark-muted", children: "\u00B7" }), _jsx("span", { className: "font-mono text-[10px] truncate", children: detail.provider.model })] }), _jsxs("div", { className: "flex items-center gap-3 text-xs tabular-nums", children: [_jsx("span", { className: "text-spark-muted", children: "7d:" }), _jsxs("span", { className: "text-spark-good", children: [detail.run_stats.completed_7d, " \u2713"] }), detail.run_stats.failed_7d > 0 && (_jsxs("span", { className: "text-spark-danger", children: [detail.run_stats.failed_7d, " \u2717"] })), detail.cost_7d_usd > 0 && (_jsxs("span", { className: "text-spark-muted ml-auto", children: ["$", detail.cost_7d_usd.toFixed(3)] }))] })] })), _jsx("div", { className: "text-[10px] text-spark-muted mt-3 border-t border-spark-border pt-2", children: _jsx(Timestamp, { ts: a.updated_at }) })] }, a.name));
                        }) }), filtered.length === 0 && (_jsxs("p", { className: "text-spark-muted text-sm text-center py-6", children: ["No agents match \"", filter, "\""] }))] }))] }));
}
