import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Download, Eye, Shield, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
import { formatRelative, formatUntil } from "../lib/utils";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/primitives";
const KIND_STYLES = {
    prompt: {
        label: "Prompt",
        dot: "bg-blue-500",
        pill: "bg-blue-500/10 text-blue-300 border-blue-500/30",
    },
    model: {
        label: "Model",
        dot: "bg-green-500",
        pill: "bg-green-500/10 text-green-300 border-green-500/30",
    },
    tool: {
        label: "Tool",
        dot: "bg-orange-500",
        pill: "bg-orange-500/10 text-orange-300 border-orange-500/30",
    },
    memory_retrieved: {
        label: "Memory read",
        dot: "bg-violet-400",
        pill: "bg-violet-400/10 text-violet-300 border-violet-400/30",
    },
    memory_written: {
        label: "Memory write",
        dot: "bg-violet-600",
        pill: "bg-violet-600/10 text-violet-300 border-violet-600/30",
    },
    reflection: {
        label: "Reflection",
        dot: "bg-spark-muted",
        pill: "bg-spark-muted/10 text-spark-muted border-spark-muted/30",
    },
};
function kindStyle(kind) {
    return (KIND_STYLES[kind] ?? {
        label: kind,
        dot: "bg-spark-muted",
        pill: "bg-spark-muted/10 text-spark-muted border-spark-muted/30",
    });
}
/**
 * Forensic review — chain-of-thought viewer (H2). Admin-only.
 *
 * Two routes mount the same component:
 *   /forensic          — no run_id param, shows the capture list.
 *   /forensic/:run_id  — decrypts and shows the snapshot chain.
 */
