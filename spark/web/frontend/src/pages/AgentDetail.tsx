import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { toast } from "sonner";
import {
  Activity,
  Brain,
  Check,
  Coins,
  Heart,
  MessageSquare,
  Play,
  Shield,
  User2,
  X,
  Zap,
} from "lucide-react";
import { api } from "../lib/api";
import { formatTimestamp } from "../lib/utils";
import { ModelPicker, PROVIDER_SECRET } from "../components/ModelPicker";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { HealthDot, StatCard } from "../components/primitives";

type AgentDetailData = {
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  provider: {
    type: string;
    model: string;
    api_key_ref: string | null;
    base_url: string | null;
    temperature: number;
  };
  provider_key_available: boolean;
  plugins: string[];
  grants: string[];
  budgets: {
    max_iterations: number;
    max_model_calls: number;
    max_tool_calls: number;
    max_runtime_seconds: number;
  };
  memory: {
    task_memory: boolean;
    session_memory: boolean;
    long_term_memory: boolean;
    namespace: string | null;
    collection: string | null;
    sharing?: {
      read_global: boolean;
      write_global: boolean;
      max_cross_scope_sensitivity: string;
    };
  };
  sandbox: {
    enabled: boolean;
    backend: string;
    cpu_seconds: number;
    memory_mb: number;
  };
  tasks: { name: string; mode: string; state: string; updated_at: string }[];
  run_stats: {
    total_7d: number;
    completed_7d: number;
    failed_7d: number;
    success_rate_7d: number | null;
  };
  cost_7d_usd: number;
  tokens_7d: number;
  playbook_count: number;
  memory_count: number;
  persona: { persona_id: string; name: string; tone: string | null } | null;
  health: {
    sandbox_ok: boolean;
    sandbox_backend: string;
    provider_key_available: boolean;
  };
};

