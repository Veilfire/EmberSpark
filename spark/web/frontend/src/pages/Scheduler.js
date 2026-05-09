import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Calendar, Pause, Pencil, Play, Square, X, Zap } from "lucide-react";
import { api } from "../lib/api";
import { ModelPicker, PROVIDER_SECRET } from "../components/ModelPicker";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { RelativeTime, Timestamp } from "../components/RelativeTime";
import { CronPreview } from "../components/CronPreview";
import { CronBuilder } from "../components/CronBuilder";
import { EmptyState } from "../components/primitives";
export default function Scheduler() {
    const qc = useQueryClient();
    const [editingAgent, setEditingAgent] = useState(null);
    const [editYaml, setEditYaml] = useState(false);
    const [yamlText, setYamlText] = useState("");
    const [providerType, setProviderType] = useState("anthropic");
    const [providerModel, setProviderModel] = useState("");
    const [providerTemp, setProviderTemp] = useState(0.2);
    const [providerBaseUrl, setProviderBaseUrl] = useState("");
    const [saving, setSaving] = useState(false);
    const [showCreateTask, setShowCreateTask] = useState(false);
    const [editingTask, setEditingTask] = useState(null);
    async function openEditTask(taskName) {
        try {
            const full = await api.get(`/api/scheduler/tasks/${encodeURIComponent(taskName)}/full`);
            setEditingTask(full);
            setShowCreateTask(true);
        }
        catch (err) {
            toast.error(`Failed to load task: ${err}`);
        }
    }
    const agents = useQuery({
        queryKey: ["agents"],
        queryFn: () => api.get("/api/scheduler/agents"),
    });
    const tasks = useQuery({
        queryKey: ["tasks"],
        queryFn: () => api.get("/api/scheduler/tasks"),
    });
    const schedules = useQuery({
        queryKey: ["schedules"],
        queryFn: () => api.get("/api/scheduler/schedules"),
    });
    async function openProviderModal(agentName) {
        try {
            const resp = await api.get(`/api/scheduler/agents/${encodeURIComponent(agentName)}/yaml`);
            setProviderType(resp.provider.type || "anthropic");
            setProviderModel(resp.provider.model || "");
            setProviderTemp(resp.provider.temperature ?? 0.2);
            setProviderBaseUrl(resp.provider.base_url || "");
            setYamlText(resp.yaml);
            setEditYaml(false);
            setEditingAgent(agentName);
        }
        catch (err) {
            toast.error(`Failed to load agent: ${err}`);
        }
    }
    async function saveProvider() {
        if (!editingAgent)
            return;
        setSaving(true);
        try {
            if (editYaml) {
                await api.put(`/api/scheduler/agents/${encodeURIComponent(editingAgent)}/yaml`, { yaml: yamlText });
            }
            else {
                if (!providerModel) {
                    toast.error("Select or enter a model");
                    setSaving(false);
                    return;
                }
                await api.put(`/api/scheduler/agents/${encodeURIComponent(editingAgent)}/provider`, {
                    type: providerType,
                    model: providerModel,
                    api_key_ref: PROVIDER_SECRET[providerType] || null,
                    base_url: providerType === "ollama" ? (providerBaseUrl || "http://localhost:11434") : (providerBaseUrl || null),
                    temperature: providerTemp,
                });
            }
            toast.success("Provider updated");
            setEditingAgent(null);
            qc.invalidateQueries({ queryKey: ["agents"] });
        }
        catch (err) {
            toast.error(`Save failed: ${err}`);
        }
        finally {
            setSaving(false);
        }
    }
    async function trigger(taskName, agentName) {
        await api.post("/api/scheduler/trigger", { task_name: taskName, agent_name: agentName });
        tasks.refetch();
    }
    async function pause(taskName) {
        await api.post(`/api/scheduler/tasks/${encodeURIComponent(taskName)}/pause`);
        tasks.refetch();
    }
    async function stop(taskName) {
        await api.post(`/api/scheduler/tasks/${encodeURIComponent(taskName)}/stop`);
        tasks.refetch();
    }
    return (_jsxs("div", { className: "space-y-6", children: [_jsx(PageHeader, { icon: _jsx(Calendar, { className: "w-6 h-6" }), title: "Scheduler", subtitle: "Agents, tasks, and schedules." }), _jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("h3", { className: "font-semibold mb-3", children: ["Agents (", agents.data?.length ?? 0, ")"] }), (agents.data ?? []).length === 0 ? (_jsx(EmptyState, { title: "No agents", description: "Install a template or create an agent to see it here.", action: { label: "Browse Templates", to: "/templates" } })) : (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left pb-2", children: "Name" }), _jsx("th", { className: "text-left pb-2", children: "Description" }), _jsx("th", { className: "text-left pb-2", children: "Updated" }), _jsx("th", { className: "pb-2" })] }) }), _jsx("tbody", { children: (agents.data ?? []).map((a) => (_jsxs("tr", { className: "border-t border-spark-border hover:bg-spark-border/20 transition", children: [_jsx("td", { className: "py-1.5 font-mono", children: _jsx(Link, { to: `/agents/${encodeURIComponent(a.name)}`, className: "text-spark-accent hover:underline", children: a.name }) }), _jsx("td", { className: "text-spark-muted", children: a.description }), _jsx("td", { children: _jsx(Timestamp, { ts: a.updated_at }) }), _jsx("td", { className: "text-right", children: _jsxs("button", { className: "btn", onClick: () => openProviderModal(a.name), children: [_jsx(Zap, { className: "w-3 h-3 mr-1 inline" }), " Set Provider"] }) })] }, a.name))) })] }))] }), _jsx(Modal, { open: !!editingAgent, onClose: () => setEditingAgent(null), children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-auto p-6 space-y-4 shadow-2xl", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("h3", { className: "text-lg font-bold", children: [editingAgent, " \u2014 ", editYaml ? "Edit YAML" : "Set Provider / Model"] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { className: "btn", onClick: () => setEditYaml(!editYaml), children: editYaml ? "Provider picker" : "Edit YAML" }), _jsx("button", { className: "btn-icon", onClick: () => setEditingAgent(null), "aria-label": "Close", children: _jsx(X, { className: "w-5 h-5" }) })] })] }), editYaml ? (_jsx("textarea", { className: "input w-full font-mono text-xs", rows: 24, value: yamlText, onChange: (e) => setYamlText(e.target.value) })) : (_jsx(ModelPicker, { provider: providerType, model: providerModel, temperature: providerTemp, baseUrl: providerBaseUrl, onProviderChange: (p) => {
                                setProviderType(p);
                                setProviderModel("");
                            }, onModelChange: setProviderModel, onTemperatureChange: setProviderTemp, onBaseUrlChange: setProviderBaseUrl })), _jsxs("div", { className: "flex justify-end gap-2 pt-2 border-t border-spark-border", children: [_jsx("button", { className: "btn", onClick: () => setEditingAgent(null), children: "Cancel" }), _jsx("button", { className: "btn btn-primary", disabled: saving, onClick: saveProvider, children: saving ? "Saving…" : "Save" })] })] }) }), _jsx(TaskCreatorModal, { open: showCreateTask, onClose: () => {
                    setShowCreateTask(false);
                    setEditingTask(null);
                }, agents: agents.data ?? [], editing: editingTask, onCreated: () => {
                    qc.invalidateQueries({ queryKey: ["tasks"] });
                    qc.invalidateQueries({ queryKey: ["schedules"] });
                    setShowCreateTask(false);
                    setEditingTask(null);
                } }), _jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsxs("h3", { className: "font-semibold", children: ["Tasks (", tasks.data?.length ?? 0, ")"] }), _jsx("button", { className: "btn btn-primary text-xs", onClick: () => setShowCreateTask(true), children: "+ New task" })] }), (tasks.data ?? []).length === 0 ? (_jsx("p", { className: "text-sm text-spark-muted py-4 text-center", children: "No tasks configured." })) : (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left pb-2", children: "Name" }), _jsx("th", { className: "text-left pb-2", children: "Agent" }), _jsx("th", { className: "text-left pb-2", children: "Mode" }), _jsx("th", { className: "text-left pb-2", children: "State" }), _jsx("th", { className: "pb-2" })] }) }), _jsx("tbody", { children: (tasks.data ?? []).map((t) => (_jsxs("tr", { className: "border-t border-spark-border hover:bg-spark-border/20", children: [_jsx("td", { className: "py-1.5 font-mono", children: t.name }), _jsx("td", { children: _jsx(Link, { to: `/agents/${encodeURIComponent(t.agent_name)}`, className: "text-spark-accent hover:underline", children: t.agent_name }) }), _jsx("td", { children: _jsx("span", { className: "chip", children: t.mode }) }), _jsx("td", { children: _jsx("span", { className: `chip ${stateClass(t.state)}`, children: t.state }) }), _jsxs("td", { className: "text-right space-x-1", children: [_jsx("button", { className: "btn-icon", onClick: () => openEditTask(t.name), title: "Edit", children: _jsx(Pencil, { className: "w-4 h-4" }) }), _jsx("button", { className: "btn-icon", onClick: () => trigger(t.name, t.agent_name), title: "Trigger now", children: _jsx(Play, { className: "w-4 h-4" }) }), _jsx("button", { className: "btn-icon", onClick: () => pause(t.name), title: "Pause", children: _jsx(Pause, { className: "w-4 h-4" }) }), _jsx("button", { className: "btn-icon hover:text-spark-danger", onClick: () => stop(t.name), title: "Stop", children: _jsx(Square, { className: "w-4 h-4" }) })] })] }, t.name))) })] }))] }), _jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("h3", { className: "font-semibold mb-3", children: ["Schedules (", schedules.data?.length ?? 0, ")"] }), (schedules.data ?? []).length === 0 ? (_jsx("p", { className: "text-sm text-spark-muted py-4 text-center", children: "No schedules configured." })) : (_jsx("div", { className: "space-y-2", children: (schedules.data ?? []).map((s) => (_jsxs("div", { className: "border border-spark-border rounded-md p-3 hover:bg-spark-border/20 transition", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("span", { className: "font-mono text-sm", children: s.task_name }), _jsx("span", { className: "chip text-xs", children: s.trigger_type }), _jsx("code", { className: "font-mono text-xs text-spark-muted", children: s.trigger_expression }), _jsx("span", { className: "text-xs text-spark-muted", children: s.timezone })] }), _jsx("span", { className: `chip text-xs ${s.enabled ? "chip-good" : "chip-danger"}`, children: s.enabled ? "enabled" : "disabled" })] }), s.trigger_type === "cron" && (_jsx("div", { className: "mt-2 pl-1 border-l-2 border-spark-accent/30 ml-1", children: _jsx(CronPreview, { expr: s.trigger_expression }) }))] }, s.task_name))) }))] }), _jsx(TriggersPanel, {})] }));
}
function stateClass(state) {
    if (state === "completed" || state === "running")
        return "chip-good";
    if (state === "failed" || state === "dlq")
        return "chip-danger";
    if (state === "paused")
        return "chip-warn";
    return "";
}
const SLUG_RE = /^[a-z0-9][a-z0-9._-]{0,127}$/;
/** Convert an ISO-8601 string to the `<input type="datetime-local">`
 *  format ``YYYY-MM-DDTHH:MM`` in the browser's local timezone. */
