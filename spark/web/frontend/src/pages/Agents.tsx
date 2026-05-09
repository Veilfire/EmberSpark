import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bot, Plus, Search, Zap } from "lucide-react";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, HealthDot, SkeletonCard } from "../components/primitives";
import { Timestamp } from "../components/RelativeTime";

type AgentDetail = {
  name: string;
  description: string;
  updated_at: string;
  provider: { type: string; model: string };
  provider_key_available: boolean;
  plugins: string[];
  run_stats: {
    total_7d: number;
    completed_7d: number;
    failed_7d: number;
    success_rate_7d: number | null;
  };
  cost_7d_usd: number;
  health: { sandbox_ok: boolean; provider_key_available: boolean };
};

type AgentSummary = { name: string; description: string; updated_at: string };

export default function Agents() {
  const [filter, setFilter] = useState("");
  const list = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: () => api.get("/api/scheduler/agents"),
  });

  // Fetch detail for each agent in parallel.
  const details = useQueries({
    queries: (list.data ?? []).map((a) => ({
      queryKey: ["agent-detail", a.name],
      queryFn: () =>
        api.get<AgentDetail>(
          `/api/scheduler/agents/${encodeURIComponent(a.name)}`,
        ),
      staleTime: 30_000,
    })),
  });

  const filtered = useMemo(() => {
    const items = list.data ?? [];
    if (!filter) return items;
    const q = filter.toLowerCase();
    return items.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q),
    );
  }, [list.data, filter]);

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<Bot className="w-6 h-6" />}
        title="Agents"
        subtitle="Installed agents with live health, provider config, and recent activity."
        actions={
          <>
            <Link to="/templates" className="btn btn-primary">
              <Plus className="w-4 h-4 mr-1 inline" /> New Agent
            </Link>
          </>
        }
      />

      {list.isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {list.data && list.data.length === 0 && (
        <EmptyState
          icon={<Bot className="w-10 h-10" />}
          title="No agents installed"
          description="Install a ready-to-run template or create your own from scratch."
          action={{ label: "Browse Templates", to: "/templates" }}
        />
      )}

      {list.data && list.data.length > 0 && (
        <>
          <div className="relative max-w-md">
            <Search className="w-4 h-4 text-spark-muted absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              className="input w-full pl-9"
              placeholder={`Search ${list.data.length} agent${list.data.length === 1 ? "" : "s"}…`}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((a) => {
              const detailQ = details.find(
                (d) => d.data?.name === a.name,
              );
              const detail = detailQ?.data;
              const healthy =
                !!detail?.health.sandbox_ok &&
                !!detail?.health.provider_key_available;
              return (
                <Link
                  key={a.name}
                  to={`/agents/${encodeURIComponent(a.name)}`}
                  className="panel-interactive p-4 block"
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <Bot className="w-4 h-4 text-spark-accent shrink-0" />
                      <h3 className="font-bold truncate">{a.name}</h3>
                    </div>
                    <HealthDot
                      ok={detail ? healthy : null}
                      pulse={detail && healthy}
                    />
                  </div>
                  <p className="text-spark-muted text-xs line-clamp-2 mb-3 min-h-[2.5rem]">
                    {a.description || "No description"}
                  </p>

                  {detail && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-1 text-xs">
                        <Zap className="w-3 h-3 text-spark-muted" />
                        <span className="text-spark-muted capitalize">
                          {detail.provider.type}
                        </span>
                        <span className="text-spark-muted">·</span>
                        <span className="font-mono text-[10px] truncate">
                          {detail.provider.model}
                        </span>
                      </div>

                      <div className="flex items-center gap-3 text-xs tabular-nums">
                        <span className="text-spark-muted">7d:</span>
                        <span className="text-spark-good">
                          {detail.run_stats.completed_7d} ✓
                        </span>
                        {detail.run_stats.failed_7d > 0 && (
                          <span className="text-spark-danger">
                            {detail.run_stats.failed_7d} ✗
                          </span>
                        )}
                        {detail.cost_7d_usd > 0 && (
                          <span className="text-spark-muted ml-auto">
                            ${detail.cost_7d_usd.toFixed(3)}
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="text-[10px] text-spark-muted mt-3 border-t border-spark-border pt-2">
                    <Timestamp ts={a.updated_at} />
                  </div>
                </Link>
              );
            })}
          </div>

          {filtered.length === 0 && (
            <p className="text-spark-muted text-sm text-center py-6">
              No agents match "{filter}"
            </p>
          )}
        </>
      )}
    </div>
  );
}