export default function AgentDetail() {
  const { agent_name } = useParams<{ agent_name: string }>();
  const qc = useQueryClient();
  const [showProviderModal, setShowProviderModal] = useState(false);
  const [providerType, setProviderType] = useState("");
  const [providerModel, setProviderModel] = useState("");
  const [providerTemp, setProviderTemp] = useState(0.2);
  const [providerBaseUrl, setProviderBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);

  const detail = useQuery<AgentDetailData>({
    queryKey: ["agent-detail", agent_name],
    queryFn: () =>
      api.get<AgentDetailData>(
        `/api/scheduler/agents/${encodeURIComponent(agent_name!)}`,
      ),
    enabled: !!agent_name,
  });

  function openProviderModal() {
    if (!detail.data) return;
    const p = detail.data.provider;
    setProviderType(p.type || "anthropic");
    setProviderModel(p.model || "");
    setProviderTemp(p.temperature ?? 0.2);
    setProviderBaseUrl(p.base_url || "");
    setShowProviderModal(true);
  }

  async function toggleSharing(
    patch: Partial<{
      read_global: boolean;
      write_global: boolean;
      max_cross_scope_sensitivity: string;
    }>,
  ) {
    if (!agent_name) return;
    const current = detail.data?.memory.sharing ?? {
      read_global: false,
      write_global: false,
      max_cross_scope_sensitivity: "moderate",
    };
    const next = { ...current, ...patch };
    try {
      await api.put(
        `/api/scheduler/agents/${encodeURIComponent(agent_name)}/memory-sharing`,
        next,
      );
      toast.success("Memory sharing updated");
      qc.invalidateQueries({ queryKey: ["agent-detail", agent_name] });
    } catch (err) {
      toast.error(`Update failed: ${err}`);
    }
  }

  async function setLtm(patch: {
    enabled?: boolean;
    namespace?: string;
    collection?: string;
  }) {
    if (!agent_name) return;
    const body = {
      enabled: patch.enabled ?? detail.data?.memory.long_term_memory ?? false,
      namespace:
        patch.namespace ?? detail.data?.memory.namespace ?? agent_name,
      collection:
        patch.collection ?? detail.data?.memory.collection ?? agent_name,
    };
    try {
      await api.put(
        `/api/scheduler/agents/${encodeURIComponent(agent_name)}/long-term-memory`,
        body,
      );
      toast.success(
        body.enabled ? "Long-term memory enabled" : "Long-term memory disabled",
      );
      qc.invalidateQueries({ queryKey: ["agent-detail", agent_name] });
    } catch (err) {
      toast.error(`Update failed: ${err}`);
    }
  }

  async function saveProvider() {
    if (!agent_name) return;
    setSaving(true);
    try {
      await api.put(
        `/api/scheduler/agents/${encodeURIComponent(agent_name)}/provider`,
        {
          type: providerType,
          model: providerModel,
          api_key_ref: PROVIDER_SECRET[providerType] || null,
          base_url:
            providerType === "ollama"
              ? providerBaseUrl || "http://localhost:11434"
              : providerBaseUrl || null,
          temperature: providerTemp,
        },
      );
      toast.success("Provider updated");
      setShowProviderModal(false);
      qc.invalidateQueries({ queryKey: ["agent-detail", agent_name] });
    } catch (err) {
      toast.error(`Save failed: ${err}`);
    } finally {
      setSaving(false);
    }
  }

  if (!agent_name) return null;
  if (detail.isLoading) {
    return <div className="p-6 text-spark-muted">Loading agent…</div>;
  }
  if (detail.isError) {
    return (
      <div className="p-6 text-red-400">
        Failed to load agent: {(detail.error as Error).message}
      </div>
    );
  }
  if (!detail.data) return null;

  const d = detail.data;
  const allHealthy =
    d.health.sandbox_ok && d.health.provider_key_available;

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
    } catch (err) {
      toast.error(`Trigger failed: ${err}`);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<HealthDot ok={allHealthy} size="md" pulse={allHealthy} />}
        title={d.name}
        subtitle={d.description}
        breadcrumbs={[
          { label: "Agents", to: "/agents" },
          { label: d.name },
        ]}
        actions={
          <>
            <Link to="/chat" className="btn" title="Open in chat">
              <MessageSquare className="w-4 h-4 mr-1 inline" /> Chat
            </Link>
            <button className="btn" onClick={triggerRun} title="Run now">
              <Play className="w-4 h-4 mr-1 inline" /> Run now
            </button>
            <button
              className="btn btn-primary"
              onClick={openProviderModal}
              title="Change provider"
            >
              <Zap className="w-4 h-4 mr-1 inline" /> Provider
            </button>
          </>
        }
      />

      {/* Stats row */}
      <section className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard
          label="Runs (7d)"
          value={d.run_stats.total_7d}
          sub={`${d.run_stats.completed_7d} ok · ${d.run_stats.failed_7d} failed`}
        />
        <StatCard
          label="Success rate"
          value={
            d.run_stats.success_rate_7d != null
              ? `${(d.run_stats.success_rate_7d * 100).toFixed(0)}%`
              : "—"
          }
          tone={
            d.run_stats.success_rate_7d != null
              ? d.run_stats.success_rate_7d > 0.8
                ? "good"
                : d.run_stats.success_rate_7d < 0.5
                  ? "danger"
                  : "warn"
              : "default"
          }
        />
        <StatCard label="Cost (7d)" value={`$${d.cost_7d_usd.toFixed(4)}`} />
        <StatCard label="Tokens (7d)" value={d.tokens_7d.toLocaleString()} />
        <StatCard
          label="Memories"
          value={d.memory_count}
          sub={`${d.playbook_count} playbooks`}
        />
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Health */}
        <section className="panel p-4 space-y-2">
          <h3 className="font-semibold flex items-center gap-2">
            <Heart className="w-4 h-4" /> Health
          </h3>
          <div className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <HealthDot ok={d.health.provider_key_available} />
              <span>
                Provider key{" "}
                <code className="font-mono text-xs">
                  {d.provider.api_key_ref ?? "none"}
                </code>
              </span>
              {d.health.provider_key_available ? (
                <Check className="w-3 h-3 text-spark-good" />
              ) : (
                <span className="text-spark-danger text-xs">
                  missing —{" "}
                  <Link to="/provider" className="underline">
                    set it
                  </Link>
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <HealthDot ok={d.health.sandbox_ok} />
              <span>
                Sandbox: {d.health.sandbox_backend}
              </span>
            </div>
          </div>
        </section>

        {/* Provider */}
        <section className="panel p-4 space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold flex items-center gap-2">
              <Zap className="w-4 h-4" /> Provider
            </h3>
            <button className="btn" onClick={openProviderModal}>
              Change
            </button>
          </div>
          <dl className="text-sm space-y-1">
            <div className="flex gap-2">
              <dt className="text-spark-muted w-24">Type</dt>
              <dd className="capitalize">{d.provider.type}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-spark-muted w-24">Model</dt>
              <dd className="font-mono text-xs">{d.provider.model}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-spark-muted w-24">Temperature</dt>
              <dd>{d.provider.temperature}</dd>
            </div>
            {d.provider.base_url && (
              <div className="flex gap-2">
                <dt className="text-spark-muted w-24">Base URL</dt>
                <dd className="font-mono text-xs">{d.provider.base_url}</dd>
              </div>
            )}
          </dl>
        </section>

        {/* Persona */}
        <section className="panel p-4 space-y-2">
          <h3 className="font-semibold flex items-center gap-2">
            <User2 className="w-4 h-4" /> Persona
          </h3>
          {d.persona ? (
            <div className="text-sm">
              <p className="font-mono">{d.persona.name}</p>
              {d.persona.tone && (
                <p className="text-spark-muted text-xs">Tone: {d.persona.tone}</p>
              )}
              <Link
                to="/persona"
                className="text-spark-accent text-xs underline"
              >
                Edit persona
              </Link>
            </div>
          ) : (
            <p className="text-spark-muted text-sm">No active persona</p>
          )}
        </section>

        {/* Plugins & permissions */}
        <section className="panel p-4 space-y-2">
          <h3 className="font-semibold flex items-center gap-2">
            <Shield className="w-4 h-4" /> Plugins & Permissions
          </h3>
          <div className="text-sm space-y-2">
            <div>
              <span className="text-xs uppercase text-spark-muted">
                Plugins ({d.plugins.length})
              </span>
              <div className="flex flex-wrap gap-1 mt-1">
                {d.plugins.map((p) => (
                  <span key={p} className="chip font-mono text-xs">
                    {p}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <span className="text-xs uppercase text-spark-muted">
                Grants ({d.grants.length})
              </span>
              <div className="flex flex-wrap gap-1 mt-1">
                {d.grants.map((g) => (
                  <span key={g} className="chip font-mono text-xs">
                    {g}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* Budgets */}
        <section className="panel p-4 space-y-2">
          <h3 className="font-semibold flex items-center gap-2">
            <Coins className="w-4 h-4" /> Budgets
          </h3>
          <dl className="text-sm grid grid-cols-2 gap-1">
            <div>
              <dt className="text-spark-muted text-xs">Iterations</dt>
              <dd>{d.budgets.max_iterations}</dd>
            </div>
            <div>
              <dt className="text-spark-muted text-xs">Model calls</dt>
              <dd>{d.budgets.max_model_calls}</dd>
            </div>
            <div>
              <dt className="text-spark-muted text-xs">Tool calls</dt>
              <dd>{d.budgets.max_tool_calls}</dd>
            </div>
            <div>
              <dt className="text-spark-muted text-xs">Runtime</dt>
              <dd>{d.budgets.max_runtime_seconds}s</dd>
            </div>
          </dl>
        </section>

        {/* Memory & sandbox */}
        <section className="panel p-4 space-y-2">
          <h3 className="font-semibold flex items-center gap-2">
            <Brain className="w-4 h-4" /> Memory & Sandbox
          </h3>
          <dl className="text-sm space-y-1">
            <div className="flex gap-2">
              <dt className="text-spark-muted w-32">Task memory</dt>
              <dd>{d.memory.task_memory ? "on" : "off"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-spark-muted w-32">Session memory</dt>
              <dd>{d.memory.session_memory ? "on" : "off"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-spark-muted w-32">Long-term</dt>
              <dd>
                {d.memory.long_term_memory
                  ? `on (${d.memory.namespace})`
                  : "off"}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-spark-muted w-32">Sandbox</dt>
              <dd>
                {d.sandbox.enabled
                  ? `${d.sandbox.backend} · ${d.sandbox.cpu_seconds}s CPU · ${d.sandbox.memory_mb}MB`
                  : "disabled"}
              </dd>
            </div>
          </dl>
        </section>

        {/* Memory configuration */}
        <section className="panel p-4 space-y-4">
          <h3 className="font-semibold flex items-center gap-2">
            <Brain className="w-4 h-4" /> Memory
          </h3>

          {/* Long-term memory */}
          <div className="space-y-2 pb-3 border-b border-spark-border">
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={d.memory.long_term_memory}
                onChange={(e) => setLtm({ enabled: e.target.checked })}
              />
              <div>
                <div>Enable long-term memory</div>
                <div className="text-xs text-spark-muted">
                  Persists distilled facts to Chroma across sessions.
                  Retrieval injects relevant memories into every run.
                </div>
              </div>
            </label>
            {d.memory.long_term_memory && (
              <div className="ml-6 text-xs text-spark-muted space-y-0.5">
                <div>
                  Namespace:{" "}
                  <code className="font-mono">
                    {d.memory.namespace ?? agent_name}
                  </code>
                </div>
                <div>
                  Collection:{" "}
                  <code className="font-mono">
                    {d.memory.collection ?? agent_name}
                  </code>
                </div>
              </div>
            )}
          </div>

          {/* Cross-agent sharing */}
          <div
            className={
              d.memory.long_term_memory
                ? "space-y-3"
                : "space-y-3 opacity-40 pointer-events-none select-none"
            }
            aria-disabled={!d.memory.long_term_memory}
          >
            <div className="text-xs uppercase tracking-wide text-spark-muted">
              Cross-agent sharing
            </div>
            {!d.memory.long_term_memory && (
              <p className="text-xs text-spark-muted -mt-2">
                Enable long-term memory above to configure sharing.
              </p>
            )}
            <p className="text-xs text-spark-muted">
              Control cross-agent memory access. All cross-scope reads and
              writes are audited at elevated severity.
            </p>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={!!d.memory.sharing?.read_global}
                onChange={(e) =>
                  toggleSharing({ read_global: e.target.checked })
                }
                disabled={!d.memory.long_term_memory}
              />
              <div>
                <div>Read from global pool</div>
                <div className="text-xs text-spark-muted">
                  Retrieval augments this agent's private memory with shared
                  memories from other agents.
                </div>
              </div>
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={!!d.memory.sharing?.write_global}
                onChange={(e) =>
                  toggleSharing({ write_global: e.target.checked })
                }
                disabled={!d.memory.long_term_memory}
              />
              <div>
                <div>Promote own memories to global</div>
                <div className="text-xs text-spark-muted">
                  Operator can promote this agent's memories up to the
                  sensitivity cap below.
                </div>
              </div>
            </label>
            <div>
              <label className="text-xs uppercase text-spark-muted block mb-1">
                Max cross-scope sensitivity
              </label>
              <select
                className="input w-full"
                value={
                  d.memory.sharing?.max_cross_scope_sensitivity ?? "moderate"
                }
                onChange={(e) =>
                  toggleSharing({ max_cross_scope_sensitivity: e.target.value })
                }
                disabled={!d.memory.long_term_memory}
              >
                <option value="low">low — only non-sensitive</option>
                <option value="moderate">moderate — default</option>
                <option value="high">high — careful</option>
              </select>
              <p className="text-xs text-spark-muted mt-1">
                Memories above this level never cross the agent boundary.
                <code className="font-mono ml-1">restricted</code> is always
                blocked.
              </p>
            </div>
          </div>
        </section>
      </div>

      {/* Tasks */}
      <section className="panel p-4">
        <h3 className="font-semibold flex items-center gap-2 mb-3">
          <Activity className="w-4 h-4" /> Tasks ({d.tasks.length})
        </h3>
        {d.tasks.length > 0 ? (
          <table className="w-full text-sm">
            <thead className="text-spark-muted text-xs uppercase">
              <tr>
                <th className="text-left">Name</th>
                <th className="text-left">Mode</th>
                <th className="text-left">State</th>
                <th className="text-left">Updated</th>
              </tr>
            </thead>
            <tbody>
              {d.tasks.map((t) => (
                <tr key={t.name} className="border-t border-spark-border">
                  <td className="py-1 font-mono">{t.name}</td>
                  <td>
                    <span className="chip">{t.mode}</span>
                  </td>
                  <td>{t.state}</td>
                  <td>{formatTimestamp(t.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-spark-muted text-sm">No tasks configured.</p>
        )}
      </section>

      {/* Provider modal */}
      <Modal open={showProviderModal} onClose={() => setShowProviderModal(false)}>
        <div className="bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-auto p-6 space-y-4 shadow-2xl">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-bold">
              {d.name} — Set Provider / Model
            </h3>
            <button
              className="btn-icon"
              onClick={() => setShowProviderModal(false)}
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <ModelPicker
            provider={providerType}
            model={providerModel}
            temperature={providerTemp}
            baseUrl={providerBaseUrl}
            onProviderChange={(p) => {
              setProviderType(p);
              setProviderModel("");
            }}
            onModelChange={setProviderModel}
            onTemperatureChange={setProviderTemp}
            onBaseUrlChange={setProviderBaseUrl}
          />

          <div className="flex justify-end gap-2 pt-2 border-t border-spark-border">
            <button
              className="btn"
              onClick={() => setShowProviderModal(false)}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={saving || !providerModel}
              onClick={saveProvider}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
