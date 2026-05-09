import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { AlertTriangle, Brain, CheckCircle, Globe, Lock, Plus, Search, ShieldAlert, Sparkles, Upload, Users, X, } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
import { formatRelative } from "../lib/utils";
import { PageHeader } from "../components/PageHeader";
export default function MemoryBrowser() {
    const [tab, setTab] = useState("index");
    const [namespace, setNamespace] = useState("");
    const [agent, setAgent] = useState("");
    const [search, setSearch] = useState("");
    const [sensitivityFilter, setSensitivityFilter] = useState("");
    const [retentionFilter, setRetentionFilter] = useState("");
    const [scope, setScope] = useState("all");
    const [showCreate, setShowCreate] = useState(false);
    const [newMem, setNewMem] = useState({
        agent_name: "",
        summary: "",
        memory_type: "fact",
        sensitivity: "low",
        retention_class: "review",
        tags: "",
        confidence: 0.7,
        is_anti_pattern: false,
    });
    const memories = useQuery({
        queryKey: ["memories", namespace, scope],
        queryFn: () => {
            const params = new URLSearchParams();
            if (namespace)
                params.set("namespace", namespace);
            if (scope !== "all")
                params.set("scope", scope);
            const qs = params.toString();
            return api.get(`/api/memory/long-term${qs ? `?${qs}` : ""}`);
        },
    });
    async function promoteToGlobal(memoryId) {
        try {
            await api.post(`/api/memory/long-term/${encodeURIComponent(memoryId)}/promote-to-global`);
            toast.success("Promoted to global pool");
            memories.refetch();
        }
        catch (err) {
            const msg = `${err}`.includes("403")
                ? "Agent lacks write_global permission, or sensitivity too high"
                : `Promotion failed: ${err}`;
            toast.error(msg);
        }
    }
    async function createMemory() {
        if (!newMem.agent_name.trim() || !newMem.summary.trim()) {
            toast.error("Agent and summary are required");
            return;
        }
        try {
            await api.post("/api/memory/long-term", {
                agent_name: newMem.agent_name,
                summary: newMem.summary,
                memory_type: newMem.memory_type,
                sensitivity: newMem.sensitivity,
                retention_class: newMem.retention_class,
                confidence: newMem.confidence,
                tags: newMem.tags
                    ? newMem.tags.split(",").map((t) => t.trim()).filter(Boolean)
                    : [],
                is_anti_pattern: newMem.is_anti_pattern,
            });
            toast.success("Memory created");
            setShowCreate(false);
            setNewMem({
                agent_name: "",
                summary: "",
                memory_type: "fact",
                sensitivity: "low",
                retention_class: "review",
                tags: "",
                confidence: 0.7,
                is_anti_pattern: false,
            });
            memories.refetch();
        }
        catch (err) {
            toast.error(`Create failed: ${err}`);
        }
    }
    async function approveMemory(memoryId) {
        try {
            await api.post(`/api/memory/long-term/${encodeURIComponent(memoryId)}/approve`);
            toast.success("Memory approved");
            memories.refetch();
        }
        catch (err) {
            toast.error(`Approve failed: ${err}`);
        }
    }
    async function quarantineMemory(memoryId) {
        try {
            await api.post(`/api/memory/long-term/${encodeURIComponent(memoryId)}/quarantine`);
            toast.success("Quarantined");
            memories.refetch();
        }
        catch (err) {
            toast.error(`Quarantine failed: ${err}`);
        }
    }
    const playbooks = useQuery({
        queryKey: ["playbooks", agent],
        queryFn: () => agent
            ? api.get(`/api/memory/playbooks/${encodeURIComponent(agent)}`)
            : Promise.resolve([]),
        enabled: !!agent,
    });
    const filteredMemories = useMemo(() => {
        const items = memories.data ?? [];
        const q = search.toLowerCase();
        return items.filter((m) => {
            if (q && !m.content_summary.toLowerCase().includes(q))
                return false;
            if (sensitivityFilter && m.sensitivity !== sensitivityFilter)
                return false;
            if (retentionFilter && m.retention_class !== retentionFilter)
                return false;
            return true;
        });
    }, [memories.data, search, sensitivityFilter, retentionFilter]);
    const pruning = useQuery({
        queryKey: ["memory-pruning-status"],
        queryFn: () => api.get("/api/memory/pruning/status"),
        enabled: tab === "pruning",
        refetchInterval: tab === "pruning" ? 15000 : false,
    });
    async function del(memoryId) {
        const ok = await confirmDialog({
            title: "Delete memory permanently?",
            description: "The record is removed from both the SQLite index and the Chroma vector store. This cannot be undone.",
            tone: "danger",
            confirmLabel: "Delete memory",
        });
        if (!ok)
            return;
        await api.del(`/api/memory/long-term/${encodeURIComponent(memoryId)}`);
        memories.refetch();
    }
    async function runDryRun() {
        try {
            const report = await api.post("/api/memory/pruning/dry-run", {});
            const summary = Object.entries(report.by_class)
                .map(([cls, n]) => `${cls}:${n}`)
                .join(", ");
            toast.success(`Dry-run: ${report.total} rows would be pruned${summary ? ` (${summary})` : ""}`);
            pruning.refetch();
        }
        catch (err) {
            toast.error(`Dry-run failed: ${err}`);
        }
    }
    async function runExecute() {
        const ok = await confirmDialog({
            title: "Run a live pruning sweep now?",
            description: "Rows past their rollover window will be permanently deleted from both SQLite and the vector store. Use the dry-run button first if you want to see counts without deleting.",
            tone: "danger",
            confirmLabel: "Run sweep",
        });
        if (!ok)
            return;
        try {
            const report = await api.post("/api/memory/pruning/execute", {});
            toast.success(`Pruned ${report.total} memories`);
            pruning.refetch();
            memories.refetch();
        }
        catch (err) {
            toast.error(`Prune failed: ${err}`);
        }
    }
    return (_jsxs("div", { className: "space-y-6", children: [_jsx(PageHeader, { icon: _jsx(Brain, { className: "w-6 h-6" }), title: "Memory", subtitle: "Long-term memory (Chroma) + learning playbooks (SQLite) + retention pruning." }), _jsx("div", { className: "flex gap-2 border-b border-spark-border", children: [
                    "index",
                    "review",
                    "visualize",
                    "circles",
                    "playbooks",
                    "pruning",
                ].map((t) => (_jsx("button", { className: `px-3 py-2 text-sm capitalize ${tab === t
                        ? "border-b-2 border-spark-accent text-spark-text"
                        : "text-spark-muted hover:text-spark-text"}`, onClick: () => setTab(t), children: t }, t))) }), tab === "index" && (_jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsxs("h3", { className: "font-semibold", children: ["Long-term memory index (", filteredMemories.length, " of", " ", memories.data?.length ?? 0, ")"] }), _jsxs("button", { className: "btn btn-primary flex items-center gap-1", onClick: () => setShowCreate(true), children: [_jsx(Plus, { className: "w-3.5 h-3.5" }), " Add memory"] })] }), _jsxs("div", { className: "flex flex-wrap gap-2 mb-3", children: [_jsxs("div", { className: "relative flex-1 min-w-[200px]", children: [_jsx(Search, { className: "w-4 h-4 text-spark-muted absolute left-3 top-1/2 -translate-y-1/2" }), _jsx("input", { className: "input w-full pl-9", placeholder: "Search summaries\u2026", value: search, onChange: (e) => setSearch(e.target.value) })] }), _jsx("input", { className: "input w-40", placeholder: "namespace", value: namespace, onChange: (e) => setNamespace(e.target.value) }), _jsxs("select", { className: "input", value: scope, onChange: (e) => setScope(e.target.value), title: "Memory scope", children: [_jsx("option", { value: "all", children: "all scopes" }), _jsx("option", { value: "private", children: "private only" }), _jsx("option", { value: "global", children: "global only" })] }), _jsxs("select", { className: "input", value: sensitivityFilter, onChange: (e) => setSensitivityFilter(e.target.value), children: [_jsx("option", { value: "", children: "all sensitivity" }), _jsx("option", { value: "low", children: "low" }), _jsx("option", { value: "moderate", children: "moderate" }), _jsx("option", { value: "high", children: "high" }), _jsx("option", { value: "restricted", children: "restricted" })] }), _jsxs("select", { className: "input", value: retentionFilter, onChange: (e) => setRetentionFilter(e.target.value), children: [_jsx("option", { value: "", children: "all retention" }), _jsx("option", { value: "temporary", children: "temporary" }), _jsx("option", { value: "expiring", children: "expiring" }), _jsx("option", { value: "review", children: "review" }), _jsx("option", { value: "persistent", children: "persistent" })] })] }), filteredMemories.length === 0 ? (_jsx("p", { className: "text-center text-spark-muted text-sm py-6", children: "No memories match these filters." })) : (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left pb-2", children: "ID" }), _jsx("th", { className: "text-left pb-2", children: "Namespace" }), _jsx("th", { className: "text-left pb-2", children: "Type" }), _jsx("th", { className: "text-left pb-2", children: "Sensitivity" }), _jsx("th", { className: "text-left pb-2", children: "Retention" }), _jsx("th", { className: "text-right pb-2 tabular-nums", children: "Confidence" }), _jsx("th", { className: "text-left pb-2", children: "Summary" }), _jsx("th", { className: "pb-2" })] }) }), _jsx("tbody", { children: filteredMemories.map((m) => (_jsxs("tr", { className: "border-t border-spark-border hover:bg-spark-border/20", children: [_jsxs("td", { className: "py-1.5 font-mono text-xs", children: [m.memory_id.slice(0, 10), "\u2026"] }), _jsx("td", { children: _jsxs("div", { className: "flex flex-wrap items-center gap-1", children: [m.is_global ? (_jsxs("span", { className: "chip chip-warn text-[10px] gap-1", title: "Shared across all agents", children: [_jsx(Globe, { className: "w-3 h-3" }), " global"] })) : m.namespace === "__consensus__" ? (_jsxs("span", { className: "chip chip-info text-[10px] gap-1", title: "Multi-agent consensus", children: [_jsx(Sparkles, { className: "w-3 h-3" }), " consensus"] })) : (_jsxs("span", { className: "chip text-[10px] gap-1", title: `Private to ${m.agent_name}`, children: [_jsx(Lock, { className: "w-3 h-3" }), m.namespace] })), m.is_anti_pattern && (_jsxs("span", { className: "chip chip-danger text-[10px] gap-1", title: "Anti-pattern (don't do this)", children: [_jsx(AlertTriangle, { className: "w-3 h-3" }), " avoid"] })), m.contradicts_with && (_jsx("span", { className: "chip chip-warn text-[10px] gap-1", title: `Contradicts: ${m.contradicts_with}`, children: "\u26A0 contradicts" })), m.status && m.status !== "active" && (_jsxs("span", { className: "chip chip-warn text-[10px] gap-1", title: `Status: ${m.status}`, children: [_jsx(ShieldAlert, { className: "w-3 h-3" }), " ", m.status] }))] }) }), _jsx("td", { children: _jsx("span", { className: "chip text-[10px]", children: m.memory_type }) }), _jsx("td", { children: _jsx("span", { className: `chip text-[10px] ${m.sensitivity === "restricted" || m.sensitivity === "high"
                                                    ? "chip-warn"
                                                    : ""}`, children: m.sensitivity }) }), _jsx("td", { className: "text-xs", children: m.retention_class }), _jsx("td", { className: "text-right tabular-nums", children: (m.confidence ?? 0).toFixed(2) }), _jsx("td", { className: "text-spark-muted max-w-md truncate", children: m.content_summary }), _jsx("td", { children: _jsxs("div", { className: "flex items-center gap-1 justify-end", children: [!m.is_global && m.sensitivity !== "restricted" && (_jsx("button", { className: "btn-icon hover:text-spark-accent", onClick: () => promoteToGlobal(m.memory_id), title: "Promote to global (shared pool)", children: _jsx(Upload, { className: "w-3.5 h-3.5" }) })), _jsx("button", { className: "btn-icon hover:text-spark-danger", onClick: () => del(m.memory_id), title: "Delete", children: "\u00D7" })] }) })] }, m.memory_id))) })] }))] })), tab === "review" && (_jsx(ReviewQueueTab, { onApprove: approveMemory, onQuarantine: quarantineMemory, onDelete: del })), tab === "visualize" && _jsx(VisualizeTab, { namespace: namespace }), tab === "circles" && _jsx(CirclesTab, {}), tab === "playbooks" && (_jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Playbooks" }), _jsx("input", { className: "input mb-3 w-80", placeholder: "agent name", value: agent, onChange: (e) => setAgent(e.target.value) }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "name" }), _jsx("th", { className: "text-left", children: "uses" }), _jsx("th", { className: "text-left", children: "success rate" }), _jsx("th", { className: "text-left", children: "avg tools" }), _jsx("th", { className: "text-left", children: "last success" }), _jsx("th", { className: "text-left", children: "sequence" })] }) }), _jsx("tbody", { children: (playbooks.data ?? []).map((p) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono", children: p.name }), _jsx("td", { children: p.uses }), _jsxs("td", { children: [(p.success_rate * 100).toFixed(0), "%"] }), _jsx("td", { children: p.avg_tool_calls.toFixed(1) }), _jsx("td", { children: formatRelative(p.last_success_at) }), _jsx("td", { className: "font-mono text-xs", children: p.tool_sequence.join(" → ") })] }, p.playbook_id))) })] })] })), tab === "pruning" && (_jsxs("section", { className: "panel p-4", children: [_jsxs("div", { className: "flex items-start justify-between mb-3", children: [_jsxs("div", { children: [_jsx("h3", { className: "font-semibold", children: "Retention pruning" }), _jsx("p", { className: "text-spark-muted text-xs mt-1", children: "Scheduled sweep deletes long-term memory rows whose retention class has aged past its window." })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { className: "btn", onClick: runDryRun, children: "Run dry-run now" }), _jsx("button", { className: "btn btn-danger", onClick: runExecute, children: "Run now" })] })] }), pruning.isLoading && _jsx("p", { className: "text-spark-muted text-sm", children: "loading\u2026" }), pruning.data && (_jsxs("div", { className: "grid gap-4 md:grid-cols-2", children: [_jsxs("div", { className: "space-y-2", children: [_jsx("h4", { className: "text-xs uppercase text-spark-muted", children: "Configuration" }), _jsxs("dl", { className: "text-sm space-y-1", children: [_jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Enabled" }), _jsx("dd", { children: pruning.data.config.enabled ? "yes" : "no" })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Schedule" }), _jsx("dd", { className: "font-mono", children: pruning.data.config.schedule })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Next run" }), _jsx("dd", { children: formatRelative(pruning.data.next_run_at) ?? "—" })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Dry-run mode" }), _jsx("dd", { children: pruning.data.config.dry_run ? "on" : "off" })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Notify on prune" }), _jsx("dd", { children: pruning.data.config.notify_on_prune ? "yes" : "no" })] })] }), _jsx("h4", { className: "text-xs uppercase text-spark-muted mt-4", children: "Rollover windows (days)" }), _jsx("dl", { className: "text-sm space-y-1", children: ["temporary", "expiring", "review", "persistent"].map((cls) => (_jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: cls }), _jsx("dd", { children: pruning.data.config.rollover_windows[cls] ?? "never prune" })] }, cls))) })] }), _jsxs("div", { className: "space-y-2", children: [_jsx("h4", { className: "text-xs uppercase text-spark-muted", children: "Last run" }), pruning.data.last_run ? (_jsxs("dl", { className: "text-sm space-y-1", children: [_jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "When" }), _jsx("dd", { children: formatRelative(pruning.data.last_run.at) ?? "—" })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Actor" }), _jsx("dd", { className: "font-mono text-xs", children: pruning.data.last_run.actor })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Total" }), _jsxs("dd", { children: [pruning.data.last_run.total, " ", pruning.data.last_run.dry_run && (_jsx("span", { className: "chip", children: "dry-run" }))] })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "By class" }), _jsx("dd", { className: "font-mono text-xs", children: Object.keys(pruning.data.last_run.by_class).length
                                                            ? Object.entries(pruning.data.last_run.by_class)
                                                                .map(([k, v]) => `${k}:${v}`)
                                                                .join(", ")
                                                            : "—" })] }), pruning.data.last_run.namespaces.length > 0 && (_jsxs("div", { className: "flex gap-2", children: [_jsx("dt", { className: "text-spark-muted w-36", children: "Namespaces" }), _jsx("dd", { className: "font-mono text-xs", children: pruning.data.last_run.namespaces.join(", ") })] }))] })) : (_jsx("p", { className: "text-spark-muted text-sm", children: "No pruning runs recorded yet." }))] })] }))] })), showCreate && (_jsx("div", { className: "fixed inset-0 bg-black/70 z-[100] flex items-center justify-center p-4", onClick: () => setShowCreate(false), children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-lg p-6 space-y-3 shadow-2xl", onClick: (e) => e.stopPropagation(), children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("h3", { className: "font-semibold", children: "Add memory" }), _jsx("button", { className: "btn-icon", onClick: () => setShowCreate(false), children: _jsx(X, { className: "w-4 h-4" }) })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Agent name" }), _jsx("input", { className: "input w-full font-mono", placeholder: "research-assistant", value: newMem.agent_name, onChange: (e) => setNewMem({ ...newMem, agent_name: e.target.value }) })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Summary" }), _jsx("textarea", { className: "input w-full", rows: 3, placeholder: "What should the agent know?", value: newMem.summary, onChange: (e) => setNewMem({ ...newMem, summary: e.target.value }) })] }), _jsxs("div", { className: "grid grid-cols-3 gap-2", children: [_jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Type" }), _jsxs("select", { className: "input w-full", value: newMem.memory_type, onChange: (e) => setNewMem({ ...newMem, memory_type: e.target.value }), children: [_jsx("option", { value: "fact", children: "fact" }), _jsx("option", { value: "lesson", children: "lesson" }), _jsx("option", { value: "pattern", children: "pattern" }), _jsx("option", { value: "preference", children: "preference" }), _jsx("option", { value: "constraint", children: "constraint" }), _jsx("option", { value: "result", children: "result" })] })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Sensitivity" }), _jsxs("select", { className: "input w-full", value: newMem.sensitivity, onChange: (e) => setNewMem({ ...newMem, sensitivity: e.target.value }), children: [_jsx("option", { value: "low", children: "low" }), _jsx("option", { value: "moderate", children: "moderate" }), _jsx("option", { value: "high", children: "high" }), _jsx("option", { value: "restricted", children: "restricted" })] })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Retention" }), _jsxs("select", { className: "input w-full", value: newMem.retention_class, onChange: (e) => setNewMem({ ...newMem, retention_class: e.target.value }), children: [_jsx("option", { value: "temporary", children: "temporary" }), _jsx("option", { value: "expiring", children: "expiring" }), _jsx("option", { value: "review", children: "review" }), _jsx("option", { value: "persistent", children: "persistent" })] })] })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Tags (comma-separated)" }), _jsx("input", { className: "input w-full", value: newMem.tags, onChange: (e) => setNewMem({ ...newMem, tags: e.target.value }) })] }), _jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Confidence (", newMem.confidence.toFixed(2), ")"] }), _jsx("input", { type: "range", min: "0", max: "1", step: "0.05", className: "w-full", value: newMem.confidence, onChange: (e) => setNewMem({
                                        ...newMem,
                                        confidence: parseFloat(e.target.value),
                                    }) })] }), _jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: newMem.is_anti_pattern, onChange: (e) => setNewMem({ ...newMem, is_anti_pattern: e.target.checked }) }), _jsx("span", { children: "Anti-pattern (frames memory as \"avoid this\")" })] }), _jsxs("div", { className: "flex justify-end gap-2 pt-2 border-t border-spark-border", children: [_jsx("button", { className: "btn", onClick: () => setShowCreate(false), children: "Cancel" }), _jsx("button", { className: "btn btn-primary", onClick: createMemory, children: "Create" })] })] }) }))] }));
}
function ReviewQueueTab({ onApprove, onQuarantine, onDelete, }) {
    const q = useQuery({
        queryKey: ["memory-review-queue"],
        queryFn: () => api.get("/api/memory/review-queue"),
        refetchInterval: 30_000,
    });
    return (_jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("h3", { className: "font-semibold mb-3 flex items-center gap-2", children: [_jsx(ShieldAlert, { className: "w-4 h-4 text-spark-accent" }), "Review queue (", q.data?.length ?? 0, ")"] }), _jsx("p", { className: "text-xs text-spark-muted mb-3", children: "Quarantined memories, low-confidence rows, and contradictions collected in one place." }), q.data && q.data.length === 0 ? (_jsx("p", { className: "text-sm text-spark-muted text-center py-6", children: "Nothing to review." })) : (_jsx("div", { className: "space-y-2", children: (q.data ?? []).map((r) => (_jsx("div", { className: "border border-spark-border rounded-md p-3 hover:border-spark-accent/40 transition", children: _jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 mb-1 flex-wrap", children: [_jsxs("span", { className: "font-mono text-xs", children: [r.memory_id.slice(0, 12), "\u2026"] }), _jsx("span", { className: "chip text-[10px]", children: r.agent_name }), _jsx("span", { className: "chip chip-warn text-[10px]", children: r.reason })] }), _jsx("p", { className: "text-sm text-spark-muted", children: r.content_summary })] }), _jsxs("div", { className: "flex items-center gap-1 shrink-0", children: [r.status !== "active" && (_jsx("button", { className: "btn-icon hover:text-spark-good", onClick: () => onApprove(r.memory_id), title: "Approve", children: _jsx(CheckCircle, { className: "w-4 h-4" }) })), r.status === "active" && (_jsx("button", { className: "btn-icon hover:text-spark-accent", onClick: () => onQuarantine(r.memory_id), title: "Quarantine", children: _jsx(ShieldAlert, { className: "w-4 h-4" }) })), _jsx("button", { className: "btn-icon hover:text-spark-danger", onClick: () => onDelete(r.memory_id), title: "Delete", children: "\u00D7" })] })] }) }, r.memory_id))) }))] }));
}
function VisualizeTab({ namespace }) {
    const q = useQuery({
        queryKey: ["memory-visualize", namespace],
        queryFn: () => api.get(`/api/memory/visualize${namespace ? `?namespace=${encodeURIComponent(namespace)}` : ""}`),
    });
    const [hover, setHover] = useState(null);
    const size = 520;
    const colorFor = (p) => {
        if (p.is_anti_pattern)
            return "#f85149";
        if (p.memory_type === "pattern")
            return "#f59e0b";
        if (p.memory_type === "lesson")
            return "#3fb950";
        if (p.memory_type === "constraint")
            return "#a78bfa";
        if (p.memory_type === "preference")
            return "#60a5fa";
        return "#7d8590";
    };
    return (_jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Memory space (PCA 2D)" }), q.data?.reason && (_jsx("p", { className: "text-sm text-spark-muted", children: q.data.reason })), q.data?.points && q.data.points.length > 0 && (_jsxs("div", { className: "relative inline-block", children: [_jsxs("svg", { width: size, height: size, className: "bg-spark-bg border border-spark-border rounded-md", children: [_jsx("line", { x1: size / 2, y1: 0, x2: size / 2, y2: size, stroke: "#1f242b", strokeWidth: "1" }), _jsx("line", { x1: 0, y1: size / 2, x2: size, y2: size / 2, stroke: "#1f242b", strokeWidth: "1" }), q.data.points.map((p) => (_jsx("circle", { cx: ((p.x + 1) / 2) * (size - 20) + 10, cy: ((1 - p.y) / 2) * (size - 20) + 10, r: 3 + Math.min(8, p.citations), fill: colorFor(p), opacity: 0.7, onMouseEnter: () => setHover(p), onMouseLeave: () => setHover(null), className: "cursor-pointer hover:opacity-100" }, p.id)))] }), hover && (_jsxs("div", { className: "absolute top-2 left-2 bg-spark-panel border border-spark-border rounded-md p-2 text-xs max-w-xs pointer-events-none shadow-lg", children: [_jsxs("div", { className: "flex items-center gap-1 mb-1", children: [_jsx("span", { className: "chip text-[10px]", children: hover.memory_type }), _jsx("span", { className: "chip text-[10px]", children: hover.namespace })] }), _jsx("div", { className: "text-spark-text", children: hover.label }), _jsxs("div", { className: "text-spark-muted mt-1", children: ["conf ", hover.confidence.toFixed(2), " \u00B7 cited ", hover.citations] })] }))] })), _jsxs("div", { className: "mt-3 flex flex-wrap gap-3 text-xs text-spark-muted", children: [_jsxs("span", { children: [_jsx("span", { className: "inline-block w-2 h-2 rounded-full", style: { background: "#f59e0b" } }), " pattern"] }), _jsxs("span", { children: [_jsx("span", { className: "inline-block w-2 h-2 rounded-full", style: { background: "#3fb950" } }), " lesson"] }), _jsxs("span", { children: [_jsx("span", { className: "inline-block w-2 h-2 rounded-full", style: { background: "#a78bfa" } }), " constraint"] }), _jsxs("span", { children: [_jsx("span", { className: "inline-block w-2 h-2 rounded-full", style: { background: "#60a5fa" } }), " preference"] }), _jsxs("span", { children: [_jsx("span", { className: "inline-block w-2 h-2 rounded-full", style: { background: "#f85149" } }), " anti-pattern"] }), _jsx("span", { className: "text-spark-muted", children: "(bubble size = citation count)" })] })] }));
}
function CirclesTab() {
    const qc = useQueryClient();
    const q = useQuery({
        queryKey: ["memory-circles"],
        queryFn: () => api.get("/api/memory/circles"),
    });
    const [newCircle, setNewCircle] = useState({
        circle_id: "",
        name: "",
        description: "",
    });
    const [addMember, setAddMember] = useState(null);
    const [newMember, setNewMember] = useState({
        agent_name: "",
        can_read: true,
        can_write: false,
    });
    async function createCircle() {
        try {
            await api.post("/api/memory/circles", newCircle);
            toast.success("Circle created");
            setNewCircle({ circle_id: "", name: "", description: "" });
            qc.invalidateQueries({ queryKey: ["memory-circles"] });
        }
        catch (err) {
            toast.error(`Create failed: ${err}`);
        }
    }
    async function addToCircle(cid) {
        try {
            await api.post(`/api/memory/circles/${encodeURIComponent(cid)}/members`, newMember);
            toast.success("Member added");
            setAddMember(null);
            setNewMember({ agent_name: "", can_read: true, can_write: false });
            qc.invalidateQueries({ queryKey: ["memory-circles"] });
        }
        catch (err) {
            toast.error(`Add failed: ${err}`);
        }
    }
    async function removeMember(cid, agent) {
        try {
            await api.del(`/api/memory/circles/${encodeURIComponent(cid)}/members/${encodeURIComponent(agent)}`);
            qc.invalidateQueries({ queryKey: ["memory-circles"] });
        }
        catch (err) {
            toast.error(`Remove failed: ${err}`);
        }
    }
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("h3", { className: "font-semibold mb-3 flex items-center gap-2", children: [_jsx(Users, { className: "w-4 h-4 text-spark-accent" }), " New circle"] }), _jsxs("div", { className: "flex flex-wrap gap-2", children: [_jsx("input", { className: "input flex-1 min-w-[150px] font-mono", placeholder: "circle-id (lowercase, hyphens)", value: newCircle.circle_id, onChange: (e) => setNewCircle({ ...newCircle, circle_id: e.target.value }) }), _jsx("input", { className: "input flex-1 min-w-[150px]", placeholder: "Name", value: newCircle.name, onChange: (e) => setNewCircle({ ...newCircle, name: e.target.value }) }), _jsx("input", { className: "input flex-[2] min-w-[200px]", placeholder: "Description", value: newCircle.description, onChange: (e) => setNewCircle({ ...newCircle, description: e.target.value }) }), _jsx("button", { className: "btn btn-primary", onClick: createCircle, disabled: !newCircle.circle_id || !newCircle.name, children: "Create" })] })] }), (q.data ?? []).map((c) => (_jsxs("section", { className: "panel p-4 shadow-sm", children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsxs("div", { children: [_jsxs("h4", { className: "font-semibold", children: [c.name, " ", _jsxs("span", { className: "font-mono text-xs text-spark-muted", children: ["(", c.circle_id, ")"] })] }), c.description && (_jsx("p", { className: "text-xs text-spark-muted", children: c.description }))] }), _jsxs("button", { className: "btn", onClick: () => setAddMember(addMember === c.circle_id ? null : c.circle_id), children: [_jsx(Plus, { className: "w-3 h-3 mr-1 inline" }), " Add member"] })] }), addMember === c.circle_id && (_jsxs("div", { className: "flex gap-2 mb-2 pl-3 border-l-2 border-spark-accent/30", children: [_jsx("input", { className: "input flex-1", placeholder: "agent name", value: newMember.agent_name, onChange: (e) => setNewMember({ ...newMember, agent_name: e.target.value }) }), _jsxs("label", { className: "flex items-center gap-1 text-xs", children: [_jsx("input", { type: "checkbox", checked: newMember.can_read, onChange: (e) => setNewMember({
                                            ...newMember,
                                            can_read: e.target.checked,
                                        }) }), "read"] }), _jsxs("label", { className: "flex items-center gap-1 text-xs", children: [_jsx("input", { type: "checkbox", checked: newMember.can_write, onChange: (e) => setNewMember({
                                            ...newMember,
                                            can_write: e.target.checked,
                                        }) }), "write"] }), _jsx("button", { className: "btn btn-primary", onClick: () => addToCircle(c.circle_id), children: "Add" })] })), c.members.length === 0 ? (_jsx("p", { className: "text-xs text-spark-muted", children: "No members." })) : (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "Agent" }), _jsx("th", { children: "Read" }), _jsx("th", { children: "Write" }), _jsx("th", {})] }) }), _jsx("tbody", { children: c.members.map((m) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono text-xs", children: m.agent_name }), _jsx("td", { className: "text-center", children: m.can_read ? "✓" : "·" }), _jsx("td", { className: "text-center", children: m.can_write ? "✓" : "·" }), _jsx("td", { className: "text-right", children: _jsx("button", { className: "btn-icon hover:text-spark-danger", onClick: () => removeMember(c.circle_id, m.agent_name), children: "\u00D7" }) })] }, m.agent_name))) })] }))] }, c.circle_id)))] }));
}
