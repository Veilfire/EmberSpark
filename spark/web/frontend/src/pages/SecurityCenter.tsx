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
// Data Classes panel
// -----------------------------------------------------------------------------

const ALL_SCOPES = [
  "user_input",
  "tool_output",
  "model_output",
  "memory_write",
  "shell_args",
] as const;

const LEVELS = ["allow", "warn", "redact", "shadow_block", "block"] as const;

type DataClassDef = {
  data_class: string;
  default_level: string;
  default_scopes: string[];
  description: string;
};

type PolicyView = {
  id: number;
  scope_kind: "global" | "agent";
  agent_name: string | null;
  data_class: string;
  level: string;
  scopes: string[];
  reason: string;
  updated_at: string;
  updated_by: string | null;
};

type PolicyResp = {
  global: PolicyView[];
  agents: Record<string, PolicyView[]>;
};

type GrantView = {
  id: number;
  agent_name: string;
  data_class: string;
  scopes: string[];
  level_override: string;
  reason: string;
  granted_by: string;
  granted_at: string;
  expires_at: string | null;
  active: boolean;
};

type DetectionRoll = {
  window_hours: number;
  total: number;
  by_class: Record<string, number>;
};

function DataClassesPanel() {
  const classes = useQuery<DataClassDef[]>({
    queryKey: ["data-classes"],
    queryFn: () => api.get<DataClassDef[]>("/api/security/data-classes"),
  });
  const policy = useQuery<PolicyResp>({
    queryKey: ["data-policy"],
    queryFn: () => api.get<PolicyResp>("/api/security/data-policy"),
  });
  const grants = useQuery<GrantView[]>({
    queryKey: ["data-grants"],
    queryFn: () => api.get<GrantView[]>("/api/security/data-grants"),
  });
  const detections = useQuery<DetectionRoll>({
    queryKey: ["data-detections"],
    queryFn: () =>
      api.get<DetectionRoll>("/api/security/data-detections?hours=24"),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <div className="panel p-4">
        <h3 className="font-semibold mb-2">Data Classification Guardrails</h3>
        <p className="text-xs text-spark-muted">
          Name-based detectors for PII, financial, credentials, and
          dangerous-CLI patterns. Resolution order:{" "}
          <code className="font-mono">grant</code> →{" "}
          <code className="font-mono">agent override</code> →{" "}
          <code className="font-mono">global</code> →{" "}
          <code className="font-mono">built-in default</code>. Levels:{" "}
          <code className="font-mono">allow</code>,{" "}
          <code className="font-mono">warn</code>,{" "}
          <code className="font-mono">redact</code>,{" "}
          <code className="font-mono">block</code>.
        </p>
      </div>

      <GlobalPolicyMatrix classes={classes.data ?? []} policy={policy.data} />

      <AgentPolicyOverrides classes={classes.data ?? []} policy={policy.data} />

      <DataGrantsSection
        classes={classes.data ?? []}
        grants={grants.data ?? []}
      />

      <DetectionRollup rollup={detections.data} />
    </div>
  );
}

function levelChip(level: string): string {
  switch (level) {
    case "block":
      return "chip bg-red-500/15 text-red-400 border border-red-500/30";
    case "shadow_block":
      return "chip bg-purple-500/15 text-purple-300 border border-purple-500/30";
    case "redact":
      return "chip bg-amber-500/15 text-amber-400 border border-amber-500/30";
    case "warn":
      return "chip bg-yellow-500/10 text-yellow-300 border border-yellow-500/30";
    default:
      return "chip";
  }
}

function levelLabel(level: string): string {
  return level === "shadow_block" ? "shadow" : level;
}

function GlobalPolicyMatrix({
  classes,
  policy,
}: {
  classes: DataClassDef[];
  policy: PolicyResp | undefined;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<DataClassDef | null>(null);

  const byClass: Record<string, PolicyView> = {};
  for (const row of policy?.global ?? []) byClass[row.data_class] = row;

  return (
    <div className="panel p-4">
      <h3 className="font-semibold mb-3">Global policy</h3>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-spark-muted">
          <tr>
            <th className="py-1">Class</th>
            <th>Level</th>
            <th>Scopes</th>
            <th>Source</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {classes.map((c) => {
            const override = byClass[c.data_class];
            const level = override?.level ?? c.default_level;
            const scopes = override?.scopes ?? c.default_scopes;
            return (
              <tr key={c.data_class} className="border-t border-spark-border">
                <td className="py-2">
                  <div className="font-mono">{c.data_class}</div>
                  <div className="text-xs text-spark-muted">{c.description}</div>
                </td>
                <td>
                  <span className={levelChip(level)}>{levelLabel(level)}</span>
                </td>
                <td>
                  <div className="flex flex-wrap gap-1">
                    {scopes.map((s) => (
                      <span key={s} className="chip text-xs font-mono">
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="text-xs text-spark-muted">
                  {override ? "override" : "default"}
                </td>
                <td className="text-right">
                  <button
                    className="btn text-xs"
                    onClick={() => setEditing(c)}
                  >
                    Edit
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {editing && (
        <PolicyEditor
          cls={editing}
          current={byClass[editing.data_class]}
          onClose={() => setEditing(null)}
          onSave={async (level, scopes, reason) => {
            if (level === "block") {
              const ok = await confirmDialog({
                title: `Block ${editing.data_class} globally?`,
                description:
                  "A block level aborts any operation that contains the matching content. This applies to every agent that doesn't have an override. Audited at elevated severity.",
                tone: "warning",
                confirmLabel: "Save as block",
              });
              if (!ok) return;
            }
            await api.put(
              `/api/security/data-policy/global/${encodeURIComponent(editing.data_class)}`,
              { level, scopes, reason },
            );
            toast.success("Global policy updated");
            qc.invalidateQueries({ queryKey: ["data-policy"] });
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function AgentPolicyOverrides({
  classes,
  policy,
}: {
  classes: DataClassDef[];
  policy: PolicyResp | undefined;
}) {
  const qc = useQueryClient();
  const [agent, setAgent] = useState("");
  const [editing, setEditing] = useState<DataClassDef | null>(null);
  const agents = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: () => api.get<AgentSummary[]>("/api/scheduler/agents"),
  });

  const overrides: PolicyView[] = agent ? policy?.agents?.[agent] ?? [] : [];
  const byClass: Record<string, PolicyView> = {};
  for (const r of overrides) byClass[r.data_class] = r;

  return (
    <div className="panel p-4">
      <h3 className="font-semibold mb-3">Per-agent overrides</h3>
      <div className="flex items-center gap-2 mb-3">
        <span className="label">Agent:</span>
        <select
          className="input"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
        >
          <option value="">(select)</option>
          {(agents.data ?? []).map((a) => (
            <option key={a.name} value={a.name}>
              {a.name}
            </option>
          ))}
        </select>
      </div>

      {!agent && (
        <p className="text-xs text-spark-muted">
          Pick an agent to view or set per-agent overrides. Without an
          override, the agent inherits the global policy above.
        </p>
      )}

      {agent && (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-spark-muted">
            <tr>
              <th className="py-1">Class</th>
              <th>Effective level</th>
              <th>Source</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {classes.map((c) => {
              const override = byClass[c.data_class];
              const level = override?.level ?? c.default_level;
              return (
                <tr key={c.data_class} className="border-t border-spark-border">
                  <td className="py-2 font-mono">{c.data_class}</td>
                  <td>
                    <span className={levelChip(level)}>{levelLabel(level)}</span>
                  </td>
                  <td className="text-xs text-spark-muted">
                    {override ? "agent override" : "inherits global/default"}
                  </td>
                  <td className="text-right space-x-1">
                    <button
                      className="btn text-xs"
                      onClick={() => setEditing(c)}
                    >
                      Set override
                    </button>
                    {override && (
                      <button
                        className="btn btn-danger text-xs"
                        onClick={async () => {
                          const ok = await confirmDialog({
                            title: `Revert ${agent} → ${c.data_class}?`,
                            description:
                              "Removes the agent-specific override so the class falls back to the global policy (or its built-in default).",
                            tone: "default",
                            confirmLabel: "Revert",
                          });
                          if (!ok) return;
                          await api.del(
                            `/api/security/data-policy/agent/${encodeURIComponent(agent)}/${encodeURIComponent(c.data_class)}`,
                          );
                          toast.success("Override removed");
                          qc.invalidateQueries({ queryKey: ["data-policy"] });
                        }}
                      >
                        Revert
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {editing && agent && (
        <PolicyEditor
          cls={editing}
          current={byClass[editing.data_class]}
          onClose={() => setEditing(null)}
          onSave={async (level, scopes, reason) => {
            await api.put(
              `/api/security/data-policy/agent/${encodeURIComponent(agent)}/${encodeURIComponent(editing.data_class)}`,
              { level, scopes, reason },
            );
            toast.success("Agent override saved");
            qc.invalidateQueries({ queryKey: ["data-policy"] });
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function PolicyEditor({
  cls,
  current,
  onClose,
  onSave,
}: {
  cls: DataClassDef;
  current: PolicyView | undefined;
  onClose: () => void;
  onSave: (level: string, scopes: string[], reason: string) => Promise<void>;
}) {
  const [level, setLevel] = useState<string>(current?.level ?? cls.default_level);
  const [scopes, setScopes] = useState<string[]>(
    current?.scopes ?? cls.default_scopes,
  );
  const [reason, setReason] = useState<string>(current?.reason ?? "");
  const [saving, setSaving] = useState(false);

  const toggleScope = (s: string) => {
    setScopes((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
    );
  };

  return (
    <div className="mt-4 panel p-4 border border-spark-accent/40 space-y-3">
      <div>
        <div className="font-mono">{cls.data_class}</div>
        <div className="text-xs text-spark-muted">{cls.description}</div>
      </div>
      <div>
        <label className="label">Level</label>
        <div className="flex gap-2 mt-1 text-sm flex-wrap">
          {LEVELS.map((l) => (
            <label key={l} className="flex items-center gap-1 cursor-pointer">
              <input
                type="radio"
                name="level"
                checked={level === l}
                onChange={() => setLevel(l)}
              />
              <span className={levelChip(l)}>{levelLabel(l)}</span>
            </label>
          ))}
        </div>
        {level === "shadow_block" && (
          <p className="text-xs text-spark-muted mt-2">
            <strong>Shadow mode:</strong> audit each hit as if the class were
            blocked, but let the operation through. Use to measure
            false-positive rate on real traffic before committing to{" "}
            <code className="font-mono">block</code>. Events land in the audit
            log under <code className="font-mono">security.data_class.shadow_block</code>.
          </p>
        )}
      </div>
      <div>
        <label className="label">Scopes</label>
        <div className="flex flex-wrap gap-2 mt-1 text-sm">
          {ALL_SCOPES.map((s) => (
            <label key={s} className="flex items-center gap-1 cursor-pointer">
              <input
                type="checkbox"
                checked={scopes.includes(s)}
                onChange={() => toggleScope(s)}
              />
              <span className="font-mono text-xs">{s}</span>
            </label>
          ))}
        </div>
      </div>
      <label className="block">
        <span className="label">Reason (audited)</span>
        <input
          className="input w-full"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="why this level?"
        />
      </label>
      <div className="flex justify-end gap-2">
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn btn-primary"
          disabled={saving || scopes.length === 0}
          onClick={async () => {
            setSaving(true);
            try {
              await onSave(level, scopes, reason);
            } finally {
              setSaving(false);
            }
          }}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function DataGrantsSection({
  classes,
  grants,
}: {
  classes: DataClassDef[];
  grants: GrantView[];
}) {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);

  return (
    <div className="panel p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">Unlimited grants</h3>
        <button
          className="btn btn-primary text-xs"
          onClick={() => setCreating(true)}
        >
          + New grant
        </button>
      </div>
      <p className="text-xs text-spark-muted mb-3">
        Explicit carve-outs that bypass the policy hierarchy. Use when an
        agent's job is to handle a given class — e.g. a CC-processing
        agent gets a <code className="font-mono">financial.card</code>{" "}
        grant with <code className="font-mono">level_override=allow</code>.
        Creation is audited at critical severity.
      </p>

      {grants.length === 0 ? (
        <p className="text-sm text-spark-muted">No active grants.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-spark-muted">
            <tr>
              <th className="py-1">Agent</th>
              <th>Class</th>
              <th>Scopes</th>
              <th>Level</th>
              <th>Reason</th>
              <th>Expires</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {grants.map((g) => (
              <tr key={g.id} className="border-t border-spark-border">
                <td className="py-1 font-mono">{g.agent_name}</td>
                <td className="font-mono">{g.data_class}</td>
                <td>
                  <div className="flex flex-wrap gap-1">
                    {g.scopes.map((s) => (
                      <span key={s} className="chip text-xs font-mono">
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
                <td>
                  <span className={levelChip(g.level_override)}>
                    {g.level_override}
                  </span>
                </td>
                <td className="text-xs text-spark-muted">{g.reason}</td>
                <td className="text-xs">{formatUntil(g.expires_at)}</td>
                <td className="text-right">
                  <button
                    className="btn btn-danger text-xs"
                    onClick={async () => {
                      const ok = await confirmDialog({
                        title: `Revoke grant for ${g.agent_name}?`,
                        description: `Immediately revokes the ${g.data_class} grant. Subsequent operations fall back to the regular policy hierarchy.`,
                        tone: "danger",
                        confirmLabel: "Revoke",
                      });
                      if (!ok) return;
                      await api.del(`/api/security/data-grants/${g.id}`);
                      toast.success("Grant revoked");
                      qc.invalidateQueries({ queryKey: ["data-grants"] });
                    }}
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {creating && (
        <GrantCreateForm
          classes={classes}
          onClose={() => setCreating(false)}
          onCreated={() => {
            qc.invalidateQueries({ queryKey: ["data-grants"] });
            setCreating(false);
          }}
        />
      )}
    </div>
  );
}

function GrantCreateForm({
  classes,
  onClose,
  onCreated,
}: {
  classes: DataClassDef[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const agents = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: () => api.get<AgentSummary[]>("/api/scheduler/agents"),
  });
  const [agent, setAgent] = useState("");
  const [dataClass, setDataClass] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [ttlHours, setTtlHours] = useState<number | null>(168);
  const [confirmName, setConfirmName] = useState("");
  const [saving, setSaving] = useState(false);

  const toggleScope = (s: string) =>
    setScopes((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
    );

  const canSave =
    agent &&
    dataClass &&
    scopes.length > 0 &&
    reason.trim().length >= 3 &&
    confirmName === agent;

  async function submit() {
    if (!canSave) return;
    if (ttlHours === null) {
      const ok = await confirmDialog({
        title: "Permanent grant?",
        description:
          "Permanent grants never expire. Prefer a TTL unless you have a concrete reason to make this carve-out forever. Audited at critical severity.",
        tone: "danger",
        confirmLabel: "Yes, permanent",
      });
      if (!ok) return;
    }
    setSaving(true);
    try {
      await api.post("/api/security/data-grants", {
        agent_name: agent,
        data_class: dataClass,
        scopes,
        level_override: "allow",
        reason,
        ttl_hours: ttlHours,
        confirm_agent_name: confirmName,
      });
      toast.success("Grant created");
      onCreated();
    } catch (err) {
      toast.error(`Grant failed: ${err}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-4 panel p-4 border border-spark-accent/40 space-y-3">
      <h4 className="font-semibold">New data-class grant</h4>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="label">Agent</span>
          <select
            className="input w-full"
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
          >
            <option value="">(select)</option>
            {(agents.data ?? []).map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Data class</span>
          <select
            className="input w-full"
            value={dataClass}
            onChange={(e) => setDataClass(e.target.value)}
          >
            <option value="">(select)</option>
            {classes.map((c) => (
              <option key={c.data_class} value={c.data_class}>
                {c.data_class}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div>
        <span className="label">Scopes</span>
        <div className="flex flex-wrap gap-2 mt-1 text-sm">
          {ALL_SCOPES.map((s) => (
            <label key={s} className="flex items-center gap-1 cursor-pointer">
              <input
                type="checkbox"
                checked={scopes.includes(s)}
                onChange={() => toggleScope(s)}
              />
              <span className="font-mono text-xs">{s}</span>
            </label>
          ))}
        </div>
      </div>
      <label className="block">
        <span className="label">Reason (audited)</span>
        <input
          className="input w-full"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="why this agent needs unrestricted access"
        />
      </label>
      <div className="grid grid-cols-2 gap-3 items-end">
        <label className="block">
          <span className="label">TTL (hours)</span>
          <input
            className="input w-full"
            type="number"
            min={1}
            max={720}
            value={ttlHours ?? ""}
            disabled={ttlHours === null}
            onChange={(e) =>
              setTtlHours(e.target.value ? Number(e.target.value) : 168)
            }
          />
        </label>
        <label className="flex items-center gap-2 text-sm cursor-pointer pb-2">
          <input
            type="checkbox"
            checked={ttlHours === null}
            onChange={(e) => setTtlHours(e.target.checked ? null : 168)}
          />
          Permanent (no TTL)
        </label>
      </div>
      <label className="block">
        <span className="label">
          Type <code className="font-mono">{agent || "<agent>"}</code> to
          confirm
        </span>
        <input
          className="input w-full font-mono"
          value={confirmName}
          onChange={(e) => setConfirmName(e.target.value)}
          placeholder={agent}
        />
      </label>
      <div className="flex justify-end gap-2">
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn btn-danger"
          disabled={!canSave || saving}
          onClick={submit}
        >
          {saving ? "Creating…" : "Create grant"}
        </button>
      </div>
    </div>
  );
}

function DetectionRollup({ rollup }: { rollup: DetectionRoll | undefined }) {
  const buckets = Object.entries(rollup?.by_class ?? {}).sort((a, b) => b[1] - a[1]);
  const max = buckets.length > 0 ? Math.max(...buckets.map(([, n]) => n)) : 0;
  return (
    <div className="panel p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">Recent detections (last 24h)</h3>
        <span className="text-xs text-spark-muted">
          total events: {rollup?.total ?? 0}
        </span>
      </div>
      {buckets.length === 0 ? (
        <p className="text-sm text-spark-muted">
          No guardrail events in the window.
        </p>
      ) : (
        <table className="w-full text-sm">
          <tbody>
            {buckets.map(([cls, n]) => (
              <tr key={cls} className="border-t border-spark-border">
                <td className="py-1 font-mono w-56">{cls}</td>
                <td>
                  <div
                    className="h-2 rounded bg-spark-accent/40"
                    style={{ width: `${max ? (n / max) * 100 : 0}%` }}
                  />
                </td>
                <td className="text-right tabular-nums w-16">{n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
