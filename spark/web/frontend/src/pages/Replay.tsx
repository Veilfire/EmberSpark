import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { MarkdownView } from "../components/MarkdownView";
import {
  FailureInspector,
  SparkErrorView,
  isSparkError,
} from "../components/FailureInspector";

interface SpanRow {
  id: number;
  parent_span_id: number | null;
  name: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  attributes: string;
  error_class: string | null;
}

interface DeliverableLink {
  id: number;
  relative_path: string;
  size_bytes: number;
  kind: string;
  source: string;
  created_at: string;
}

interface ModelCallEvent {
  id: number;
  sequence: number;
  started_at: string;
  finished_at: string | null;
  latency_ms: number;
  provider: string;
  model: string;
  request_id: string | null;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  cache_creation_tokens: number;
  reasoning_tokens: number;
  cost_usd: number | null;
  cost_source: string;
}

interface CostBlock {
  total_usd: number;
  currency: string;
  source_mix: Record<string, number>;
  call_count: number;
}

interface ReplayData {
  run_id: string;
  task_name: string;
  agent_name: string;
  state: string;
  started_at: string;
  finished_at: string | null;
  iterations: number;
  model_calls: number;
  tool_calls: number;
  summary: string | null;
  result_text: string | null;
  trigger_payload_json: string | null;
  triggered_by: string | null;
  error: string | null;
  /** Parsed SparkError when the run failed via a structured exception.
   * Null for runs that failed with a bare exception or that legacy-
   * stored a plain-string error. Frontend feature-detects on shape. */
  error_payload?: SparkErrorView | null;
  cost?: CostBlock;
  model_call_events?: ModelCallEvent[];
  deliverables: DeliverableLink[];
  spans: SpanRow[];
}

