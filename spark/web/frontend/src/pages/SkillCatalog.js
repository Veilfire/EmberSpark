import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
export default function SkillCatalog() {
    const client = useQueryClient();
    const pending = useQuery({
        queryKey: ["skills-pending"],
        queryFn: () => api.get("/api/skills/pending"),
    });
    const [agent, setAgent] = useState("");
    const approved = useQuery({
        queryKey: ["skills-approved", agent],
        queryFn: () => agent
            ? api.get(`/api/skills/approved/${encodeURIComponent(agent)}`)
            : Promise.resolve([]),
        enabled: !!agent,
    });
    const decide = useMutation({
        mutationFn: (args) => api.post(`/api/skills/reviews/${encodeURIComponent(args.review_id)}`, {
            decision: args.decision,
            notes: args.notes,
            final_name: args.final_name,
            final_description: args.final_description,
        }),
        onSuccess: () => {
            client.invalidateQueries({ queryKey: ["skills-pending"] });
            client.invalidateQueries({ queryKey: ["skills-approved"] });
        },
    });
    return (_jsxs("div", { className: "space-y-6", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Skills" }), _jsxs("p", { className: "text-spark-muted text-sm", children: ["Agent skills awaiting review. ", _jsx("em", { children: "API" }), " skills come from the discovery engine crawling docs; ", _jsx("em", { children: "behavior" }), " and", " ", _jsx("em", { children: "knowledge" }), " skills come from agents themselves via the", " ", _jsx("code", { children: "propose_skill" }), " plugin. Approve to register, reject to discard."] })] }), _jsx(PendingPanel, { pending: pending.data ?? [], onApprove: (p, final_name, final_description, notes) => decide.mutate({
                    review_id: p.review_id,
                    decision: "approve",
                    final_name,
                    final_description,
                    notes,
                }), onReject: (p, notes) => decide.mutate({
                    review_id: p.review_id,
                    decision: "reject",
                    notes,
                }) }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Approved skills" }), _jsx("input", { className: "input mb-3 w-80", placeholder: "agent name", value: agent, onChange: (e) => setAgent(e.target.value) }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "name" }), _jsx("th", { className: "text-left", children: "service" }), _jsx("th", { className: "text-left", children: "auth" }), _jsx("th", { className: "text-left", children: "hosts" }), _jsx("th", { className: "text-left", children: "secrets" }), _jsx("th", { className: "text-left", children: "uses" }), _jsx("th", { className: "text-left", children: "status" })] }) }), _jsx("tbody", { children: (approved.data ?? []).map((s) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono", children: s.name }), _jsx("td", { children: s.service_name }), _jsx("td", { children: s.auth_method }), _jsx("td", { className: "text-xs", children: s.required_hosts.join(", ") }), _jsx("td", { className: "text-xs", children: s.required_secrets.join(", ") }), _jsx("td", { children: s.uses }), _jsx("td", { children: _jsx("span", { className: `chip ${s.status === "approved" ? "chip-good" : ""}`, children: s.status }) })] }, s.skill_id))) })] })] })] }));
}
function PendingPanel({ pending, onApprove, onReject, }) {
    const [filter, setFilter] = useState("all");
    const counts = {
        all: pending.length,
        api: pending.filter((p) => (p.kind ?? "api") === "api").length,
        behavior: pending.filter((p) => p.kind === "behavior").length,
        knowledge: pending.filter((p) => p.kind === "knowledge").length,
    };
    const visible = pending.filter((p) => filter === "all" ? true : (p.kind ?? "api") === filter);
    return (_jsxs("section", { className: "panel p-4", children: [_jsxs("div", { className: "flex items-center justify-between mb-3 gap-3 flex-wrap", children: [_jsxs("h3", { className: "font-semibold", children: ["Pending review (", pending.length, ")"] }), _jsx("div", { className: "flex gap-1 text-xs", children: ["all", "api", "behavior", "knowledge"].map((k) => (_jsxs("button", { type: "button", onClick: () => setFilter(k), className: `px-2 py-1 rounded-md border ${filter === k
                                ? "border-spark-link text-spark-text bg-spark-border/30"
                                : "border-spark-border text-spark-muted hover:text-spark-text"}`, children: [k, " (", counts[k], ")"] }, k))) })] }), _jsxs("div", { className: "space-y-3", children: [visible.map((p) => (_jsx(PendingCard, { skill: p, onApprove: (final_name, final_description, notes) => onApprove(p, final_name, final_description, notes), onReject: (notes) => onReject(p, notes) }, p.review_id))), visible.length === 0 && (_jsx("div", { className: "text-spark-muted text-sm", children: pending.length === 0
                            ? "No pending reviews."
                            : `No ${filter} skills pending. Switch the filter to see others.` }))] })] }));
}
function PendingCard({ skill, onApprove, onReject, }) {
    const [name, setName] = useState(skill.proposed_name);
    const [description, setDescription] = useState(skill.proposed_description);
    const [notes, setNotes] = useState("");
    const kind = skill.kind ?? "api";
    const isApi = kind === "api";
    const kindChipClass = kind === "api"
        ? "chip-good"
        : kind === "behavior"
            ? "chip-warn"
            : "";
    return (_jsxs("div", { className: "border border-spark-border rounded-lg p-3 space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between flex-wrap gap-2", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: "chip chip-warn", children: "pending" }), _jsx("span", { className: `chip ${kindChipClass}`, children: kind }), _jsx("span", { className: "font-semibold", children: isApi ? skill.service_name : skill.proposed_name }), _jsxs("span", { className: "text-spark-muted text-xs", children: ["from ", skill.agent_name] }), _jsxs("span", { className: "text-spark-muted text-xs", children: ["confidence ", (skill.confidence * 100).toFixed(0), "%"] })] }), skill.source_url && skill.source_url.startsWith("http") && (_jsx("a", { href: skill.source_url, target: "_blank", rel: "noreferrer", className: "text-spark-muted text-xs underline", children: "source" }))] }), skill.rationale && (_jsxs("div", { className: "border-l-2 border-spark-border pl-2 text-sm", children: [_jsx("span", { className: "label block", children: "Rationale" }), _jsx("span", { className: "text-spark-text", children: skill.rationale })] })), _jsxs("div", { className: "grid grid-cols-2 gap-2 text-sm", children: [_jsxs("label", { children: [_jsx("span", { className: "label", children: "Skill name" }), _jsx("input", { className: "input w-full", value: name, onChange: (e) => setName(e.target.value) })] }), isApi ? (_jsxs("label", { children: [_jsx("span", { className: "label", children: "Base URL" }), _jsx("input", { className: "input w-full font-mono text-xs", value: skill.base_url, readOnly: true })] })) : (_jsxs("div", { children: [_jsx("span", { className: "label", children: "Kind" }), _jsx("div", { className: "text-xs text-spark-muted", children: kind === "behavior"
                                    ? "How-to-think heuristic. No external service."
                                    : "Domain rule / fact. Surfaced via long-term memory after approval." })] }))] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Description" }), _jsx("textarea", { className: "input w-full h-16", value: description, onChange: (e) => setDescription(e.target.value) })] }), skill.examples && skill.examples.length > 0 && (_jsxs("div", { children: [_jsxs("span", { className: "label", children: ["Examples (", skill.examples.length, ")"] }), _jsx("ul", { className: "list-disc pl-5 text-xs text-spark-muted space-y-0.5", children: skill.examples.slice(0, 5).map((ex, i) => (_jsx("li", { children: ex }, i))) })] })), skill.success_criteria && (_jsxs("div", { className: "text-xs", children: [_jsx("span", { className: "label", children: "Success criteria" }), _jsx("div", { className: "text-spark-muted", children: skill.success_criteria })] })), isApi && (_jsxs("div", { className: "text-xs text-spark-muted grid grid-cols-2 gap-2", children: [_jsxs("div", { children: [_jsx("span", { className: "label", children: "required hosts" }), _jsx("div", { className: "font-mono", children: skill.required_hosts.join(", ") || "—" })] }), _jsxs("div", { children: [_jsx("span", { className: "label", children: "required secrets" }), _jsx("div", { className: "font-mono", children: skill.required_secrets.join(", ") || "—" })] })] })), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Review notes" }), _jsx("input", { className: "input w-full", value: notes, onChange: (e) => setNotes(e.target.value) })] }), _jsxs("div", { className: "flex gap-2 justify-end", children: [_jsx("button", { className: "btn btn-danger", onClick: () => onReject(notes), children: "Reject" }), _jsx("button", { className: "btn btn-primary", onClick: () => onApprove(name, description, notes), children: "Approve" })] })] }));
}
