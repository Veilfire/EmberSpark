import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { formatUsd } from "../lib/utils";
export default function CostDashboard() {
    const [period, setPeriod] = useState("day");
    const client = useQueryClient();
    const window = useQuery({
        queryKey: ["cost", period],
        queryFn: () => api.get(`/api/cost/window/${period}`),
    });
    const budgets = useQuery({
        queryKey: ["budgets"],
        queryFn: () => api.get("/api/cost/budgets"),
    });
    const events = useQuery({
        queryKey: ["cost-events"],
        queryFn: () => api.get("/api/cost/events?limit=50"),
    });
    const create = useMutation({
        mutationFn: (body) => api.post("/api/cost/budgets", body),
        onSuccess: () => {
            client.invalidateQueries({ queryKey: ["budgets"] });
        },
    });
    async function onSubmit(e) {
        e.preventDefault();
        const data = new FormData(e.currentTarget);
        create.mutate({
            budget_id: String(data.get("budget_id")),
            scope: data.get("scope"),
            scope_key: String(data.get("scope_key")),
            period: data.get("period"),
            limit_usd: Number(data.get("limit_usd")),
            soft_alert_usd: Number(data.get("soft_alert_usd") || 0),
            hard_stop: data.get("hard_stop") === "on",
        });
        e.currentTarget.reset();
    }
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Cost & Budgets" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Token spend by provider, agent, model." })] }), _jsx("div", { className: "flex gap-2", children: ["day", "week", "month"].map((p) => (_jsx("button", { className: `btn ${p === period ? "btn-primary" : ""}`, onClick: () => setPeriod(p), children: p }, p))) }), _jsxs("section", { className: "grid grid-cols-1 md:grid-cols-3 gap-4", children: [_jsx(Breakdown, { title: "by provider", data: window.data?.by_provider }), _jsx(Breakdown, { title: "by agent", data: window.data?.by_agent }), _jsx(Breakdown, { title: "by model", data: window.data?.by_model })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Budgets" }), _jsxs("table", { className: "w-full text-sm mb-4", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "id" }), _jsx("th", { className: "text-left", children: "scope" }), _jsx("th", { className: "text-left", children: "key" }), _jsx("th", { className: "text-left", children: "period" }), _jsx("th", { className: "text-left", children: "limit" }), _jsx("th", { className: "text-left", children: "alert" }), _jsx("th", { className: "text-left", children: "hard stop" })] }) }), _jsx("tbody", { children: (budgets.data ?? []).map((b) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono", children: b.budget_id }), _jsx("td", { children: b.scope }), _jsx("td", { children: b.scope_key }), _jsx("td", { children: b.period }), _jsx("td", { children: formatUsd(b.limit_usd) }), _jsx("td", { children: formatUsd(b.soft_alert_usd) }), _jsx("td", { children: b.hard_stop ? "yes" : "no" })] }, b.budget_id))) })] }), _jsxs("form", { onSubmit: onSubmit, className: "grid grid-cols-3 md:grid-cols-7 gap-2 text-sm", children: [_jsx("input", { className: "input", name: "budget_id", placeholder: "budget id", required: true }), _jsxs("select", { className: "input", name: "scope", defaultValue: "agent", children: [_jsx("option", { value: "global", children: "global" }), _jsx("option", { value: "agent", children: "agent" }), _jsx("option", { value: "provider", children: "provider" })] }), _jsx("input", { className: "input", name: "scope_key", placeholder: "scope key (*)", defaultValue: "*" }), _jsxs("select", { className: "input", name: "period", defaultValue: "monthly", children: [_jsx("option", { value: "daily", children: "daily" }), _jsx("option", { value: "weekly", children: "weekly" }), _jsx("option", { value: "monthly", children: "monthly" })] }), _jsx("input", { className: "input", type: "number", step: "0.01", name: "limit_usd", placeholder: "limit", required: true }), _jsx("input", { className: "input", type: "number", step: "0.01", name: "soft_alert_usd", placeholder: "alert" }), _jsxs("label", { className: "flex items-center gap-1 text-xs", children: [_jsx("input", { type: "checkbox", name: "hard_stop", defaultChecked: true }), " hard stop"] }), _jsx("button", { className: "btn btn-primary col-span-7 md:col-span-1", type: "submit", children: "Create" })] })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Recent cost events" }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "run" }), _jsx("th", { className: "text-left", children: "agent" }), _jsx("th", { className: "text-left", children: "provider" }), _jsx("th", { className: "text-left", children: "model" }), _jsx("th", { className: "text-left", children: "tokens" }), _jsx("th", { className: "text-left", children: "cost" })] }) }), _jsx("tbody", { children: (events.data ?? []).map((e) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono text-xs", children: e.run_id }), _jsx("td", { children: e.agent }), _jsx("td", { children: e.provider }), _jsx("td", { children: e.model }), _jsx("td", { children: e.total_tokens.toLocaleString() }), _jsx("td", { children: formatUsd(e.total_usd) })] }, e.run_id))) })] })] })] }));
}
function Breakdown({ title, data, }) {
    const entries = Object.entries(data ?? {}).sort((a, b) => b[1] - a[1]);
    return (_jsxs("div", { className: "panel p-4", children: [_jsx("div", { className: "label", children: title }), _jsx("div", { className: "mt-2 space-y-1", children: entries.length === 0 ? (_jsx("div", { className: "text-spark-muted text-sm", children: "no data" })) : (entries.map(([k, v]) => (_jsxs("div", { className: "flex items-center justify-between text-sm", children: [_jsx("span", { className: "truncate", children: k }), _jsx("span", { className: "font-mono", children: formatUsd(v) })] }, k)))) })] }));
}
