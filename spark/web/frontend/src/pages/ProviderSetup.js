import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Check, Key, Server, TestTube, X, Zap } from "lucide-react";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { ConfirmDialog } from "../components/ConfirmDialog";
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
];
export default function ProviderSetup() {
    const qc = useQueryClient();
    const [selectedProvider, setSelectedProvider] = useState("anthropic");
    const [apiKey, setApiKey] = useState("");
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);
    const [deleteConfirm, setDeleteConfirm] = useState(false);
    const secrets = useQuery({
        queryKey: ["secret-names"],
        queryFn: () => api.get("/api/security/secrets"),
    });
    const agents = useQuery({
        queryKey: ["agents"],
        queryFn: () => api.get("/api/scheduler/agents"),
    });
    const provider = PROVIDERS.find((p) => p.id === selectedProvider);
    const isConfigured = provider.secretName === null ||
        (secrets.data ?? []).includes(provider.secretName);
    async function saveKey() {
        if (!provider.secretName || !apiKey.trim())
            return;
        setSaving(true);
        try {
            await api.put("/api/security/secrets", {
                name: provider.secretName,
                value: apiKey.trim(),
            });
            toast.success(`Saved ${provider.secretName}`);
            setApiKey("");
            qc.invalidateQueries({ queryKey: ["secret-names"] });
        }
        catch (err) {
            toast.error(`Failed to save: ${err}`);
        }
        finally {
            setSaving(false);
        }
    }
    async function deleteKey() {
        if (!provider.secretName)
            return;
        try {
            await api.del(`/api/security/secrets/${encodeURIComponent(provider.secretName)}`);
            toast.success(`Deleted ${provider.secretName}`);
            qc.invalidateQueries({ queryKey: ["secret-names"] });
        }
        catch (err) {
            toast.error(`Failed to delete: ${err}`);
        }
        finally {
            setDeleteConfirm(false);
        }
    }
    async function testConnection() {
        setTesting(true);
        setTestResult(null);
        try {
            const res = await api.post(`/api/providers/${provider.id}/test`);
            setTestResult(res);
            if (res.ok)
                toast.success(res.detail);
            else
                toast.error(res.detail);
        }
        catch (err) {
            const detail = `${err}`;
            setTestResult({ ok: false, detail });
            toast.error(detail);
        }
        finally {
            setTesting(false);
        }
    }
    return (_jsxs("div", { className: "space-y-6", children: [_jsx(PageHeader, { icon: _jsx(Zap, { className: "w-6 h-6" }), title: "Provider Setup", subtitle: "Configure the LLM provider and API key so agents can run. The key is stored encrypted in the age vault \u2014 never in plaintext." }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "1. Choose a provider" }), _jsx("div", { className: "grid grid-cols-2 md:grid-cols-4 gap-3", children: PROVIDERS.map((p) => {
                            const configured = p.secretName === null ||
                                (secrets.data ?? []).includes(p.secretName);
                            return (_jsxs("button", { className: `border rounded p-3 text-left transition ${selectedProvider === p.id
                                    ? "border-spark-accent bg-spark-accent/5"
                                    : "border-spark-border hover:border-spark-accent/50"}`, onClick: () => setSelectedProvider(p.id), children: [_jsxs("div", { className: "flex items-center justify-between mb-1", children: [_jsx("span", { className: "font-medium text-sm", children: p.label }), configured && (_jsx(Check, { className: "w-4 h-4 text-spark-good" }))] }), _jsx("p", { className: "text-xs text-spark-muted", children: p.secretName ?? "No key needed" })] }, p.id));
                        }) })] }), _jsxs("section", { className: "panel p-4", children: [_jsxs("h3", { className: "font-semibold mb-3 flex items-center gap-2", children: [_jsx(Key, { className: "w-4 h-4" }), " 2. API key"] }), provider.secretName === null ? (_jsxs("p", { className: "text-spark-muted text-sm", children: ["Ollama runs locally \u2014 no API key required. Make sure", " ", _jsx("code", { className: "font-mono", children: "ollama serve" }), " is running and reachable from the container."] })) : (_jsxs("div", { className: "space-y-3", children: [_jsx("p", { className: "text-spark-muted text-xs", children: provider.hint }), isConfigured ? (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center gap-3 flex-wrap", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm text-spark-good", children: [_jsx(Check, { className: "w-4 h-4" }), _jsxs("span", { children: [_jsx("code", { className: "font-mono", children: provider.secretName }), " ", "is set in the vault"] })] }), _jsxs("button", { className: "btn flex items-center gap-1", onClick: testConnection, disabled: testing, children: [_jsx(TestTube, { className: "w-3.5 h-3.5" }), testing ? "Testing…" : "Test connection"] }), _jsx("button", { className: "btn btn-danger", onClick: () => setDeleteConfirm(true), children: "Remove" })] }), testResult && (_jsxs("div", { className: `flex items-center gap-2 text-xs p-2 rounded-md border ${testResult.ok
                                            ? "border-spark-good/40 bg-spark-good/5 text-spark-good"
                                            : "border-spark-danger/40 bg-spark-danger/5 text-spark-danger"}`, children: [testResult.ok ? (_jsx(Check, { className: "w-4 h-4" })) : (_jsx(X, { className: "w-4 h-4" })), testResult.detail] }))] })) : (_jsxs("div", { className: "flex gap-2", children: [_jsx("input", { type: "password", className: "input flex-1", placeholder: provider.placeholder, value: apiKey, onChange: (e) => setApiKey(e.target.value), onKeyDown: (e) => e.key === "Enter" && saveKey() }), _jsx("button", { className: "btn btn-primary", disabled: !apiKey.trim() || saving, onClick: saveKey, children: saving ? "Saving..." : "Save to vault" })] }))] }))] }), _jsxs("section", { className: "panel p-4", children: [_jsxs("h3", { className: "font-semibold mb-3 flex items-center gap-2", children: [_jsx(Server, { className: "w-4 h-4" }), " 3. Available models"] }), _jsxs("p", { className: "text-spark-muted text-xs mb-2", children: ["The model is set in each agent's YAML (", _jsx("code", { className: "font-mono", children: "spec.runtime.provider.model" }), "). These are common options for ", provider.label, ":"] }), _jsx("div", { className: "flex flex-wrap gap-2", children: provider.models.map((m) => (_jsx("span", { className: "chip font-mono text-xs", children: m }, m))) })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Installed agents" }), agents.isLoading && (_jsx("p", { className: "text-spark-muted text-sm", children: "loading..." })), agents.data && agents.data.length === 0 && (_jsxs("p", { className: "text-spark-muted text-sm", children: ["No agents installed yet. Go to", " ", _jsx("a", { className: "text-spark-accent underline", href: "/templates", children: "Templates" }), " ", "to install one."] })), agents.data && agents.data.length > 0 && (_jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "Agent" }), _jsx("th", { className: "text-left", children: "Description" })] }) }), _jsx("tbody", { children: agents.data.map((a) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-2 font-mono", children: a.name }), _jsx("td", { className: "text-spark-muted", children: a.description })] }, a.name))) })] }))] }), _jsx(ConfirmDialog, { open: deleteConfirm, title: `Remove ${provider.secretName}?`, description: "Agents using this provider will stop working until you re-add a key.", tone: "danger", confirmLabel: "Remove", onCancel: () => setDeleteConfirm(false), onConfirm: deleteKey })] }));
}
