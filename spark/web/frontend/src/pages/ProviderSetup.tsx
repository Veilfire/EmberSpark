import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Check, Key, Server, TestTube, X, Zap } from "lucide-react";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { ConfirmDialog } from "../components/ConfirmDialog";

type AgentSummary = { name: string; description: string };

const PROVIDERS = [
  {
    id: "anthropic",
    label: "Anthropic",
    secretName: "anthropic_key",
    placeholder: "sk-ant-...",
    hint: "Get your key at console.anthropic.com/settings/keys",
    models: [
      "claude-opus-4-6",
      "claude-sonnet-4-6",
      "claude-haiku-4-5-20251001",
    ],
  },
  {
    id: "openai",
    label: "OpenAI",
    secretName: "openai_key",
    placeholder: "sk-...",
    hint: "Get your key at platform.openai.com/api-keys",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini"],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    secretName: "openrouter_key",
    placeholder: "sk-or-...",
    hint: "Get your key at openrouter.ai/keys",
    models: [
      "anthropic/claude-sonnet-4",
      "anthropic/claude-haiku",
      "openai/gpt-4o",
      "google/gemini-2.5-pro",
      "meta-llama/llama-4-maverick",
    ],
  },
  {
    id: "ollama",
    label: "Ollama (local)",
    secretName: null,
    placeholder: "",
    hint: "No API key needed — runs locally via ollama serve",
    models: ["llama3.1", "llama3.2", "mistral", "codellama", "gemma2"],
  },
] as const;

