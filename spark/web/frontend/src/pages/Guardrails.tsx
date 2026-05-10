import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../lib/api";

interface GuardrailsWindow {
  window_hours: number;
  total_events: number;
  critical: number;
  elevated: number;
  info: number;
  categories: Record<string, number>;
  /** Map of category → primary audit ``kind``, supplied by the
   * backend so category links land on a pre-filtered audit log. */
  category_kinds?: Record<string, string>;
}

export default function GuardrailsPage() {
  const data = useQuery<GuardrailsWindow>({
    queryKey: ["guardrails"],
    queryFn: () => api.get("/api/guardrails/?hours=24"),
  });

  if (!data.data) return <div className="text-spark-muted">Loading…</div>;
  const g = data.data;

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-2xl font-bold">Guardrails</h2>
        <p className="text-spark-muted text-sm">
          Last {g.window_hours}h. Click any category to jump into the filtered
          audit log; expand for the top offenders.
        </p>
      </header>

      <section className="grid grid-cols-3 gap-4">
        <SeverityCard label="Critical" count={g.critical} tone="danger" />
        <SeverityCard label="Elevated" count={g.elevated} tone="warn" />
        <SeverityCard label="Info" count={g.info} tone="neutral" />
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">Categories</h3>
        <ul className="divide-y divide-spark-border">
          {Object.entries(g.categories).map(([cat, count]) => (
            <CategoryRow
              key={cat}
              category={cat}
              count={count}
              auditKind={g.category_kinds?.[cat]}
            />
          ))}
        </ul>
      </section>
    </div>
  );
}

interface OffendersResponse {
  kind: string;
  window_hours: number;
  total: number;
  top_actors: { name: string; count: number }[];
  top_targets: { name: string; count: number }[];
}

function CategoryRow({
  category,
  count,
  auditKind,
}: {
  category: string;
  count: number;
  auditKind?: string;
}) {
  const [open, setOpen] = useState(false);
  const linkTo = auditKind ? `/audit?kind=${encodeURIComponent(auditKind)}` : "/audit";

  return (
    <li className="py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {count > 0 && (
            <button
              className="btn-icon p-0.5"
              onClick={() => setOpen((o) => !o)}
              aria-label={open ? "Hide offenders" : "Show offenders"}
            >
              {open ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
            </button>
          )}
          <Link
            to={linkTo}
            className="font-mono text-sm text-spark-text hover:underline truncate"
          >
            {category}
          </Link>
        </div>
        <span className={`chip ${count > 0 ? "chip-warn" : ""}`}>{count}</span>
      </div>
      {open && count > 0 && auditKind && (
        <CategoryOffenders kind={auditKind} />
      )}
    </li>
  );
}

function CategoryOffenders({ kind }: { kind: string }) {
  const data = useQuery<OffendersResponse>({
    queryKey: ["guardrails-offenders", kind],
    queryFn: () =>
      api.get(
        `/api/guardrails/offenders?kind=${encodeURIComponent(kind)}&limit=5`,
      ),
  });
  if (!data.data) {
    return (
      <div className="mt-2 ml-7 text-xs text-spark-muted">Loading offenders…</div>
    );
  }
  const r = data.data;
  return (
    <div className="mt-2 ml-7 grid grid-cols-2 gap-3 text-xs">
      <OffenderTable label="Top actors" rows={r.top_actors} kind={kind} />
      <OffenderTable label="Top targets" rows={r.top_targets} kind={kind} />
    </div>
  );
}

function OffenderTable({
  label,
  rows,
  kind,
}: {
  label: string;
  rows: { name: string; count: number }[];
  kind: string;
}) {
  if (rows.length === 0) {
    return (
      <div>
        <div className="label mb-1">{label}</div>
        <div className="text-spark-muted">—</div>
      </div>
    );
  }
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <table className="w-full">
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-spark-border/50 first:border-0">
              <td className="py-1 truncate">
                <Link
                  to={`/audit?kind=${encodeURIComponent(kind)}`}
                  className="text-spark-text hover:underline"
                >
                  {r.name}
                </Link>
              </td>
              <td className="py-1 text-right tabular-nums w-12 text-spark-muted">
                {r.count}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SeverityCard({
  label,
  count,
  tone,
}: {
  label: string;
  count: number;
  tone: "danger" | "warn" | "neutral";
}) {
  const color =
    tone === "danger"
      ? "text-spark-danger"
      : tone === "warn"
        ? "text-spark-accent"
        : "text-spark-muted";
  return (
    <div className="panel p-4">
      <div className="label">{label}</div>
      <div className={`text-3xl font-bold mt-1 ${color}`}>{count}</div>
    </div>
  );
}
