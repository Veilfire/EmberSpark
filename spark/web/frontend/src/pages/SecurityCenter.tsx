import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
import { AgentSummary, GlobalPosture, InternalGrant } from "../lib/types";
import { formatRelative, formatUntil } from "../lib/utils";

type Section =
  | "global"
  | "network"
  | "filesystem"
  | "sandbox"
  | "plugins"
  | "privacy"
  | "data-classes"
  | "secrets"
  | "trusted-docs";

export default function SecurityCenter() {
  const [section, setSection] = useState<Section>("global");

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-2xl font-bold">Security Center</h2>
        <p className="text-spark-muted text-sm">
          Global posture, per-agent policies, trusted sources, and audit-backed changes.
        </p>
      </header>

      <div className="flex flex-wrap gap-2 text-sm">
        {[
          ["global", "Global Posture"],
          ["network", "Network"],
          ["filesystem", "Filesystem"],
          ["sandbox", "Sandbox"],
          ["plugins", "Plugins"],
          ["privacy", "Privacy"],
          ["data-classes", "Data Classes"],
          ["secrets", "Secrets"],
          ["trusted-docs", "Trusted Docs"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={`btn ${section === key ? "btn-primary" : ""}`}
            onClick={() => setSection(key as Section)}
          >
            {label}
          </button>
        ))}
      </div>

      {section === "global" && <GlobalPanel />}
      {section === "network" && <NetworkPanel />}
      {section === "filesystem" && <FilesystemPanel />}
      {section === "sandbox" && <SandboxPanel />}
      {section === "plugins" && <PluginsPanel />}
      {section === "privacy" && <PrivacyPanel />}
      {section === "data-classes" && <DataClassesPanel />}
      {section === "secrets" && <SecretsPanel />}
      {section === "trusted-docs" && <TrustedDocsPanel />}
    </div>
  );
}

// -----------------------------------------------------------------------------

function GlobalPanel() {
  const client = useQueryClient();
  const posture = useQuery<GlobalPosture>({
    queryKey: ["posture"],
    queryFn: () => api.get("/api/security/global"),
  });

  const [reason, setReason] = useState("");
  const [confirmText, setConfirmText] = useState("");

  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post("/api/security/global", body),
    onSuccess: () => client.invalidateQueries({ queryKey: ["posture"] }),
  });

  async function freeze() {
    if (!reason) {
      toast.error("Reason required to freeze the runtime");
      return;
    }
    await api.post(`/api/security/global/freeze?reason=${encodeURIComponent(reason)}`);
    client.invalidateQueries({ queryKey: ["posture"] });
  }

  async function unfreeze() {
    await api.post("/api/security/global/unfreeze");
    client.invalidateQueries({ queryKey: ["posture"] });
  }

  const p = posture.data;

  return (
    <div className="space-y-4">
      <div className="panel p-4 space-y-3">
        <div className="flex items-center gap-4">
          <Kv label="Frozen" value={p?.frozen ? "YES" : "no"} highlight={p?.frozen} />
          <Kv label="Compliance" value={p?.compliance_mode ?? "—"} />
          <Kv label="Default privacy" value={p?.default_privacy_mode ?? "—"} />
          <Kv label="Internal IPs" value={p?.allow_internal_ips ? "ALLOWED" : "blocked"} highlight={p?.allow_internal_ips} />
          <Kv label="Raw logging" value={p?.allow_raw_logging ? "ON" : "off"} highlight={p?.allow_raw_logging} />
        </div>
        <div className="text-xs text-spark-muted">
          Last updated {formatRelative(p?.updated_at)} by {p?.updated_by ?? "unknown"}
        </div>
      </div>

      <div className="panel p-4 space-y-3">
        <h3 className="font-semibold text-spark-danger">Emergency freeze</h3>
        <p className="text-sm text-spark-muted">
          Halts the scheduler and refuses new runs until unfrozen. Persists across restart.
        </p>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="reason for freeze"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button className="btn btn-danger" onClick={freeze} disabled={p?.frozen}>
            Freeze
          </button>
          <button className="btn" onClick={unfreeze} disabled={!p?.frozen}>
            Unfreeze
          </button>
        </div>
      </div>

      <div className="panel p-4 space-y-3">
        <h3 className="font-semibold">Elevated toggles</h3>
        <p className="text-xs text-spark-muted">
          Type <span className="kbd">confirm</span> in the box below, then press a toggle.
        </p>
        <input
          className="input"
          placeholder="type confirm"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
        />
        <div className="flex gap-2 flex-wrap">
          <button
            className="btn btn-danger"
            onClick={() =>
              update.mutate({
                allow_internal_ips: !p?.allow_internal_ips,
                confirm_agent_name: confirmText,
                reason: "UI toggle",
              })
            }
          >
            Toggle internal-IP access
          </button>
          <button
            className="btn btn-danger"
            onClick={() =>
              update.mutate({
                allow_raw_logging: !p?.allow_raw_logging,
                confirm_agent_name: confirmText,
                reason: "UI toggle",
              })
            }
          >
            Toggle raw logging
          </button>
          <button
            className="btn"
            onClick={() =>
              update.mutate({
                compliance_mode: p?.compliance_mode === "audit" ? "standard" : "audit",
              })
            }
          >
            Toggle audit mode
          </button>
        </div>
      </div>
    </div>
  );
}

