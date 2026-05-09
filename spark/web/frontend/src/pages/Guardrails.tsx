import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";

interface GuardrailsWindow {
  window_hours: number;
  total_events: number;
  critical: number;
  elevated: number;
  info: number;
  categories: Record<string, number>;
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
          Last {g.window_hours}h. Click any category to jump into the filtered audit log.
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
            <li key={cat} className="flex items-center justify-between py-2">
              <Link to="/audit" className="font-mono text-sm text-spark-text hover:underline">
                {cat}
              </Link>
              <span
                className={`chip ${
                  count > 0 ? "chip-warn" : ""
                }`}
              >
                {count}
              </span>
            </li>
          ))}
        </ul>
      </section>
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
