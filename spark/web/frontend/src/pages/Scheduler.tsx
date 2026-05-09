import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Calendar, Pause, Pencil, Play, Square, X, Zap } from "lucide-react";
import { api } from "../lib/api";
import { AgentSummary, TaskSummary } from "../lib/types";
import { ModelPicker, PROVIDER_SECRET } from "../components/ModelPicker";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { RelativeTime, Timestamp } from "../components/RelativeTime";
import { CronPreview } from "../components/CronPreview";
import { CronBuilder } from "../components/CronBuilder";
import { EmptyState } from "../components/primitives";

interface Schedule {
  task_name: string;
  trigger_type: string;
  trigger_expression: string;
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
}

type ProviderInfo = {
  type: string;
  model: string;
  api_key_ref: string | null;
  base_url: string | null;
  temperature: number;
};

type AgentYamlResponse = {
  yaml: string;
  provider: ProviderInfo;
};

export default function Scheduler() {
  const qc = useQueryClient();
  const [editingAgent, setEditingAgent] = useState<string | null>(null);
  const [editYaml, setEditYaml] = useState(false);
  const [yamlText, setYamlText] = useState("");
  const [providerType, setProviderType] = useState("anthropic");
  const [providerModel, setProviderModel] = useState("");
  const [providerTemp, setProviderTemp] = useState(0.2);
  const [providerBaseUrl, setProviderBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [showCreateTask, setShowCreateTask] = useState(false);
  const [editingTask, setEditingTask] = useState<FullTaskResponse | null>(null);

  async function openEditTask(taskName: string) {
    try {
      const full = await api.get<FullTaskResponse>(
        `/api/scheduler/tasks/${encodeURIComponent(taskName)}/full`,
      );
      setEditingTask(full);
      setShowCreateTask(true);
    } catch (err) {
      toast.error(`Failed to load task: ${err}`);
    }
  }

  const agents = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: () => api.get("/api/scheduler/agents"),
  });
  const tasks = useQuery<TaskSummary[]>({
    queryKey: ["tasks"],
    queryFn: () => api.get("/api/scheduler/tasks"),
  });
  const schedules = useQuery<Schedule[]>({
    queryKey: ["schedules"],
    queryFn: () => api.get("/api/scheduler/schedules"),
  });

  async function openProviderModal(agentName: string) {
    try {
      const resp = await api.get<AgentYamlResponse>(
        `/api/scheduler/agents/${encodeURIComponent(agentName)}/yaml`,
      );
      setProviderType(resp.provider.type || "anthropic");
      setProviderModel(resp.provider.model || "");
      setProviderTemp(resp.provider.temperature ?? 0.2);
      setProviderBaseUrl(resp.provider.base_url || "");
      setYamlText(resp.yaml);
      setEditYaml(false);
      setEditingAgent(agentName);
    } catch (err) {
      toast.error(`Failed to load agent: ${err}`);
    }
  }

  async function saveProvider() {
    if (!editingAgent) return;
    setSaving(true);
    try {
      if (editYaml) {
        await api.put(
          `/api/scheduler/agents/${encodeURIComponent(editingAgent)}/yaml`,
          { yaml: yamlText },
        );
      } else {
        if (!providerModel) {
          toast.error("Select or enter a model");
          setSaving(false);
          return;
        }
        await api.put(
          `/api/scheduler/agents/${encodeURIComponent(editingAgent)}/provider`,
          {
            type: providerType,
            model: providerModel,
            api_key_ref: PROVIDER_SECRET[providerType] || null,
            base_url: providerType === "ollama" ? (providerBaseUrl || "http://localhost:11434") : (providerBaseUrl || null),
            temperature: providerTemp,
          },
        );
      }
      toast.success("Provider updated");
      setEditingAgent(null);
      qc.invalidateQueries({ queryKey: ["agents"] });
    } catch (err) {
      toast.error(`Save failed: ${err}`);
    } finally {
      setSaving(false);
    }
  }

  async function trigger(taskName: string, agentName: string) {
    await api.post("/api/scheduler/trigger", { task_name: taskName, agent_name: agentName });
    tasks.refetch();
  }
  async function pause(taskName: string) {
    await api.post(`/api/scheduler/tasks/${encodeURIComponent(taskName)}/pause`);
    tasks.refetch();
  }
  async function stop(taskName: string) {
    await api.post(`/api/scheduler/tasks/${encodeURIComponent(taskName)}/stop`);
    tasks.refetch();
  }

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<Calendar className="w-6 h-6" />}
        title="Scheduler"
        subtitle="Agents, tasks, and schedules."
      />

      <section className="panel p-4 shadow-sm">
        <h3 className="font-semibold mb-3">
          Agents ({agents.data?.length ?? 0})
        </h3>
        {(agents.data ?? []).length === 0 ? (
          <EmptyState
            title="No agents"
            description="Install a template or create an agent to see it here."
            action={{ label: "Browse Templates", to: "/templates" }}
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-spark-muted text-xs uppercase">
              <tr>
                <th className="text-left pb-2">Name</th>
                <th className="text-left pb-2">Description</th>
                <th className="text-left pb-2">Updated</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {(agents.data ?? []).map((a) => (
                <tr
                  key={a.name}
                  className="border-t border-spark-border hover:bg-spark-border/20 transition"
                >
                  <td className="py-1.5 font-mono">
                    <Link
                      to={`/agents/${encodeURIComponent(a.name)}`}
                      className="text-spark-accent hover:underline"
                    >
                      {a.name}
                    </Link>
                  </td>
                  <td className="text-spark-muted">{a.description}</td>
                  <td>
                    <Timestamp ts={a.updated_at} />
                  </td>
                  <td className="text-right">
                    <button
                      className="btn"
                      onClick={() => openProviderModal(a.name)}
                    >
                      <Zap className="w-3 h-3 mr-1 inline" /> Set Provider
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Provider / YAML edit modal */}
      <Modal open={!!editingAgent} onClose={() => setEditingAgent(null)}>
        <div className="bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-auto p-6 space-y-4 shadow-2xl">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-bold">
              {editingAgent} — {editYaml ? "Edit YAML" : "Set Provider / Model"}
            </h3>
            <div className="flex gap-2">
              <button className="btn" onClick={() => setEditYaml(!editYaml)}>
                {editYaml ? "Provider picker" : "Edit YAML"}
              </button>
              <button
                className="btn-icon"
                onClick={() => setEditingAgent(null)}
                aria-label="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {editYaml ? (
            <textarea
              className="input w-full font-mono text-xs"
              rows={24}
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
            />
          ) : (
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
          )}

          <div className="flex justify-end gap-2 pt-2 border-t border-spark-border">
            <button className="btn" onClick={() => setEditingAgent(null)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={saving}
              onClick={saveProvider}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </Modal>

      <TaskCreatorModal
        open={showCreateTask}
        onClose={() => {
          setShowCreateTask(false);
          setEditingTask(null);
        }}
        agents={agents.data ?? []}
        editing={editingTask}
        onCreated={() => {
          qc.invalidateQueries({ queryKey: ["tasks"] });
          qc.invalidateQueries({ queryKey: ["schedules"] });
          setShowCreateTask(false);
          setEditingTask(null);
        }}
      />

      <section className="panel p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">Tasks ({tasks.data?.length ?? 0})</h3>
          <button
            className="btn btn-primary text-xs"
            onClick={() => setShowCreateTask(true)}
          >
            + New task
          </button>
        </div>
        {(tasks.data ?? []).length === 0 ? (
          <p className="text-sm text-spark-muted py-4 text-center">
            No tasks configured.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-spark-muted text-xs uppercase">
              <tr>
                <th className="text-left pb-2">Name</th>
                <th className="text-left pb-2">Agent</th>
                <th className="text-left pb-2">Mode</th>
                <th className="text-left pb-2">State</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {(tasks.data ?? []).map((t) => (
                <tr
                  key={t.name}
                  className="border-t border-spark-border hover:bg-spark-border/20"
                >
                  <td className="py-1.5 font-mono">{t.name}</td>
                  <td>
                    <Link
                      to={`/agents/${encodeURIComponent(t.agent_name)}`}
                      className="text-spark-accent hover:underline"
                    >
                      {t.agent_name}
                    </Link>
                  </td>
                  <td>
                    <span className="chip">{t.mode}</span>
                  </td>
                  <td>
                    <span
                      className={`chip ${stateClass(t.state)}`}
                    >
                      {t.state}
                    </span>
                  </td>
                  <td className="text-right space-x-1">
                    <button
                      className="btn-icon"
                      onClick={() => openEditTask(t.name)}
                      title="Edit"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      className="btn-icon"
                      onClick={() => trigger(t.name, t.agent_name)}
                      title="Trigger now"
                    >
                      <Play className="w-4 h-4" />
                    </button>
                    <button
                      className="btn-icon"
                      onClick={() => pause(t.name)}
                      title="Pause"
                    >
                      <Pause className="w-4 h-4" />
                    </button>
                    <button
                      className="btn-icon hover:text-spark-danger"
                      onClick={() => stop(t.name)}
                      title="Stop"
                    >
                      <Square className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel p-4 shadow-sm">
        <h3 className="font-semibold mb-3">
          Schedules ({schedules.data?.length ?? 0})
        </h3>
        {(schedules.data ?? []).length === 0 ? (
          <p className="text-sm text-spark-muted py-4 text-center">
            No schedules configured.
          </p>
        ) : (
          <div className="space-y-2">
            {(schedules.data ?? []).map((s) => (
              <div
                key={s.task_name}
                className="border border-spark-border rounded-md p-3 hover:bg-spark-border/20 transition"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-sm">{s.task_name}</span>
                    <span className="chip text-xs">{s.trigger_type}</span>
                    <code className="font-mono text-xs text-spark-muted">
                      {s.trigger_expression}
                    </code>
                    <span className="text-xs text-spark-muted">
                      {s.timezone}
                    </span>
                  </div>
                  <span
                    className={`chip text-xs ${
                      s.enabled ? "chip-good" : "chip-danger"
                    }`}
                  >
                    {s.enabled ? "enabled" : "disabled"}
                  </span>
                </div>
                {s.trigger_type === "cron" && (
                  <div className="mt-2 pl-1 border-l-2 border-spark-accent/30 ml-1">
                    <CronPreview expr={s.trigger_expression} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <TriggersPanel />
    </div>
  );
}

function stateClass(state: string): string {
  if (state === "completed" || state === "running") return "chip-good";
  if (state === "failed" || state === "dlq") return "chip-danger";
  if (state === "paused") return "chip-warn";
  return "";
}


// -----------------------------------------------------------------------------
// Task Creator
// -----------------------------------------------------------------------------

type TaskMode = "one_shot" | "recurring" | "perpetual";
type ScheduleType = "cron" | "interval";

type SimulateResp = { count: number; fires: string[] };

interface FullTaskResponse {
  name: string;
  agent: string;
  mode: TaskMode | "event";
  objective: string;
  inputs: Record<string, string | number | boolean>;
  schedule: {
    type: ScheduleType;
    expression: string;
    timezone: string;
    start_at: string | null;
    end_at: string | null;
  } | null;
  budgets: {
    max_runtime_seconds: number | null;
    max_model_calls: number | null;
    max_tool_calls: number | null;
    max_tokens_per_run: number | null;
  };
  forensic: { enabled: boolean; reason: string; ttl_hours: number };
  state: string;
  config_path: string;
}

interface TaskCreatorProps {
  open: boolean;
  onClose: () => void;
  agents: AgentSummary[];
  onCreated: () => void;
  /** When set, the modal opens in edit mode pre-populated from this task. */
  editing?: FullTaskResponse | null;
}

const SLUG_RE = /^[a-z0-9][a-z0-9._-]{0,127}$/;

/** Convert an ISO-8601 string to the `<input type="datetime-local">`
 *  format ``YYYY-MM-DDTHH:MM`` in the browser's local timezone. */
function toLocalDateTimeInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function TaskCreatorModal({
  open,
  onClose,
  agents,
  onCreated,
  editing = null,
}: TaskCreatorProps) {
  const isEdit = editing !== null;
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [mode, setMode] = useState<TaskMode>("one_shot");
  const [objective, setObjective] = useState("");
  const [inputs, setInputs] = useState<{ key: string; value: string }[]>([]);

  // Schedule fields. Visibility / requirement is mode-driven.
  const [scheduleType, setScheduleType] = useState<ScheduleType>("cron");
  const [scheduleExpr, setScheduleExpr] = useState("0 8 * * 1");
  const [scheduleTz, setScheduleTz] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [startAt, setStartAt] = useState<string>("");
  const [endAt, setEndAt] = useState<string>("");
  const [delayedOneShot, setDelayedOneShot] = useState(false);

  // Optional collapsibles.
  const [showBudgets, setShowBudgets] = useState(false);
  const [showForensic, setShowForensic] = useState(false);
  const [budgetRuntime, setBudgetRuntime] = useState<string>("");
  const [budgetModelCalls, setBudgetModelCalls] = useState<string>("");
  const [budgetToolCalls, setBudgetToolCalls] = useState<string>("");
  const [budgetTokens, setBudgetTokens] = useState<string>("");
  const [forensicEnabled, setForensicEnabled] = useState(false);
  const [forensicReason, setForensicReason] = useState("");
  const [forensicTtl, setForensicTtl] = useState("168");

  const [autoStart, setAutoStart] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string[]>([]);

  // Hydrate from `editing` when the modal opens. Re-runs whenever the
  // editing target changes (so reopening on a different task pre-fills
  // the right values).
  useEffect(() => {
    if (!open) return;
    if (editing) {
      setName(editing.name);
      setAgent(editing.agent);
      // ``event`` mode tasks aren't editable here — those fire from
      // external triggers and have no schedule. Coerce to one_shot for
      // the modal so the operator at least sees a sane shape.
      setMode((editing.mode === "event" ? "one_shot" : editing.mode) as TaskMode);
      setObjective(editing.objective);
      setInputs(
        Object.entries(editing.inputs ?? {}).map(([k, v]) => ({
          key: k,
          value: String(v),
        })),
      );
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
      } else {
        setScheduleType("cron");
        setScheduleExpr("0 8 * * 1");
        setStartAt("");
        setEndAt("");
        setDelayedOneShot(false);
      }
      const b = editing.budgets;
      const hasBudget =
        b.max_runtime_seconds != null ||
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
  const scheduleVisible =
    mode === "recurring" || mode === "perpetual" || (mode === "one_shot" && delayedOneShot);
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

  function localValidate(): string | null {
    if (!SLUG_RE.test(name)) {
      return "Name must be lowercase a-z0-9, start with a letter or digit, max 128 chars.";
    }
    if (!agent) return "Pick an agent.";
    if (!objective.trim()) return "Objective is required.";
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
      const resp = await api.post<SimulateResp>("/api/scheduler/simulate", {
        schedule_type: scheduleType,
        expression: scheduleExpr,
        timezone: scheduleTz,
        horizon_hours: 168,
      });
      setPreview(resp.fires.slice(0, 5));
    } catch (err) {
      setPreview([]);
      setError(`Schedule preview failed: ${(err as Error).message ?? err}`);
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

    const payload: Record<string, unknown> = {
      name,
      agent,
      mode,
      objective,
      inputs: inputs.reduce<Record<string, string>>(
        (acc, { key, value }) => (key ? { ...acc, [key]: value } : acc),
        {},
      ),
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
      const budgets: Record<string, number> = {};
      if (budgetRuntime) budgets.max_runtime_seconds = parseInt(budgetRuntime, 10);
      if (budgetModelCalls) budgets.max_model_calls = parseInt(budgetModelCalls, 10);
      if (budgetToolCalls) budgets.max_tool_calls = parseInt(budgetToolCalls, 10);
      if (budgetTokens) budgets.max_tokens_per_run = parseInt(budgetTokens, 10);
      if (Object.keys(budgets).length > 0) payload.budgets = budgets;
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
        await api.put(
          `/api/scheduler/tasks/${encodeURIComponent(name)}`,
          payload,
        );
        toast.success(`Task "${name}" updated`);
      } else {
        await api.post("/api/scheduler/tasks", payload);
        toast.success(`Task "${name}" created`);
      }
      reset();
      onCreated();
    } catch (err) {
      setError((err as Error).message || (isEdit ? "Update failed" : "Create failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <div className="bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[88vh] overflow-auto p-5 shadow-xl space-y-3">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="font-semibold text-lg">
              {isEdit ? `Edit task: ${name}` : "Create task"}
            </h3>
            <p className="text-xs text-spark-muted">
              {isEdit
                ? "Updates the task YAML on disk and reschedules. Refused while a run is in flight."
                : (
                  <>
                    Writes{" "}
                    <code className="font-mono">
                      ~/.spark/tasks/{name || "<name>"}.yaml
                    </code>{" "}
                    and registers the task in the scheduler.
                  </>
                )}
            </p>
          </div>
          <button
            className="btn-icon"
            onClick={() => {
              reset();
              onClose();
            }}
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="label">Name</span>
            <input
              className="input w-full font-mono"
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder="research-digest"
              disabled={isEdit}
              title={isEdit ? "Renames are not supported — delete + recreate" : ""}
            />
            <span className="text-[10px] text-spark-muted">
              {isEdit
                ? "Renames are not supported — delete and recreate to change the name."
                : "Lowercase a-z0-9 . _ -"}
            </span>
          </label>
          <label className="block">
            <span className="label">Agent</span>
            <select
              className="input w-full"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
            >
              <option value="">(select)</option>
              {agents.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name}
                </option>
              ))}
            </select>
            {isEdit && editing && agent !== editing.agent && (
              <span className="text-[10px] text-amber-400">
                ⚠ Changing the agent rebinds plugins, permissions, and
                memory namespace. Audited at elevated severity.
              </span>
            )}
          </label>
        </div>

        <div>
          <span className="label">Mode</span>
          <div className="flex gap-2 mt-1 text-sm">
            {(["one_shot", "recurring", "perpetual"] as TaskMode[]).map((m) => (
              <label
                key={m}
                className="flex items-center gap-1 cursor-pointer"
              >
                <input
                  type="radio"
                  name="mode"
                  checked={mode === m}
                  onChange={() => {
                    setMode(m);
                    // Reset constraints that no longer apply.
                    if (m === "one_shot") {
                      setEndAt("");
                      setDelayedOneShot(false);
                    } else if (m === "perpetual") {
                      setEndAt("");
                    }
                  }}
                />
                <span className="font-mono">{m}</span>
              </label>
            ))}
          </div>
          <p className="text-[11px] text-spark-muted mt-1">
            {mode === "one_shot" &&
              "Runs once. Optionally schedule for later via the toggle below."}
            {mode === "recurring" &&
              "Fires on cron/interval inside a finite window. Both start and end required."}
            {mode === "perpetual" &&
              "Fires on cron/interval forever, starting at start_at."}
          </p>
        </div>

        <label className="block">
          <span className="label">Objective</span>
          <textarea
            className="input w-full font-mono text-xs h-24"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            placeholder="What should the agent do?"
          />
        </label>

        <details>
          <summary className="text-xs text-spark-muted cursor-pointer">
            Inputs ({inputs.length})
          </summary>
          <div className="space-y-1 mt-2">
            {inputs.map((row, i) => (
              <div key={i} className="flex gap-1">
                <input
                  className="input flex-1 text-xs font-mono"
                  placeholder="key"
                  value={row.key}
                  onChange={(e) => {
                    const next = [...inputs];
                    next[i] = { ...row, key: e.target.value };
                    setInputs(next);
                  }}
                />
                <input
                  className="input flex-1 text-xs"
                  placeholder="value"
                  value={row.value}
                  onChange={(e) => {
                    const next = [...inputs];
                    next[i] = { ...row, value: e.target.value };
                    setInputs(next);
                  }}
                />
                <button
                  className="btn-icon hover:text-spark-danger"
                  onClick={() => setInputs(inputs.filter((_, j) => j !== i))}
                  aria-label="Remove"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
            <button
              className="btn text-xs"
              onClick={() => setInputs([...inputs, { key: "", value: "" }])}
            >
              + Add input
            </button>
          </div>
        </details>

        {mode === "one_shot" && (
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={delayedOneShot}
              onChange={(e) => setDelayedOneShot(e.target.checked)}
            />
            Schedule for later
          </label>
        )}

        {scheduleVisible && (
          <div className="border border-spark-border rounded p-3 space-y-2">
            <div className="text-xs uppercase tracking-wide text-spark-muted">
              Schedule
            </div>

            {(mode === "recurring" || mode === "perpetual") && (
              <div className="grid grid-cols-3 gap-2">
                <label className="block col-span-3">
                  <span className="label text-xs">Type</span>
                  <select
                    className="input w-full max-w-[160px]"
                    value={scheduleType}
                    onChange={(e) =>
                      setScheduleType(e.target.value as ScheduleType)
                    }
                  >
                    <option value="cron">cron (visual builder)</option>
                    <option value="interval">interval (raw seconds)</option>
                  </select>
                </label>
                <div className="col-span-3">
                  {scheduleType === "cron" ? (
                    <CronBuilder
                      value={scheduleExpr}
                      onChange={setScheduleExpr}
                    />
                  ) : (
                    <label className="block">
                      <span className="label text-xs">Interval seconds</span>
                      <input
                        className="input w-full font-mono text-xs"
                        value={scheduleExpr}
                        onChange={(e) => setScheduleExpr(e.target.value)}
                        placeholder="3600"
                      />
                    </label>
                  )}
                </div>
              </div>
            )}

            <label className="block">
              <span className="label text-xs">Timezone</span>
              <input
                className="input w-full font-mono text-xs"
                value={scheduleTz}
                onChange={(e) => setScheduleTz(e.target.value)}
              />
            </label>

            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="label text-xs">
                  Start at {startRequired || (mode === "one_shot" && delayedOneShot) ? "(required)" : "(optional)"}
                </span>
                <input
                  type="datetime-local"
                  className="input w-full text-xs"
                  value={startAt}
                  onChange={(e) => setStartAt(e.target.value)}
                />
              </label>
              {endAllowed && (
                <label className="block">
                  <span className="label text-xs">
                    End at {endRequired ? "(required)" : "(optional)"}
                  </span>
                  <input
                    type="datetime-local"
                    className="input w-full text-xs"
                    value={endAt}
                    onChange={(e) => setEndAt(e.target.value)}
                  />
                </label>
              )}
            </div>

            {(mode === "recurring" || mode === "perpetual") && (
              <div className="space-y-1">
                <button
                  type="button"
                  className="btn text-xs"
                  onClick={runPreview}
                >
                  Preview next 5 fires
                </button>
                {preview.length > 0 && (
                  <ul className="text-[11px] font-mono text-spark-muted">
                    {preview.map((iso) => (
                      <li key={iso}>· {iso}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        <details
          className="border border-spark-border rounded"
          open={showBudgets}
          onToggle={(e) =>
            setShowBudgets((e.target as HTMLDetailsElement).open)
          }
        >
          <summary className="px-3 py-2 text-xs cursor-pointer">
            Budgets (optional, fall back to agent defaults)
          </summary>
          <div className="px-3 pb-3 grid grid-cols-2 gap-2 text-xs">
            <label className="block">
              <span className="label text-xs">max_runtime_seconds</span>
              <input
                type="number"
                className="input w-full"
                value={budgetRuntime}
                onChange={(e) => setBudgetRuntime(e.target.value)}
                placeholder="900"
              />
            </label>
            <label className="block">
              <span className="label text-xs">max_model_calls</span>
              <input
                type="number"
                className="input w-full"
                value={budgetModelCalls}
                onChange={(e) => setBudgetModelCalls(e.target.value)}
                placeholder="30"
              />
            </label>
            <label className="block">
              <span className="label text-xs">max_tool_calls</span>
              <input
                type="number"
                className="input w-full"
                value={budgetToolCalls}
                onChange={(e) => setBudgetToolCalls(e.target.value)}
                placeholder="25"
              />
            </label>
            <label className="block">
              <span className="label text-xs">max_tokens_per_run</span>
              <input
                type="number"
                className="input w-full"
                value={budgetTokens}
                onChange={(e) => setBudgetTokens(e.target.value)}
                placeholder="(unbounded)"
              />
            </label>
          </div>
        </details>

        <details
          className="border border-spark-border rounded"
          open={showForensic}
          onToggle={(e) =>
            setShowForensic((e.target as HTMLDetailsElement).open)
          }
        >
          <summary className="px-3 py-2 text-xs cursor-pointer">
            Forensic capture (default off)
          </summary>
          <div className="px-3 pb-3 space-y-2 text-xs">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={forensicEnabled}
                onChange={(e) => setForensicEnabled(e.target.checked)}
              />
              Enable forensic capture for runs of this task
            </label>
            {forensicEnabled && (
              <div className="grid grid-cols-2 gap-2">
                <label className="block col-span-2">
                  <span className="label text-xs">Reason (audited)</span>
                  <input
                    className="input w-full"
                    value={forensicReason}
                    onChange={(e) => setForensicReason(e.target.value)}
                    placeholder="why are we capturing?"
                  />
                </label>
                <label className="block">
                  <span className="label text-xs">TTL hours (1–720)</span>
                  <input
                    type="number"
                    min={1}
                    max={720}
                    className="input w-full"
                    value={forensicTtl}
                    onChange={(e) => setForensicTtl(e.target.value)}
                  />
                </label>
              </div>
            )}
          </div>
        </details>

        {!isEdit && (
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={autoStart}
              onChange={(e) => setAutoStart(e.target.checked)}
            />
            Start the task immediately after create
          </label>
        )}

        {error && (
          <div className="text-spark-danger text-xs border border-spark-danger/30 rounded px-3 py-2">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2 border-t border-spark-border">
          <button
            className="btn"
            onClick={() => {
              reset();
              onClose();
            }}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            className="btn btn-primary"
            disabled={submitting}
            onClick={submit}
          >
            {submitting
              ? isEdit ? "Saving…" : "Creating…"
              : isEdit ? "Save changes" : "Create task"}
          </button>
        </div>
      </div>
    </Modal>
  );
}


// -----------------------------------------------------------------------------
// Triggers panel — webhook integrations (GitHub, Slack, generic bearer)
// -----------------------------------------------------------------------------

interface TriggerSummary {
  trigger_id: string;
  task_name: string;
  enabled: boolean;
  auth_mode: "bearer" | "hmac_sha256";
  payload_forwarding: boolean;
  event_filter: string | null;
  rate_limit_per_hour: number;
  fires_total: number;
  last_fired_at: string | null;
  failed_verify_count: number;
  locked_until: string | null;
}

interface CreatedTrigger {
  trigger_id: string;
  task_name: string;
  auth_mode: string;
  secret: string;
}

function TriggersPanel() {
  const qc = useQueryClient();
  const triggers = useQuery<TriggerSummary[]>({
    queryKey: ["triggers"],
    queryFn: () => api.get("/api/scheduler/triggers"),
  });
  const tasks = useQuery<TaskSummary[]>({
    queryKey: ["tasks"],
    queryFn: () => api.get("/api/scheduler/tasks"),
  });

  const [showCreate, setShowCreate] = useState(false);
  const [revealed, setRevealed] = useState<CreatedTrigger | null>(null);

  async function deleteTrigger(id: string) {
    if (!confirm(`Delete trigger '${id}'? Its credential becomes invalid immediately.`)) {
      return;
    }
    try {
      await api.del(`/api/scheduler/triggers/${encodeURIComponent(id)}`);
      toast.success(`Deleted '${id}'`);
      qc.invalidateQueries({ queryKey: ["triggers"] });
    } catch (err) {
      toast.error(`Delete failed: ${err}`);
    }
  }

  return (
    <section className="panel p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">
          Triggers ({(triggers.data ?? []).length})
        </h3>
        <button
          className="btn btn-primary text-xs"
          onClick={() => setShowCreate(true)}
        >
          + New trigger
        </button>
      </div>
      <p className="text-xs text-spark-muted mb-3">
        Webhook entry points that fire a task. <strong>Bearer</strong>{" "}
        triggers expect a token in the <code>X-Spark-Token</code> header
        (good for hand-rolled scripts). <strong>HMAC-SHA256</strong>{" "}
        triggers verify the body against{" "}
        <code>X-Hub-Signature-256: sha256=…</code> — the standard for
        GitHub, Slack, and most modern providers.
      </p>
      {(triggers.data ?? []).length === 0 ? (
        <EmptyState title="No triggers configured" />
      ) : (
        <div className="space-y-2">
          {(triggers.data ?? []).map((t) => (
            <div
              key={t.trigger_id}
              className="border border-spark-border rounded-md p-3 hover:bg-spark-border/20 transition"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-sm">{t.trigger_id}</span>
                    <span className="chip text-xs">{t.auth_mode}</span>
                    {t.payload_forwarding && (
                      <span className="chip text-xs">payload→task</span>
                    )}
                    {t.event_filter && (
                      <span className="chip text-xs">filtered</span>
                    )}
                    {t.locked_until && (
                      <span className="chip chip-danger text-xs">locked</span>
                    )}
                  </div>
                  <div className="mt-1 text-xs text-spark-muted">
                    fires{" "}
                    <Link
                      to={`/scheduler#task-${t.task_name}`}
                      className="text-spark-link hover:underline font-mono"
                    >
                      {t.task_name}
                    </Link>
                    {" · "}
                    POST <code>/api/scheduler/webhooks/{t.trigger_id}</code>
                    {" · "}
                    rate {t.rate_limit_per_hour}/hr · fired {t.fires_total}
                    {t.last_fired_at && (
                      <>
                        {" · "}
                        last <RelativeTime ts={t.last_fired_at} />
                      </>
                    )}
                    {t.failed_verify_count > 0 && (
                      <>
                        {" · "}
                        <span className="text-spark-danger">
                          {t.failed_verify_count} verify failures
                        </span>
                      </>
                    )}
                  </div>
                </div>
                <button
                  className="btn text-xs shrink-0"
                  onClick={() => deleteTrigger(t.trigger_id)}
                  title="Delete trigger"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <NewTriggerModal
          tasks={tasks.data ?? []}
          onClose={() => setShowCreate(false)}
          onCreated={(t) => {
            setShowCreate(false);
            setRevealed(t);
            qc.invalidateQueries({ queryKey: ["triggers"] });
          }}
        />
      )}

      {revealed && (
        <RevealCredentialModal
          trigger={revealed}
          onClose={() => setRevealed(null)}
        />
      )}
    </section>
  );
}

function NewTriggerModal({
  tasks,
  onClose,
  onCreated,
}: {
  tasks: TaskSummary[];
  onClose: () => void;
  onCreated: (t: CreatedTrigger) => void;
}) {
  const [triggerId, setTriggerId] = useState("");
  const [taskName, setTaskName] = useState(tasks[0]?.name ?? "");
  const [authMode, setAuthMode] = useState<"bearer" | "hmac_sha256">("bearer");
  const [payloadForwarding, setPayloadForwarding] = useState(false);
  const [eventFilterText, setEventFilterText] = useState("");
  const [rateLimit, setRateLimit] = useState(60);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    let event_filter: Record<string, unknown> | null = null;
    const trimmed = eventFilterText.trim();
    if (trimmed) {
      try {
        event_filter = JSON.parse(trimmed);
        if (typeof event_filter !== "object" || event_filter === null || Array.isArray(event_filter)) {
          throw new Error("must be a JSON object");
        }
      } catch (err) {
        toast.error(`Event filter must be a JSON object: ${err}`);
        return;
      }
    }
    setSubmitting(true);
    try {
      const created = await api.post<CreatedTrigger>(
        "/api/scheduler/triggers",
        {
          trigger_id: triggerId,
          task_name: taskName,
          auth_mode: authMode,
          payload_forwarding: payloadForwarding,
          event_filter,
          rate_limit_per_hour: rateLimit,
        },
      );
      onCreated(created);
    } catch (err) {
      toast.error(`Create failed: ${err}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open onClose={onClose}>
      <div className="w-full max-w-lg max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl ">
        <header className="sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3 flex items-center justify-between z-10">
          <h3 className="text-lg font-bold">New trigger</h3>
          <button
            type="button"
            onClick={onClose}
            className="text-spark-muted hover:text-spark-text"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </header>
        <div className="p-4 space-y-4">
        <label className="block">
          <div className="text-xs text-spark-muted mb-1">Trigger ID</div>
          <input
            type="text"
            value={triggerId}
            onChange={(e) => setTriggerId(e.target.value)}
            placeholder="e.g. github-pr-merge"
            className="w-full font-mono text-sm bg-spark-bg border border-spark-border rounded px-2 py-1.5"
          />
        </label>
        <label className="block">
          <div className="text-xs text-spark-muted mb-1">Target task</div>
          <select
            value={taskName}
            onChange={(e) => setTaskName(e.target.value)}
            className="w-full text-sm bg-spark-bg border border-spark-border rounded px-2 py-1.5"
          >
            {tasks.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="space-y-2">
          <legend className="text-xs text-spark-muted mb-1">Auth mode</legend>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              checked={authMode === "bearer"}
              onChange={() => setAuthMode("bearer")}
              className="mt-1"
            />
            <span>
              <strong>Bearer token</strong>
              <div className="text-xs text-spark-muted">
                Caller sends <code>X-Spark-Token: &lt;token&gt;</code>. Cleartext is
                shown once at create time.
              </div>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              checked={authMode === "hmac_sha256"}
              onChange={() => setAuthMode("hmac_sha256")}
              className="mt-1"
            />
            <span>
              <strong>HMAC-SHA256 signature</strong>
              <div className="text-xs text-spark-muted">
                Verifies <code>X-Hub-Signature-256: sha256=…</code> against
                a shared secret in the age vault. Use for GitHub, Slack,
                and any modern signed-webhook provider.
              </div>
            </span>
          </label>
        </fieldset>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={payloadForwarding}
            onChange={(e) => setPayloadForwarding(e.target.checked)}
            className="mt-1"
          />
          <span>
            <strong>Forward request body</strong> to the task as{" "}
            <code>trigger_payload</code>
            <div className="text-xs text-spark-muted">
              The planner sees the (truncated) JSON in its first system
              prompt; the full body is persisted on the run row.
            </div>
          </span>
        </label>
        <label className="block">
          <div className="text-xs text-spark-muted mb-1">
            Event filter (optional, JSON object)
          </div>
          <textarea
            value={eventFilterText}
            onChange={(e) => setEventFilterText(e.target.value)}
            placeholder={'{"action": "closed", "pull_request.merged": true}'}
            rows={3}
            className="w-full font-mono text-xs bg-spark-bg border border-spark-border rounded px-2 py-1.5"
          />
          <div className="text-xs text-spark-muted mt-1">
            Dotted-path lookups against the inbound JSON body. Every key
            must match for the task to fire. Empty = always fire.
          </div>
        </label>
        <label className="block">
          <div className="text-xs text-spark-muted mb-1">
            Rate limit (per hour, 0 = unlimited)
          </div>
          <input
            type="number"
            min={0}
            max={10000}
            value={rateLimit}
            onChange={(e) => setRateLimit(Number(e.target.value))}
            className="w-full text-sm bg-spark-bg border border-spark-border rounded px-2 py-1.5"
          />
        </label>
        <div className="flex justify-end gap-2 pt-2 border-t border-spark-border">
          <button className="btn text-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary text-sm"
            onClick={submit}
            disabled={!triggerId || !taskName || submitting}
          >
            {submitting ? "Creating…" : "Create trigger"}
          </button>
        </div>
        </div>
      </div>
    </Modal>
  );
}

function RevealCredentialModal({
  trigger,
  onClose,
}: {
  trigger: CreatedTrigger;
  onClose: () => void;
}) {
  const isHmac = trigger.auth_mode === "hmac_sha256";
  return (
    <Modal open onClose={onClose}>
      <div className="w-full max-w-lg max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl ">
        <header className="sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3 flex items-center justify-between z-10">
          <h3 className="text-lg font-bold">Save this credential</h3>
          <button
            type="button"
            onClick={onClose}
            className="text-spark-muted hover:text-spark-text"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </header>
      <div className="p-4 space-y-3">
        <p className="text-sm">
          {isHmac
            ? "Configure this as the webhook signing secret in the upstream provider (GitHub: 'Webhook secret'; Slack: 'Signing Secret')."
            : "Send this token in the X-Spark-Token header on each webhook call."}
        </p>
        <div className="bg-spark-bg border border-spark-border rounded p-3">
          <div className="text-xs text-spark-muted mb-1">
            {isHmac ? "Shared secret" : "Bearer token"}
          </div>
          <div className="font-mono text-sm break-all select-all">
            {trigger.secret}
          </div>
        </div>
        <div className="text-xs text-spark-danger">
          ⚠️ This is shown <strong>exactly once</strong>. If lost, delete
          the trigger and create a new one.
        </div>
        <div className="flex justify-end pt-2 border-t border-spark-border">
          <button
            className="btn btn-primary text-sm"
            onClick={() => {
              navigator.clipboard.writeText(trigger.secret);
              toast.success("Copied to clipboard");
            }}
          >
            Copy &amp; close
          </button>
          <button className="btn text-sm ml-2" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      </div>
    </Modal>
  );
}
