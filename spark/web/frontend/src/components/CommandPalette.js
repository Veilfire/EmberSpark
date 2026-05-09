import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Modal } from "./Modal";
export function CommandPalette() {
    const navigate = useNavigate();
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    useEffect(() => {
        function onKey(e) {
            // ⌘K / Ctrl+K is the one and only entry point. Escape closes.
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                setOpen((v) => !v);
                return;
            }
            if (e.key === "Escape")
                setOpen(false);
        }
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, []);
    const commands = [
        { label: "Overview", hint: "home dashboard", go: () => navigate("/") },
        { label: "Provider", hint: "LLM provider + API key setup", go: () => navigate("/provider") },
        { label: "Agents", hint: "installed agents + health", go: () => navigate("/agents") },
        { label: "Chat", hint: "conversational session", go: () => navigate("/chat") },
        { label: "Runs", hint: "task run history", go: () => navigate("/runs") },
        { label: "Persona", hint: "edit agent system prompt", go: () => navigate("/persona") },
        { label: "Plugins", hint: "configure plugin behavior", go: () => navigate("/plugins") },
        { label: "Scheduler", hint: "agents + tasks + schedules", go: () => navigate("/scheduler") },
        { label: "Cost", hint: "spend + budgets", go: () => navigate("/cost") },
        { label: "Memory", hint: "long-term memory + playbooks", go: () => navigate("/memory") },
        { label: "Skills", hint: "skill catalog + reviews", go: () => navigate("/skills") },
        { label: "Stats", hint: "rolling agent metrics", go: () => navigate("/stats") },
        { label: "Guardrails", hint: "redactions + denials + incidents", go: () => navigate("/guardrails") },
        { label: "Security Center", hint: "policy editor", go: () => navigate("/security") },
        { label: "Audit Log", hint: "immutable change history", go: () => navigate("/audit") },
        { label: "Ops", hint: "host health + logs", go: () => navigate("/ops") },
        { label: "Downloads", hint: "agent deliverables + downloads", go: () => navigate("/downloads") },
        { label: "Settings", hint: "notification preferences", go: () => navigate("/settings") },
        { label: "Templates", hint: "ready-to-run agent templates", go: () => navigate("/templates") },
        { label: "Forensic", hint: "per-run chain-of-thought viewer (admin)", go: () => navigate("/forensic") },
    ];
    const filtered = query
        ? commands.filter((c) => c.label.toLowerCase().includes(query.toLowerCase()) ||
            c.hint.toLowerCase().includes(query.toLowerCase()))
        : commands;
    return (_jsx(Modal, { open: open, onClose: () => setOpen(false), children: _jsxs("div", { className: "w-[640px] max-w-[92vw] panel shadow-2xl mt-[10vh] self-start", children: [_jsx("input", { autoFocus: true, className: "input w-full border-0 border-b border-spark-border rounded-b-none rounded-t-lg text-base px-4 py-3 focus:ring-0", placeholder: "Type to search\u2026  \u00B7  \u2318K to toggle  \u00B7  esc to close", value: query, onChange: (e) => setQuery(e.target.value) }), _jsxs("ul", { className: "max-h-[60vh] overflow-auto py-1", children: [filtered.length === 0 && (_jsxs("li", { className: "px-4 py-6 text-center text-sm text-spark-muted", children: ["No commands match \"", query, "\""] })), filtered.map((c) => (_jsx("li", { children: _jsxs("button", { className: "w-full text-left px-4 py-2 text-sm hover:bg-spark-border/50 transition-colors", onClick: () => {
                                    setOpen(false);
                                    c.go();
                                }, children: [_jsx("div", { className: "font-semibold", children: c.label }), _jsx("div", { className: "text-xs text-spark-muted", children: c.hint })] }) }, c.label)))] })] }) }));
}
