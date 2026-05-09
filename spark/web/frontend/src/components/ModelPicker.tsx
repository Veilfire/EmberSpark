import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { CheckCircle2, AlertCircle } from "lucide-react";
import { api } from "../lib/api";

type ModelEntry = { id: string; name: string };

const PROVIDER_SECRET: Record<string, string | null> = {
  anthropic: "anthropic_key",
  openai: "openai_key",
  openrouter: "openrouter_key",
  ollama: null,
};

interface ModelPickerProps {
  provider: string;
  model: string;
  temperature: number;
  baseUrl?: string;
  onProviderChange: (provider: string) => void;
  onModelChange: (model: string) => void;
  onTemperatureChange: (t: number) => void;
  onBaseUrlChange?: (url: string) => void;
}

export function ModelPicker({
  provider,
  model,
  temperature,
  baseUrl,
  onProviderChange,
  onModelChange,
  onTemperatureChange,
  onBaseUrlChange,
}: ModelPickerProps) {
  const [search, setSearch] = useState("");

  const models = useQuery<ModelEntry[]>({
    queryKey: ["provider-models", provider],
    queryFn: () => api.get<ModelEntry[]>(`/api/providers/${provider}/models`),
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });

  // Fetch configured secret names once; used to show a green check on
  // provider chips whose required key is already in the vault.
  const secrets = useQuery<string[]>({
    queryKey: ["security-secrets"],
    queryFn: () => api.get<string[]>("/api/security/secrets"),
    staleTime: 30_000,
    retry: 1,
  });

  function secretForProvider(p: string): string | null {
    return PROVIDER_SECRET[p] ?? null;
  }

  function providerKeyConfigured(p: string): boolean {
    const secretName = secretForProvider(p);
    if (secretName === null) return true; // e.g. ollama doesn't need a key
    return (secrets.data ?? []).includes(secretName);
  }

  const filtered = (models.data ?? []).filter(
    (m) =>
      !search || m.id.toLowerCase().includes(search.toLowerCase()) ||
      m.name.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs uppercase text-spark-muted block mb-1">
          Provider
        </label>
        <div className="grid grid-cols-4 gap-2">
          {["anthropic", "openai", "openrouter", "ollama"].map((p) => {
            const configured = providerKeyConfigured(p);
            const title = configured
              ? secretForProvider(p) === null
                ? "No API key required"
                : `${secretForProvider(p)} is configured`
              : `${secretForProvider(p)} not set in the vault`;
            return (
              <button
                key={p}
                className={`border rounded px-3 py-2 text-sm capitalize flex items-center justify-center gap-1.5 ${
                  provider === p
                    ? "border-spark-accent bg-spark-accent/10"
                    : "border-spark-border hover:border-spark-accent/50"
                }`}
                onClick={() => {
                  onProviderChange(p);
                  setSearch("");
                }}
                title={title}
              >
                <span>{p}</span>
                {configured ? (
                  <CheckCircle2
                    className="w-3.5 h-3.5 text-green-500 shrink-0"
                    aria-label="Key configured"
                  />
                ) : (
                  <AlertCircle
                    className="w-3.5 h-3.5 text-amber-500 shrink-0"
                    aria-label="Key missing"
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <label className="text-xs uppercase text-spark-muted block mb-1">
          Model ({filtered.length} available)
        </label>
        <input
          className="input w-full mb-2"
          placeholder="Search models…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {models.isLoading && (
          <p className="text-spark-muted text-xs">Fetching models…</p>
        )}
        {models.isError && (
          <p className="text-red-400 text-xs">
            Failed to fetch. Enter a model ID manually below.
          </p>
        )}
        <div className="max-h-48 overflow-auto border border-spark-border rounded">
          {filtered.map((m) => (
            <button
              key={m.id}
              className={`w-full text-left px-3 py-1.5 text-sm border-b border-spark-border last:border-0 hover:bg-spark-accent/5 ${
                model === m.id ? "bg-spark-accent/10 text-spark-accent" : ""
              }`}
              onClick={() => {
                onModelChange(m.id);
                setSearch("");
              }}
            >
              <span className="font-mono text-xs">{m.id}</span>
              {m.name !== m.id && (
                <span className="text-spark-muted text-xs ml-2">
                  {m.name}
                </span>
              )}
            </button>
          ))}
          {filtered.length === 0 && !models.isLoading && (
            <div className="px-3 py-2 text-xs text-spark-muted">
              No models match. Type the model ID:
            </div>
          )}
        </div>
        <input
          className="input w-full mt-2 font-mono text-xs"
          placeholder="Or type model ID directly"
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
        />
      </div>

      {provider === "ollama" && onBaseUrlChange && (
        <div>
          <label className="text-xs uppercase text-spark-muted block mb-1">
            Base URL
          </label>
          <input
            className="input w-full"
            placeholder="http://localhost:11434"
            value={baseUrl ?? ""}
            onChange={(e) => onBaseUrlChange(e.target.value)}
          />
        </div>
      )}

      <div>
        <label className="text-xs uppercase text-spark-muted block mb-1">
          Temperature ({temperature})
        </label>
        <input
          type="range"
          min="0"
          max="2"
          step="0.1"
          value={temperature}
          onChange={(e) => onTemperatureChange(parseFloat(e.target.value))}
          className="w-full"
        />
      </div>

      <p className="text-xs text-spark-muted flex items-center gap-1.5">
        API key:{" "}
        <code className="font-mono">
          {PROVIDER_SECRET[provider] ?? "none needed"}
        </code>
        {providerKeyConfigured(provider) ? (
          <span className="inline-flex items-center gap-1 text-green-500">
            <CheckCircle2 className="w-3.5 h-3.5" /> configured
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-amber-500">
            <AlertCircle className="w-3.5 h-3.5" /> not set
          </span>
        )}
        {PROVIDER_SECRET[provider] && (
          <>
            {" — "}
            <a href="/provider" className="text-spark-accent underline">
              set in Provider Setup
            </a>
          </>
        )}
      </p>
    </div>
  );
}

export { PROVIDER_SECRET };
