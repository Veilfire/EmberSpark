import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Activity, AlertTriangle, Bot, ChevronRight, LayoutDashboard, } from "lucide-react";
import { api } from "../lib/api";
import { formatUsd } from "../lib/utils";
import { PageHeader } from "../components/PageHeader";
import { StatCard, EmptyState, HealthDot } from "../components/primitives";
import { RelativeTime } from "../components/RelativeTime";
export default function Overview() {
    const cost = useQuery({
        queryKey: ["cost", "day"],
        queryFn: () => api.get("/api/cost/window/day"),
    });
    const costAllTime = useQuery({
        queryKey: ["cost", "all"],
        queryFn: () => api.get("/api/cost/window/all"),
    });
    const hourly = useQuery({
        queryKey: ["cost", "hourly"],
        queryFn: () => api.get("/api/cost/hourly?hours=24"),
    });
    const runs = useQuery({
        queryKey: ["runs-head"],
        queryFn: () => api.get("/api/scheduler/runs?limit=10"),
    });
    const posture = useQuery({
        queryKey: ["posture"],
        queryFn: () => api.get("/api/security/global"),
    });
    const agents = useQuery({
        queryKey: ["agents"],
        queryFn: () => api.get("/api/scheduler/agents"),
    });
    const attention = useQuery({
        queryKey: ["attention"],
        queryFn: () => api.get("/api/scheduler/attention"),
        refetchInterval: 30_000,
    });
    const activeRuns = (runs.data ?? []).filter((r) => r.state === "running");
    const totalAgents = agents.data?.length ?? 0;
    return (_jsxs("div", { className: "space-y-6", children: [_jsx(PageHeader, { icon: _jsx(LayoutDashboard, { className: "w-6 h-6" }), title: "Overview", subtitle: "Runtime snapshot." }), attention.data && attention.data.total > 0 && (_jsxs(Link, { to: "/audit", className: "panel-interactive p-3 flex items-center gap-3 border-spark-accent/30 bg-spark-accent/5", children: [_jsx(AlertTriangle, { className: "w-5 h-5 text-spark-accent shrink-0" }), _jsxs("div", { className: "flex-1 flex flex-wrap gap-x-4 gap-y-1 text-sm", children: [attention.data.failed_runs_24h > 0 && (_jsxs("span", { children: [_jsx("span", { className: "font-bold text-spark-danger", children: attention.data.failed_runs_24h }), " ", _jsx("span", { className: "text-spark-muted", children: "failed runs (24h)" })] })), attention.data.pending_skills > 0 && (_jsxs("span", { children: [_jsx("span", { className: "font-bold text-spark-accent", children: attention.data.pending_skills }), " ", _jsx("span", { className: "text-spark-muted", children: "pending skill reviews" })] })), attention.data.expiring_grants > 0 && (_jsxs("span", { children: [_jsx("span", { className: "font-bold text-spark-accent", children: attention.data.expiring_grants }), " ", _jsx("span", { className: "text-spark-muted", children: "grants expiring soon" })] })), attention.data.expiring_forensic > 0 && (_jsxs("span", { children: [_jsx("span", { className: "font-bold text-spark-accent", children: attention.data.expiring_forensic }), " ", _jsx("span", { className: "text-spark-muted", children: "forensic captures expiring" })] })), attention.data.dlq_tasks > 0 && (_jsxs("span", { children: [_jsx("span", { className: "font-bold text-spark-danger", children: attention.data.dlq_tasks }), " ", _jsx("span", { className: "text-spark-muted", children: "tasks in DLQ" })] }))] }), _jsx(ChevronRight, { className: "w-4 h-4 text-spark-muted" })] })), _jsxs("section", { className: "grid grid-cols-1 md:grid-cols-5 gap-4", children: [_jsx(StatCard, { label: "Spend (24h)", value: cost.data ? formatUsd(cost.data.total_usd) : "—", sub: `across ${Object.keys(cost.data?.by_agent ?? {}).length} agents`, trend: hourly.data?.buckets, tone: (cost.data?.total_usd ?? 0) > 5
                            ? "warn"
                            : "default" }), _jsx(StatCard, { label: "Spend (all-time)", value: costAllTime.data ? formatUsd(costAllTime.data.total_usd) : "—", sub: `${Object.keys(costAllTime.data?.by_model ?? {}).length} models, ${Object.keys(costAllTime.data?.by_agent ?? {}).length} agents` }), _jsx(StatCard, { label: "Active runs", value: activeRuns.length, sub: activeRuns.length > 0 ? "running now" : "none in flight", tone: activeRuns.length > 0 ? "good" : "default" }), _jsx(StatCard, { label: "Agents", value: totalAgents, sub: totalAgents === 0 ? "none installed" : "installed" }), _jsx(StatCard, { label: "Posture", value: posture.data?.frozen
                            ? "FROZEN"
                            : (posture.data?.compliance_mode ?? "—"), sub: `privacy: ${posture.data?.default_privacy_mode ?? "—"}`, tone: posture.data?.frozen ? "danger" : "default" })] }), _jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Bot, { className: "w-4 h-4 text-spark-accent" }), "Agents (", totalAgents, ")"] }), _jsxs(Link, { to: "/agents", className: "text-spark-accent text-xs hover:underline flex items-center gap-0.5", children: ["View all ", _jsx(ChevronRight, { className: "w-3 h-3" })] })] }), totalAgents === 0 ? (_jsx(EmptyState, { icon: _jsx(Bot, { className: "w-8 h-8" }), title: "No agents yet", description: "Install a template to get started, or create one from scratch.", action: { label: "Browse Templates", to: "/templates" } })) : (_jsx("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3", children: (agents.data ?? []).map((a) => (_jsxs(Link, { to: `/agents/${encodeURIComponent(a.name)}`, className: "panel-interactive p-3 block", children: [_jsxs("div", { className: "flex items-center justify-between mb-1", children: [_jsx("div", { className: "font-mono text-sm truncate", children: a.name }), _jsx(HealthDot, { ok: true })] }), _jsx("p", { className: "text-spark-muted text-xs line-clamp-2", children: a.description || "—" }), _jsx("div", { className: "text-xs text-spark-muted mt-2", children: _jsx(RelativeTime, { ts: a.updated_at }) })] }, a.name))) }))] }), _jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Activity, { className: "w-4 h-4 text-spark-accent" }), " Recent runs"] }), _jsxs(Link, { to: "/runs", className: "text-spark-accent text-xs hover:underline flex items-center gap-0.5", children: ["View all ", _jsx(ChevronRight, { className: "w-3 h-3" })] })] }), (runs.data ?? []).length === 0 ? (_jsx("p", { className: "text-spark-muted text-sm text-center py-6", children: "No runs yet. Start a chat or trigger a task." })) : (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left pb-2", children: "Run" }), _jsx("th", { className: "text-left pb-2", children: "Task" }), _jsx("th", { className: "text-left pb-2", children: "State" }), _jsx("th", { className: "text-left pb-2", children: "Started" }), _jsx("th", { className: "text-right pb-2 tabular-nums", children: "Tools" }), _jsx("th", { className: "text-right pb-2 tabular-nums", children: "Iters" })] }) }), _jsx("tbody", { children: (runs.data ?? []).map((r) => (_jsxs("tr", { className: "border-t border-spark-border hover:bg-spark-border/20 transition", children: [_jsx("td", { className: "py-1.5 font-mono text-xs", children: _jsxs(Link, { to: `/runs/${encodeURIComponent(r.run_id)}/replay`, className: "hover:text-spark-accent transition", children: [r.run_id.slice(0, 12), "\u2026"] }) }), _jsx("td", { children: r.task_name }), _jsx("td", { children: _jsx("span", { className: `chip ${stateClass(r.state)}`, children: r.state }) }), _jsx("td", { children: _jsx(RelativeTime, { ts: r.started_at }) }), _jsx("td", { className: "text-right tabular-nums", children: r.tool_calls }), _jsx("td", { className: "text-right tabular-nums", children: r.iterations })] }, r.run_id))) })] }))] })] }));
}
function stateClass(state) {
    if (state === "completed")
        return "chip-good";
    if (state === "failed")
        return "chip-danger";
    if (state === "running")
        return "chip-warn";
    return "";
}
