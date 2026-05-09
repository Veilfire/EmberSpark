import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { formatRelative } from "../lib/utils";
export default function RunHistory() {
    const [stateFilter, setStateFilter] = useState("");
    const [taskFilter, setTaskFilter] = useState("");
    const runs = useQuery({
        queryKey: ["runs", stateFilter, taskFilter],
        queryFn: () => {
            const params = new URLSearchParams();
            params.set("limit", "200");
            if (stateFilter)
                params.set("state", stateFilter);
            if (taskFilter)
                params.set("task_name", taskFilter);
            return api.get(`/api/scheduler/runs?${params.toString()}`);
        },
    });
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Run History" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Every run state, outcome, and budget summary." })] }), _jsxs("div", { className: "flex gap-2", children: [_jsxs("select", { className: "input", value: stateFilter, onChange: (e) => setStateFilter(e.target.value), children: [_jsx("option", { value: "", children: "All states" }), ["running", "completed", "failed", "stopped", "paused"].map((s) => (_jsx("option", { value: s, children: s }, s)))] }), _jsx("input", { className: "input flex-1", placeholder: "filter task name", value: taskFilter, onChange: (e) => setTaskFilter(e.target.value) })] }), _jsx("div", { className: "panel p-0 overflow-hidden", children: _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase bg-spark-bg", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left px-3 py-2", children: "run id" }), _jsx("th", { className: "text-left", children: "task" }), _jsx("th", { className: "text-left", children: "state" }), _jsx("th", { className: "text-left", children: "started" }), _jsx("th", { className: "text-left", children: "iters" }), _jsx("th", { className: "text-left", children: "models" }), _jsx("th", { className: "text-left", children: "tools" }), _jsx("th", { className: "text-left", children: "error" })] }) }), _jsx("tbody", { children: (runs.data ?? []).map((r) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 px-3 font-mono text-xs", children: _jsx("a", { href: `/runs/${encodeURIComponent(r.run_id)}/replay`, className: "hover:text-spark-accent", children: r.run_id }) }), _jsx("td", { children: r.task_name }), _jsx("td", { children: _jsx("span", { className: `chip ${stateClass(r.state)}`, children: r.state }) }), _jsx("td", { children: formatRelative(r.started_at) }), _jsx("td", { children: r.iterations }), _jsx("td", { children: r.model_calls }), _jsx("td", { children: r.tool_calls }), _jsx("td", { className: "text-spark-danger text-xs max-w-sm truncate", children: r.error ?? "" })] }, r.run_id))) })] }) })] }));
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
