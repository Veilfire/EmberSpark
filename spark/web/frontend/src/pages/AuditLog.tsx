import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { AuditEntry } from "../lib/types";
import { formatRelative, severityColor } from "../lib/utils";

export default function AuditLog() {
  const [kind, setKind] = useState("");
  const [severity, setSeverity] = useState("");
  const entries = useQuery<AuditEntry[]>({
    queryKey: ["audit", kind, severity],
    queryFn: () => {
      const params = new URLSearchParams();
      params.set("limit", "300");
      if (kind) params.set("kind", kind);
      if (severity) params.set("min_severity", severity);
      return api.get(`/api/audit/?${params.toString()}`);
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

      <div className="flex gap-2">
        <input
          className="input w-48"
          placeholder="kind filter"
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
      </div>

      <div className="panel p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase bg-spark-bg">
            <tr>
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
              <tr key={i} className="border-t border-spark-border align-top">
                <td className="py-1 px-3 text-xs">{formatRelative(e.ts)}</td>
                <td>{e.actor}</td>
                <td className="font-mono text-xs">{e.kind}</td>
                <td className="font-mono text-xs">{e.target}</td>
                <td>
                  <span className={`chip ${severityColor(e.severity)}`}>{e.severity}</span>
                </td>
                <td className="text-xs text-spark-muted max-w-md truncate">
                  {e.reason || e.diff}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