export default function ForensicReview() {
    const params = useParams();
    if (params.run_id) {
        return _jsx(ForensicRunDetail, { runId: params.run_id });
    }
    return _jsx(ForensicList, {});
}
function ForensicList() {
    const list = useQuery({
        queryKey: ["forensic-list"],
        queryFn: () => api.get("/api/forensic/"),
    });
    return (_jsxs("div", { className: "space-y-4", children: [_jsx(PageHeader, { icon: _jsx(Shield, { className: "w-6 h-6" }), title: "Forensic review", subtitle: "Opt-in, encrypted-at-rest capture of the full prompt \u2192 reasoning \u2192 tool \u2192 memory chain for a task run. Admin-only." }), _jsxs("section", { className: "panel p-4 shadow-sm", children: [list.isLoading && _jsx("p", { className: "text-spark-muted text-sm", children: "loading\u2026" }), list.data && list.data.length === 0 && (_jsx(EmptyState, { icon: _jsx(Shield, { className: "w-8 h-8" }), title: "No forensic captures", description: "Start a run with spark task run --forensic \"reason\" ... to capture one." })), list.data && list.data.length > 0 && (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "Run" }), _jsx("th", { className: "text-left", children: "Agent / task" }), _jsx("th", { className: "text-left", children: "Reason" }), _jsx("th", { className: "text-left", children: "Captured" }), _jsx("th", { className: "text-left", children: "Expires" }), _jsx("th", { className: "text-left", children: "Snapshots" }), _jsx("th", {})] }) }), _jsx("tbody", { children: list.data.map((c) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "font-mono py-2 text-xs", children: c.run_id }), _jsxs("td", { children: [_jsx("div", { children: c.agent_name }), _jsx("div", { className: "text-spark-muted text-xs", children: c.task_name })] }), _jsx("td", { className: "max-w-xs truncate", children: c.enabled_reason }), _jsx("td", { children: formatRelative(c.captured_at) ?? "—" }), _jsx("td", { children: formatUntil(c.expires_at) ?? "—" }), _jsxs("td", { children: [c.snapshot_count, c.wiped_at && (_jsx("span", { className: "chip ml-2", children: "wiped" }))] }), _jsx("td", { children: !c.wiped_at && (_jsxs(Link, { className: "btn btn-primary inline-flex items-center gap-1", to: `/forensic/${encodeURIComponent(c.run_id)}`, children: [_jsx(Eye, { className: "w-3 h-3" }), " Inspect"] })) })] }, c.run_id))) })] }))] })] }));
}
function ForensicRunDetail({ runId }) {
    const navigate = useNavigate();
    const snaps = useQuery({
        queryKey: ["forensic-snapshots", runId],
        queryFn: () => api.get(`/api/forensic/${encodeURIComponent(runId)}/snapshots`),
        retry: false,
    });
    const [selectedId, setSelectedId] = useState(null);
    const [iterationFilter, setIterationFilter] = useState(null);
    const iterations = useMemo(() => {
        if (!snaps.data)
            return [];
        const set = new Set();
        for (const s of snaps.data.snapshots)
            set.add(s.iteration);
        return Array.from(set).sort((a, b) => a - b);
    }, [snaps.data]);
    const visibleSnapshots = useMemo(() => {
        if (!snaps.data)
            return [];
        if (iterationFilter == null)
            return snaps.data.snapshots;
        return snaps.data.snapshots.filter((s) => s.iteration === iterationFilter);
    }, [snaps.data, iterationFilter]);
    const selected = useMemo(() => snaps.data?.snapshots.find((s) => s.id === selectedId) ?? null, [snaps.data, selectedId]);
    function exportMarkdown() {
        if (!snaps.data)
            return;
        const lines = [];
        const c = snaps.data.capture;
        lines.push(`# Forensic run: ${c.run_id}`);
        lines.push("");
        lines.push(`- Agent: \`${c.agent_name}\``);
        lines.push(`- Task: \`${c.task_name}\``);
        lines.push(`- Reason: ${c.enabled_reason}`);
        lines.push(`- Captured: ${c.captured_at}`);
        lines.push(`- Expires: ${c.expires_at}`);
        lines.push(`- Snapshots: ${c.snapshot_count}`);
        lines.push("");
        for (const s of snaps.data.snapshots) {
            lines.push(`## ${s.kind} · iter #${s.iteration}.${s.sequence}`);
            lines.push("```json");
            lines.push(JSON.stringify(s.payload, null, 2));
            lines.push("```");
            lines.push("");
        }
        const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `forensic-${c.run_id}.md`;
        a.click();
        URL.revokeObjectURL(url);
        toast.success("Exported");
    }
    async function wipe() {
        const ok = await confirmDialog({
            title: `Wipe forensic capture for run ${runId}?`,
            description: "This shreds the per-run age identity cryptographically and then deletes the rows. The capture becomes unrecoverable — no restore, no undelete. Type the run id to confirm.",
            tone: "danger",
            confirmLabel: "Wipe capture",
            requireTypedName: runId,
        });
        if (!ok)
            return;
        try {
            await api.del(`/api/forensic/${encodeURIComponent(runId)}`);
            toast.success("Capture wiped");
            navigate("/forensic");
        }
        catch (err) {
            toast.error(`Wipe failed: ${err}`);
        }
    }
    if (snaps.isLoading) {
        return (_jsx("div", { className: "panel p-4", children: _jsx("p", { className: "text-spark-muted text-sm", children: "Decrypting forensic snapshots\u2026" }) }));
    }
    if (snaps.isError) {
        return (_jsx("div", { className: "panel p-4", children: _jsxs("p", { className: "text-red-400 text-sm", children: ["Failed to load snapshots: ", snaps.error.message] }) }));
    }
    if (!snaps.data)
        return null;
    const { capture } = snaps.data;
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-start justify-between", children: [_jsxs("div", { children: [_jsxs("h2", { className: "text-2xl font-bold flex items-center gap-2", children: [_jsx(Shield, { className: "w-6 h-6 text-spark-accent" }), " Forensic run"] }), _jsx("p", { className: "text-spark-muted text-sm font-mono", children: runId })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx(Link, { className: "btn", to: "/forensic", children: "Back" }), _jsxs("button", { className: "btn flex items-center gap-1", onClick: exportMarkdown, title: "Download as Markdown", children: [_jsx(Download, { className: "w-3 h-3" }), " Export"] }), _jsxs("button", { className: "btn btn-danger flex items-center gap-1", onClick: wipe, children: [_jsx(Trash2, { className: "w-3 h-3" }), " Wipe"] })] })] }), _jsxs("dl", { className: "grid grid-cols-2 md:grid-cols-4 gap-3 text-sm", children: [_jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs uppercase", children: "Agent" }), _jsx("dd", { children: capture.agent_name })] }), _jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs uppercase", children: "Task" }), _jsx("dd", { children: capture.task_name })] }), _jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs uppercase", children: "Captured" }), _jsx("dd", { children: formatRelative(capture.captured_at) ?? "—" })] }), _jsxs("div", { children: [_jsx("dt", { className: "text-spark-muted text-xs uppercase", children: "Expires" }), _jsx("dd", { children: formatUntil(capture.expires_at) ?? "—" })] }), _jsxs("div", { className: "col-span-full", children: [_jsx("dt", { className: "text-spark-muted text-xs uppercase", children: "Reason" }), _jsx("dd", { children: capture.enabled_reason })] })] })] }), _jsx("section", { className: "panel p-4", children: _jsxs("div", { className: "flex items-center gap-2 mb-2 flex-wrap", children: [_jsx("span", { className: "text-xs uppercase text-spark-muted mr-2", children: "Iterations" }), _jsx("button", { className: `chip ${iterationFilter == null ? "ring-1 ring-spark-accent" : ""}`, onClick: () => setIterationFilter(null), children: "all" }), iterations.map((it) => (_jsxs("button", { className: `chip ${iterationFilter === it ? "ring-1 ring-spark-accent" : ""}`, onClick: () => setIterationFilter(it), children: ["#", it] }, it)))] }) }), _jsxs("div", { className: "grid grid-cols-1 lg:grid-cols-5 gap-4", children: [_jsxs("section", { className: "lg:col-span-2 panel p-4 max-h-[70vh] overflow-auto space-y-2", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Chain" }), visibleSnapshots.map((s) => {
                                const style = kindStyle(s.kind);
                                const summary = summarizeSnapshot(s);
                                const isSelected = selected?.id === s.id;
                                return (_jsxs("button", { className: `w-full text-left border border-spark-border rounded px-3 py-2 hover:border-spark-accent transition ${isSelected ? "border-spark-accent bg-spark-accent/5" : ""}`, onClick: () => setSelectedId(s.id), children: [_jsxs("div", { className: "flex items-center gap-2 mb-1", children: [_jsx("span", { className: `inline-block w-2 h-2 rounded-full ${style.dot}` }), _jsx("span", { className: `chip border ${style.pill}`, children: style.label }), _jsxs("span", { className: "text-xs text-spark-muted", children: ["#", s.iteration, ".", s.sequence] })] }), _jsx("p", { className: "text-xs text-spark-muted line-clamp-2", children: summary })] }, s.id));
                            }), visibleSnapshots.length === 0 && (_jsx("p", { className: "text-spark-muted text-sm", children: "No snapshots in this iteration." }))] }), _jsxs("section", { className: "lg:col-span-3 panel p-4 max-h-[70vh] overflow-auto", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Payload" }), selected ? (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: `chip border ${kindStyle(selected.kind).pill}`, children: kindStyle(selected.kind).label }), _jsxs("span", { className: "text-xs text-spark-muted", children: ["iter #", selected.iteration, " \u00B7 seq ", selected.sequence] }), _jsx("span", { className: "text-xs text-spark-muted", children: formatRelative(selected.captured_at) ?? "—" }), _jsx("button", { className: "btn ml-auto", onClick: () => {
                                                    navigator.clipboard.writeText(JSON.stringify(selected.payload, null, 2));
                                                    toast.success("Copied");
                                                }, children: "Copy JSON" })] }), _jsx("pre", { className: "bg-spark-bg border border-spark-border rounded p-3 text-xs overflow-auto whitespace-pre-wrap break-words", children: JSON.stringify(selected.payload, null, 2) })] })) : (_jsx("p", { className: "text-spark-muted text-sm", children: "Select a snapshot to view its decrypted payload." }))] })] })] }));
}
function summarizeSnapshot(s) {
    const p = s.payload;
    switch (s.kind) {
        case "prompt": {
            const sys = typeof p.system_prompt === "string" ? p.system_prompt : "";
            return sys.slice(0, 140);
        }
        case "model": {
            const content = typeof p.content === "string" ? p.content : "";
            const calls = Array.isArray(p.tool_calls_requested)
                ? p.tool_calls_requested.length
                : 0;
            return calls > 0
                ? `→ ${calls} tool call(s): ${content.slice(0, 100)}`
                : content.slice(0, 140) || "(empty)";
        }
        case "tool": {
            const plugin = typeof p.plugin === "string" ? p.plugin : "";
            const err = typeof p.error_code === "string" ? p.error_code : null;
            return err ? `${plugin} → ${err}` : `${plugin} ✓`;
        }
        case "memory_retrieved":
        case "memory_written": {
            const ids = Array.isArray(p.memory_ids) ? p.memory_ids.length : 0;
            return `${ids} memory row(s)`;
        }
        case "reflection": {
            return typeof p.summary === "string" ? p.summary.slice(0, 140) : "";
        }
        default:
            return "";
    }
}
