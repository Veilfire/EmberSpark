import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Brain,
  CheckCircle,
  Globe,
  Lock,
  Plus,
  Search,
  ShieldAlert,
  Sparkles,
  Upload,
  Users,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
import { LongTermMemory, Playbook } from "../lib/types";
import { formatRelative } from "../lib/utils";
import { PageHeader } from "../components/PageHeader";

type PruningStatus = {
  config: {
    enabled: boolean;
    schedule: string;
    rollover_windows: {
      temporary: number | null;
      expiring: number | null;
      review: number | null;
      persistent: number | null;
    };
    dry_run: boolean;
    notify_on_prune: boolean;
  };
  next_run_at: string | null;
  last_run: {
    at: string | null;
    actor: string;
    total: number;
    by_class: Record<string, number>;
    namespaces: string[];
    dry_run: boolean;
  } | null;
};

type PruningReport = {
  total: number;
  by_class: Record<string, number>;
  namespaces: string[];
  dry_run: boolean;
};

export default function MemoryBrowser() {
  const [tab, setTab] = useState<
    "index" | "playbooks" | "pruning" | "review" | "visualize" | "circles"
  >("index");
  const [namespace, setNamespace] = useState("");
  const [agent, setAgent] = useState("");
  const [search, setSearch] = useState("");
  const [sensitivityFilter, setSensitivityFilter] = useState<string>("");
  const [retentionFilter, setRetentionFilter] = useState<string>("");
  const [scope, setScope] = useState<"all" | "private" | "global">("all");
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
  const memories = useQuery<LongTermMemory[]>({
    queryKey: ["memories", namespace, scope],
    queryFn: () => {
      const params = new URLSearchParams();
      if (namespace) params.set("namespace", namespace);
      if (scope !== "all") params.set("scope", scope);
      const qs = params.toString();
      return api.get(`/api/memory/long-term${qs ? `?${qs}` : ""}`);
    },
  });

  async function promoteToGlobal(memoryId: string) {
    try {
      await api.post(
        `/api/memory/long-term/${encodeURIComponent(memoryId)}/promote-to-global`,
      );
      toast.success("Promoted to global pool");
      memories.refetch();
    } catch (err) {
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
    } catch (err) {
      toast.error(`Create failed: ${err}`);
    }
  }

  async function approveMemory(memoryId: string) {
    try {
      await api.post(
        `/api/memory/long-term/${encodeURIComponent(memoryId)}/approve`,
      );
      toast.success("Memory approved");
      memories.refetch();
    } catch (err) {
      toast.error(`Approve failed: ${err}`);
    }
  }

  async function quarantineMemory(memoryId: string) {
    try {
      await api.post(
        `/api/memory/long-term/${encodeURIComponent(memoryId)}/quarantine`,
      );
      toast.success("Quarantined");
      memories.refetch();
    } catch (err) {
      toast.error(`Quarantine failed: ${err}`);
    }
  }
  const playbooks = useQuery<Playbook[]>({
    queryKey: ["playbooks", agent],
    queryFn: () =>
      agent
        ? api.get(`/api/memory/playbooks/${encodeURIComponent(agent)}`)
        : Promise.resolve([]),
    enabled: !!agent,
  });

  const filteredMemories = useMemo(() => {
    const items = memories.data ?? [];
    const q = search.toLowerCase();
    return items.filter((m) => {
      if (q && !m.content_summary.toLowerCase().includes(q)) return false;
      if (sensitivityFilter && m.sensitivity !== sensitivityFilter) return false;
      if (retentionFilter && m.retention_class !== retentionFilter) return false;
      return true;
    });
  }, [memories.data, search, sensitivityFilter, retentionFilter]);

  const pruning = useQuery<PruningStatus>({
    queryKey: ["memory-pruning-status"],
    queryFn: () => api.get("/api/memory/pruning/status"),
    enabled: tab === "pruning",
    refetchInterval: tab === "pruning" ? 15000 : false,
  });

  async function del(memoryId: string) {
    const ok = await confirmDialog({
      title: "Delete memory permanently?",
      description:
        "The record is removed from both the SQLite index and the Chroma vector store. This cannot be undone.",
      tone: "danger",
      confirmLabel: "Delete memory",
    });
    if (!ok) return;
    await api.del(`/api/memory/long-term/${encodeURIComponent(memoryId)}`);
    memories.refetch();
  }

  async function runDryRun() {
    try {
      const report = await api.post<PruningReport>(
        "/api/memory/pruning/dry-run",
        {},
      );
      const summary = Object.entries(report.by_class)
        .map(([cls, n]) => `${cls}:${n}`)
        .join(", ");
      toast.success(
        `Dry-run: ${report.total} rows would be pruned${summary ? ` (${summary})` : ""}`,
      );
      pruning.refetch();
    } catch (err) {
      toast.error(`Dry-run failed: ${err}`);
    }
  }

  async function runExecute() {
    const ok = await confirmDialog({
      title: "Run a live pruning sweep now?",
      description:
        "Rows past their rollover window will be permanently deleted from both SQLite and the vector store. Use the dry-run button first if you want to see counts without deleting.",
      tone: "danger",
      confirmLabel: "Run sweep",
    });
    if (!ok) return;
    try {
      const report = await api.post<PruningReport>(
        "/api/memory/pruning/execute",
        {},
      );
      toast.success(`Pruned ${report.total} memories`);
      pruning.refetch();
      memories.refetch();
    } catch (err) {
      toast.error(`Prune failed: ${err}`);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<Brain className="w-6 h-6" />}
        title="Memory"
        subtitle="Long-term memory (Chroma) + learning playbooks (SQLite) + retention pruning."
      />

      <div className="flex gap-2 border-b border-spark-border">
        {(
          [
            "index",
            "review",
            "visualize",
            "circles",
            "playbooks",
            "pruning",
          ] as const
        ).map((t) => (
          <button
            key={t}
            className={`px-3 py-2 text-sm capitalize ${
              tab === t
                ? "border-b-2 border-spark-accent text-spark-text"
                : "text-spark-muted hover:text-spark-text"
            }`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "index" && (
      <section className="panel p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">
            Long-term memory index ({filteredMemories.length} of{" "}
            {memories.data?.length ?? 0})
          </h3>
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={() => setShowCreate(true)}
          >
            <Plus className="w-3.5 h-3.5" /> Add memory
          </button>
        </div>
        <div className="flex flex-wrap gap-2 mb-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="w-4 h-4 text-spark-muted absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              className="input w-full pl-9"
              placeholder="Search summaries…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <input
            className="input w-40"
            placeholder="namespace"
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
          />
          <select
            className="input"
            value={scope}
            onChange={(e) =>
              setScope(e.target.value as "all" | "private" | "global")
            }
            title="Memory scope"
          >
            <option value="all">all scopes</option>
            <option value="private">private only</option>
            <option value="global">global only</option>
          </select>
          <select
            className="input"
            value={sensitivityFilter}
            onChange={(e) => setSensitivityFilter(e.target.value)}
          >
            <option value="">all sensitivity</option>
            <option value="low">low</option>
            <option value="moderate">moderate</option>
            <option value="high">high</option>
            <option value="restricted">restricted</option>
          </select>
          <select
            className="input"
            value={retentionFilter}
            onChange={(e) => setRetentionFilter(e.target.value)}
          >
            <option value="">all retention</option>
            <option value="temporary">temporary</option>
            <option value="expiring">expiring</option>
            <option value="review">review</option>
            <option value="persistent">persistent</option>
          </select>
        </div>
        {filteredMemories.length === 0 ? (
          <p className="text-center text-spark-muted text-sm py-6">
            No memories match these filters.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-spark-muted text-xs uppercase">
              <tr>
                <th className="text-left pb-2">ID</th>
                <th className="text-left pb-2">Namespace</th>
                <th className="text-left pb-2">Type</th>
                <th className="text-left pb-2">Sensitivity</th>
                <th className="text-left pb-2">Retention</th>
                <th className="text-right pb-2 tabular-nums">Confidence</th>
                <th className="text-left pb-2">Summary</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {filteredMemories.map((m) => (
                <tr
                  key={m.memory_id}
                  className="border-t border-spark-border hover:bg-spark-border/20"
                >
                  <td className="py-1.5 font-mono text-xs">
                    {m.memory_id.slice(0, 10)}…
                  </td>
                  <td>
                    <div className="flex flex-wrap items-center gap-1">
                      {m.is_global ? (
                        <span
                          className="chip chip-warn text-[10px] gap-1"
                          title="Shared across all agents"
                        >
                          <Globe className="w-3 h-3" /> global
                        </span>
                      ) : m.namespace === "__consensus__" ? (
                        <span
                          className="chip chip-info text-[10px] gap-1"
                          title="Multi-agent consensus"
                        >
                          <Sparkles className="w-3 h-3" /> consensus
                        </span>
                      ) : (
                        <span
                          className="chip text-[10px] gap-1"
                          title={`Private to ${m.agent_name}`}
                        >
                          <Lock className="w-3 h-3" />
                          {m.namespace}
                        </span>
                      )}
                      {m.is_anti_pattern && (
                        <span
                          className="chip chip-danger text-[10px] gap-1"
                          title="Anti-pattern (don't do this)"
                        >
                          <AlertTriangle className="w-3 h-3" /> avoid
                        </span>
                      )}
                      {m.contradicts_with && (
                        <span
                          className="chip chip-warn text-[10px] gap-1"
                          title={`Contradicts: ${m.contradicts_with}`}
                        >
                          ⚠ contradicts
                        </span>
                      )}
                      {m.status && m.status !== "active" && (
                        <span
                          className="chip chip-warn text-[10px] gap-1"
                          title={`Status: ${m.status}`}
                        >
                          <ShieldAlert className="w-3 h-3" /> {m.status}
                        </span>
                      )}
                    </div>
                  </td>
                  <td>
                    <span className="chip text-[10px]">{m.memory_type}</span>
                  </td>
                  <td>
                    <span
                      className={`chip text-[10px] ${
                        m.sensitivity === "restricted" || m.sensitivity === "high"
                          ? "chip-warn"
                          : ""
                      }`}
                    >
                      {m.sensitivity}
                    </span>
                  </td>
                  <td className="text-xs">{m.retention_class}</td>
                  <td className="text-right tabular-nums">
                    {(m.confidence ?? 0).toFixed(2)}
                  </td>
                  <td className="text-spark-muted max-w-md truncate">
                    {m.content_summary}
                  </td>
                  <td>
                    <div className="flex items-center gap-1 justify-end">
                      {!m.is_global && m.sensitivity !== "restricted" && (
                        <button
                          className="btn-icon hover:text-spark-accent"
                          onClick={() => promoteToGlobal(m.memory_id)}
                          title="Promote to global (shared pool)"
                        >
                          <Upload className="w-3.5 h-3.5" />
                        </button>
                      )}
                      <button
                        className="btn-icon hover:text-spark-danger"
                        onClick={() => del(m.memory_id)}
                        title="Delete"
                      >
                        ×
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      )}

      {tab === "review" && (
        <ReviewQueueTab
          onApprove={approveMemory}
          onQuarantine={quarantineMemory}
          onDelete={del}
        />
      )}

      {tab === "visualize" && <VisualizeTab namespace={namespace} />}

      {tab === "circles" && <CirclesTab />}

      {tab === "playbooks" && (
      <section className="panel p-4">
        <h3 className="font-semibold mb-2">Playbooks</h3>
        <input
          className="input mb-3 w-80"
          placeholder="agent name"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
        />
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">name</th>
              <th className="text-left">uses</th>
              <th className="text-left">success rate</th>
              <th className="text-left">avg tools</th>
              <th className="text-left">last success</th>
              <th className="text-left">sequence</th>
            </tr>
          </thead>
          <tbody>
            {(playbooks.data ?? []).map((p) => (
              <tr key={p.playbook_id} className="border-t border-spark-border">
                <td className="py-1 font-mono">{p.name}</td>
                <td>{p.uses}</td>
                <td>{(p.success_rate * 100).toFixed(0)}%</td>
                <td>{p.avg_tool_calls.toFixed(1)}</td>
                <td>{formatRelative(p.last_success_at)}</td>
                <td className="font-mono text-xs">{p.tool_sequence.join(" → ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      )}

      {tab === "pruning" && (
      <section className="panel p-4">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h3 className="font-semibold">Retention pruning</h3>
            <p className="text-spark-muted text-xs mt-1">
              Scheduled sweep deletes long-term memory rows whose retention class has aged past its window.
            </p>
          </div>
          <div className="flex gap-2">
            <button className="btn" onClick={runDryRun}>
              Run dry-run now
            </button>
            <button className="btn btn-danger" onClick={runExecute}>
              Run now
            </button>
          </div>
        </div>

        {pruning.isLoading && <p className="text-spark-muted text-sm">loading…</p>}
        {pruning.data && (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <h4 className="text-xs uppercase text-spark-muted">Configuration</h4>
              <dl className="text-sm space-y-1">
                <div className="flex gap-2">
                  <dt className="text-spark-muted w-36">Enabled</dt>
                  <dd>{pruning.data.config.enabled ? "yes" : "no"}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-spark-muted w-36">Schedule</dt>
                  <dd className="font-mono">{pruning.data.config.schedule}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-spark-muted w-36">Next run</dt>
                  <dd>{formatRelative(pruning.data.next_run_at) ?? "—"}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-spark-muted w-36">Dry-run mode</dt>
                  <dd>{pruning.data.config.dry_run ? "on" : "off"}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-spark-muted w-36">Notify on prune</dt>
                  <dd>{pruning.data.config.notify_on_prune ? "yes" : "no"}</dd>
                </div>
              </dl>
              <h4 className="text-xs uppercase text-spark-muted mt-4">
                Rollover windows (days)
              </h4>
              <dl className="text-sm space-y-1">
                {(["temporary", "expiring", "review", "persistent"] as const).map(
                  (cls) => (
                    <div className="flex gap-2" key={cls}>
                      <dt className="text-spark-muted w-36">{cls}</dt>
                      <dd>
                        {pruning.data!.config.rollover_windows[cls] ?? "never prune"}
                      </dd>
                    </div>
                  ),
                )}
              </dl>
            </div>
            <div className="space-y-2">
              <h4 className="text-xs uppercase text-spark-muted">Last run</h4>
              {pruning.data.last_run ? (
                <dl className="text-sm space-y-1">
                  <div className="flex gap-2">
                    <dt className="text-spark-muted w-36">When</dt>
                    <dd>{formatRelative(pruning.data.last_run.at) ?? "—"}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-spark-muted w-36">Actor</dt>
                    <dd className="font-mono text-xs">
                      {pruning.data.last_run.actor}
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-spark-muted w-36">Total</dt>
                    <dd>
                      {pruning.data.last_run.total}{" "}
                      {pruning.data.last_run.dry_run && (
                        <span className="chip">dry-run</span>
                      )}
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-spark-muted w-36">By class</dt>
                    <dd className="font-mono text-xs">
                      {Object.keys(pruning.data.last_run.by_class).length
                        ? Object.entries(pruning.data.last_run.by_class)
                            .map(([k, v]) => `${k}:${v}`)
                            .join(", ")
                        : "—"}
                    </dd>
                  </div>
                  {pruning.data.last_run.namespaces.length > 0 && (
                    <div className="flex gap-2">
                      <dt className="text-spark-muted w-36">Namespaces</dt>
                      <dd className="font-mono text-xs">
                        {pruning.data.last_run.namespaces.join(", ")}
                      </dd>
                    </div>
                  )}
                </dl>
              ) : (
                <p className="text-spark-muted text-sm">No pruning runs recorded yet.</p>
              )}
            </div>
          </div>
        )}
      </section>
      )}

      {/* Manual create modal */}
      {showCreate && (
        <div
          className="fixed inset-0 bg-black/70 z-[100] flex items-center justify-center p-4"
          onClick={() => setShowCreate(false)}
        >
          <div
            className="bg-spark-panel border border-spark-border rounded-lg w-full max-w-lg p-6 space-y-3 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Add memory</h3>
              <button className="btn-icon" onClick={() => setShowCreate(false)}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div>
              <label className="text-xs uppercase text-spark-muted block mb-1">
                Agent name
              </label>
              <input
                className="input w-full font-mono"
                placeholder="research-assistant"
                value={newMem.agent_name}
                onChange={(e) =>
                  setNewMem({ ...newMem, agent_name: e.target.value })
                }
              />
            </div>
            <div>
              <label className="text-xs uppercase text-spark-muted block mb-1">
                Summary
              </label>
              <textarea
                className="input w-full"
                rows={3}
                placeholder="What should the agent know?"
                value={newMem.summary}
                onChange={(e) =>
                  setNewMem({ ...newMem, summary: e.target.value })
                }
              />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-xs uppercase text-spark-muted block mb-1">
                  Type
                </label>
                <select
                  className="input w-full"
                  value={newMem.memory_type}
                  onChange={(e) =>
                    setNewMem({ ...newMem, memory_type: e.target.value })
                  }
                >
                  <option value="fact">fact</option>
                  <option value="lesson">lesson</option>
                  <option value="pattern">pattern</option>
                  <option value="preference">preference</option>
                  <option value="constraint">constraint</option>
                  <option value="result">result</option>
                </select>
              </div>
              <div>
                <label className="text-xs uppercase text-spark-muted block mb-1">
                  Sensitivity
                </label>
                <select
                  className="input w-full"
                  value={newMem.sensitivity}
                  onChange={(e) =>
                    setNewMem({ ...newMem, sensitivity: e.target.value })
                  }
                >
                  <option value="low">low</option>
                  <option value="moderate">moderate</option>
                  <option value="high">high</option>
                  <option value="restricted">restricted</option>
                </select>
              </div>
              <div>
                <label className="text-xs uppercase text-spark-muted block mb-1">
                  Retention
                </label>
                <select
                  className="input w-full"
                  value={newMem.retention_class}
                  onChange={(e) =>
                    setNewMem({ ...newMem, retention_class: e.target.value })
                  }
                >
                  <option value="temporary">temporary</option>
                  <option value="expiring">expiring</option>
                  <option value="review">review</option>
                  <option value="persistent">persistent</option>
                </select>
              </div>
            </div>
            <div>
              <label className="text-xs uppercase text-spark-muted block mb-1">
                Tags (comma-separated)
              </label>
              <input
                className="input w-full"
                value={newMem.tags}
                onChange={(e) => setNewMem({ ...newMem, tags: e.target.value })}
              />
            </div>
            <div>
              <label className="text-xs uppercase text-spark-muted block mb-1">
                Confidence ({newMem.confidence.toFixed(2)})
              </label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                className="w-full"
                value={newMem.confidence}
                onChange={(e) =>
                  setNewMem({
                    ...newMem,
                    confidence: parseFloat(e.target.value),
                  })
                }
              />
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={newMem.is_anti_pattern}
                onChange={(e) =>
                  setNewMem({ ...newMem, is_anti_pattern: e.target.checked })
                }
              />
              <span>
                Anti-pattern (frames memory as "avoid this")
              </span>
            </label>
            <div className="flex justify-end gap-2 pt-2 border-t border-spark-border">
              <button className="btn" onClick={() => setShowCreate(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={createMemory}>
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Review queue tab (T5.5)
// ---------------------------------------------------------------------------

type ReviewItem = {
  memory_id: string;
  agent_name: string;
  namespace: string;
  status: string;
  confidence: number;
  contradicts_with: string | null;
  superseded_by: string | null;
  content_summary: string;
  memory_type: string;
  sensitivity: string;
  updated_at: string;
  reason: string;
};

function ReviewQueueTab({
  onApprove,
  onQuarantine,
  onDelete,
}: {
  onApprove: (id: string) => void;
  onQuarantine: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const q = useQuery<ReviewItem[]>({
    queryKey: ["memory-review-queue"],
    queryFn: () => api.get("/api/memory/review-queue"),
    refetchInterval: 30_000,
  });
  return (
    <section className="panel p-4 shadow-sm">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <ShieldAlert className="w-4 h-4 text-spark-accent" />
        Review queue ({q.data?.length ?? 0})
      </h3>
      <p className="text-xs text-spark-muted mb-3">
        Quarantined memories, low-confidence rows, and contradictions
        collected in one place.
      </p>
      {q.data && q.data.length === 0 ? (
        <p className="text-sm text-spark-muted text-center py-6">
          Nothing to review.
        </p>
      ) : (
        <div className="space-y-2">
          {(q.data ?? []).map((r) => (
            <div
              key={r.memory_id}
              className="border border-spark-border rounded-md p-3 hover:border-spark-accent/40 transition"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="font-mono text-xs">
                      {r.memory_id.slice(0, 12)}…
                    </span>
                    <span className="chip text-[10px]">{r.agent_name}</span>
                    <span className="chip chip-warn text-[10px]">
                      {r.reason}
                    </span>
                  </div>
                  <p className="text-sm text-spark-muted">
                    {r.content_summary}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {r.status !== "active" && (
                    <button
                      className="btn-icon hover:text-spark-good"
                      onClick={() => onApprove(r.memory_id)}
                      title="Approve"
                    >
                      <CheckCircle className="w-4 h-4" />
                    </button>
                  )}
                  {r.status === "active" && (
                    <button
                      className="btn-icon hover:text-spark-accent"
                      onClick={() => onQuarantine(r.memory_id)}
                      title="Quarantine"
                    >
                      <ShieldAlert className="w-4 h-4" />
                    </button>
                  )}
                  <button
                    className="btn-icon hover:text-spark-danger"
                    onClick={() => onDelete(r.memory_id)}
                    title="Delete"
                  >
                    ×
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Visualize tab (T5.4) — 2D PCA scatter
// ---------------------------------------------------------------------------

type ScatterPoint = {
  id: string;
  x: number;
  y: number;
  label: string;
  memory_type: string;
  sensitivity: string;
  is_anti_pattern: boolean;
  namespace: string;
  confidence: number;
  citations: number;
};

function VisualizeTab({ namespace }: { namespace: string }) {
  const q = useQuery<{ points: ScatterPoint[]; reason?: string }>({
    queryKey: ["memory-visualize", namespace],
    queryFn: () =>
      api.get(
        `/api/memory/visualize${
          namespace ? `?namespace=${encodeURIComponent(namespace)}` : ""
        }`,
      ),
  });
  const [hover, setHover] = useState<ScatterPoint | null>(null);
  const size = 520;
  const colorFor = (p: ScatterPoint): string => {
    if (p.is_anti_pattern) return "#f85149";
    if (p.memory_type === "pattern") return "#f59e0b";
    if (p.memory_type === "lesson") return "#3fb950";
    if (p.memory_type === "constraint") return "#a78bfa";
    if (p.memory_type === "preference") return "#60a5fa";
    return "#7d8590";
  };
  return (
    <section className="panel p-4 shadow-sm">
      <h3 className="font-semibold mb-3">
        Memory space (PCA 2D)
      </h3>
      {q.data?.reason && (
        <p className="text-sm text-spark-muted">{q.data.reason}</p>
      )}
      {q.data?.points && q.data.points.length > 0 && (
        <div className="relative inline-block">
          <svg
            width={size}
            height={size}
            className="bg-spark-bg border border-spark-border rounded-md"
          >
            <line
              x1={size / 2}
              y1={0}
              x2={size / 2}
              y2={size}
              stroke="#1f242b"
              strokeWidth="1"
            />
            <line
              x1={0}
              y1={size / 2}
              x2={size}
              y2={size / 2}
              stroke="#1f242b"
              strokeWidth="1"
            />
            {q.data.points.map((p) => (
              <circle
                key={p.id}
                cx={((p.x + 1) / 2) * (size - 20) + 10}
                cy={((1 - p.y) / 2) * (size - 20) + 10}
                r={3 + Math.min(8, p.citations)}
                fill={colorFor(p)}
                opacity={0.7}
                onMouseEnter={() => setHover(p)}
                onMouseLeave={() => setHover(null)}
                className="cursor-pointer hover:opacity-100"
              />
            ))}
          </svg>
          {hover && (
            <div className="absolute top-2 left-2 bg-spark-panel border border-spark-border rounded-md p-2 text-xs max-w-xs pointer-events-none shadow-lg">
              <div className="flex items-center gap-1 mb-1">
                <span className="chip text-[10px]">{hover.memory_type}</span>
                <span className="chip text-[10px]">{hover.namespace}</span>
              </div>
              <div className="text-spark-text">{hover.label}</div>
              <div className="text-spark-muted mt-1">
                conf {hover.confidence.toFixed(2)} · cited {hover.citations}
              </div>
            </div>
          )}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-spark-muted">
        <span><span className="inline-block w-2 h-2 rounded-full" style={{ background: "#f59e0b" }} /> pattern</span>
        <span><span className="inline-block w-2 h-2 rounded-full" style={{ background: "#3fb950" }} /> lesson</span>
        <span><span className="inline-block w-2 h-2 rounded-full" style={{ background: "#a78bfa" }} /> constraint</span>
        <span><span className="inline-block w-2 h-2 rounded-full" style={{ background: "#60a5fa" }} /> preference</span>
        <span><span className="inline-block w-2 h-2 rounded-full" style={{ background: "#f85149" }} /> anti-pattern</span>
        <span className="text-spark-muted">(bubble size = citation count)</span>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Circles tab (T3.3)
// ---------------------------------------------------------------------------

type CircleMember = {
  agent_name: string;
  can_read: boolean;
  can_write: boolean;
};
type Circle = {
  circle_id: string;
  name: string;
  description: string;
  members: CircleMember[];
  created_at: string;
};

function CirclesTab() {
  const qc = useQueryClient();
  const q = useQuery<Circle[]>({
    queryKey: ["memory-circles"],
    queryFn: () => api.get("/api/memory/circles"),
  });
  const [newCircle, setNewCircle] = useState({
    circle_id: "",
    name: "",
    description: "",
  });
  const [addMember, setAddMember] = useState<string | null>(null);
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
    } catch (err) {
      toast.error(`Create failed: ${err}`);
    }
  }

  async function addToCircle(cid: string) {
    try {
      await api.post(
        `/api/memory/circles/${encodeURIComponent(cid)}/members`,
        newMember,
      );
      toast.success("Member added");
      setAddMember(null);
      setNewMember({ agent_name: "", can_read: true, can_write: false });
      qc.invalidateQueries({ queryKey: ["memory-circles"] });
    } catch (err) {
      toast.error(`Add failed: ${err}`);
    }
  }

  async function removeMember(cid: string, agent: string) {
    try {
      await api.del(
        `/api/memory/circles/${encodeURIComponent(cid)}/members/${encodeURIComponent(agent)}`,
      );
      qc.invalidateQueries({ queryKey: ["memory-circles"] });
    } catch (err) {
      toast.error(`Remove failed: ${err}`);
    }
  }

  return (
    <div className="space-y-4">
      <section className="panel p-4 shadow-sm">
        <h3 className="font-semibold mb-3 flex items-center gap-2">
          <Users className="w-4 h-4 text-spark-accent" /> New circle
        </h3>
        <div className="flex flex-wrap gap-2">
          <input
            className="input flex-1 min-w-[150px] font-mono"
            placeholder="circle-id (lowercase, hyphens)"
            value={newCircle.circle_id}
            onChange={(e) =>
              setNewCircle({ ...newCircle, circle_id: e.target.value })
            }
          />
          <input
            className="input flex-1 min-w-[150px]"
            placeholder="Name"
            value={newCircle.name}
            onChange={(e) =>
              setNewCircle({ ...newCircle, name: e.target.value })
            }
          />
          <input
            className="input flex-[2] min-w-[200px]"
            placeholder="Description"
            value={newCircle.description}
            onChange={(e) =>
              setNewCircle({ ...newCircle, description: e.target.value })
            }
          />
          <button
            className="btn btn-primary"
            onClick={createCircle}
            disabled={!newCircle.circle_id || !newCircle.name}
          >
            Create
          </button>
        </div>
      </section>

      {(q.data ?? []).map((c) => (
        <section key={c.circle_id} className="panel p-4 shadow-sm">
          <div className="flex items-center justify-between mb-2">
            <div>
              <h4 className="font-semibold">
                {c.name}{" "}
                <span className="font-mono text-xs text-spark-muted">
                  ({c.circle_id})
                </span>
              </h4>
              {c.description && (
                <p className="text-xs text-spark-muted">{c.description}</p>
              )}
            </div>
            <button
              className="btn"
              onClick={() =>
                setAddMember(
                  addMember === c.circle_id ? null : c.circle_id,
                )
              }
            >
              <Plus className="w-3 h-3 mr-1 inline" /> Add member
            </button>
          </div>
          {addMember === c.circle_id && (
            <div className="flex gap-2 mb-2 pl-3 border-l-2 border-spark-accent/30">
              <input
                className="input flex-1"
                placeholder="agent name"
                value={newMember.agent_name}
                onChange={(e) =>
                  setNewMember({ ...newMember, agent_name: e.target.value })
                }
              />
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={newMember.can_read}
                  onChange={(e) =>
                    setNewMember({
                      ...newMember,
                      can_read: e.target.checked,
                    })
                  }
                />
                read
              </label>
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={newMember.can_write}
                  onChange={(e) =>
                    setNewMember({
                      ...newMember,
                      can_write: e.target.checked,
                    })
                  }
                />
                write
              </label>
              <button
                className="btn btn-primary"
                onClick={() => addToCircle(c.circle_id)}
              >
                Add
              </button>
            </div>
          )}
          {c.members.length === 0 ? (
            <p className="text-xs text-spark-muted">No members.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-spark-muted text-xs uppercase">
                <tr>
                  <th className="text-left">Agent</th>
                  <th>Read</th>
                  <th>Write</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {c.members.map((m) => (
                  <tr
                    key={m.agent_name}
                    className="border-t border-spark-border"
                  >
                    <td className="py-1 font-mono text-xs">{m.agent_name}</td>
                    <td className="text-center">
                      {m.can_read ? "✓" : "·"}
                    </td>
                    <td className="text-center">
                      {m.can_write ? "✓" : "·"}
                    </td>
                    <td className="text-right">
                      <button
                        className="btn-icon hover:text-spark-danger"
                        onClick={() => removeMember(c.circle_id, m.agent_name)}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ))}
    </div>
  );
}
