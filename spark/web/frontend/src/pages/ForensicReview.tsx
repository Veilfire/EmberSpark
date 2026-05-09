import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Download, Eye, Shield, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
import { formatRelative, formatUntil } from "../lib/utils";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/primitives";

type Capture = {
  run_id: string;
  agent_name: string;
  task_name: string;
  enabled_by: string;
  enabled_reason: string;
  captured_at: string | null;
  expires_at: string | null;
  iteration_count: number;
  snapshot_count: number;
  wiped_at: string | null;
};

type Snapshot = {
  id: number;
  iteration: number;
  sequence: number;
  kind: string;
  captured_at: string;
  span_id: number | null;
  payload: Record<string, unknown>;
};

type SnapshotsResponse = {
  capture: Capture;
  snapshots: Snapshot[];
};

const KIND_STYLES: Record<string, { label: string; dot: string; pill: string }> = {
  prompt: {
    label: "Prompt",
    dot: "bg-blue-500",
    pill: "bg-blue-500/10 text-blue-300 border-blue-500/30",
  },
  model: {
    label: "Model",
    dot: "bg-green-500",
    pill: "bg-green-500/10 text-green-300 border-green-500/30",
  },
  tool: {
    label: "Tool",
    dot: "bg-orange-500",
    pill: "bg-orange-500/10 text-orange-300 border-orange-500/30",
  },
  memory_retrieved: {
    label: "Memory read",
    dot: "bg-violet-400",
    pill: "bg-violet-400/10 text-violet-300 border-violet-400/30",
  },
  memory_written: {
    label: "Memory write",
    dot: "bg-violet-600",
    pill: "bg-violet-600/10 text-violet-300 border-violet-600/30",
  },
  reflection: {
    label: "Reflection",
    dot: "bg-spark-muted",
    pill: "bg-spark-muted/10 text-spark-muted border-spark-muted/30",
  },
};

function kindStyle(kind: string) {
  return (
    KIND_STYLES[kind] ?? {
      label: kind,
      dot: "bg-spark-muted",
      pill: "bg-spark-muted/10 text-spark-muted border-spark-muted/30",
    }
  );
}

/**
 * Forensic review — chain-of-thought viewer (H2). Admin-only.
 *
 * Two routes mount the same component:
 *   /forensic          — no run_id param, shows the capture list.
 *   /forensic/:run_id  — decrypts and shows the snapshot chain.
 */
export default function ForensicReview() {
  const params = useParams<{ run_id?: string }>();
  if (params.run_id) {
    return <ForensicRunDetail runId={params.run_id} />;
  }
  return <ForensicList />;
}

