import { useEffect, useState } from "react";
import { api } from "../lib/api";

interface AuditEntry {
  ts: string;
  actor: string;
  kind: string;
  target: string;
  severity: string;
  reason: string;
}

/** Persistent banner that surfaces the most recent critical audit entry. */
export function IncidentBanner() {
  const [incident, setIncident] = useState<AuditEntry | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const rows = await api.get<AuditEntry[]>(
          "/api/audit/?limit=20&min_severity=critical"
        );
        if (!cancelled) setIncident(rows[0] ?? null);
      } catch {
        /* silent */
      }
    }
    load();
    const interval = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  if (!incident) return null;
  return (
    <div className="bg-spark-danger/10 border-b border-spark-danger text-spark-danger px-4 py-2 text-sm flex items-center justify-between">
      <div>
        <span className="font-bold">⚠ Incident</span> — {incident.kind} · {incident.target}
        {incident.reason && <span className="text-xs ml-2">({incident.reason})</span>}
      </div>
      <button className="btn btn-danger text-xs" onClick={() => setIncident(null)}>
        Dismiss
      </button>
    </div>
  );
}
