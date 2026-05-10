import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { AuditEntry } from "../lib/types";
import { formatRelative, severityColor } from "../lib/utils";
import {
  FailureInspector,
  SparkErrorView,
  isSparkError,
} from "../components/FailureInspector";

export default function AuditLog() {
  const [params, setParams] = useSearchParams();
  // URL is the source of truth so /audit?kind=security.permission_denied
  // links from Guardrails / NotificationBell hydrate the filter.
  const [kind, setKind] = useState(() => params.get("kind") ?? "");
  const [severity, setSeverity] = useState(() => params.get("severity") ?? "");

  useEffect(() => {
    const next = new URLSearchParams(params);
    if (kind) next.set("kind", kind);
    else next.delete("kind");
    if (severity) next.set("severity", severity);
    else next.delete("severity");
    if (next.toString() !== params.toString()) {
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, severity]);

  const entries = useQuery<AuditEntry[]>({
    queryKey: ["audit", kind, severity],
    queryFn: () => {
      const qs = new URLSearchParams();
      qs.set("limit", "300");
      if (kind) qs.set("kind", kind);
      if (severity) qs.set("min_severity", severity);
      return api.get(`/api/audit/?${qs.toString()}`);
    },
  });

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-2xl font-bold">Audit Log</h2>
        <p className="text-spark-muted text-sm">
          Every security-relevant mutation, immutable and searchable.
        </p>
      </header>

      <div className="flex gap-2 items-center flex-wrap">
        <input
          className="input w-72"
          placeholder="kind filter (e.g. security.permission_denied)"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        />
        <select
          className="input"
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
        >
          <option value="">All severities</option>
          <option value="info">info+</option>
          <option value="elevated">elevated+</option>
          <option value="critical">critical</option>
        </select>
        {(kind || severity) && (
          <button
            className="btn btn-ghost text-xs"
            onClick={() => {
              setKind("");
              setSeverity("");
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="panel p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase bg-spark-bg">
            <tr>
              <th className="w-6"></th>
              <th className="text-left px-3 py-2">when</th>
              <th className="text-left">actor</th>
              <th className="text-left">kind</th>
              <th className="text-left">target</th>
              <th className="text-left">severity</th>
              <th className="text-left">reason / diff</th>
            </tr>
          </thead>
          <tbody>
            {(entries.data ?? []).map((e, i) => (
              <AuditRow key={i} entry={e} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const sparkError = parseEmbeddedSparkError(entry.diff);
  const expandable = !!sparkError || isMeaningfulDiff(entry.diff);

  return (
    <>
      <tr className="border-t border-spark-border align-top">
        <td className="px-2 py-1">
          {expandable && (
            <button
              className="btn-icon p-0.5"
              onClick={() => setOpen((o) => !o)}
              aria-label={open ? "Collapse" : "Expand"}
            >
              {open ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
            </button>
          )}
        </td>
        <td className="py-1 px-3 text-xs">{formatRelative(entry.ts)}</td>
        <td>{entry.actor}</td>
        <td className="font-mono text-xs">{entry.kind}</td>
        <td className="font-mono text-xs">{entry.target}</td>
        <td>
          <span className={`chip ${severityColor(entry.severity)}`}>
            {entry.severity}
          </span>
        </td>
        <td className="text-xs text-spark-muted max-w-md truncate">
          {entry.reason || entry.diff}
        </td>
      </tr>
      {open && (
        <tr className="border-t border-spark-border/50 bg-spark-bg/40">
          <td></td>
          <td colSpan={6} className="px-3 py-2">
            {sparkError ? (
              <FailureInspector error={sparkError} variant="inline" />
            ) : (
              <pre className="text-xs font-mono text-spark-text whitespace-pre-wrap break-all">
                {prettyDiff(entry.diff)}
              </pre>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

/** Try to extract an embedded :class:`SparkError.to_dict()` from the
 * audit diff. Some gate-failure audits will eventually carry the full
 * payload; for now this gracefully degrades when the diff is plain JSON
 * with no SparkError shape. */
function parseEmbeddedSparkError(diff: string | null): SparkErrorView | null {
  if (!diff) return null;
  try {
    const parsed = JSON.parse(diff);
    if (isSparkError(parsed)) return parsed;
    // Some entries embed under `error` or `spark_error`.
    if (parsed && typeof parsed === "object") {
      for (const k of ["error", "spark_error", "payload"]) {
        if (isSparkError((parsed as Record<string, unknown>)[k])) {
          return (parsed as Record<string, unknown>)[k] as SparkErrorView;
        }
      }
    }
  } catch {
    // not JSON; fall through
  }
  return null;
}

function isMeaningfulDiff(diff: string | null): boolean {
  if (!diff) return false;
  if (diff.length > 80) return true;
  return /[{[]/.test(diff);
}

function prettyDiff(diff: string | null): string {
  if (!diff) return "";
  try {
    return JSON.stringify(JSON.parse(diff), null, 2);
  } catch {
    return diff;
  }
}