export default function ProviderSetup() {
  const qc = useQueryClient();
  const [selectedProvider, setSelectedProvider] = useState<string>("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    detail: string;
  } | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false);

  const secrets = useQuery<string[]>({
    queryKey: ["secret-names"],
    queryFn: () => api.get<string[]>("/api/security/secrets"),
  });

  const agents = useQuery<AgentSummary[]>({
    queryKey: ["agents"],
    queryFn: () => api.get<AgentSummary[]>("/api/scheduler/agents"),
  });

  const provider = PROVIDERS.find((p) => p.id === selectedProvider)!;
  const isConfigured =
    provider.secretName === null ||
    (secrets.data ?? []).includes(provider.secretName);

  async function saveKey() {
    if (!provider.secretName || !apiKey.trim()) return;
    setSaving(true);
    try {
      await api.put("/api/security/secrets", {
        name: provider.secretName,
        value: apiKey.trim(),
      });
      toast.success(`Saved ${provider.secretName}`);
      setApiKey("");
      qc.invalidateQueries({ queryKey: ["secret-names"] });
    } catch (err) {
      toast.error(`Failed to save: ${err}`);
    } finally {
      setSaving(false);
    }
  }

  async function deleteKey() {
    if (!provider.secretName) return;
    try {
      await api.del(`/api/security/secrets/${encodeURIComponent(provider.secretName)}`);
      toast.success(`Deleted ${provider.secretName}`);
      qc.invalidateQueries({ queryKey: ["secret-names"] });
    } catch (err) {
      toast.error(`Failed to delete: ${err}`);
    } finally {
      setDeleteConfirm(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.post<{ ok: boolean; detail: string }>(
        `/api/providers/${provider.id}/test`,
      );
      setTestResult(res);
      if (res.ok) toast.success(res.detail);
      else toast.error(res.detail);
    } catch (err) {
      const detail = `${err}`;
      setTestResult({ ok: false, detail });
      toast.error(detail);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<Zap className="w-6 h-6" />}
        title="Provider Setup"
        subtitle="Configure the LLM provider and API key so agents can run. The key is stored encrypted in the age vault — never in plaintext."
      />

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">1. Choose a provider</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {PROVIDERS.map((p) => {
            const configured =
              p.secretName === null ||
              (secrets.data ?? []).includes(p.secretName);
            return (
              <button
                key={p.id}
                className={`border rounded p-3 text-left transition ${
                  selectedProvider === p.id
                    ? "border-spark-accent bg-spark-accent/5"
                    : "border-spark-border hover:border-spark-accent/50"
                }`}
                onClick={() => setSelectedProvider(p.id)}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-sm">{p.label}</span>
                  {configured && (
                    <Check className="w-4 h-4 text-spark-good" />
                  )}
                </div>
                <p className="text-xs text-spark-muted">
                  {p.secretName ?? "No key needed"}
                </p>
              </button>
            );
          })}
        </div>
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-3 flex items-center gap-2">
          <Key className="w-4 h-4" /> 2. API key
        </h3>
        {provider.secretName === null ? (
          <p className="text-spark-muted text-sm">
            Ollama runs locally — no API key required. Make sure{" "}
            <code className="font-mono">ollama serve</code> is running
            and reachable from the container.
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-spark-muted text-xs">{provider.hint}</p>
            {isConfigured ? (
              <div className="space-y-2">
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="flex items-center gap-2 text-sm text-spark-good">
                    <Check className="w-4 h-4" />
                    <span>
                      <code className="font-mono">{provider.secretName}</code>{" "}
                      is set in the vault
                    </span>
                  </div>
                  <button
                    className="btn flex items-center gap-1"
                    onClick={testConnection}
                    disabled={testing}
                  >
                    <TestTube className="w-3.5 h-3.5" />
                    {testing ? "Testing…" : "Test connection"}
                  </button>
                  <button
                    className="btn btn-danger"
                    onClick={() => setDeleteConfirm(true)}
                  >
                    Remove
                  </button>
                </div>
                {testResult && (
                  <div
                    className={`flex items-center gap-2 text-xs p-2 rounded-md border ${
                      testResult.ok
                        ? "border-spark-good/40 bg-spark-good/5 text-spark-good"
                        : "border-spark-danger/40 bg-spark-danger/5 text-spark-danger"
                    }`}
                  >
                    {testResult.ok ? (
                      <Check className="w-4 h-4" />
                    ) : (
                      <X className="w-4 h-4" />
                    )}
                    {testResult.detail}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex gap-2">
                <input
                  type="password"
                  className="input flex-1"
                  placeholder={provider.placeholder}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && saveKey()}
                />
                <button
                  className="btn btn-primary"
                  disabled={!apiKey.trim() || saving}
                  onClick={saveKey}
                >
                  {saving ? "Saving..." : "Save to vault"}
                </button>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-3 flex items-center gap-2">
          <Server className="w-4 h-4" /> 3. Available models
        </h3>
        <p className="text-spark-muted text-xs mb-2">
          The model is set in each agent's YAML (
          <code className="font-mono">spec.runtime.provider.model</code>
          ). These are common options for {provider.label}:
        </p>
        <div className="flex flex-wrap gap-2">
          {provider.models.map((m) => (
            <span key={m} className="chip font-mono text-xs">
              {m}
            </span>
          ))}
        </div>
      </section>

      <section className="panel p-4">
        <h3 className="font-semibold mb-3">Installed agents</h3>
        {agents.isLoading && (
          <p className="text-spark-muted text-sm">loading...</p>
        )}
        {agents.data && agents.data.length === 0 && (
          <p className="text-spark-muted text-sm">
            No agents installed yet. Go to{" "}
            <a className="text-spark-accent underline" href="/templates">
              Templates
            </a>{" "}
            to install one.
          </p>
        )}
        {agents.data && agents.data.length > 0 && (
          <table className="w-full text-sm">
            <thead className="text-spark-muted text-xs uppercase">
              <tr>
                <th className="text-left">Agent</th>
                <th className="text-left">Description</th>
              </tr>
            </thead>
            <tbody>
              {agents.data.map((a) => (
                <tr key={a.name} className="border-t border-spark-border">
                  <td className="py-2 font-mono">{a.name}</td>
                  <td className="text-spark-muted">{a.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ConfirmDialog
        open={deleteConfirm}
        title={`Remove ${provider.secretName}?`}
        description="Agents using this provider will stop working until you re-add a key."
        tone="danger"
        confirmLabel="Remove"
        onCancel={() => setDeleteConfirm(false)}
        onConfirm={deleteKey}
      />
    </div>
  );
}
