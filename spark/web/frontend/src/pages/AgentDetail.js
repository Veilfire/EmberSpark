import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { toast } from "sonner";
import { Activity, Brain, Check, Coins, Heart, MessageSquare, Play, Shield, User2, X, Zap, } from "lucide-react";
import { api } from "../lib/api";
import { formatTimestamp } from "../lib/utils";
import { ModelPicker, PROVIDER_SECRET } from "../components/ModelPicker";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { HealthDot, StatCard } from "../components/primitives";
export default function AgentDetail() {
    const { agent_name } = useParams();
    const qc = useQueryClient();
    const [showProviderModal, setShowProviderModal] = useState(false);
    const [providerType, setProviderType] = useState("");
    const [providerModel, setProviderModel] = useState("");
    const [providerTemp, setProviderTemp] = useState(0.2);
    const [providerBaseUrl, setProviderBaseUrl] = useState("");
    const [saving, setSaving] = useState(false);
    const detail = useQuery({
        queryKey: ["agent-detail", agent_name],
        queryFn: () => api.get(`/api/scheduler/agents/${encodeURIComponent(agent_name)}`),
        enabled: !!agent_name,
    });
    function openProviderModal() {
        if (!detail.data)
            return;
        const p = detail.data.provider;
        setProviderType(p.type || "anthropic");
        setProviderModel(p.model || "");
        setProviderTemp(p.temperature ?? 0.2);
        setProviderBaseUrl(p.base_url || "");
        setShowProviderModal(true);
    }
    async function toggleSharing(patch) {
        if (!agent_name)
            return;
        const current = detail.data?.memory.sharing ?? {
            read_global: false,
            write_global: false,
            max_cross_scope_sensitivity: "moderate",
        };
        const next = { ...current, ...patch };
        try {
            await api.put(`/api/scheduler/agents/${encodeURIComponent(agent_name)}/memory-sharing`, next);
            toast.success("Memory sharing updated");
            qc.invalidateQueries({ queryKey: ["agent-detail", agent_name] });
        }
        catch (err) {
            toast.error(`Update failed: ${err}`);
        }
    }
    async function setLtm(patch) {
        if (!agent_name)
            return;
        const body = {
            enabled: patch.enabled ?? detail.data?.memory.long_term_memory ?? false,
            namespace: patch.namespace ?? detail.data?.memory.namespace ?? agent_name,
            collection: patch.collection ?? detail.data?.memory.collection ?? agent_name,
        };
        try {
            await api.put(`/api/scheduler/agents/${encodeURIComponent(agent_name)}/long-term-memory`, body);
            toast.success(body.enabled ? "Long-term memory enabled" : "Long-term memory disabled");
            qc.invalidateQueries({ queryKey: ["agent-detail", agent_name] });
        }
        catch (err) {
            toast.error(`Update failed: ${err}`);
        }
    }
    async function saveProvider() {
        if (!agent_name)
            return;
        setSaving(true);
        try {
            await api.put(`/api/scheduler/agents/${encodeURIComponent(agent_name)}/provider`, {
                type: providerType,
                model: providerModel,
                api_key_ref: PROVIDER_SECRET[providerType] || null,
                base_url: providerType === "ollama"
                    ? providerBaseUrl || "http://localhost:11434"
                    : providerBaseUrl || null,
                temperature: providerTemp,
            });
            toast.success("Provider updated");
            setShowProviderModal(false);
            qc.invalidateQueries({ queryKey: ["agent-detail", agent_name] });
        }
        catch (err) {
            toast.error(`Save failed: ${err}`);
        }
        finally {
            setSaving(false);
        }
    }
    if (!agent_name)
        return null;
    if (detail.isLoading) {
        return _jsx("div", { className: "p-6 text-spark-muted", children: "Loading agent\u2026" });
    }
    if (detail.isError) {
        return (_jsxs("div", { className: "p-6 text-red-400", children: ["Failed to load agent: ", detail.error.message] }));
    }
    if (!detail.data)
        return null;
    const d = detail.data;
    const allHealthy = d.health.sandbox_ok && d.health.provider_key_available;
    async function triggerRun() {
        if (!d.tasks || d.tasks.length === 0) {
            toast.error("No task configured for this agent");
            return;
        }
        try {
            await api.post("/api/scheduler/trigger", {
                task_name: d.tasks[0].name,
                agent_name: d.name,
            });
            toast.success(`Triggered ${d.tasks[0].name}`);
        }
        catch (err) {
            toast.error(`Trigger failed: ${err}`);
        }
    }
    return (_jsxs("div", { className: "space-y-6", children: [_jsx(PageHeader, { icon: _jsx(HealthDot, { ok: allHealthy, size: "md", pulse: allHealthy }), title: d.name, subtitle: d.description, breadcrumbs: [
                    { label: "Agents", to: "/agents" },
                    { label: d.name },
                ], actions: _jsxs(_Fragment, { children: [_jsxs(Link, { to: "/chat", className: "btn", title: "Open in chat", children: [_jsx(MessageSquare, { className: "w-4 h-4 mr-1 inline" }), " Chat"] }), _jsxs("button", { className: "btn", onClick: triggerRun, title: "Run now", children: [_jsx(Play, { className: "w-4 h-4 mr-1 inline" }), " Run now"] }), _jsxs("button", { className: "btn btn-primary", onClick: openProviderModal, title: "Change provider", children: [_jsx(Zap, { className: "w-4 h-4 mr-1 inline" }), " Provider"] })] }) }), _jsxs("section", { className: "grid grid-cols-2 md:grid-cols-5 gap-4", children: [_jsx(StatCard, { label: "Runs (7d)", value: d.run_stats.total_7d, sub: `${d.run_stats.completed_7d} ok · ${d.run_stats.failed_7d} failed` }), _jsx(StatCard, { label: "Success rate", value: d.run_stats.success_rate_7d != null
                            ? `${(d.run_stats.success_rate_7d * 100).toFixed(0)}%`
                            : "—", tone: d.run_stats.success_rate_7d != null
                            ? d.run_stats.success_rate_7d > 0.8
                                ? "good"
                                : d.run_stats.success_rate_7d < 0.5
                                    ? "danger"
                                    : "warn"
                            : "default" }), _jsx(StatCard, { label: "Cost (7d)", value: `$${d.cost_7d_usd.toFixed(4)}` }), _jsx(StatCard, { label: "Tokens (7d)", value: d.tokens_7d.toLocaleString() }), _jsx(StatCard, { label: "Memories", value: d.memory_count, sub: `${d.playbook_count} playbooks` })] }), _jsxs("div", { className: "grid grid-cols-1 lg:grid-cols-2 gap-4", children: [_jsxs("section", { className: "panel p-4 space-y-2", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Heart, { className: "w-4 h-4" }), " Health"] }), _jsxs("div", { className: "space-y-1 text-sm", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(HealthDot, { ok: d.health.provider_key_available }), _jsxs("span", { children: ["Provider key", " ", _jsx("code", { className: "font-mono text-xs", children: d.provider.api_key_ref ?? "none" })] }), d.health.provider_key_available ? (_jsx(Check, { className: "w-3 h-3 text-spark-good" })) : (_jsxs("span", { className: "text-spark-danger text-xs", children: ["missing \u2014", " ", _jsx(Link, { to: "/provider", className: "underline", children: "set it" })] }))] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(HealthDot, { ok: d.health.sandbox_ok }), _jsxs("span", { children: ["Sandbox: ", d.health.sandbox_backend] })] })] })] }), _jsxs("section", { className: "panel p-4 space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Zap, { className: "w-4 h-4" }), " Provider"] }), _jsx("button", { className: "btn", onClick: openProviderModal, children: "Change" })] }), _jsxs("dl", { className: "text-sm space-y-1", children: [_jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-24", children: "Type" }), _jsx("dd", { className: "capitalize", children: d.provider.type })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-24", children: "Model" }), _jsx("dd", { className: "font-mono text-xs", children: d.provider.model })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-24", children: "Temperature" }), _jsx("dd", { children: d.provider.temperature })] }), d.provider.base_url && (_jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-24", children: "Base URL" }), _jsx("dd", { className: "font-mono text-xs", children: d.provider.base_url })] }))] })] }), _jsxs("section", { className: "panel p-4 space-y-2", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(User2, { className: "w-4 h-4" }), " Persona"] }), d.persona ? (_jsxs("div", { className: "text-sm", children: [_jsx("p", { className: "font-mono", children: d.persona.name }), d.persona.tone && (_jsxs("p", { className: "text-spark-muted text-xs", children: ["Tone: ", d.persona.tone] })), _jsx(Link, { to: "/persona", className: "text-spark-accent text-xs underline", children: "Edit persona" })] })) : (_jsx("p", { className: "text-spark-muted text-sm", children: "No active persona" }))] }), _jsxs("section", { className: "panel p-4 space-y-2", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Shield, { className: "w-4 h-4" }), " Plugins & Permissions"] }), _jsxs("div", { className: "text-sm space-y-2", children: [_jsxs("div", { children: [_jsxs("span", { className: "text-xs uppercase text-spark-muted", children: ["Plugins (", d.plugins.length, ")"] }), _jsx("div", { className: "flex flex-wrap gap-1 mt-1", children: d.plugins.map((p) => (_jsx("span", { className: "chip font-mono text-xs", children: p }, p))) })] }), _jsxs("div", { children: [_jsxs("span", { className: "text-xs uppercase text-spark-muted", children: ["Grants (", d.grants.length, ")"] }), _jsx("div", { className: "flex flex-wrap gap-1 mt-1", children: d.grants.map((g) => (_jsx("span", { className: "chip font-mono text-xs", children: g }, g))) })] })] })] }), _jsxs("section", { className: "panel p-4 space-y-2", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Coins, { className: "w-4 h-4" }), " Budgets"] }), _jsxs("dl", { className: "text-sm grid grid-cols-2 gap-1", children: [_jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs", children: "Iterations" }), _jsx("dd", { children: d.budgets.max_iterations })] }), _jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs", children: "Model calls" }), _jsx("dd", { children: d.budgets.max_model_calls })] }), _jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs", children: "Tool calls" }), _jsx("dd", { children: d.budgets.max_tool_calls })] }), _jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs", children: "Runtime" }), _jsxs("dd", { children: [d.budgets.max_runtime_seconds, "s"] })] })] })] }), _jsxs("section", { className: "panel p-4 space-y-2", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Brain, { className: "w-4 h-4" }), " Memory & Sandbox"] }), _jsxs("dl", { className: "text-sm space-y-1", children: [_jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-32", children: "Task memory" }), _jsx("dd", { children: d.memory.task_memory ? "on" : "off" })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-32", children: "Session memory" }), _jsx("dd", { children: d.memory.session_memory ? "on" : "off" })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-32", children: "Long-term" }), _jsx("dd", { children: d.memory.long_term_memory
                                                    ? `on (${d.memory.namespace})`
                                                    : "off" })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-32", children: "Sandbox" }), _jsx("dd", { children: d.sandbox.enabled
                                                    ? `${d.sandbox.backend} · ${d.sandbox.cpu_seconds}s CPU · ${d.sandbox.memory_mb}MB`
                                                    : "disabled" })] })] })] }), _jsxs("section", { className: "panel p-4 space-y-4", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Brain, { className: "w-4 h-4" }), " Memory"] }), _jsxs("div", { className: "space-y-2 pb-3 border-b border-spark-border", children: [_jsxs("label", { className: "flex items-start gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: d.memory.long_term_memory, onChange: (e) => setLtm({ enabled: e.target.checked }) }), _jsxs("div", { children: [_jsx("div", { children: "Enable long-term memory" }), _jsx("div", { className: "text-xs text-spark-muted", children: "Persists distilled facts to Chroma across sessions. Retrieval injects relevant memories into every run." })] })] }), d.memory.long_term_memory && (_jsxs("div", { className: "ml-6 text-xs text-spark-muted space-y-0.5", children: [_jsxs("div", { children: ["Namespace:", " ", _jsx("code", { className: "font-mono", children: d.memory.namespace ?? agent_name })] }), _jsxs("div", { children: ["Collection:", " ", _jsx("code", { className: "font-mono", children: d.memory.collection ?? agent_name })] })] }))] }), _jsxs("div", { className: d.memory.long_term_memory
                                    ? "space-y-3"
                                    : "space-y-3 opacity-40 pointer-events-none select-none", "aria-disabled": !d.memory.long_term_memory, children: [_jsx("div", { className: "text-xs uppercase tracking-wide text-spark-muted", children: "Cross-agent sharing" }), !d.memory.long_term_memory && (_jsx("p", { className: "text-xs text-spark-muted -mt-2", children: "Enable long-term memory above to configure sharing." })), _jsx("p", { className: "text-xs text-spark-muted", children: "Control cross-agent memory access. All cross-scope reads and writes are audited at elevated severity." }), _jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: !!d.memory.sharing?.read_global, onChange: (e) => toggleSharing({ read_global: e.target.checked }), disabled: !d.memory.long_term_memory }), _jsxs("div", { children: [_jsx("div", { children: "Read from global pool" }), _jsx("div", { className: "text-xs text-spark-muted", children: "Retrieval augments this agent's private memory with shared memories from other agents." })] })] }), _jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: !!d.memory.sharing?.write_global, onChange: (e) => toggleSharing({ write_global: e.target.checked }), disabled: !d.memory.long_term_memory }), _jsxs("div", { children: [_jsx("div", { children: "Promote own memories to global" }), _jsx("div", { className: "text-xs text-spark-muted", children: "Operator can promote this agent's memories up to the sensitivity cap below." })] })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Max cross-scope sensitivity" }), _jsxs("select", { className: "input w-full", value: d.memory.sharing?.max_cross_scope_sensitivity ?? "moderate", onChange: (e) => toggleSharing({ max_cross_scope_sensitivity: e.target.value }), disabled: !d.memory.long_term_memory, children: [_jsx("option", { value: "low", children: "low \u2014 only non-sensitive" }), _jsx("option", { value: "moderate", children: "moderate \u2014 default" }), _jsx("option", { value: "high", children: "high \u2014 careful" })] }), _jsxs("p", { className: "text-xs text-spark-muted mt-1", children: ["Memories above this level never cross the agent boundary.", _jsx("code", { className: "font-mono ml-1", children: "restricted" }), " is always blocked."] })] })] })] })] }), _jsxs("section", { className: "panel p-4", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2 mb-3", children: [_jsx(Activity, { className: "w-4 h-4" }), " Tasks (", d.tasks.length, ")"] }), d.tasks.length > 0 ? (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "Name" }), _jsx("th", { className: "text-left", children: "Mode" }), _jsx("th", { className: "text-left", children: "State" }), _jsx("th", { className: "text-left", children: "Updated" })] }) }), _jsx("tbody", { children: d.tasks.map((t) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono", children: t.name }), _jsx("td", { children: _jsx("span", { className: "chip", children: t.mode }) }), _jsx("td", { children: t.state }), _jsx("td", { children: formatTimestamp(t.updated_at) })] }, t.name))) })] })) : (_jsx("p", { className: "text-spark-muted text-sm", children: "No tasks configured." }))] }), _jsx(Modal, { open: showProviderModal, onClose: () => setShowProviderModal(false), children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-auto p-6 space-y-4 shadow-2xl", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("h3", { className: "text-lg font-bold", children: [d.name, " \u2014 Set Provider / Model"] }), _jsx("button", { className: "btn-icon", onClick: () => setShowProviderModal(false), "aria-label": "Close", children: _jsx(X, { className: "w-5 h-5" }) })] }), _jsx(ModelPicker, { provider: providerType, model: providerModel, temperature: providerTemp, baseUrl: providerBaseUrl, onProviderChange: (p) => {
                                setProviderType(p);
                                setProviderModel("");
                            }, onModelChange: setProviderModel, onTemperatureChange: setProviderTemp, onBaseUrlChange: setProviderBaseUrl }), _jsxs("div", { className: "flex justify-end gap-2 pt-2 border-t border-spark-border", children: [_jsx("button", { className: "btn", onClick: () => setShowProviderModal(false), children: "Cancel" }), _jsx("button", { className: "btn btn-primary", disabled: saving || !providerModel, onClick: saveProvider, children: saving ? "Saving…" : "Save" })] })] }) })] }));
}
