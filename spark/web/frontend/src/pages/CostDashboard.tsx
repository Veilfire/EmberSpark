import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api } from "../lib/api";
import { Budget, CostWindow } from "../lib/types";
import { formatUsd } from "../lib/utils";

export default function CostDashboard() {
  const [period, setPeriod] = useState<"day" | "week" | "month">("day");
  const client = useQueryClient();
  const window = useQuery<CostWindow>({
    queryKey: ["cost", period],
    queryFn: () => api.get(`/api/cost/window/${period}`),
  });
  const budgets = useQuery<Budget[]>({
    queryKey: ["budgets"],
    queryFn: () => api.get("/api/cost/budgets"),
  });
  const events = useQuery<
    {
      run_id: string;
      agent: string;
      task: string | null;
      provider: string;
      model: string;
      total_tokens: number;
      total_usd: number;
      recorded_at: string;
    }[]
  >({
    queryKey: ["cost-events"],
    queryFn: () => api.get("/api/cost/events?limit=50"),
  });

  const create = useMutation({
    mutationFn: (body: Partial<Budget>) => api.post("/api/cost/budgets", body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["budgets"] });
    },
  });

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    create.mutate({
      budget_id: String(data.get("budget_id")),
      scope: data.get("scope") as "global" | "agent" | "provider",
      scope_key: String(data.get("scope_key")),
      period: data.get("period") as "daily" | "weekly" | "monthly",
      limit_usd: Number(data.get("limit_usd")),
      soft_alert_usd: Number(data.get("soft_alert_usd") || 0),
      hard_stop: data.get("hard_stop") === "on",
    });
    e.currentTarget.reset();
  }

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-2xl font-bold">Cost & Budgets</h2>
        <p className="text-spark-muted text-sm">Token spend by provider, agent, model.</p>
      </header>

      <div className="flex gap-2">
        {(["day", "week", "month"] as const).map((p) => (
          <button
            key={p}
            className={`btn ${p === period ? "btn-primary" : ""}`}
            onClick={() => setPeriod(p)}
          >
            {p}
          </button>
        ))}
      </div>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Breakdown title="by provider" data={window.data?.by_provider} />
        <Breakdown title="by agent" data={window.data?.by_agent} />
        <Breakdown title="by model" data={window.data?.by_model} />
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">Budgets</h3>
        <table className="w-full text-sm mb-4">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">id</th>
              <th className="text-left">scope</th>
              <th className="text-left">key</th>
              <th className="text-left">period</th>
              <th className="text-left">limit</th>
              <th className="text-left">alert</th>
              <th className="text-left">hard stop</th>
            </tr>
          </thead>
          <tbody>
            {(budgets.data ?? []).map((b) => (
              <tr key={b.budget_id} className="border-t border-spark-border">
                <td className="py-1 font-mono">{b.budget_id}</td>
                <td>{b.scope}</td>
                <td>{b.scope_key}</td>
                <td>{b.period}</td>
                <td>{formatUsd(b.limit_usd)}</td>
                <td>{formatUsd(b.soft_alert_usd)}</td>
                <td>{b.hard_stop ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <form onSubmit={onSubmit} className="grid grid-cols-3 md:grid-cols-7 gap-2 text-sm">
          <input className="input" name="budget_id" placeholder="budget id" required />
          <select className="input" name="scope" defaultValue="agent">
            <option value="global">global</option>
            <option value="agent">agent</option>
            <option value="provider">provider</option>
          </select>
          <input className="input" name="scope_key" placeholder="scope key (*)" defaultValue="*" />
          <select className="input" name="period" defaultValue="monthly">
            <option value="daily">daily</option>
            <option value="weekly">weekly</option>
            <option value="monthly">monthly</option>
          </select>
          <input
            className="input"
            type="number"
            step="0.01"
            name="limit_usd"
            placeholder="limit"
            required
          />
          <input
            className="input"
            type="number"
            step="0.01"
            name="soft_alert_usd"
            placeholder="alert"
          />
          <label className="flex items-center gap-1 text-xs">
            <input type="checkbox" name="hard_stop" defaultChecked /> hard stop
          </label>
          <button className="btn btn-primary col-span-7 md:col-span-1" type="submit">
            Create
          </button>
        </form>
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">Recent cost events</h3>
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">run</th>
              <th className="text-left">agent</th>
              <th className="text-left">provider</th>
              <th className="text-left">model</th>
              <th className="text-left">tokens</th>
              <th className="text-left">cost</th>
            </tr>
          </thead>
          <tbody>
            {(events.data ?? []).map((e) => (
              <tr key={e.run_id} className="border-t border-spark-border">
                <td className="py-1 font-mono text-xs">{e.run_id}</td>
                <td>{e.agent}</td>
                <td>{e.provider}</td>
                <td>{e.model}</td>
                <td>{e.total_tokens.toLocaleString()}</td>
                <td>{formatUsd(e.total_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function Breakdown({
  title,
  data,
}: {
  title: string;
  data: Record<string, number> | undefined;
}) {
  const entries = Object.entries(data ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <div className="panel p-4">
      <div className="label">{title}</div>
      <div className="mt-2 space-y-1">
        {entries.length === 0 ? (
          <div className="text-spark-muted text-sm">no data</div>
        ) : (
          entries.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between text-sm">
              <span className="truncate">{k}</span>
              <span className="font-mono">{formatUsd(v)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
