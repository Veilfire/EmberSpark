import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { formatUsd } from "../lib/utils";

interface AgentStats {
  window_days: number;
  runs_total: number;
  runs_completed: number;
  runs_failed: number;
  success_rate: number;
  wall_time_p50_s: number;
  wall_time_p95_s: number;
  total_cost_usd: number;
  avg_cost_per_run_usd: number;
  memory_writes: number;
  skills_approved: number;
}

export default function StatsPage() {
  const stats = useQuery<AgentStats>({
    queryKey: ["agent-stats"],
    queryFn: () => api.get("/api/stats/"),
  });

  if (!stats.data) {
    return <div className="text-spark-muted">Loading…</div>;
  }
  const s = stats.data;

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-2xl font-bold">Agent stats</h2>
        <p className="text-spark-muted text-sm">Rolling {s.window_days}-day window.</p>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Runs (total)" value={String(s.runs_total)} />
        <Stat
          label="Success rate"
          value={`${(s.success_rate * 100).toFixed(1)}%`}
          highlight={s.success_rate < 0.5 ? "danger" : undefined}
        />
        <Stat label="Completed" value={String(s.runs_completed)} />
        <Stat
          label="Failed"
          value={String(s.runs_failed)}
          highlight={s.runs_failed > 0 ? "danger" : undefined}
        />
        <Stat label="Wall p50" value={`${s.wall_time_p50_s.toFixed(1)}s`} />
        <Stat label="Wall p95" value={`${s.wall_time_p95_s.toFixed(1)}s`} />
        <Stat label="Total cost" value={formatUsd(s.total_cost_usd)} />
        <Stat label="Avg / run" value={formatUsd(s.avg_cost_per_run_usd)} />
        <Stat label="Memory writes" value={String(s.memory_writes)} />
        <Stat label="Skills approved" value={String(s.skills_approved)} />
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: "danger";
}) {
  return (
    <div className="panel p-4">
      <div className="label">{label}</div>
      <div
        className={`text-2xl font-semibold mt-1 ${
          highlight === "danger" ? "text-spark-danger" : ""
        }`}
      >
        {value}
      </div>
    </div>
  );
}