function toLocalDateTimeInput(iso) {
    if (!iso)
        return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime()))
        return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function TaskCreatorModal({ open, onClose, agents, onCreated, editing = null, }) {
    const isEdit = editing !== null;
    const [name, setName] = useState("");
    const [agent, setAgent] = useState("");
    const [mode, setMode] = useState("one_shot");
    const [objective, setObjective] = useState("");
    const [inputs, setInputs] = useState([]);
    // Schedule fields. Visibility / requirement is mode-driven.
    const [scheduleType, setScheduleType] = useState("cron");
    const [scheduleExpr, setScheduleExpr] = useState("0 8 * * 1");
    const [scheduleTz, setScheduleTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    const [startAt, setStartAt] = useState("");
    const [endAt, setEndAt] = useState("");
    const [delayedOneShot, setDelayedOneShot] = useState(false);
    // Optional collapsibles.
    const [showBudgets, setShowBudgets] = useState(false);
    const [showForensic, setShowForensic] = useState(false);
    const [budgetRuntime, setBudgetRuntime] = useState("");
    const [budgetModelCalls, setBudgetModelCalls] = useState("");
    const [budgetToolCalls, setBudgetToolCalls] = useState("");
    const [budgetTokens, setBudgetTokens] = useState("");
    const [forensicEnabled, setForensicEnabled] = useState(false);
    const [forensicReason, setForensicReason] = useState("");
    const [forensicTtl, setForensicTtl] = useState("168");
    const [autoStart, setAutoStart] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);
    const [preview, setPreview] = useState([]);
    // Hydrate from `editing` when the modal opens. Re-runs whenever the
    // editing target changes (so reopening on a different task pre-fills
    // the right values).
    useEffect(() => {
        if (!open)
            return;
        if (editing) {
            setName(editing.name);
            setAgent(editing.agent);
            // ``event`` mode tasks aren't editable here — those fire from
            // external triggers and have no schedule. Coerce to one_shot for
            // the modal so the operator at least sees a sane shape.
            setMode((editing.mode === "event" ? "one_shot" : editing.mode));
            setObjective(editing.objective);
            setInputs(Object.entries(editing.inputs ?? {}).map(([k, v]) => ({
                key: k,
                value: String(v),
            })));
            if (editing.schedule) {
                setScheduleType(editing.schedule.type);
                setScheduleExpr(editing.schedule.expression);
                setScheduleTz(editing.schedule.timezone);
                setStartAt(toLocalDateTimeInput(editing.schedule.start_at));
                setEndAt(toLocalDateTimeInput(editing.schedule.end_at));
                // For one-shot edits with a saved start_at, surface the
                // "Schedule for later" toggle so the operator can see/clear it.
                if (editing.mode === "one_shot" && editing.schedule.start_at) {
                    setDelayedOneShot(true);
                }
            }
            else {
                setScheduleType("cron");
                setScheduleExpr("0 8 * * 1");
                setStartAt("");
                setEndAt("");
                setDelayedOneShot(false);
            }
            const b = editing.budgets;
            const hasBudget = b.max_runtime_seconds != null ||
                b.max_model_calls != null ||
                b.max_tool_calls != null ||
                b.max_tokens_per_run != null;
            setShowBudgets(hasBudget);
            setBudgetRuntime(b.max_runtime_seconds?.toString() ?? "");
            setBudgetModelCalls(b.max_model_calls?.toString() ?? "");
            setBudgetToolCalls(b.max_tool_calls?.toString() ?? "");
            setBudgetTokens(b.max_tokens_per_run?.toString() ?? "");
            setShowForensic(editing.forensic.enabled);
            setForensicEnabled(editing.forensic.enabled);
            setForensicReason(editing.forensic.reason);
            setForensicTtl(editing.forensic.ttl_hours.toString());
            // Auto-start has no meaning when editing — task already exists.
            setAutoStart(false);
            setError(null);
            setPreview([]);
        }
    }, [open, editing]);
    // Mode-derived schedule visibility.
    const scheduleVisible = mode === "recurring" || mode === "perpetual" || (mode === "one_shot" && delayedOneShot);
    const startRequired = mode === "recurring" || mode === "perpetual";
    const endRequired = mode === "recurring";
    const endAllowed = mode === "recurring";
    function reset() {
        setName("");
        setAgent("");
        setMode("one_shot");
        setObjective("");
        setInputs([]);
        setScheduleType("cron");
        setScheduleExpr("0 8 * * 1");
        setStartAt("");
        setEndAt("");
        setDelayedOneShot(false);
        setShowBudgets(false);
        setShowForensic(false);
        setBudgetRuntime("");
        setBudgetModelCalls("");
        setBudgetToolCalls("");
        setBudgetTokens("");
        setForensicEnabled(false);
        setForensicReason("");
        setForensicTtl("168");
        setAutoStart(false);
        setError(null);
        setPreview([]);
    }
    function localValidate() {
        if (!SLUG_RE.test(name)) {
            return "Name must be lowercase a-z0-9, start with a letter or digit, max 128 chars.";
        }
        if (!agent)
            return "Pick an agent.";
        if (!objective.trim())
            return "Objective is required.";
        if (mode === "recurring") {
            if (!startAt || !endAt) {
                return "Recurring tasks need both start_at and end_at.";
            }
            if (new Date(startAt).getTime() >= new Date(endAt).getTime()) {
                return "start_at must precede end_at.";
            }
        }
        if (mode === "perpetual" && !startAt) {
            return "Perpetual tasks need a start_at.";
        }
        if (mode === "perpetual" && endAt) {
            return "Perpetual tasks cannot have an end_at — use recurring for a finite window.";
        }
        if (mode === "one_shot" && delayedOneShot && !startAt) {
            return "Delayed one-shot needs a start_at.";
        }
        if (mode === "one_shot" && endAt) {
            return "One-shot tasks cannot have an end_at.";
        }
        if (scheduleVisible && scheduleType === "interval") {
            const s = parseInt(scheduleExpr, 10);
            if (!Number.isFinite(s) || s <= 0) {
                return "Interval schedule expression must be a positive integer second count.";
            }
        }
        if (forensicEnabled && !forensicReason.trim()) {
            return "Forensic enabled requires a reason.";
        }
        return null;
    }
    async function runPreview() {
        if (!scheduleVisible || mode === "one_shot") {
            setPreview([]);
            return;
        }
        try {
            const resp = await api.post("/api/scheduler/simulate", {
                schedule_type: scheduleType,
                expression: scheduleExpr,
                timezone: scheduleTz,
                horizon_hours: 168,
            });
            setPreview(resp.fires.slice(0, 5));
        }
        catch (err) {
            setPreview([]);
            setError(`Schedule preview failed: ${err.message ?? err}`);
        }
    }
    async function submit() {
        const validation = localValidate();
        if (validation) {
            setError(validation);
            return;
        }
        setError(null);
        setSubmitting(true);
        const payload = {
            name,
            agent,
            mode,
            objective,
            inputs: inputs.reduce((acc, { key, value }) => (key ? { ...acc, [key]: value } : acc), {}),
            auto_start: autoStart,
        };
        if (scheduleVisible) {
            payload.schedule = {
                type: scheduleType,
                expression: scheduleExpr,
                timezone: scheduleTz,
                start_at: startAt ? new Date(startAt).toISOString() : null,
                end_at: endAt && endAllowed ? new Date(endAt).toISOString() : null,
            };
        }
        if (showBudgets) {
            const budgets = {};
            if (budgetRuntime)
                budgets.max_runtime_seconds = parseInt(budgetRuntime, 10);
            if (budgetModelCalls)
                budgets.max_model_calls = parseInt(budgetModelCalls, 10);
            if (budgetToolCalls)
                budgets.max_tool_calls = parseInt(budgetToolCalls, 10);
            if (budgetTokens)
                budgets.max_tokens_per_run = parseInt(budgetTokens, 10);
            if (Object.keys(budgets).length > 0)
                payload.budgets = budgets;
        }
        if (showForensic && forensicEnabled) {
            payload.forensic = {
                enabled: true,
                reason: forensicReason,
                ttl_hours: parseInt(forensicTtl, 10),
            };
        }
        try {
            if (isEdit) {
                await api.put(`/api/scheduler/tasks/${encodeURIComponent(name)}`, payload);
                toast.success(`Task "${name}" updated`);
            }
            else {
                await api.post("/api/scheduler/tasks", payload);
                toast.success(`Task "${name}" created`);
            }
            reset();
            onCreated();
        }
        catch (err) {
            setError(err.message || (isEdit ? "Update failed" : "Create failed"));
        }
        finally {
            setSubmitting(false);
        }
    }
    return (_jsx(Modal, { open: open, onClose: onClose, children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[88vh] overflow-auto p-5 shadow-xl space-y-3", children: [_jsxs("div", { className: "flex items-start justify-between", children: [_jsxs("div", { children: [_jsx("h3", { className: "font-semibold text-lg", children: isEdit ? `Edit task: ${name}` : "Create task" }), _jsx("p", { className: "text-xs text-spark-muted", children: isEdit
                                        ? "Updates the task YAML on disk and reschedules. Refused while a run is in flight."
                                        : (_jsxs(_Fragment, { children: ["Writes", " ", _jsxs("code", { className: "font-mono", children: ["~/.spark/tasks/", name || "<name>", ".yaml"] }), " ", "and registers the task in the scheduler."] })) })] }), _jsx("button", { className: "btn-icon", onClick: () => {
                                reset();
                                onClose();
                            }, "aria-label": "Close", children: _jsx(X, { className: "w-4 h-4" }) })] }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Name" }), _jsx("input", { className: "input w-full font-mono", value: name, onChange: (e) => setName(e.target.value.toLowerCase()), placeholder: "research-digest", disabled: isEdit, title: isEdit ? "Renames are not supported — delete + recreate" : "" }), _jsx("span", { className: "text-[10px] text-spark-muted", children: isEdit
                                        ? "Renames are not supported — delete and recreate to change the name."
                                        : "Lowercase a-z0-9 . _ -" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Agent" }), _jsxs("select", { className: "input w-full", value: agent, onChange: (e) => setAgent(e.target.value), children: [_jsx("option", { value: "", children: "(select)" }), agents.map((a) => (_jsx("option", { value: a.name, children: a.name }, a.name)))] }), isEdit && editing && agent !== editing.agent && (_jsx("span", { className: "text-[10px] text-amber-400", children: "\u26A0 Changing the agent rebinds plugins, permissions, and memory namespace. Audited at elevated severity." }))] })] }), _jsxs("div", { children: [_jsx("span", { className: "label", children: "Mode" }), _jsx("div", { className: "flex gap-2 mt-1 text-sm", children: ["one_shot", "recurring", "perpetual"].map((m) => (_jsxs("label", { className: "flex items-center gap-1 cursor-pointer", children: [_jsx("input", { type: "radio", name: "mode", checked: mode === m, onChange: () => {
                                            setMode(m);
                                            // Reset constraints that no longer apply.
                                            if (m === "one_shot") {
                                                setEndAt("");
                                                setDelayedOneShot(false);
                                            }
                                            else if (m === "perpetual") {
                                                setEndAt("");
                                            }
                                        } }), _jsx("span", { className: "font-mono", children: m })] }, m))) }), _jsxs("p", { className: "text-[11px] text-spark-muted mt-1", children: [mode === "one_shot" &&
                                    "Runs once. Optionally schedule for later via the toggle below.", mode === "recurring" &&
                                    "Fires on cron/interval inside a finite window. Both start and end required.", mode === "perpetual" &&
                                    "Fires on cron/interval forever, starting at start_at."] })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Objective" }), _jsx("textarea", { className: "input w-full font-mono text-xs h-24", value: objective, onChange: (e) => setObjective(e.target.value), placeholder: "What should the agent do?" })] }), _jsxs("details", { children: [_jsxs("summary", { className: "text-xs text-spark-muted cursor-pointer", children: ["Inputs (", inputs.length, ")"] }), _jsxs("div", { className: "space-y-1 mt-2", children: [inputs.map((row, i) => (_jsxs("div", { className: "flex gap-1", children: [_jsx("input", { className: "input flex-1 text-xs font-mono", placeholder: "key", value: row.key, onChange: (e) => {
                                                const next = [...inputs];
                                                next[i] = { ...row, key: e.target.value };
                                                setInputs(next);
                                            } }), _jsx("input", { className: "input flex-1 text-xs", placeholder: "value", value: row.value, onChange: (e) => {
                                                const next = [...inputs];
                                                next[i] = { ...row, value: e.target.value };
                                                setInputs(next);
                                            } }), _jsx("button", { className: "btn-icon hover:text-spark-danger", onClick: () => setInputs(inputs.filter((_, j) => j !== i)), "aria-label": "Remove", children: _jsx(X, { className: "w-3.5 h-3.5" }) })] }, i))), _jsx("button", { className: "btn text-xs", onClick: () => setInputs([...inputs, { key: "", value: "" }]), children: "+ Add input" })] })] }), mode === "one_shot" && (_jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: delayedOneShot, onChange: (e) => setDelayedOneShot(e.target.checked) }), "Schedule for later"] })), scheduleVisible && (_jsxs("div", { className: "border border-spark-border rounded p-3 space-y-2", children: [_jsx("div", { className: "text-xs uppercase tracking-wide text-spark-muted", children: "Schedule" }), (mode === "recurring" || mode === "perpetual") && (_jsxs("div", { className: "grid grid-cols-3 gap-2", children: [_jsxs("label", { className: "block col-span-3", children: [_jsx("span", { className: "label text-xs", children: "Type" }), _jsxs("select", { className: "input w-full max-w-[160px]", value: scheduleType, onChange: (e) => setScheduleType(e.target.value), children: [_jsx("option", { value: "cron", children: "cron (visual builder)" }), _jsx("option", { value: "interval", children: "interval (raw seconds)" })] })] }), _jsx("div", { className: "col-span-3", children: scheduleType === "cron" ? (_jsx(CronBuilder, { value: scheduleExpr, onChange: setScheduleExpr })) : (_jsxs("label", { className: "block", children: [_jsx("span", { className: "label text-xs", children: "Interval seconds" }), _jsx("input", { className: "input w-full font-mono text-xs", value: scheduleExpr, onChange: (e) => setScheduleExpr(e.target.value), placeholder: "3600" })] })) })] })), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label text-xs", children: "Timezone" }), _jsx("input", { className: "input w-full font-mono text-xs", value: scheduleTz, onChange: (e) => setScheduleTz(e.target.value) })] }), _jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsxs("label", { className: "block", children: [_jsxs("span", { className: "label text-xs", children: ["Start at ", startRequired || (mode === "one_shot" && delayedOneShot) ? "(required)" : "(optional)"] }), _jsx("input", { type: "datetime-local", className: "input w-full text-xs", value: startAt, onChange: (e) => setStartAt(e.target.value) })] }), endAllowed && (_jsxs("label", { className: "block", children: [_jsxs("span", { className: "label text-xs", children: ["End at ", endRequired ? "(required)" : "(optional)"] }), _jsx("input", { type: "datetime-local", className: "input w-full text-xs", value: endAt, onChange: (e) => setEndAt(e.target.value) })] }))] }), (mode === "recurring" || mode === "perpetual") && (_jsxs("div", { className: "space-y-1", children: [_jsx("button", { type: "button", className: "btn text-xs", onClick: runPreview, children: "Preview next 5 fires" }), preview.length > 0 && (_jsx("ul", { className: "text-[11px] font-mono text-spark-muted", children: preview.map((iso) => (_jsxs("li", { children: ["\u00B7 ", iso] }, iso))) }))] }))] })), _jsxs("details", { className: "border border-spark-border rounded", open: showBudgets, onToggle: (e) => setShowBudgets(e.target.open), children: [_jsx("summary", { className: "px-3 py-2 text-xs cursor-pointer", children: "Budgets (optional, fall back to agent defaults)" }), _jsxs("div", { className: "px-3 pb-3 grid grid-cols-2 gap-2 text-xs", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label text-xs", children: "max_runtime_seconds" }), _jsx("input", { type: "number", className: "input w-full", value: budgetRuntime, onChange: (e) => setBudgetRuntime(e.target.value), placeholder: "900" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label text-xs", children: "max_model_calls" }), _jsx("input", { type: "number", className: "input w-full", value: budgetModelCalls, onChange: (e) => setBudgetModelCalls(e.target.value), placeholder: "30" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label text-xs", children: "max_tool_calls" }), _jsx("input", { type: "number", className: "input w-full", value: budgetToolCalls, onChange: (e) => setBudgetToolCalls(e.target.value), placeholder: "25" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label text-xs", children: "max_tokens_per_run" }), _jsx("input", { type: "number", className: "input w-full", value: budgetTokens, onChange: (e) => setBudgetTokens(e.target.value), placeholder: "(unbounded)" })] })] })] }), _jsxs("details", { className: "border border-spark-border rounded", open: showForensic, onToggle: (e) => setShowForensic(e.target.open), children: [_jsx("summary", { className: "px-3 py-2 text-xs cursor-pointer", children: "Forensic capture (default off)" }), _jsxs("div", { className: "px-3 pb-3 space-y-2 text-xs", children: [_jsxs("label", { className: "flex items-center gap-2 cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: forensicEnabled, onChange: (e) => setForensicEnabled(e.target.checked) }), "Enable forensic capture for runs of this task"] }), forensicEnabled && (_jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsxs("label", { className: "block col-span-2", children: [_jsx("span", { className: "label text-xs", children: "Reason (audited)" }), _jsx("input", { className: "input w-full", value: forensicReason, onChange: (e) => setForensicReason(e.target.value), placeholder: "why are we capturing?" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label text-xs", children: "TTL hours (1\u2013720)" }), _jsx("input", { type: "number", min: 1, max: 720, className: "input w-full", value: forensicTtl, onChange: (e) => setForensicTtl(e.target.value) })] })] }))] })] }), !isEdit && (_jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: autoStart, onChange: (e) => setAutoStart(e.target.checked) }), "Start the task immediately after create"] })), error && (_jsx("div", { className: "text-spark-danger text-xs border border-spark-danger/30 rounded px-3 py-2", children: error })), _jsxs("div", { className: "flex justify-end gap-2 pt-2 border-t border-spark-border", children: [_jsx("button", { className: "btn", onClick: () => {
                                reset();
                                onClose();
                            }, disabled: submitting, children: "Cancel" }), _jsx("button", { className: "btn btn-primary", disabled: submitting, onClick: submit, children: submitting
                                ? isEdit ? "Saving…" : "Creating…"
                                : isEdit ? "Save changes" : "Create task" })] })] }) }));
}
function TriggersPanel() {
    const qc = useQueryClient();
    const triggers = useQuery({
        queryKey: ["triggers"],
        queryFn: () => api.get("/api/scheduler/triggers"),
    });
    const tasks = useQuery({
        queryKey: ["tasks"],
        queryFn: () => api.get("/api/scheduler/tasks"),
    });
    const [showCreate, setShowCreate] = useState(false);
    const [revealed, setRevealed] = useState(null);
    async function deleteTrigger(id) {
        if (!confirm(`Delete trigger '${id}'? Its credential becomes invalid immediately.`)) {
            return;
        }
        try {
            await api.del(`/api/scheduler/triggers/${encodeURIComponent(id)}`);
            toast.success(`Deleted '${id}'`);
            qc.invalidateQueries({ queryKey: ["triggers"] });
        }
        catch (err) {
            toast.error(`Delete failed: ${err}`);
        }
    }
    return (_jsxs("section", { className: "panel p-4", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsxs("h3", { className: "font-semibold", children: ["Triggers (", (triggers.data ?? []).length, ")"] }), _jsx("button", { className: "btn btn-primary text-xs", onClick: () => setShowCreate(true), children: "+ New trigger" })] }), _jsxs("p", { className: "text-xs text-spark-muted mb-3", children: ["Webhook entry points that fire a task. ", _jsx("strong", { children: "Bearer" }), " ", "triggers expect a token in the ", _jsx("code", { children: "X-Spark-Token" }), " header (good for hand-rolled scripts). ", _jsx("strong", { children: "HMAC-SHA256" }), " ", "triggers verify the body against", " ", _jsx("code", { children: "X-Hub-Signature-256: sha256=\u2026" }), " \u2014 the standard for GitHub, Slack, and most modern providers."] }), (triggers.data ?? []).length === 0 ? (_jsx(EmptyState, { title: "No triggers configured" })) : (_jsx("div", { className: "space-y-2", children: (triggers.data ?? []).map((t) => (_jsx("div", { className: "border border-spark-border rounded-md p-3 hover:bg-spark-border/20 transition", children: _jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: "font-mono text-sm", children: t.trigger_id }), _jsx("span", { className: "chip text-xs", children: t.auth_mode }), t.payload_forwarding && (_jsx("span", { className: "chip text-xs", children: "payload\u2192task" })), t.event_filter && (_jsx("span", { className: "chip text-xs", children: "filtered" })), t.locked_until && (_jsx("span", { className: "chip chip-danger text-xs", children: "locked" }))] }), _jsxs("div", { className: "mt-1 text-xs text-spark-muted", children: ["fires", " ", _jsx(Link, { to: `/scheduler#task-${t.task_name}`, className: "text-spark-link hover:underline font-mono", children: t.task_name }), " · ", "POST ", _jsxs("code", { children: ["/api/scheduler/webhooks/", t.trigger_id] }), " · ", "rate ", t.rate_limit_per_hour, "/hr \u00B7 fired ", t.fires_total, t.last_fired_at && (_jsxs(_Fragment, { children: [" · ", "last ", _jsx(RelativeTime, { ts: t.last_fired_at })] })), t.failed_verify_count > 0 && (_jsxs(_Fragment, { children: [" · ", _jsxs("span", { className: "text-spark-danger", children: [t.failed_verify_count, " verify failures"] })] }))] })] }), _jsx("button", { className: "btn text-xs shrink-0", onClick: () => deleteTrigger(t.trigger_id), title: "Delete trigger", children: _jsx(X, { className: "w-3.5 h-3.5" }) })] }) }, t.trigger_id))) })), showCreate && (_jsx(NewTriggerModal, { tasks: tasks.data ?? [], onClose: () => setShowCreate(false), onCreated: (t) => {
                    setShowCreate(false);
                    setRevealed(t);
                    qc.invalidateQueries({ queryKey: ["triggers"] });
                } })), revealed && (_jsx(RevealCredentialModal, { trigger: revealed, onClose: () => setRevealed(null) }))] }));
}
function NewTriggerModal({ tasks, onClose, onCreated, }) {
    const [triggerId, setTriggerId] = useState("");
    const [taskName, setTaskName] = useState(tasks[0]?.name ?? "");
    const [authMode, setAuthMode] = useState("bearer");
    const [payloadForwarding, setPayloadForwarding] = useState(false);
    const [eventFilterText, setEventFilterText] = useState("");
    const [rateLimit, setRateLimit] = useState(60);
    const [submitting, setSubmitting] = useState(false);
    async function submit() {
        let event_filter = null;
        const trimmed = eventFilterText.trim();
        if (trimmed) {
            try {
                event_filter = JSON.parse(trimmed);
                if (typeof event_filter !== "object" || event_filter === null || Array.isArray(event_filter)) {
                    throw new Error("must be a JSON object");
                }
            }
            catch (err) {
                toast.error(`Event filter must be a JSON object: ${err}`);
                return;
            }
        }
        setSubmitting(true);
        try {
            const created = await api.post("/api/scheduler/triggers", {
                trigger_id: triggerId,
                task_name: taskName,
                auth_mode: authMode,
                payload_forwarding: payloadForwarding,
                event_filter,
                rate_limit_per_hour: rateLimit,
            });
            onCreated(created);
        }
        catch (err) {
            toast.error(`Create failed: ${err}`);
        }
        finally {
            setSubmitting(false);
        }
    }
    return (_jsx(Modal, { open: true, onClose: onClose, children: _jsxs("div", { className: "w-full max-w-lg max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl ", children: [_jsxs("header", { className: "sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3 flex items-center justify-between z-10", children: [_jsx("h3", { className: "text-lg font-bold", children: "New trigger" }), _jsx("button", { type: "button", onClick: onClose, className: "text-spark-muted hover:text-spark-text", "aria-label": "Close", children: _jsx(X, { className: "w-4 h-4" }) })] }), _jsxs("div", { className: "p-4 space-y-4", children: [_jsxs("label", { className: "block", children: [_jsx("div", { className: "text-xs text-spark-muted mb-1", children: "Trigger ID" }), _jsx("input", { type: "text", value: triggerId, onChange: (e) => setTriggerId(e.target.value), placeholder: "e.g. github-pr-merge", className: "w-full font-mono text-sm bg-spark-bg border border-spark-border rounded px-2 py-1.5" })] }), _jsxs("label", { className: "block", children: [_jsx("div", { className: "text-xs text-spark-muted mb-1", children: "Target task" }), _jsx("select", { value: taskName, onChange: (e) => setTaskName(e.target.value), className: "w-full text-sm bg-spark-bg border border-spark-border rounded px-2 py-1.5", children: tasks.map((t) => (_jsx("option", { value: t.name, children: t.name }, t.name))) })] }), _jsxs("fieldset", { className: "space-y-2", children: [_jsx("legend", { className: "text-xs text-spark-muted mb-1", children: "Auth mode" }), _jsxs("label", { className: "flex items-start gap-2 text-sm", children: [_jsx("input", { type: "radio", checked: authMode === "bearer", onChange: () => setAuthMode("bearer"), className: "mt-1" }), _jsxs("span", { children: [_jsx("strong", { children: "Bearer token" }), _jsxs("div", { className: "text-xs text-spark-muted", children: ["Caller sends ", _jsx("code", { children: "X-Spark-Token: <token>" }), ". Cleartext is shown once at create time."] })] })] }), _jsxs("label", { className: "flex items-start gap-2 text-sm", children: [_jsx("input", { type: "radio", checked: authMode === "hmac_sha256", onChange: () => setAuthMode("hmac_sha256"), className: "mt-1" }), _jsxs("span", { children: [_jsx("strong", { children: "HMAC-SHA256 signature" }), _jsxs("div", { className: "text-xs text-spark-muted", children: ["Verifies ", _jsx("code", { children: "X-Hub-Signature-256: sha256=\u2026" }), " against a shared secret in the age vault. Use for GitHub, Slack, and any modern signed-webhook provider."] })] })] })] }), _jsxs("label", { className: "flex items-start gap-2 text-sm", children: [_jsx("input", { type: "checkbox", checked: payloadForwarding, onChange: (e) => setPayloadForwarding(e.target.checked), className: "mt-1" }), _jsxs("span", { children: [_jsx("strong", { children: "Forward request body" }), " to the task as", " ", _jsx("code", { children: "trigger_payload" }), _jsx("div", { className: "text-xs text-spark-muted", children: "The planner sees the (truncated) JSON in its first system prompt; the full body is persisted on the run row." })] })] }), _jsxs("label", { className: "block", children: [_jsx("div", { className: "text-xs text-spark-muted mb-1", children: "Event filter (optional, JSON object)" }), _jsx("textarea", { value: eventFilterText, onChange: (e) => setEventFilterText(e.target.value), placeholder: '{"action": "closed", "pull_request.merged": true}', rows: 3, className: "w-full font-mono text-xs bg-spark-bg border border-spark-border rounded px-2 py-1.5" }), _jsx("div", { className: "text-xs text-spark-muted mt-1", children: "Dotted-path lookups against the inbound JSON body. Every key must match for the task to fire. Empty = always fire." })] }), _jsxs("label", { className: "block", children: [_jsx("div", { className: "text-xs text-spark-muted mb-1", children: "Rate limit (per hour, 0 = unlimited)" }), _jsx("input", { type: "number", min: 0, max: 10000, value: rateLimit, onChange: (e) => setRateLimit(Number(e.target.value)), className: "w-full text-sm bg-spark-bg border border-spark-border rounded px-2 py-1.5" })] }), _jsxs("div", { className: "flex justify-end gap-2 pt-2 border-t border-spark-border", children: [_jsx("button", { className: "btn text-sm", onClick: onClose, children: "Cancel" }), _jsx("button", { className: "btn btn-primary text-sm", onClick: submit, disabled: !triggerId || !taskName || submitting, children: submitting ? "Creating…" : "Create trigger" })] })] })] }) }));
}
function RevealCredentialModal({ trigger, onClose, }) {
    const isHmac = trigger.auth_mode === "hmac_sha256";
    return (_jsx(Modal, { open: true, onClose: onClose, children: _jsxs("div", { className: "w-full max-w-lg max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl ", children: [_jsxs("header", { className: "sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3 flex items-center justify-between z-10", children: [_jsx("h3", { className: "text-lg font-bold", children: "Save this credential" }), _jsx("button", { type: "button", onClick: onClose, className: "text-spark-muted hover:text-spark-text", "aria-label": "Close", children: _jsx(X, { className: "w-4 h-4" }) })] }), _jsxs("div", { className: "p-4 space-y-3", children: [_jsx("p", { className: "text-sm", children: isHmac
                                ? "Configure this as the webhook signing secret in the upstream provider (GitHub: 'Webhook secret'; Slack: 'Signing Secret')."
                                : "Send this token in the X-Spark-Token header on each webhook call." }), _jsxs("div", { className: "bg-spark-bg border border-spark-border rounded p-3", children: [_jsx("div", { className: "text-xs text-spark-muted mb-1", children: isHmac ? "Shared secret" : "Bearer token" }), _jsx("div", { className: "font-mono text-sm break-all select-all", children: trigger.secret })] }), _jsxs("div", { className: "text-xs text-spark-danger", children: ["\u26A0\uFE0F This is shown ", _jsx("strong", { children: "exactly once" }), ". If lost, delete the trigger and create a new one."] }), _jsxs("div", { className: "flex justify-end pt-2 border-t border-spark-border", children: [_jsx("button", { className: "btn btn-primary text-sm", onClick: () => {
                                        navigator.clipboard.writeText(trigger.secret);
                                        toast.success("Copied to clipboard");
                                    }, children: "Copy & close" }), _jsx("button", { className: "btn text-sm ml-2", onClick: onClose, children: "Close" })] })] })] }) }));
}
