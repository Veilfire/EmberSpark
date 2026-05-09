import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { TaskRunSummary } from "../lib/types";
import { formatRelative } from "../lib/utils";

export default function RunHistory() {
  const [stateFilter, setStateFilter] = useState<string>("");
  const [taskFilter, setTaskFilter] = useState<string>("");
  const runs = useQuery<TaskRunSummary[]>({
    queryKey: ["runs", stateFilter, taskFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      params.set("limit", "200");
      if (stateFilter) params.set("state", stateFilter);
      if (taskFilter) params.set("task_name", taskFilter);
      return api.get(`/api/scheduler/runs?${params.toString()}`);
    },
  });

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-2xl font-bold">Run History</h2>
        <p className="text-spark-muted text-sm">
          Every run state, outcome, and budget summary.
        </p>
      </header>
      <div className="flex gap-2">
        <select
          className="input"
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
        >
          <option value="">All states</option>
          {["running", "completed", "failed", "stopped", "paused"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <input
          className="input flex-1"
          placeholder="filter task name"
          value={taskFilter}
          onChange={(e) => setTaskFilter(e.target.value)}
        />
      </div>

      <div className="panel p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase bg-spark-bg">
            <tr>
              <th className="text-left px-3 py-2">run id</th>
              <th className="text-left">task</th>
              <th className="text-left">state</th>
              <th className="text-left">started</th>
              <th className="text-left">iters</th>
              <th className="text-left">models</th>
              <th className="text-left">tools</th>
              <th className="text-left">error</th>
            </tr>
          </thead>
          <tbody>
            {(runs.data ?? []).map((r) => (
              <tr key={r.run_id} className="border-t border-spark-border">
                <td className="py-1 px-3 font-mono text-xs">
                  <a
                    href={`/runs/${encodeURIComponent(r.run_id)}/replay`}
                    className="hover:text-spark-accent"
                  >
                    {r.run_id}
                  </a>
                </td>
                <td>{r.task_name}</td>
                <td>
                  <span className={`chip ${stateClass(r.state)}`}>{r.state}</span>
                </td>
                <td>{formatRelative(r.started_at)}</td>
                <td>{r.iterations}</td>
                <td>{r.model_calls}</td>
                <td>{r.tool_calls}</td>
                <td className="text-spark-danger text-xs max-w-sm truncate">
                  {r.error ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function stateClass(state: string): string {
  if (state === "completed") return "chip-good";
  if (state === "failed") return "chip-danger";
  if (state === "running") return "chip-warn";
  return "";
}
