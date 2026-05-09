import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Bot,
  ChevronRight,
  LayoutDashboard,
} from "lucide-react";
import { api } from "../lib/api";
import {
  AgentSummary,
  CostWindow,
  GlobalPosture,
  TaskRunSummary,
} from "../lib/types";
import { formatUsd } from "../lib/utils";
import { PageHeader } from "../components/PageHeader";
import { StatCard, EmptyState, HealthDot } from "../components/primitives";
import { RelativeTime } from "../components/RelativeTime";

type HourlyCost = { buckets: number[] };
type Attention = {
  total: number;
  failed_runs_24h: number;
  pending_skills: number;
  expiring_grants: number;
  expiring_forensic: number;
  dlq_tasks: number;
};

export default function Overview() {
  const cost = useQuery<CostWindow>({
    queryKey: ["cost", "day"],
    queryFn: () => api.get("/api/cost/window/day"),
  });
  const costAllTime = useQuery<CostWindow>({
    queryKey: ["cost", "all"],
    queryFn: () => api.get("/api/cost/window/all"),
  });
  const hourly = useQuery<HourlyCost>({
    queryKey: ["cost", "hourly"],
    queryFn: () => api.get("/api/cost/hourly?hours=24"),
  });
  const runs = useQuery<TaskRunSummary[]>({
    queryKey: ["runs-head"],
    queryFn: () => api.get("/api/scheduler/runs?limit=10"),
  });
  const posture = useQuery<GlobalPosture>({
    queryKey: ["posture"],
    queryFn: () => api.get("/api/security/global"),
  });
  const agents = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: () => api.get("/api/scheduler/agents"),
  });
  const attention = useQuery<Attention>({
    queryKey: ["attention"],
    queryFn: () => api.get("/api/scheduler/attention"),
    refetchInterval: 30_000,
  });

  const activeRuns = (runs.data ?? []).filter((r) => r.state === "running");
  const totalAgents = agents.data?.length ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<LayoutDashboard className="w-6 h-6" />}
        title="Overview"
        subtitle="Runtime snapshot."
      />

      {/* Needs attention banner */}
      {attention.data && attention.data.total > 0 && (
        <Link
          to="/audit"
          className="panel-interactive p-3 flex items-center gap-3 border-spark-accent/30 bg-spark-accent/5"
        >
          <AlertTriangle className="w-5 h-5 text-spark-accent shrink-0" />
          <div className="flex-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {attention.data.failed_runs_24h > 0 && (
              <span>
                <span className="font-bold text-spark-danger">
                  {attention.data.failed_runs_24h}
                </span>{" "}
                <span className="text-spark-muted">failed runs (24h)</span>
              </span>
            )}
            {attention.data.pending_skills > 0 && (
              <span>
                <span className="font-bold text-spark-accent">
                  {attention.data.pending_skills}
                </span>{" "}
                <span className="text-spark-muted">pending skill reviews</span>
              </span>
            )}
            {attention.data.expiring_grants > 0 && (
              <span>
                <span className="font-bold text-spark-accent">
                  {attention.data.expiring_grants}
                </span>{" "}
                <span className="text-spark-muted">grants expiring soon</span>
              </span>
            )}
            {attention.data.expiring_forensic > 0 && (
              <span>
                <span className="font-bold text-spark-accent">
                  {attention.data.expiring_forensic}
                </span>{" "}
                <span className="text-spark-muted">forensic captures expiring</span>
              </span>
            )}
            {attention.data.dlq_tasks > 0 && (
              <span>
                <span className="font-bold text-spark-danger">
                  {attention.data.dlq_tasks}
                </span>{" "}
                <span className="text-spark-muted">tasks in DLQ</span>
              </span>
            )}
          </div>
          <ChevronRight className="w-4 h-4 text-spark-muted" />
        </Link>
      )}

      {/* Stats row */}
      <section className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <StatCard
          label="Spend (24h)"
          value={cost.data ? formatUsd(cost.data.total_usd) : "—"}
          sub={`across ${Object.keys(cost.data?.by_agent ?? {}).length} agents`}
          trend={hourly.data?.buckets}
          tone={
            (cost.data?.total_usd ?? 0) > 5
              ? "warn"
              : "default"
          }
        />
        <StatCard
          label="Spend (all-time)"
          value={costAllTime.data ? formatUsd(costAllTime.data.total_usd) : "—"}
          sub={`${Object.keys(costAllTime.data?.by_model ?? {}).length} models, ${Object.keys(costAllTime.data?.by_agent ?? {}).length} agents`}
        />
        <StatCard
          label="Active runs"
          value={activeRuns.length}
          sub={activeRuns.length > 0 ? "running now" : "none in flight"}
          tone={activeRuns.length > 0 ? "good" : "default"}
        />
        <StatCard
          label="Agents"
          value={totalAgents}
          sub={totalAgents === 0 ? "none installed" : "installed"}
        />
        <StatCard
          label="Posture"
          value={
            posture.data?.frozen
              ? "FROZEN"
              : (posture.data?.compliance_mode ?? "—")
          }
          sub={`privacy: ${posture.data?.default_privacy_mode ?? "—"}`}
          tone={posture.data?.frozen ? "danger" : "default"}
        />
      </section>

      {/* Agents */}
      <section className="panel p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold flex items-center gap-2">
            <Bot className="w-4 h-4 text-spark-accent" />
            Agents ({totalAgents})
          </h3>
          <Link
            to="/agents"
            className="text-spark-accent text-xs hover:underline flex items-center gap-0.5"
          >
            View all <ChevronRight className="w-3 h-3" />
          </Link>
        </div>
        {totalAgents === 0 ? (
          <EmptyState
            icon={<Bot className="w-8 h-8" />}
            title="No agents yet"
            description="Install a template to get started, or create one from scratch."
            action={{ label: "Browse Templates", to: "/templates" }}
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {(agents.data ?? []).map((a) => (
              <Link
                key={a.name}
                to={`/agents/${encodeURIComponent(a.name)}`}
                className="panel-interactive p-3 block"
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="font-mono text-sm truncate">{a.name}</div>
                  <HealthDot ok={true} />
                </div>
                <p className="text-spark-muted text-xs line-clamp-2">
                  {a.description || "—"}
                </p>
                <div className="text-xs text-spark-muted mt-2">
                  <RelativeTime ts={a.updated_at} />
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* Recent runs */}
      <section className="panel p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold flex items-center gap-2">
            <Activity className="w-4 h-4 text-spark-accent" /> Recent runs
          </h3>
          <Link
            to="/runs"
            className="text-spark-accent text-xs hover:underline flex items-center gap-0.5"
          >
            View all <ChevronRight className="w-3 h-3" />
          </Link>
        </div>
        {(runs.data ?? []).length === 0 ? (
          <p className="text-spark-muted text-sm text-center py-6">
            No runs yet. Start a chat or trigger a task.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-spark-muted text-xs uppercase">
              <tr>
                <th className="text-left pb-2">Run</th>
                <th className="text-left pb-2">Task</th>
                <th className="text-left pb-2">State</th>
                <th className="text-left pb-2">Started</th>
                <th className="text-right pb-2 tabular-nums">Tools</th>
                <th className="text-right pb-2 tabular-nums">Iters</th>
              </tr>
            </thead>
            <tbody>
              {(runs.data ?? []).map((r) => (
                <tr
                  key={r.run_id}
                  className="border-t border-spark-border hover:bg-spark-border/20 transition"
                >
                  <td className="py-1.5 font-mono text-xs">
                    <Link
                      to={`/runs/${encodeURIComponent(r.run_id)}/replay`}
                      className="hover:text-spark-accent transition"
                    >
                      {r.run_id.slice(0, 12)}…
                    </Link>
                  </td>
                  <td>{r.task_name}</td>
                  <td>
                    <span className={`chip ${stateClass(r.state)}`}>
                      {r.state}
                    </span>
                  </td>
                  <td>
                    <RelativeTime ts={r.started_at} />
                  </td>
                  <td className="text-right tabular-nums">{r.tool_calls}</td>
                  <td className="text-right tabular-nums">{r.iterations}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function stateClass(state: string): string {
  if (state === "completed") return "chip-good";
  if (state === "failed") return "chip-danger";
  if (state === "running") return "chip-warn";
  return "";
}
