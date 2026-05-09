import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FlaskConical, Save, RotateCcw, Settings2, ShieldCheck, } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "sonner";
import { MaskStyleSelector, } from "../components/MaskStyleSelector";
import { Modal } from "../components/Modal";
// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
const ALL_SCOPES = [
    "user_input",
    "tool_output",
    "model_output",
    "memory_write",
    "shell_args",
];
const LEVEL_LABEL = {
    allow: "Allow",
    warn: "Warn",
    redact: "Redact",
    shadow_block: "Shadow",
    block: "Block",
};
const FAMILY_ICON = {
    pii: "🪪",
    financial: "💳",
    credentials: "🔐",
    cli: "💻",
    prompt: "🧠",
};
export default function Filtering() {
    const qc = useQueryClient();
    const policy = useQuery({
        queryKey: ["filtering", "policy"],
        queryFn: () => api.get("/api/filtering/policy"),
    });
    if (policy.isLoading) {
        return _jsx("div", { className: "text-spark-muted", children: "Loading filtering policy\u2026" });
    }
    if (policy.error || !policy.data) {
        return (_jsx("div", { className: "panel p-4 text-sm text-spark-danger", children: "Failed to load filtering policy." }));
    }
    return _jsx(PolicyEditor, { data: policy.data, qc: qc });
}
function PolicyEditor({ data, qc, }) {
    const [pending, setPending] = useState({});
    const [drawerOpenFor, setDrawerOpenFor] = useState(null);
    const [dryRunOpen, setDryRunOpen] = useState(false);
    const setEdit = (dataClass, patch) => {
        setPending((p) => ({
            ...p,
            [dataClass]: { ...(p[dataClass] || {}), ...patch },
        }));
    };
    const discardEdit = (dataClass) => setPending((p) => {
        const next = { ...p };
        delete next[dataClass];
        return next;
    });
    const dirtyClasses = Object.keys(pending);
    const isDirty = dirtyClasses.length > 0;
    const saveMutation = useMutation({
        mutationFn: async () => {
            // Save each dirty category sequentially. The audit trail wants
            // one row per change, and the operator picks edits one card at a
            // time, so we never expect huge batches.
            for (const cls of dirtyClasses) {
                const cat = data.categories.find((c) => c.data_class === cls);
                const cur = effectiveCategory(cat);
                const next = { ...cur, ...pending[cls] };
                await api.put(`/api/filtering/policy/category/${cls}`, {
                    level: next.level,
                    scopes: next.scopes,
                    reason: next.reason || "edited via Filtering page",
                    mask_style: next.mask_style ?? null,
                    min_confidence: next.min_confidence ?? null,
                    require_consensus: next.require_consensus ?? null,
                });
            }
        },
        onSuccess: () => {
            toast.success(`Saved ${dirtyClasses.length} categor${dirtyClasses.length === 1 ? "y" : "ies"}`);
            setPending({});
            qc.invalidateQueries({ queryKey: ["filtering", "policy"] });
        },
        onError: (e) => toast.error(`Save failed: ${e.message}`),
    });
    const drawerCategory = drawerOpenFor
        ? data.categories.find((c) => c.data_class === drawerOpenFor) ?? null
        : null;
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("header", { className: "flex items-start justify-between gap-4", children: [_jsxs("div", { children: [_jsxs("h2", { className: "text-2xl font-bold flex items-center gap-2", children: [_jsx(ShieldCheck, { size: 22, className: "text-spark-accent" }), "Filtering"] }), _jsx("p", { className: "text-spark-muted text-sm mt-1 max-w-3xl", children: "Per-category control over the data-class guardrails. Pick a redaction style, choose which detectors run, and dry-run sample text before saving. Every save is audited at elevated severity." })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("button", { className: "btn btn-ghost text-sm", onClick: () => setDryRunOpen(true), children: [_jsx(FlaskConical, { size: 14, className: "mr-1.5 inline" }), "Dry-run"] }), isDirty && (_jsxs("button", { className: "btn text-sm", onClick: () => setPending({}), children: [_jsx(RotateCcw, { size: 14, className: "mr-1.5 inline" }), "Discard (", dirtyClasses.length, ")"] })), _jsxs("button", { className: "btn btn-primary text-sm", disabled: !isDirty || saveMutation.isPending, onClick: () => saveMutation.mutate(), children: [_jsx(Save, { size: 14, className: "mr-1.5 inline" }), saveMutation.isPending
                                        ? "Saving…"
                                        : isDirty
                                            ? `Save ${dirtyClasses.length}`
                                            : "Saved"] })] })] }), data.families.map((fam) => {
                const cats = data.categories.filter((c) => c.family === fam.id);
                if (cats.length === 0)
                    return null;
                return (_jsxs("section", { children: [_jsxs("h3", { className: "text-sm font-semibold tracking-wide uppercase text-spark-muted mb-3 flex items-center gap-2", children: [_jsx("span", { "aria-hidden": true, children: FAMILY_ICON[fam.id] ?? "•" }), fam.label] }), _jsx("div", { className: "grid grid-cols-1 lg:grid-cols-2 gap-4", children: cats.map((cat) => (_jsx(CategoryCard, { cat: cat, pending: pending[cat.data_class] || null, maskStyles: data.mask_styles, onChange: (patch) => setEdit(cat.data_class, patch), onDiscard: () => discardEdit(cat.data_class), onAdvanced: () => setDrawerOpenFor(cat.data_class) }, cat.data_class))) })] }, fam.id));
            }), drawerCategory && (_jsx(DetectorDrawer, { cat: drawerCategory, onClose: () => setDrawerOpenFor(null), onChanged: () => qc.invalidateQueries({ queryKey: ["filtering", "policy"] }) })), dryRunOpen && (_jsx(DryRunSandbox, { categories: data.categories, onClose: () => setDryRunOpen(false) }))] }));
}
function effectiveCategory(cat) {
    const o = cat.global_override;
    return {
        level: o?.level ?? cat.default_level,
        scopes: o?.scopes ?? cat.default_scopes,
        mask_style: o?.mask_style ?? null,
        min_confidence: o?.min_confidence ?? null,
        require_consensus: o?.require_consensus ?? null,
        reason: o?.reason ?? "",
    };
}
// ---------------------------------------------------------------------------
// Category card
// ---------------------------------------------------------------------------
function CategoryCard({ cat, pending, maskStyles, onChange, onDiscard, onAdvanced, }) {
    const base = effectiveCategory(cat);
    const view = { ...base, ...(pending || {}) };
    const isDirty = pending !== null;
    const overrideCount = Object.keys(cat.global_override?.detector_overrides ?? {}).length;
    const totalDetectors = cat.detectors.length;
    return (_jsxs("div", { className: `panel p-4 ${isDirty ? "ring-1 ring-amber-400/60" : ""}`, children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("code", { className: "font-mono text-sm font-semibold text-spark-text", children: cat.data_class }), _jsx(LevelChip, { level: view.level }), isDirty && (_jsx("span", { className: "chip chip-warn text-[10px]", children: "unsaved" }))] }), _jsx("p", { className: "text-xs text-spark-muted mt-1 line-clamp-2", children: cat.description })] }), isDirty && (_jsx("button", { className: "btn-ghost btn-icon", onClick: onDiscard, title: "Discard", children: _jsx(RotateCcw, { size: 14 }) }))] }), _jsxs("div", { className: "grid grid-cols-2 gap-3 mt-4", children: [_jsxs("div", { children: [_jsx("label", { className: "label block mb-1", children: "Level" }), _jsx("select", { className: "input w-full text-sm", value: view.level, onChange: (e) => onChange({ level: e.target.value }), children: ["allow", "warn", "redact", "shadow_block", "block"].map((l) => (_jsx("option", { value: l, children: LEVEL_LABEL[l] }, l))) })] }), _jsxs("div", { children: [_jsx("label", { className: "label block mb-1", children: "Mask style" }), _jsx(MaskStyleSelector, { options: maskStyles, value: view.mask_style, dataClass: cat.data_class, defaultStyle: cat.default_mask_style, onChange: (v) => onChange({ mask_style: v }) })] })] }), _jsxs("div", { className: "mt-4", children: [_jsx("label", { className: "label block mb-1.5", children: "Scopes" }), _jsx("div", { className: "flex flex-wrap gap-2", children: ALL_SCOPES.map((s) => {
                            const active = view.scopes.includes(s);
                            return (_jsx("button", { onClick: () => onChange({
                                    scopes: active
                                        ? view.scopes.filter((x) => x !== s)
                                        : [...view.scopes, s],
                                }), className: `chip text-[11px] ${active ? "chip-info" : ""}`, children: s }, s));
                        }) })] }), _jsxs("div", { className: "grid grid-cols-2 gap-3 mt-4", children: [_jsxs("div", { children: [_jsxs("label", { className: "label flex items-center justify-between mb-1", children: [_jsx("span", { children: "Min confidence" }), _jsx("span", { className: "font-mono text-spark-muted", children: (view.min_confidence ?? cat.default_min_confidence).toFixed(2) })] }), _jsx("input", { type: "range", min: 0, max: 1, step: 0.05, value: view.min_confidence ?? cat.default_min_confidence, onChange: (e) => onChange({ min_confidence: Number(e.target.value) }), className: "w-full" })] }), _jsxs("div", { children: [_jsx("label", { className: "label block mb-1", children: "Consensus" }), _jsxs("select", { className: "input w-full text-sm", value: view.require_consensus === null
                                    ? "default"
                                    : view.require_consensus
                                        ? "require"
                                        : "off", onChange: (e) => {
                                    const v = e.target.value;
                                    onChange({
                                        require_consensus: v === "default" ? null : v === "require" ? true : false,
                                    });
                                }, children: [_jsxs("option", { value: "default", children: ["Default (", cat.default_require_consensus ? "required" : "off", ")"] }), _jsx("option", { value: "require", children: "Require 2+ detectors" }), _jsx("option", { value: "off", children: "Single detector OK" })] })] })] }), _jsxs("div", { className: "mt-4 flex items-center justify-between", children: [_jsxs("button", { className: "btn-ghost btn-icon text-xs flex items-center gap-1.5", onClick: onAdvanced, children: [_jsx(Settings2, { size: 13 }), "Advanced \u2014 ", totalDetectors, " detector", totalDetectors === 1 ? "" : "s", overrideCount > 0 && (_jsxs("span", { className: "chip chip-warn text-[10px]", children: [overrideCount, " override", overrideCount === 1 ? "" : "s"] }))] }), cat.global_override?.updated_by && (_jsxs("span", { className: "text-[11px] text-spark-muted", children: ["edited by ", cat.global_override.updated_by] }))] })] }));
}
function LevelChip({ level }) {
    const className = (() => {
        switch (level) {
            case "block":
                return "chip-danger";
            case "shadow_block":
                return "chip-danger";
            case "redact":
                return "chip-warn";
            case "warn":
                return "chip-info";
            case "allow":
                return "chip-good";
        }
    })();
    return _jsx("span", { className: `chip ${className} text-[11px]`, children: LEVEL_LABEL[level] });
}
// ---------------------------------------------------------------------------
// Advanced drawer — per-detector toggles
// ---------------------------------------------------------------------------
function DetectorDrawer({ cat, onClose, onChanged, }) {
    const overrides = cat.global_override?.detector_overrides ?? {};
    const toggle = useMutation({
        mutationFn: async ({ ruleId, enabled, }) => {
            await api.put(`/api/filtering/policy/category/${cat.data_class}/detector/${encodeURIComponent(ruleId)}`, { enabled });
        },
        onSuccess: () => {
            onChanged();
        },
        onError: (e) => toast.error(`Update failed: ${e.message}`),
    });
    return (_jsx(Modal, { open: true, onClose: onClose, children: _jsxs("div", { className: "panel w-[640px] max-w-full max-h-[80vh] overflow-hidden flex flex-col", children: [_jsxs("div", { className: "p-4 border-b border-spark-border flex items-start justify-between", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-spark-muted uppercase tracking-wide", children: "Advanced" }), _jsxs("h3", { className: "text-lg font-semibold mt-0.5", children: [_jsx("code", { className: "font-mono", children: cat.data_class }), " detectors"] }), _jsx("p", { className: "text-xs text-spark-muted mt-1 max-w-md", children: "Per-detector toggles. Disabling a detector here suppresses its hits across every scope this category covers, no matter the level." })] }), _jsx("button", { className: "btn btn-ghost text-sm", onClick: onClose, children: "Done" })] }), _jsxs("div", { className: "overflow-y-auto p-4 space-y-2", children: [cat.detectors.length === 0 && (_jsx("div", { className: "text-sm text-spark-muted", children: "No detectors registered for this category." })), cat.detectors.map((d) => {
                            const ov = overrides[d.rule_id];
                            const enabled = ov?.enabled !== false;
                            return (_jsxs("div", { className: "flex items-start justify-between gap-3 p-2.5 border border-spark-border rounded-md hover:border-spark-accent/40 transition-colors", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "text-sm font-medium", children: d.label }), d.tier === "tier2" && (_jsx("span", { className: "chip text-[9px]", children: "tier 2" }))] }), _jsx("code", { className: "block font-mono text-[10px] text-spark-muted", children: d.rule_id }), _jsx("p", { className: "text-xs text-spark-muted mt-0.5", children: d.description })] }), _jsxs("label", { className: "flex items-center gap-2 text-xs whitespace-nowrap pt-1", children: [_jsx("input", { type: "checkbox", checked: enabled, onChange: () => toggle.mutate({
                                                    ruleId: d.rule_id,
                                                    enabled: enabled ? false : null,
                                                }) }), enabled ? (_jsx("span", { className: "text-spark-text", children: "Enabled" })) : (_jsx("span", { className: "text-spark-muted", children: "Disabled" }))] })] }, d.rule_id));
                        })] })] }) }));
}
function DryRunSandbox({ categories, onClose, }) {
    const [text, setText] = useState("Hi, I'm Jane Doe. My card is 4111-1111-1111-1234 and my AWS key is AKIAIOSFODNN7EXAMPLE.");
    const [scope, setScope] = useState("model_output");
    const [agent, setAgent] = useState("");
    const [result, setResult] = useState(null);
    const run = useMutation({
        mutationFn: async () => api.post("/api/filtering/dry-run", {
            text,
            scope,
            agent_name: agent.trim() || null,
        }),
        onSuccess: (r) => setResult(r),
        onError: (e) => toast.error(`Dry-run failed: ${e.message}`),
    });
    const ruleLabel = useMemo(() => {
        const map = {};
        for (const c of categories) {
            for (const d of c.detectors)
                map[d.rule_id] = d.label;
        }
        return map;
    }, [categories]);
    return (_jsx(Modal, { open: true, onClose: onClose, children: _jsxs("div", { className: "panel w-[920px] max-w-full max-h-[85vh] overflow-hidden flex flex-col", children: [_jsxs("div", { className: "p-4 border-b border-spark-border flex items-start justify-between", children: [_jsxs("div", { children: [_jsxs("div", { className: "text-xs text-spark-muted uppercase tracking-wide flex items-center gap-1.5", children: [_jsx(FlaskConical, { size: 12 }), " Sandbox"] }), _jsx("h3", { className: "text-lg font-semibold mt-0.5", children: "Dry-run filtering" }), _jsx("p", { className: "text-xs text-spark-muted mt-1 max-w-2xl", children: "Paste sample text, pick a scope and (optionally) an agent. Runs the resolved policy without persisting anything \u2014 the run itself is recorded as info-severity audit so we can spot abusive use." })] }), _jsx("button", { className: "btn btn-ghost text-sm", onClick: onClose, children: "Close" })] }), _jsxs("div", { className: "overflow-y-auto p-4 space-y-4", children: [_jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("div", { children: [_jsx("label", { className: "label block mb-1", children: "Scope" }), _jsx("select", { className: "input w-full text-sm", value: scope, onChange: (e) => setScope(e.target.value), children: ALL_SCOPES.map((s) => (_jsx("option", { value: s, children: s }, s))) })] }), _jsxs("div", { children: [_jsx("label", { className: "label block mb-1", children: "Agent (optional \u2014 leave blank for global)" }), _jsx("input", { className: "input w-full text-sm", placeholder: "my-agent", value: agent, onChange: (e) => setAgent(e.target.value) })] })] }), _jsxs("div", { children: [_jsx("label", { className: "label block mb-1", children: "Input" }), _jsx("textarea", { className: "input w-full text-sm font-mono", rows: 5, value: text, onChange: (e) => setText(e.target.value) })] }), _jsx("button", { className: "btn btn-primary text-sm", disabled: run.isPending || text.trim().length === 0, onClick: () => run.mutate(), children: run.isPending ? "Running…" : "Run" }), result && (_jsxs("div", { className: "space-y-3", children: [result.blocked ? (_jsxs("div", { className: "panel p-3 border-spark-danger/50 bg-spark-danger/5", children: [_jsxs("div", { className: "flex items-center gap-2 text-spark-danger", children: [_jsx(AlertTriangle, { size: 14 }), _jsx("strong", { className: "text-sm", children: "Blocked" }), _jsx("code", { className: "text-xs", children: result.error_code })] }), _jsx("p", { className: "text-sm mt-1", children: result.message })] })) : (_jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("div", { children: [_jsx("div", { className: "label mb-1", children: "Input" }), _jsx("pre", { className: "panel p-2 text-xs font-mono whitespace-pre-wrap break-words", children: result.input })] }), _jsxs("div", { children: [_jsx("div", { className: "label mb-1", children: "Redacted output" }), _jsx("pre", { className: "panel p-2 text-xs font-mono whitespace-pre-wrap break-words", children: result.output ?? "(blocked)" })] })] })), _jsxs("div", { children: [_jsxs("div", { className: "label mb-1", children: ["Hits (", result.hits.length, ")"] }), result.hits.length === 0 ? (_jsx("p", { className: "text-sm text-spark-muted", children: "Nothing matched. The category levels you have set didn't fire on this input." })) : (_jsxs("table", { className: "w-full text-xs", children: [_jsx("thead", { className: "text-spark-muted", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left py-1.5 pr-3", children: "Class" }), _jsx("th", { className: "text-left py-1.5 pr-3", children: "Detector" }), _jsx("th", { className: "text-left py-1.5 pr-3", children: "Match" }), _jsx("th", { className: "text-left py-1.5 pr-3", children: "Tier" }), _jsx("th", { className: "text-right py-1.5", children: "Confidence" })] }) }), _jsx("tbody", { className: "divide-y divide-spark-border", children: result.hits.map((h, i) => (_jsxs("tr", { children: [_jsx("td", { className: "py-1.5 pr-3 font-mono", children: h.data_class }), _jsx("td", { className: "py-1.5 pr-3", children: ruleLabel[h.rule_id] ?? h.rule_id }), _jsx("td", { className: "py-1.5 pr-3 font-mono text-spark-muted truncate max-w-[180px]", children: h.matched }), _jsx("td", { className: "py-1.5 pr-3", children: _jsx("span", { className: "chip text-[10px]", children: h.tier }) }), _jsx("td", { className: "py-1.5 text-right font-mono", children: h.confidence.toFixed(2) })] }, i))) })] }))] })] }))] })] }) }));
}