function ForensicList() {
  const list = useQuery<Capture[]>({
    queryKey: ["forensic-list"],
    queryFn: () => api.get<Capture[]>("/api/forensic/"),
  });

  return (
    <div className="space-y-4">
      <PageHeader
        icon={<Shield className="w-6 h-6" />}
        title="Forensic review"
        subtitle="Opt-in, encrypted-at-rest capture of the full prompt → reasoning → tool → memory chain for a task run. Admin-only."
      />

      <section className="panel p-4 shadow-sm">
        {list.isLoading && <p className="text-spark-muted text-sm">loading…</p>}
        {list.data && list.data.length === 0 && (
          <EmptyState
            icon={<Shield className="w-8 h-8" />}
            title="No forensic captures"
            description={
              "Start a run with spark task run --forensic \"reason\" ... to capture one."
            }
          />
        )}
        {list.data && list.data.length > 0 && (
          <table className="w-full text-sm">
            <thead className="text-spark-muted text-xs uppercase">
              <tr>
                <th className="text-left">Run</th>
                <th className="text-left">Agent / task</th>
                <th className="text-left">Reason</th>
                <th className="text-left">Captured</th>
                <th className="text-left">Expires</th>
                <th className="text-left">Snapshots</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((c) => (
                <tr key={c.run_id} className="border-t border-spark-border">
                  <td className="font-mono py-2 text-xs">{c.run_id}</td>
                  <td>
                    <div>{c.agent_name}</div>
                    <div className="text-spark-muted text-xs">{c.task_name}</div>
                  </td>
                  <td className="max-w-xs truncate">{c.enabled_reason}</td>
                  <td>{formatRelative(c.captured_at) ?? "—"}</td>
                  <td>{formatUntil(c.expires_at) ?? "—"}</td>
                  <td>
                    {c.snapshot_count}
                    {c.wiped_at && (
                      <span className="chip ml-2">wiped</span>
                    )}
                  </td>
                  <td>
                    {!c.wiped_at && (
                      <Link
                        className="btn btn-primary inline-flex items-center gap-1"
                        to={`/forensic/${encodeURIComponent(c.run_id)}`}
                      >
                        <Eye className="w-3 h-3" /> Inspect
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function ForensicRunDetail({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const snaps = useQuery<SnapshotsResponse>({
    queryKey: ["forensic-snapshots", runId],
    queryFn: () =>
      api.get<SnapshotsResponse>(
        `/api/forensic/${encodeURIComponent(runId)}/snapshots`,
      ),
    retry: false,
  });

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [iterationFilter, setIterationFilter] = useState<number | null>(null);

  const iterations = useMemo(() => {
    if (!snaps.data) return [];
    const set = new Set<number>();
    for (const s of snaps.data.snapshots) set.add(s.iteration);
    return Array.from(set).sort((a, b) => a - b);
  }, [snaps.data]);

  const visibleSnapshots = useMemo(() => {
    if (!snaps.data) return [];
    if (iterationFilter == null) return snaps.data.snapshots;
    return snaps.data.snapshots.filter((s) => s.iteration === iterationFilter);
  }, [snaps.data, iterationFilter]);

  const selected = useMemo(
    () =>
      snaps.data?.snapshots.find((s) => s.id === selectedId) ?? null,
    [snaps.data, selectedId],
  );

  function exportMarkdown() {
    if (!snaps.data) return;
    const lines: string[] = [];
    const c = snaps.data.capture;
    lines.push(`# Forensic run: ${c.run_id}`);
    lines.push("");
    lines.push(`- Agent: \`${c.agent_name}\``);
    lines.push(`- Task: \`${c.task_name}\``);
    lines.push(`- Reason: ${c.enabled_reason}`);
    lines.push(`- Captured: ${c.captured_at}`);
    lines.push(`- Expires: ${c.expires_at}`);
    lines.push(`- Snapshots: ${c.snapshot_count}`);
    lines.push("");
    for (const s of snaps.data.snapshots) {
      lines.push(`## ${s.kind} · iter #${s.iteration}.${s.sequence}`);
      lines.push("```json");
      lines.push(JSON.stringify(s.payload, null, 2));
      lines.push("```");
      lines.push("");
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `forensic-${c.run_id}.md`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("Exported");
  }

  async function wipe() {
    const ok = await confirmDialog({
      title: `Wipe forensic capture for run ${runId}?`,
      description:
        "This shreds the per-run age identity cryptographically and then deletes the rows. The capture becomes unrecoverable — no restore, no undelete. Type the run id to confirm.",
      tone: "danger",
      confirmLabel: "Wipe capture",
      requireTypedName: runId,
    });
    if (!ok) return;
    try {
      await api.del(`/api/forensic/${encodeURIComponent(runId)}`);
      toast.success("Capture wiped");
      navigate("/forensic");
    } catch (err) {
      toast.error(`Wipe failed: ${err}`);
    }
  }

  if (snaps.isLoading) {
    return (
      <div className="panel p-4">
        <p className="text-spark-muted text-sm">Decrypting forensic snapshots…</p>
      </div>
    );
  }
  if (snaps.isError) {
    return (
      <div className="panel p-4">
        <p className="text-red-400 text-sm">
          Failed to load snapshots: {(snaps.error as Error).message}
        </p>
      </div>
    );
  }
  if (!snaps.data) return null;

  const { capture } = snaps.data;

  return (
    <div className="space-y-4">
      <header className="space-y-2">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-2xl font-bold flex items-center gap-2">
              <Shield className="w-6 h-6 text-spark-accent" /> Forensic run
            </h2>
            <p className="text-spark-muted text-sm font-mono">{runId}</p>
          </div>
          <div className="flex gap-2">
            <Link className="btn" to="/forensic">
              Back
            </Link>
            <button
              className="btn flex items-center gap-1"
              onClick={exportMarkdown}
              title="Download as Markdown"
            >
              <Download className="w-3 h-3" /> Export
            </button>
            <button className="btn btn-danger flex items-center gap-1" onClick={wipe}>
              <Trash2 className="w-3 h-3" /> Wipe
            </button>
          </div>
        </div>
        <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <div>
            <dt className="text-spark-muted text-xs uppercase">Agent</dt>
            <dd>{capture.agent_name}</dd>
          </div>
          <div>
            <dt className="text-spark-muted text-xs uppercase">Task</dt>
            <dd>{capture.task_name}</dd>
          </div>
          <div>
            <dt className="text-spark-muted text-xs uppercase">Captured</dt>
            <dd>{formatRelative(capture.captured_at) ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-spark-muted text-xs uppercase">Expires</dt>
            <dd>{formatUntil(capture.expires_at) ?? "—"}</dd>
          </div>
          <div className="col-span-full">
            <dt className="text-spark-muted text-xs uppercase">Reason</dt>
            <dd>{capture.enabled_reason}</dd>
          </div>
        </dl>
      </header>

      <section className="panel p-4">
        <div className="flex items-center gap-2 mb-2 flex-wrap">
          <span className="text-xs uppercase text-spark-muted mr-2">
            Iterations
          </span>
          <button
            className={`chip ${iterationFilter == null ? "ring-1 ring-spark-accent" : ""}`}
            onClick={() => setIterationFilter(null)}
          >
            all
          </button>
          {iterations.map((it) => (
            <button
              key={it}
              className={`chip ${
                iterationFilter === it ? "ring-1 ring-spark-accent" : ""
              }`}
              onClick={() => setIterationFilter(it)}
            >
              #{it}
            </button>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <section className="lg:col-span-2 panel p-4 max-h-[70vh] overflow-auto space-y-2">
          <h3 className="font-semibold mb-2">Chain</h3>
          {visibleSnapshots.map((s) => {
            const style = kindStyle(s.kind);
            const summary = summarizeSnapshot(s);
            const isSelected = selected?.id === s.id;
            return (
              <button
                key={s.id}
                className={`w-full text-left border border-spark-border rounded px-3 py-2 hover:border-spark-accent transition ${
                  isSelected ? "border-spark-accent bg-spark-accent/5" : ""
                }`}
                onClick={() => setSelectedId(s.id)}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className={`inline-block w-2 h-2 rounded-full ${style.dot}`} />
                  <span className={`chip border ${style.pill}`}>{style.label}</span>
                  <span className="text-xs text-spark-muted">
                    #{s.iteration}.{s.sequence}
                  </span>
                </div>
                <p className="text-xs text-spark-muted line-clamp-2">{summary}</p>
              </button>
            );
          })}
          {visibleSnapshots.length === 0 && (
            <p className="text-spark-muted text-sm">No snapshots in this iteration.</p>
          )}
        </section>

        <section className="lg:col-span-3 panel p-4 max-h-[70vh] overflow-auto">
          <h3 className="font-semibold mb-2">Payload</h3>
          {selected ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`chip border ${kindStyle(selected.kind).pill}`}>
                  {kindStyle(selected.kind).label}
                </span>
                <span className="text-xs text-spark-muted">
                  iter #{selected.iteration} · seq {selected.sequence}
                </span>
                <span className="text-xs text-spark-muted">
                  {formatRelative(selected.captured_at) ?? "—"}
                </span>
                <button
                  className="btn ml-auto"
                  onClick={() => {
                    navigator.clipboard.writeText(
                      JSON.stringify(selected.payload, null, 2),
                    );
                    toast.success("Copied");
                  }}
                >
                  Copy JSON
                </button>
              </div>
              <pre className="bg-spark-bg border border-spark-border rounded p-3 text-xs overflow-auto whitespace-pre-wrap break-words">
                {JSON.stringify(selected.payload, null, 2)}
              </pre>
            </div>
          ) : (
            <p className="text-spark-muted text-sm">
              Select a snapshot to view its decrypted payload.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

function summarizeSnapshot(s: Snapshot): string {
  const p = s.payload as Record<string, unknown>;
  switch (s.kind) {
    case "prompt": {
      const sys = typeof p.system_prompt === "string" ? p.system_prompt : "";
      return sys.slice(0, 140);
    }
    case "model": {
      const content = typeof p.content === "string" ? p.content : "";
      const calls = Array.isArray(p.tool_calls_requested)
        ? p.tool_calls_requested.length
        : 0;
      return calls > 0
        ? `→ ${calls} tool call(s): ${content.slice(0, 100)}`
        : content.slice(0, 140) || "(empty)";
    }
    case "tool": {
      const plugin = typeof p.plugin === "string" ? p.plugin : "";
      const err = typeof p.error_code === "string" ? p.error_code : null;
      return err ? `${plugin} → ${err}` : `${plugin} ✓`;
    }
    case "memory_retrieved":
    case "memory_written": {
      const ids = Array.isArray(p.memory_ids) ? p.memory_ids.length : 0;
      return `${ids} memory row(s)`;
    }
    case "reflection": {
      return typeof p.summary === "string" ? p.summary.slice(0, 140) : "";
    }
    default:
      return "";
  }
}
