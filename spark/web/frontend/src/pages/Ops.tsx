import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatTimestamp } from "../lib/utils";

interface PluginRow {
  name: string;
  version: string;
  module_hash: string;
  first_seen_at: string;
  last_seen_at: string;
}

interface DataResidency {
  db: { path: string; exists: boolean; size_bytes: number };
  chroma: { path: string; exists: boolean; size_bytes: number };
  logs: { path: string; exists: boolean; size_bytes: number };
  scheduler: { path: string; exists: boolean; size_bytes: number };
  web_token: { path: string; exists: boolean; size_bytes: number };
  disk: { total: number; used: number; free: number };
}

export default function Ops() {
  const health = useQuery<{ ok: boolean; sandbox_backend?: string; sandbox_error?: string }>({
    queryKey: ["ops-health"],
    queryFn: () => api.get("/api/ops/health"),
  });
  const residency = useQuery<DataResidency>({
    queryKey: ["ops-residency"],
    queryFn: () => api.get("/api/ops/data-residency"),
  });
  const plugins = useQuery<PluginRow[]>({
    queryKey: ["ops-plugins"],
    queryFn: () => api.get("/api/ops/plugins"),
  });
  const [logs, setLogs] = useState<unknown[]>([]);

  useEffect(() => {
    const source = new EventSource("/api/stream/logs", { withCredentials: true });
    source.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        setLogs((prev) => [...prev.slice(-400), payload]);
      } catch {
        /* ignore */
      }
    };
    return () => source.close();
  }, []);

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-2xl font-bold">Ops</h2>
        <p className="text-spark-muted text-sm">
          Host health, data residency, plugin registry, live log tail.
        </p>
      </header>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="panel p-4">
          <div className="label">Sandbox</div>
          <div className={`text-lg font-semibold ${health.data?.ok ? "text-spark-good" : "text-spark-danger"}`}>
            {health.data?.ok ? health.data.sandbox_backend : "unavailable"}
          </div>
          {health.data?.sandbox_error && (
            <div className="text-xs text-spark-danger mt-1">{health.data.sandbox_error}</div>
          )}
        </div>
        <div className="panel p-4">
          <div className="label">Disk free</div>
          <div className="text-lg font-semibold">
            {residency.data ? formatBytes(residency.data.disk.free) : "…"}
          </div>
          <div className="text-xs text-spark-muted">
            of {residency.data ? formatBytes(residency.data.disk.total) : "…"}
          </div>
        </div>
        <div className="panel p-4">
          <div className="label">Registered plugins</div>
          <div className="text-lg font-semibold">{plugins.data?.length ?? 0}</div>
        </div>
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-2">Data residency</h3>
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">what</th>
              <th className="text-left">path</th>
              <th className="text-left">exists</th>
              <th className="text-left">size</th>
            </tr>
          </thead>
          <tbody>
            {residency.data &&
              (["db", "chroma", "logs", "scheduler", "web_token"] as const).map((k) => {
                const info = residency.data?.[k];
                if (!info) return null;
                return (
                  <tr key={k} className="border-t border-spark-border">
                    <td className="py-1">{k}</td>
                    <td className="font-mono text-xs">{info.path}</td>
                    <td>{info.exists ? "yes" : "no"}</td>
                    <td>{formatBytes(info.size_bytes)}</td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-2">Plugin registry</h3>
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">name</th>
              <th className="text-left">version</th>
              <th className="text-left">hash</th>
              <th className="text-left">first seen</th>
              <th className="text-left">last seen</th>
            </tr>
          </thead>
          <tbody>
            {(plugins.data ?? []).map((p) => (
              <tr key={p.name} className="border-t border-spark-border">
                <td className="py-1 font-mono">{p.name}</td>
                <td>{p.version}</td>
                <td className="font-mono text-xs truncate max-w-xs">{p.module_hash.slice(0, 16)}</td>
                <td className="text-xs">{formatTimestamp(p.first_seen_at)}</td>
                <td className="text-xs">{formatTimestamp(p.last_seen_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-2">Log tail (live)</h3>
        <div className="bg-spark-bg border border-spark-border rounded p-2 h-96 overflow-auto font-mono text-xs">
          {logs.map((line, i) => (
            <div key={i}>{JSON.stringify(line)}</div>
          ))}
        </div>
      </section>
    </div>
  );
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KiB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MiB`;
  return `${(b / 1024 ** 3).toFixed(2)} GiB`;
}