function Kv({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`font-semibold ${highlight ? "text-spark-danger" : ""}`}>{value}</div>
    </div>
  );
}

// -----------------------------------------------------------------------------

function AgentPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const agents = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: () => api.get("/api/scheduler/agents"),
  });
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">pick agent…</option>
      {(agents.data ?? []).map((a) => (
        <option key={a.name} value={a.name}>
          {a.name}
        </option>
      ))}
    </select>
  );
}

// -----------------------------------------------------------------------------

function NetworkPanel() {
  const [agent, setAgent] = useState("");
  const grants = useQuery<InternalGrant[]>({
    queryKey: ["grants", agent],
    queryFn: () =>
      agent ? api.get(`/api/security/internal-grants/${agent}`) : Promise.resolve([]),
    enabled: !!agent,
  });

  async function addGrant(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    await api.post("/api/security/internal-grants", {
      agent_name: agent,
      cidr: String(data.get("cidr")),
      reason: String(data.get("reason")),
      ttl_hours: Number(data.get("ttl_hours") || 4),
      confirm_agent_name: String(data.get("confirm")),
    });
    grants.refetch();
    e.currentTarget.reset();
  }

  async function patchNet(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const body = {
      allow_hosts: String(data.get("allow_hosts") || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      allow_http: data.get("allow_http") === "on",
      max_response_bytes: Number(data.get("max_response_bytes")),
      connect_timeout_seconds: Number(data.get("connect_timeout_seconds")),
      read_timeout_seconds: Number(data.get("read_timeout_seconds")),
    };
    await api.post(`/api/security/agents/${agent}/network`, body);
    toast.success("Patch queued — audited");
  }

  return (
    <div className="space-y-4">
      <div className="panel p-4 flex items-center gap-2">
        <span className="label">Agent:</span>
        <AgentPicker value={agent} onChange={setAgent} />
      </div>

      {agent && (
        <>
          <form onSubmit={patchNet} className="panel p-4 space-y-3">
            <h3 className="font-semibold">Outbound network policy</h3>
            <label className="block">
              <span className="label">Allowed hosts (comma separated)</span>
              <input className="input w-full" name="allow_hosts" placeholder="api.github.com, example.com" />
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" name="allow_http" />
              <span className="text-sm">Allow plain http:// (not recommended)</span>
            </label>
            <div className="grid grid-cols-3 gap-2">
              <label className="block">
                <span className="label">Max response bytes</span>
                <input className="input w-full" type="number" name="max_response_bytes" defaultValue={5_000_000} />
              </label>
              <label className="block">
                <span className="label">Connect timeout (s)</span>
                <input className="input w-full" type="number" step="0.1" name="connect_timeout_seconds" defaultValue={5} />
              </label>
              <label className="block">
                <span className="label">Read timeout (s)</span>
                <input className="input w-full" type="number" step="0.1" name="read_timeout_seconds" defaultValue={15} />
              </label>
            </div>
            <button className="btn btn-primary" type="submit">
              Queue patch
            </button>
          </form>

          <div className="panel p-4 space-y-3">
            <h3 className="font-semibold text-spark-danger">Internal IP grants</h3>
            <p className="text-xs text-spark-muted">
              Allow an agent to reach an internal CIDR for a bounded window. Hard-blocked otherwise.
            </p>
            <table className="w-full text-sm">
              <thead className="text-spark-muted text-xs uppercase">
                <tr>
                  <th className="text-left">cidr</th>
                  <th className="text-left">reason</th>
                  <th className="text-left">expires</th>
                  <th className="text-left">granted by</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(grants.data ?? []).map((g) => (
                  <tr key={g.id} className="border-t border-spark-border">
                    <td className="py-1 font-mono">{g.cidr}</td>
                    <td>{g.reason}</td>
                    <td>{formatUntil(g.expires_at)}</td>
                    <td>{g.granted_by}</td>
                    <td>
                      <button
                        className="btn btn-danger"
                        onClick={async () => {
                          await api.del(`/api/security/internal-grants/${g.id}`);
                          grants.refetch();
                        }}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <form onSubmit={addGrant} className="grid grid-cols-2 md:grid-cols-5 gap-2">
              <input className="input" name="cidr" placeholder="10.0.5.0/24" required />
              <input className="input md:col-span-2" name="reason" placeholder="reason" required />
              <input className="input" name="ttl_hours" type="number" min={1} max={24} defaultValue={4} />
              <input className="input" name="confirm" placeholder={`type ${agent}`} required />
              <button className="btn btn-danger col-span-2 md:col-span-5" type="submit">
                Grant (elevated)
              </button>
            </form>
          </div>
        </>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------------

function FilesystemPanel() {
  const [agent, setAgent] = useState("");
  async function patch(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const body = {
      allow_paths: String(data.get("allow_paths") || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      deny_paths: String(data.get("deny_paths") || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      max_read_bytes: Number(data.get("max_read_bytes")),
      max_files_per_call: Number(data.get("max_files_per_call")),
    };
    await api.post(`/api/security/agents/${agent}/filesystem`, body);
    toast.success("Patch queued — audited");
  }
  return (
    <div className="space-y-4">
      <div className="panel p-4 flex items-center gap-2">
        <span className="label">Agent:</span>
        <AgentPicker value={agent} onChange={setAgent} />
      </div>
      {agent && (
        <form onSubmit={patch} className="panel p-4 space-y-3">
          <label className="block">
            <span className="label">Allow paths (one per line)</span>
            <textarea className="input w-full h-24 font-mono text-xs" name="allow_paths" />
          </label>
          <label className="block">
            <span className="label">Deny paths</span>
            <textarea className="input w-full h-16 font-mono text-xs" name="deny_paths" />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="label">Max read bytes</span>
              <input className="input w-full" type="number" name="max_read_bytes" defaultValue={5_000_000} />
            </label>
            <label className="block">
              <span className="label">Max files per call</span>
              <input className="input w-full" type="number" name="max_files_per_call" defaultValue={256} />
            </label>
          </div>
          <button className="btn btn-primary" type="submit">
            Queue patch
          </button>
        </form>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------------

function SandboxPanel() {
  const [agent, setAgent] = useState("");
  async function patch(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    await api.post(`/api/security/agents/${agent}/sandbox`, {
      backend: data.get("backend"),
      cpu_seconds: Number(data.get("cpu_seconds")),
      memory_mb: Number(data.get("memory_mb")),
      max_open_files: Number(data.get("max_open_files")),
      max_processes: Number(data.get("max_processes")),
      timeout_seconds: Number(data.get("timeout_seconds")),
    });
    toast.success("Patch queued — audited");
  }
  async function selfTest() {
    const resp = await api.post<{ backend: string; available: boolean }>(
      "/api/security/sandbox/self-test"
    );
    if (resp.available) {
      toast.success(`Sandbox OK — backend ${resp.backend}`, {
        description: "Self-test completed successfully.",
      });
    } else {
      toast.error(`Sandbox unavailable — backend ${resp.backend}`, {
        description:
          "The configured sandbox failed its self-test. Tool calls will be refused until this is resolved.",
      });
    }
  }
  return (
    <div className="space-y-4">
      <div className="panel p-4 flex items-center gap-2 justify-between">
        <div className="flex items-center gap-2">
          <span className="label">Agent:</span>
          <AgentPicker value={agent} onChange={setAgent} />
        </div>
        <button className="btn" onClick={selfTest}>
          Run self-test
        </button>
      </div>
      {agent && (
        <form onSubmit={patch} className="panel p-4 space-y-3">
          <div className="text-xs text-spark-muted">
            Mandatory: sandbox cannot be disabled. Backend selection and rlimits only.
          </div>
          <div className="grid grid-cols-3 gap-2">
            <label className="block">
              <span className="label">Backend</span>
              <select className="input w-full" name="backend" defaultValue="auto">
                <option value="auto">auto</option>
                <option value="bubblewrap">bubblewrap</option>
                <option value="nsjail">nsjail (strict)</option>
                <option value="seatbelt">seatbelt (macOS)</option>
              </select>
            </label>
            <label className="block">
              <span className="label">CPU seconds</span>
              <input className="input w-full" type="number" name="cpu_seconds" defaultValue={30} />
            </label>
            <label className="block">
              <span className="label">Memory MB</span>
              <input className="input w-full" type="number" name="memory_mb" defaultValue={512} />
            </label>
            <label className="block">
              <span className="label">Max open files</span>
              <input className="input w-full" type="number" name="max_open_files" defaultValue={128} />
            </label>
            <label className="block">
              <span className="label">Max processes</span>
              <input className="input w-full" type="number" name="max_processes" defaultValue={8} />
            </label>
            <label className="block">
              <span className="label">Timeout (s)</span>
              <input className="input w-full" type="number" name="timeout_seconds" defaultValue={60} />
            </label>
          </div>
          <button className="btn btn-primary" type="submit">
            Queue patch
          </button>
        </form>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------------

function PluginsPanel() {
  const [agent, setAgent] = useState("");
  async function patch(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    await api.post(`/api/security/agents/${agent}/plugins`, {
      allow: String(data.get("allow") || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      grants: String(data.get("grants") || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    });
    toast.success("Patch queued — audited");
  }
  return (
    <div className="space-y-4">
      <div className="panel p-4 flex items-center gap-2">
        <span className="label">Agent:</span>
        <AgentPicker value={agent} onChange={setAgent} />
      </div>
      {agent && (
        <form onSubmit={patch} className="panel p-4 space-y-3">
          <label className="block">
            <span className="label">Allowed plugins (comma separated)</span>
            <input className="input w-full" name="allow" placeholder="filesystem, http_client, markdown_writer" />
          </label>
          <label className="block">
            <span className="label">Permission grants</span>
            <input className="input w-full" name="grants" placeholder="fs.read, fs.write, net.http, secrets.read" />
          </label>
          <p className="text-xs text-spark-muted">
            Missing grants → deny. Plugin declared permissions must be a subset of this list.
          </p>
          <button className="btn btn-primary" type="submit">
            Queue patch
          </button>
        </form>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------------

function PrivacyPanel() {
  const [agent, setAgent] = useState("");
  async function patch(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const raw_prompts = data.get("raw_prompts") === "on";
    const raw_outputs = data.get("raw_outputs") === "on";
    if (raw_prompts || raw_outputs) {
      const ok = await confirmDialog({
        title: "Enable raw logging?",
        description:
          "Raw logging bypasses the redaction pipeline. Prompts and model outputs will be written to logs unfiltered. This is a CRITICAL-severity change and will be audited.",
        tone: "danger",
        confirmLabel: "Enable raw logging",
      });
      if (!ok) return;
    }
    await api.post(`/api/security/agents/${agent}/privacy`, {
      privacy_mode: data.get("privacy_mode"),
      raw_prompts,
      raw_model_outputs: raw_outputs,
    });
    toast.success("Patch queued — audited");
  }
  return (
    <div className="space-y-4">
      <div className="panel p-4 flex items-center gap-2">
        <span className="label">Agent:</span>
        <AgentPicker value={agent} onChange={setAgent} />
      </div>
      {agent && (
        <form onSubmit={patch} className="panel p-4 space-y-3">
          <label className="block">
            <span className="label">Privacy mode</span>
            <select className="input" name="privacy_mode" defaultValue="strict">
              <option value="strict">strict</option>
              <option value="balanced">balanced</option>
              <option value="regex_only">regex_only</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" name="raw_prompts" />
            Raw prompt logging (critical)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" name="raw_outputs" />
            Raw model output logging (critical)
          </label>
          <button className="btn btn-primary" type="submit">
            Queue patch
          </button>
        </form>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------------

function SecretsPanel() {
  const names = useQuery<string[]>({
    queryKey: ["secrets-names"],
    queryFn: () => api.get("/api/security/secrets"),
  });

  const [canary, setCanary] = useState("");

  async function test() {
    const resp = await api.post<{ ok: boolean }>("/api/security/secrets/canary", {
      name: canary,
    });
    if (resp.ok) {
      toast.success(`Secret "${canary}" is reachable`);
    } else {
      toast.error(`Secret "${canary}" NOT found in the vault`);
    }
  }

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <h3 className="font-semibold mb-2">Secret names (values never shown)</h3>
        <div className="flex flex-wrap gap-1">
          {(names.data ?? []).map((n) => (
            <span key={n} className="chip font-mono">
              {n}
            </span>
          ))}
          {names.data?.length === 0 && (
            <span className="text-spark-muted text-sm">
              no secrets declared in the age vault or env fallback
            </span>
          )}
        </div>
      </div>
      <div className="panel p-4 space-y-2">
        <h3 className="font-semibold">Canary test</h3>
        <p className="text-xs text-spark-muted">
          Ask the runtime to resolve a secret by name — it will not return the value, only ok/not found.
        </p>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="secret name"
            value={canary}
            onChange={(e) => setCanary(e.target.value)}
          />
          <button className="btn btn-primary" onClick={test}>
            Test
          </button>
        </div>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------------

function TrustedDocsPanel() {
  const client = useQueryClient();
  const docs = useQuery<{ host: string; added_by: string; notes: string }[]>({
    queryKey: ["trusted-docs"],
    queryFn: () => api.get("/api/security/trusted-docs"),
  });

  async function onAdd(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    await api.post("/api/security/trusted-docs", {
      host: String(data.get("host")),
      notes: String(data.get("notes") || ""),
    });
    client.invalidateQueries({ queryKey: ["trusted-docs"] });
    e.currentTarget.reset();
  }

  async function onRemove(host: string) {
    await api.del(`/api/security/trusted-docs/${encodeURIComponent(host)}`);
    client.invalidateQueries({ queryKey: ["trusted-docs"] });
  }

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <h3 className="font-semibold mb-2">Trusted documentation sources</h3>
        <p className="text-xs text-spark-muted mb-3">
          Hosts the skill discovery pipeline is allowed to fetch documentation from. Distinct
          from an agent's regular network allowlist.
        </p>
        <table className="w-full text-sm">
          <thead className="text-spark-muted text-xs uppercase">
            <tr>
              <th className="text-left">host</th>
              <th className="text-left">added by</th>
              <th className="text-left">notes</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(docs.data ?? []).map((d) => (
              <tr key={d.host} className="border-t border-spark-border">
                <td className="py-1 font-mono">{d.host}</td>
                <td>{d.added_by}</td>
                <td className="text-spark-muted">{d.notes}</td>
                <td>
                  {d.added_by !== "default" && (
                    <button className="btn btn-danger" onClick={() => onRemove(d.host)}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form onSubmit={onAdd} className="panel p-4 flex gap-2">
        <input className="input flex-1" name="host" placeholder="docs.example.com" required />
        <input className="input flex-1" name="notes" placeholder="notes" />
        <button className="btn btn-primary" type="submit">
          Add
        </button>
      </form>
    </div>
  );
}


// -----------------------------------------------------------------------------
// Data Classes panel — redirect to /filtering (kept one release for muscle-memory)
// -----------------------------------------------------------------------------

function DataClassesPanel() {
  // Configuration of data-class levels, scopes, mask styles, and
  // per-detector toggles moved to the dedicated Filtering page under
  // SECURE → Filtering. This tab remains for one release as a redirect
  // so links / muscle-memory don't 404 on operators upgrading from
  // 0.x; remove in a follow-up once everyone has the new sidebar.
  return (
    <div className="panel p-6 max-w-2xl">
      <h3 className="font-semibold text-base mb-2">
        Moved → SECURE → Filtering
      </h3>
      <p className="text-sm text-spark-muted mb-4">
        Data-class levels, scopes, redaction mask styles, per-detector
        toggles, and the dry-run sandbox now live on the dedicated{" "}
        <a href="/filtering" className="text-spark-accent hover:underline">
          Filtering
        </a>{" "}
        page. Per-agent overrides and time-bound grants stay on this
        Security Center for now.
      </p>
      <a href="/filtering" className="btn btn-primary text-sm inline-flex">
        Open Filtering
      </a>
    </div>
  );
}

