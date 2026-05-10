import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
import { formatRelative, formatUntil } from "../lib/utils";
export default function SecurityCenter() {
    const [section, setSection] = useState("global");
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Security Center" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Global posture, per-agent policies, trusted sources, and audit-backed changes." })] }), _jsx("div", { className: "flex flex-wrap gap-2 text-sm", children: [
                    ["global", "Global Posture"],
                    ["network", "Network"],
                    ["filesystem", "Filesystem"],
                    ["sandbox", "Sandbox"],
                    ["plugins", "Plugins"],
                    ["privacy", "Privacy"],
                    ["secrets", "Secrets"],
                    ["trusted-docs", "Trusted Docs"],
                ].map(([key, label]) => (_jsx("button", { className: `btn ${section === key ? "btn-primary" : ""}`, onClick: () => setSection(key), children: label }, key))) }), section === "global" && _jsx(GlobalPanel, {}), section === "network" && _jsx(NetworkPanel, {}), section === "filesystem" && _jsx(FilesystemPanel, {}), section === "sandbox" && _jsx(SandboxPanel, {}), section === "plugins" && _jsx(PluginsPanel, {}), section === "privacy" && _jsx(PrivacyPanel, {}), section === "secrets" && _jsx(SecretsPanel, {}), section === "trusted-docs" && _jsx(TrustedDocsPanel, {})] }));
}
// -----------------------------------------------------------------------------
function GlobalPanel() {
    const client = useQueryClient();
    const posture = useQuery({
        queryKey: ["posture"],
        queryFn: () => api.get("/api/security/global"),
    });
    const [reason, setReason] = useState("");
    const [confirmText, setConfirmText] = useState("");
    const update = useMutation({
        mutationFn: (body) => api.post("/api/security/global", body),
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
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4 space-y-3", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsx(Kv, { label: "Frozen", value: p?.frozen ? "YES" : "no", highlight: p?.frozen }), _jsx(Kv, { label: "Compliance", value: p?.compliance_mode ?? "—" }), _jsx(Kv, { label: "Default privacy", value: p?.default_privacy_mode ?? "—" }), _jsx(Kv, { label: "Internal IPs", value: p?.allow_internal_ips ? "ALLOWED" : "blocked", highlight: p?.allow_internal_ips }), _jsx(Kv, { label: "Raw logging", value: p?.allow_raw_logging ? "ON" : "off", highlight: p?.allow_raw_logging })] }), _jsxs("div", { className: "text-xs text-spark-muted", children: ["Last updated ", formatRelative(p?.updated_at), " by ", p?.updated_by ?? "unknown"] })] }), _jsxs("div", { className: "panel p-4 space-y-3", children: [_jsx("h3", { className: "font-semibold text-spark-danger", children: "Emergency freeze" }), _jsx("p", { className: "text-sm text-spark-muted", children: "Halts the scheduler and refuses new runs until unfrozen. Persists across restart." }), _jsxs("div", { className: "flex gap-2", children: [_jsx("input", { className: "input flex-1", placeholder: "reason for freeze", value: reason, onChange: (e) => setReason(e.target.value) }), _jsx("button", { className: "btn btn-danger", onClick: freeze, disabled: p?.frozen, children: "Freeze" }), _jsx("button", { className: "btn", onClick: unfreeze, disabled: !p?.frozen, children: "Unfreeze" })] })] }), _jsxs("div", { className: "panel p-4 space-y-3", children: [_jsx("h3", { className: "font-semibold", children: "Elevated toggles" }), _jsxs("p", { className: "text-xs text-spark-muted", children: ["Type ", _jsx("span", { className: "kbd", children: "confirm" }), " in the box below, then press a toggle."] }), _jsx("input", { className: "input", placeholder: "type confirm", value: confirmText, onChange: (e) => setConfirmText(e.target.value) }), _jsxs("div", { className: "flex gap-2 flex-wrap", children: [_jsx("button", { className: "btn btn-danger", onClick: () => update.mutate({
                                    allow_internal_ips: !p?.allow_internal_ips,
                                    confirm_agent_name: confirmText,
                                    reason: "UI toggle",
                                }), children: "Toggle internal-IP access" }), _jsx("button", { className: "btn btn-danger", onClick: () => update.mutate({
                                    allow_raw_logging: !p?.allow_raw_logging,
                                    confirm_agent_name: confirmText,
                                    reason: "UI toggle",
                                }), children: "Toggle raw logging" }), _jsx("button", { className: "btn", onClick: () => update.mutate({
                                    compliance_mode: p?.compliance_mode === "audit" ? "standard" : "audit",
                                }), children: "Toggle audit mode" })] })] })] }));
}
function Kv({ label, value, highlight, }) {
    return (_jsxs("div", { children: [_jsx("div", { className: "label", children: label }), _jsx("div", { className: `font-semibold ${highlight ? "text-spark-danger" : ""}`, children: value })] }));
}
// -----------------------------------------------------------------------------
function AgentPicker({ value, onChange, }) {
    const agents = useQuery({
        queryKey: ["agents"],
        queryFn: () => api.get("/api/scheduler/agents"),
    });
    return (_jsxs("select", { className: "input", value: value, onChange: (e) => onChange(e.target.value), children: [_jsx("option", { value: "", children: "pick agent\u2026" }), (agents.data ?? []).map((a) => (_jsx("option", { value: a.name, children: a.name }, a.name)))] }));
}
// -----------------------------------------------------------------------------
function NetworkPanel() {
    const [agent, setAgent] = useState("");
    const grants = useQuery({
        queryKey: ["grants", agent],
        queryFn: () => agent ? api.get(`/api/security/internal-grants/${agent}`) : Promise.resolve([]),
        enabled: !!agent,
    });
    async function addGrant(e) {
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
    async function patchNet(e) {
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
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4 flex items-center gap-2", children: [_jsx("span", { className: "label", children: "Agent:" }), _jsx(AgentPicker, { value: agent, onChange: setAgent })] }), agent && (_jsxs(_Fragment, { children: [_jsxs("form", { onSubmit: patchNet, className: "panel p-4 space-y-3", children: [_jsx("h3", { className: "font-semibold", children: "Outbound network policy" }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Allowed hosts (comma separated)" }), _jsx("input", { className: "input w-full", name: "allow_hosts", placeholder: "api.github.com, example.com" })] }), _jsxs("label", { className: "flex items-center gap-2", children: [_jsx("input", { type: "checkbox", name: "allow_http" }), _jsx("span", { className: "text-sm", children: "Allow plain http:// (not recommended)" })] }), _jsxs("div", { className: "grid grid-cols-3 gap-2", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Max response bytes" }), _jsx("input", { className: "input w-full", type: "number", name: "max_response_bytes", defaultValue: 5_000_000 })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Connect timeout (s)" }), _jsx("input", { className: "input w-full", type: "number", step: "0.1", name: "connect_timeout_seconds", defaultValue: 5 })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Read timeout (s)" }), _jsx("input", { className: "input w-full", type: "number", step: "0.1", name: "read_timeout_seconds", defaultValue: 15 })] })] }), _jsx("button", { className: "btn btn-primary", type: "submit", children: "Queue patch" })] }), _jsxs("div", { className: "panel p-4 space-y-3", children: [_jsx("h3", { className: "font-semibold text-spark-danger", children: "Internal IP grants" }), _jsx("p", { className: "text-xs text-spark-muted", children: "Allow an agent to reach an internal CIDR for a bounded window. Hard-blocked otherwise." }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "cidr" }), _jsx("th", { className: "text-left", children: "reason" }), _jsx("th", { className: "text-left", children: "expires" }), _jsx("th", { className: "text-left", children: "granted by" }), _jsx("th", {})] }) }), _jsx("tbody", { children: (grants.data ?? []).map((g) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono", children: g.cidr }), _jsx("td", { children: g.reason }), _jsx("td", { children: formatUntil(g.expires_at) }), _jsx("td", { children: g.granted_by }), _jsx("td", { children: _jsx("button", { className: "btn btn-danger", onClick: async () => {
                                                            await api.del(`/api/security/internal-grants/${g.id}`);
                                                            grants.refetch();
                                                        }, children: "Revoke" }) })] }, g.id))) })] }), _jsxs("form", { onSubmit: addGrant, className: "grid grid-cols-2 md:grid-cols-5 gap-2", children: [_jsx("input", { className: "input", name: "cidr", placeholder: "10.0.5.0/24", required: true }), _jsx("input", { className: "input md:col-span-2", name: "reason", placeholder: "reason", required: true }), _jsx("input", { className: "input", name: "ttl_hours", type: "number", min: 1, max: 24, defaultValue: 4 }), _jsx("input", { className: "input", name: "confirm", placeholder: `type ${agent}`, required: true }), _jsx("button", { className: "btn btn-danger col-span-2 md:col-span-5", type: "submit", children: "Grant (elevated)" })] })] })] }))] }));
}
// -----------------------------------------------------------------------------
function FilesystemPanel() {
    const [agent, setAgent] = useState("");
    async function patch(e) {
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
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4 flex items-center gap-2", children: [_jsx("span", { className: "label", children: "Agent:" }), _jsx(AgentPicker, { value: agent, onChange: setAgent })] }), agent && (_jsxs("form", { onSubmit: patch, className: "panel p-4 space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Allow paths (one per line)" }), _jsx("textarea", { className: "input w-full h-24 font-mono text-xs", name: "allow_paths" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Deny paths" }), _jsx("textarea", { className: "input w-full h-16 font-mono text-xs", name: "deny_paths" })] }), _jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Max read bytes" }), _jsx("input", { className: "input w-full", type: "number", name: "max_read_bytes", defaultValue: 5_000_000 })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Max files per call" }), _jsx("input", { className: "input w-full", type: "number", name: "max_files_per_call", defaultValue: 256 })] })] }), _jsx("button", { className: "btn btn-primary", type: "submit", children: "Queue patch" })] }))] }));
}
// -----------------------------------------------------------------------------
function SandboxPanel() {
    const [agent, setAgent] = useState("");
    async function patch(e) {
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
        const resp = await api.post("/api/security/sandbox/self-test");
        if (resp.available) {
            toast.success(`Sandbox OK — backend ${resp.backend}`, {
                description: "Self-test completed successfully.",
            });
        }
        else {
            toast.error(`Sandbox unavailable — backend ${resp.backend}`, {
                description: "The configured sandbox failed its self-test. Tool calls will be refused until this is resolved.",
            });
        }
    }
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4 flex items-center gap-2 justify-between", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "label", children: "Agent:" }), _jsx(AgentPicker, { value: agent, onChange: setAgent })] }), _jsx("button", { className: "btn", onClick: selfTest, children: "Run self-test" })] }), agent && (_jsxs("form", { onSubmit: patch, className: "panel p-4 space-y-3", children: [_jsx("div", { className: "text-xs text-spark-muted", children: "Mandatory: sandbox cannot be disabled. Backend selection and rlimits only." }), _jsxs("div", { className: "grid grid-cols-3 gap-2", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Backend" }), _jsxs("select", { className: "input w-full", name: "backend", defaultValue: "auto", children: [_jsx("option", { value: "auto", children: "auto" }), _jsx("option", { value: "bubblewrap", children: "bubblewrap" }), _jsx("option", { value: "nsjail", children: "nsjail (strict)" }), _jsx("option", { value: "seatbelt", children: "seatbelt (macOS)" })] })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "CPU seconds" }), _jsx("input", { className: "input w-full", type: "number", name: "cpu_seconds", defaultValue: 30 })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Memory MB" }), _jsx("input", { className: "input w-full", type: "number", name: "memory_mb", defaultValue: 512 })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Max open files" }), _jsx("input", { className: "input w-full", type: "number", name: "max_open_files", defaultValue: 128 })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Max processes" }), _jsx("input", { className: "input w-full", type: "number", name: "max_processes", defaultValue: 8 })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Timeout (s)" }), _jsx("input", { className: "input w-full", type: "number", name: "timeout_seconds", defaultValue: 60 })] })] }), _jsx("button", { className: "btn btn-primary", type: "submit", children: "Queue patch" })] }))] }));
}
// -----------------------------------------------------------------------------
function PluginsPanel() {
    const [agent, setAgent] = useState("");
    async function patch(e) {
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
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4 flex items-center gap-2", children: [_jsx("span", { className: "label", children: "Agent:" }), _jsx(AgentPicker, { value: agent, onChange: setAgent })] }), agent && (_jsxs("form", { onSubmit: patch, className: "panel p-4 space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Allowed plugins (comma separated)" }), _jsx("input", { className: "input w-full", name: "allow", placeholder: "filesystem, http_client, markdown_writer" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Permission grants" }), _jsx("input", { className: "input w-full", name: "grants", placeholder: "fs.read, fs.write, net.http, secrets.read" })] }), _jsx("p", { className: "text-xs text-spark-muted", children: "Missing grants \u2192 deny. Plugin declared permissions must be a subset of this list." }), _jsx("button", { className: "btn btn-primary", type: "submit", children: "Queue patch" })] }))] }));
}
// -----------------------------------------------------------------------------
function PrivacyPanel() {
    const [agent, setAgent] = useState("");
    async function patch(e) {
        e.preventDefault();
        const data = new FormData(e.currentTarget);
        const raw_prompts = data.get("raw_prompts") === "on";
        const raw_outputs = data.get("raw_outputs") === "on";
        if (raw_prompts || raw_outputs) {
            const ok = await confirmDialog({
                title: "Enable raw logging?",
                description: "Raw logging bypasses the redaction pipeline. Prompts and model outputs will be written to logs unfiltered. This is a CRITICAL-severity change and will be audited.",
                tone: "danger",
                confirmLabel: "Enable raw logging",
            });
            if (!ok)
                return;
        }
        await api.post(`/api/security/agents/${agent}/privacy`, {
            privacy_mode: data.get("privacy_mode"),
            raw_prompts,
            raw_model_outputs: raw_outputs,
        });
        toast.success("Patch queued — audited");
    }
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4 flex items-center gap-2", children: [_jsx("span", { className: "label", children: "Agent:" }), _jsx(AgentPicker, { value: agent, onChange: setAgent })] }), agent && (_jsxs("form", { onSubmit: patch, className: "panel p-4 space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Privacy mode" }), _jsxs("select", { className: "input", name: "privacy_mode", defaultValue: "strict", children: [_jsx("option", { value: "strict", children: "strict" }), _jsx("option", { value: "balanced", children: "balanced" }), _jsx("option", { value: "regex_only", children: "regex_only" })] })] }), _jsxs("label", { className: "flex items-center gap-2 text-sm", children: [_jsx("input", { type: "checkbox", name: "raw_prompts" }), "Raw prompt logging (critical)"] }), _jsxs("label", { className: "flex items-center gap-2 text-sm", children: [_jsx("input", { type: "checkbox", name: "raw_outputs" }), "Raw model output logging (critical)"] }), _jsx("button", { className: "btn btn-primary", type: "submit", children: "Queue patch" })] }))] }));
}
// -----------------------------------------------------------------------------
function SecretsPanel() {
    const names = useQuery({
        queryKey: ["secrets-names"],
        queryFn: () => api.get("/api/security/secrets"),
    });
    const [canary, setCanary] = useState("");
    async function test() {
        const resp = await api.post("/api/security/secrets/canary", {
            name: canary,
        });
        if (resp.ok) {
            toast.success(`Secret "${canary}" is reachable`);
        }
        else {
            toast.error(`Secret "${canary}" NOT found in the vault`);
        }
    }
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Secret names (values never shown)" }), _jsxs("div", { className: "flex flex-wrap gap-1", children: [(names.data ?? []).map((n) => (_jsx("span", { className: "chip font-mono", children: n }, n))), names.data?.length === 0 && (_jsx("span", { className: "text-spark-muted text-sm", children: "no secrets declared in the age vault or env fallback" }))] })] }), _jsxs("div", { className: "panel p-4 space-y-2", children: [_jsx("h3", { className: "font-semibold", children: "Canary test" }), _jsx("p", { className: "text-xs text-spark-muted", children: "Ask the runtime to resolve a secret by name \u2014 it will not return the value, only ok/not found." }), _jsxs("div", { className: "flex gap-2", children: [_jsx("input", { className: "input flex-1", placeholder: "secret name", value: canary, onChange: (e) => setCanary(e.target.value) }), _jsx("button", { className: "btn btn-primary", onClick: test, children: "Test" })] })] })] }));
}
// -----------------------------------------------------------------------------
function TrustedDocsPanel() {
    const client = useQueryClient();
    const docs = useQuery({
        queryKey: ["trusted-docs"],
        queryFn: () => api.get("/api/security/trusted-docs"),
    });
    async function onAdd(e) {
        e.preventDefault();
        const data = new FormData(e.currentTarget);
        await api.post("/api/security/trusted-docs", {
            host: String(data.get("host")),
            notes: String(data.get("notes") || ""),
        });
        client.invalidateQueries({ queryKey: ["trusted-docs"] });
        e.currentTarget.reset();
    }
    async function onRemove(host) {
        await api.del(`/api/security/trusted-docs/${encodeURIComponent(host)}`);
        client.invalidateQueries({ queryKey: ["trusted-docs"] });
    }
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Trusted documentation sources" }), _jsx("p", { className: "text-xs text-spark-muted mb-3", children: "Hosts the skill discovery pipeline is allowed to fetch documentation from. Distinct from an agent's regular network allowlist." }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "host" }), _jsx("th", { className: "text-left", children: "added by" }), _jsx("th", { className: "text-left", children: "notes" }), _jsx("th", {})] }) }), _jsx("tbody", { children: (docs.data ?? []).map((d) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono", children: d.host }), _jsx("td", { children: d.added_by }), _jsx("td", { className: "text-spark-muted", children: d.notes }), _jsx("td", { children: d.added_by !== "default" && (_jsx("button", { className: "btn btn-danger", onClick: () => onRemove(d.host), children: "Remove" })) })] }, d.host))) })] })] }), _jsxs("form", { onSubmit: onAdd, className: "panel p-4 flex gap-2", children: [_jsx("input", { className: "input flex-1", name: "host", placeholder: "docs.example.com", required: true }), _jsx("input", { className: "input flex-1", name: "notes", placeholder: "notes" }), _jsx("button", { className: "btn btn-primary", type: "submit", children: "Add" })] })] }));
}