export default function Replay() {
  const { run_id } = useParams<{ run_id: string }>();
  const data = useQuery<ReplayData>({
    queryKey: ["replay", run_id],
    queryFn: () => api.get(`/api/replay/${encodeURIComponent(run_id ?? "")}`),
    enabled: !!run_id,
  });

  if (!data.data) return <div className="text-spark-muted">Loading…</div>;
  const r = data.data;

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-2xl font-bold">Run replay</h2>
        <p className="text-spark-muted text-sm font-mono">{r.run_id}</p>
        <div className="mt-2 flex gap-4 text-sm flex-wrap">
          <span className={`chip ${r.state === "completed" ? "chip-good" : r.state === "failed" ? "chip-danger" : "chip-warn"}`}>
            {r.state}
          </span>
          <span>task: {r.task_name}</span>
          <span>iters: {r.iterations}</span>
          <span>model calls: {r.model_calls}</span>
          <span>tool calls: {r.tool_calls}</span>
          {r.cost && r.cost.call_count > 0 && (
            <span title={costSourceTitle(r.cost)}>
              cost: ${r.cost.total_usd.toFixed(4)}
            </span>
          )}
          {r.triggered_by && (
            <span className="text-spark-muted">via {r.triggered_by}</span>
          )}
        </div>
      </header>

      {r.error && (
        <section className="panel p-4 border-spark-danger/40">
          <h3 className="font-semibold mb-2 text-spark-danger">Error</h3>
          {isSparkError(r.error_payload) ? (
            <FailureInspector
              error={r.error_payload}
              context={{ agent_name: r.agent_name, run_id: r.run_id }}
              variant="inline"
            />
          ) : (
            <pre className="text-sm whitespace-pre-wrap text-spark-text">
              {r.error}
            </pre>
          )}
        </section>
      )}

      {r.result_text && (
        <section className="panel p-4">
          <h3 className="font-semibold mb-3">Final response</h3>
          <MarkdownView content={r.result_text} className="text-spark-text text-sm" />
        </section>
      )}

      {r.summary && (
        <section className="panel p-4">
          <h3 className="font-semibold mb-2 text-spark-muted text-xs uppercase tracking-wide">
            Reflection summary
          </h3>
          <MarkdownView content={r.summary} className="text-spark-text text-sm" />
        </section>
      )}

      {r.deliverables.length > 0 && (
        <section className="panel p-4">
          <h3 className="font-semibold mb-3">Deliverables ({r.deliverables.length})</h3>
          <ul className="space-y-1 text-sm">
            {r.deliverables.map((d) => (
              <li key={d.id} className="flex items-center justify-between border-b border-spark-border last:border-0 py-1">
                <a
                  href={`/api/deliverables/${encodeURI(d.relative_path)}`}
                  className="font-mono text-spark-link hover:underline"
                >
                  {d.relative_path}
                </a>
                <span className="text-xs text-spark-muted">
                  {formatBytes(d.size_bytes)} · {d.kind}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {r.trigger_payload_json && <TriggerPayloadPanel raw={r.trigger_payload_json} />}

      {r.model_call_events && r.model_call_events.length > 0 && (
        <ModelCallsPanel calls={r.model_call_events} />
      )}

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">Flame graph ({r.spans.length} spans)</h3>
        <FlameGraph spans={r.spans} />
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">Timeline</h3>
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">depth</th>
              <th className="text-left">span</th>
              <th className="text-left">duration</th>
              <th className="text-left">error</th>
            </tr>
          </thead>
          <tbody>
            {r.spans.map((s) => (
              <tr key={s.id} className="border-t border-spark-border">
                <td className="py-1 text-spark-muted">{s.parent_span_id ? "  ↳" : "●"}</td>
                <td className="font-mono">{s.name}</td>
                <td>{s.duration_ms ? `${s.duration_ms.toFixed(1)} ms` : "—"}</td>
                <td className="text-spark-danger text-xs">{s.error_class ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function costSourceTitle(cost: CostBlock): string {
  const parts = Object.entries(cost.source_mix)
    .map(([k, v]) => `${v} ${k}`)
    .join(", ");
  return `${cost.call_count} model calls (${parts || "no detail"})`;
}

function openrouterDeepLink(req: string | null): string | null {
  if (!req || !req.startsWith("gen-")) return null;
  return `https://openrouter.ai/activity?gen=${encodeURIComponent(req)}`;
}

function ModelCallsPanel({ calls }: { calls: ModelCallEvent[] }) {
  const sorted = [...calls].sort((a, b) => a.sequence - b.sequence);
  const totalReported = sorted.filter((c) => c.cost_source === "reported").length;
  const totalComputed = sorted.length - totalReported;
  return (
    <section className="panel p-4">
      <h3 className="font-semibold mb-3">
        Model calls ({sorted.length})
        <span className="text-spark-muted text-xs ml-2 font-normal">
          {totalReported} reported · {totalComputed} computed
        </span>
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left py-1">#</th>
              <th className="text-left py-1">model</th>
              <th className="text-right py-1">in</th>
              <th className="text-right py-1">out</th>
              <th className="text-right py-1">cache</th>
              <th className="text-right py-1">latency</th>
              <th className="text-right py-1">cost</th>
              <th className="text-left py-1 pl-3">request</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((c) => {
              const cacheTotal = c.cached_input_tokens + c.cache_creation_tokens;
              const link = c.provider === "openrouter" ? openrouterDeepLink(c.request_id) : null;
              return (
                <tr key={c.id} className="border-t border-spark-border">
                  <td className="py-1 font-mono text-spark-muted">{c.sequence}</td>
                  <td className="py-1 font-mono">
                    {c.provider}/{c.model}
                  </td>
                  <td className="py-1 text-right tabular-nums">{c.input_tokens.toLocaleString()}</td>
                  <td className="py-1 text-right tabular-nums">
                    {c.output_tokens.toLocaleString()}
                    {c.reasoning_tokens > 0 && (
                      <span className="text-spark-muted text-xs"> ({c.reasoning_tokens} r)</span>
                    )}
                  </td>
                  <td className="py-1 text-right tabular-nums">
                    {cacheTotal > 0 ? (
                      <span title={`cache_read=${c.cached_input_tokens}, cache_creation=${c.cache_creation_tokens}`}>
                        {cacheTotal.toLocaleString()}
                      </span>
                    ) : (
                      <span className="text-spark-muted">—</span>
                    )}
                  </td>
                  <td className="py-1 text-right tabular-nums">{c.latency_ms} ms</td>
                  <td className="py-1 text-right tabular-nums">
                    {c.cost_usd != null ? (
                      <>
                        ${c.cost_usd.toFixed(5)}
                        <span
                          className={`ml-1 text-xs ${c.cost_source === "reported" ? "text-spark-good" : "text-spark-muted"}`}
                          title={c.cost_source === "reported" ? "Provider-authoritative cost" : "Computed from local price table"}
                        >
                          {c.cost_source === "reported" ? "✓" : "≈"}
                        </span>
                      </>
                    ) : (
                      <span className="text-spark-muted">—</span>
                    )}
                  </td>
                  <td className="py-1 pl-3 font-mono text-xs">
                    {link ? (
                      <a className="text-spark-link hover:underline" href={link} target="_blank" rel="noreferrer">
                        {c.request_id}
                      </a>
                    ) : (
                      <span className="text-spark-muted">{c.request_id ?? "—"}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TriggerPayloadPanel({ raw }: { raw: string }) {
  const [expanded, setExpanded] = useState(false);
  let pretty = raw;
  try {
    pretty = JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    /* leave raw */
  }
  return (
    <section className="panel p-4">
      <button
        type="button"
        className="font-semibold text-left w-full flex items-center justify-between"
        onClick={() => setExpanded((v) => !v)}
      >
        <span>Trigger payload</span>
        <span className="text-spark-muted text-xs">{expanded ? "hide" : "show"}</span>
      </button>
      {expanded && (
        <pre className="mt-3 text-xs font-mono bg-spark-bg p-3 rounded overflow-x-auto whitespace-pre-wrap">
          {pretty}
        </pre>
      )}
    </section>
  );
}

function FlameGraph({ spans }: { spans: SpanRow[] }) {
  const { rows, total } = useMemo(() => {
    if (spans.length === 0) return { rows: [], total: 1 };
    const earliest = Math.min(
      ...spans.map((s) => new Date(s.started_at).getTime())
    );
    const latest = Math.max(
      ...spans.map((s) =>
        s.finished_at ? new Date(s.finished_at).getTime() : new Date(s.started_at).getTime()
      )
    );
    const total = Math.max(latest - earliest, 1);
    // Build depth buckets
    const depthById = new Map<number, number>();
    function depth(s: SpanRow): number {
      if (s.parent_span_id == null) return 0;
      if (depthById.has(s.id)) return depthById.get(s.id)!;
      const parent = spans.find((x) => x.id === s.parent_span_id);
      const d = parent ? depth(parent) + 1 : 0;
      depthById.set(s.id, d);
      return d;
    }
    const rows = spans.map((s) => {
      const start = new Date(s.started_at).getTime() - earliest;
      const end = s.finished_at
        ? new Date(s.finished_at).getTime() - earliest
        : start + (s.duration_ms ?? 0);
      return {
        span: s,
        depth: depth(s),
        start,
        width: Math.max(end - start, 1),
      };
    });
    return { rows, total };
  }, [spans]);

  const maxDepth = rows.reduce((m, r) => Math.max(m, r.depth), 0);
  const rowHeight = 18;
  const height = (maxDepth + 1) * (rowHeight + 2) + 4;

  return (
    <div className="bg-spark-bg border border-spark-border rounded overflow-x-auto">
      <svg width="100%" height={height} viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none">
        {rows.map((r) => {
          const x = (r.start / total) * 1000;
          const w = (r.width / total) * 1000;
          const y = r.depth * (rowHeight + 2) + 2;
          const fill = r.span.error_class ? "#f85149" : "#f59e0b";
          return (
            <g key={r.span.id}>
              <rect x={x} y={y} width={Math.max(w, 1)} height={rowHeight} fill={fill} opacity={0.75} rx={2} />
              <title>
                {r.span.name} — {r.span.duration_ms?.toFixed(1) ?? "?"} ms
              </title>
              {w > 40 && (
                <text
                  x={x + 4}
                  y={y + rowHeight - 4}
                  fontSize={10}
                  fill="#14181d"
                  fontFamily="monospace"
                >
                  {r.span.name}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
