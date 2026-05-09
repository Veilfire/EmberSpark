import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Modal } from "./Modal";

interface Command {
  label: string;
  hint: string;
  go: () => void;
}

export function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // ⌘K / Ctrl+K is the one and only entry point. Escape closes.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const commands: Command[] = [
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
    ? commands.filter(
        (c) =>
          c.label.toLowerCase().includes(query.toLowerCase()) ||
          c.hint.toLowerCase().includes(query.toLowerCase())
      )
    : commands;

  return (
    <Modal open={open} onClose={() => setOpen(false)}>
      <div className="w-[640px] max-w-[92vw] panel shadow-2xl mt-[10vh] self-start">
        <input
          autoFocus
          className="input w-full border-0 border-b border-spark-border rounded-b-none rounded-t-lg text-base px-4 py-3 focus:ring-0"
          placeholder="Type to search…  ·  ⌘K to toggle  ·  esc to close"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ul className="max-h-[60vh] overflow-auto py-1">
          {filtered.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-spark-muted">
              No commands match "{query}"
            </li>
          )}
          {filtered.map((c) => (
            <li key={c.label}>
              <button
                className="w-full text-left px-4 py-2 text-sm hover:bg-spark-border/50 transition-colors"
                onClick={() => {
                  setOpen(false);
                  c.go();
                }}
              >
                <div className="font-semibold">{c.label}</div>
                <div className="text-xs text-spark-muted">{c.hint}</div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </Modal>
  );
}
