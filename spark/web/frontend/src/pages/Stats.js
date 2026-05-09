import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { formatUsd } from "../lib/utils";
export default function StatsPage() {
    const stats = useQuery({
        queryKey: ["agent-stats"],
        queryFn: () => api.get("/api/stats/"),
    });
    if (!stats.data) {
        return _jsx("div", { className: "text-spark-muted", children: "Loading\u2026" });
    }
    const s = stats.data;
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Agent stats" }), _jsxs("p", { className: "text-spark-muted text-sm", children: ["Rolling ", s.window_days, "-day window."] })] }), _jsxs("section", { className: "grid grid-cols-2 md:grid-cols-4 gap-4", children: [_jsx(Stat, { label: "Runs (total)", value: String(s.runs_total) }), _jsx(Stat, { label: "Success rate", value: `${(s.success_rate * 100).toFixed(1)}%`, highlight: s.success_rate < 0.5 ? "danger" : undefined }), _jsx(Stat, { label: "Completed", value: String(s.runs_completed) }), _jsx(Stat, { label: "Failed", value: String(s.runs_failed), highlight: s.runs_failed > 0 ? "danger" : undefined }), _jsx(Stat, { label: "Wall p50", value: `${s.wall_time_p50_s.toFixed(1)}s` }), _jsx(Stat, { label: "Wall p95", value: `${s.wall_time_p95_s.toFixed(1)}s` }), _jsx(Stat, { label: "Total cost", value: formatUsd(s.total_cost_usd) }), _jsx(Stat, { label: "Avg / run", value: formatUsd(s.avg_cost_per_run_usd) }), _jsx(Stat, { label: "Memory writes", value: String(s.memory_writes) }), _jsx(Stat, { label: "Skills approved", value: String(s.skills_approved) })] })] }));
}
function Stat({ label, value, highlight, }) {
    return (_jsxs("div", { className: "panel p-4", children: [_jsx("div", { className: "label", children: label }), _jsx("div", { className: `text-2xl font-semibold mt-1 ${highlight === "danger" ? "text-spark-danger" : ""}`, children: value })] }));
}
