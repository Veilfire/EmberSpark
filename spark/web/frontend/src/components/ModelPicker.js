import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { CheckCircle2, AlertCircle } from "lucide-react";
import { api } from "../lib/api";
const PROVIDER_SECRET = {
    anthropic: "anthropic_key",
    openai: "openai_key",
    openrouter: "openrouter_key",
    ollama: null,
};
export function ModelPicker({ provider, model, temperature, baseUrl, onProviderChange, onModelChange, onTemperatureChange, onBaseUrlChange, }) {
    const [search, setSearch] = useState("");
    const models = useQuery({
        queryKey: ["provider-models", provider],
        queryFn: () => api.get(`/api/providers/${provider}/models`),
        staleTime: 5 * 60 * 1000,
        retry: 1,
    });
    // Fetch configured secret names once; used to show a green check on
    // provider chips whose required key is already in the vault.
    const secrets = useQuery({
        queryKey: ["security-secrets"],
        queryFn: () => api.get("/api/security/secrets"),
        staleTime: 30_000,
        retry: 1,
    });
    function secretForProvider(p) {
        return PROVIDER_SECRET[p] ?? null;
    }
    function providerKeyConfigured(p) {
        const secretName = secretForProvider(p);
        if (secretName === null)
            return true; // e.g. ollama doesn't need a key
        return (secrets.data ?? []).includes(secretName);
    }
    const filtered = (models.data ?? []).filter((m) => !search || m.id.toLowerCase().includes(search.toLowerCase()) ||
        m.name.toLowerCase().includes(search.toLowerCase()));
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Provider" }), _jsx("div", { className: "grid grid-cols-4 gap-2", children: ["anthropic", "openai", "openrouter", "ollama"].map((p) => {
                            const configured = providerKeyConfigured(p);
                            const title = configured
                                ? secretForProvider(p) === null
                                    ? "No API key required"
                                    : `${secretForProvider(p)} is configured`
                                : `${secretForProvider(p)} not set in the vault`;
                            return (_jsxs("button", { className: `border rounded px-3 py-2 text-sm capitalize flex items-center justify-center gap-1.5 ${provider === p
                                    ? "border-spark-accent bg-spark-accent/10"
                                    : "border-spark-border hover:border-spark-accent/50"}`, onClick: () => {
                                    onProviderChange(p);
                                    setSearch("");
                                }, title: title, children: [_jsx("span", { children: p }), configured ? (_jsx(CheckCircle2, { className: "w-3.5 h-3.5 text-green-500 shrink-0", "aria-label": "Key configured" })) : (_jsx(AlertCircle, { className: "w-3.5 h-3.5 text-amber-500 shrink-0", "aria-label": "Key missing" }))] }, p));
                        }) })] }), _jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Model (", filtered.length, " available)"] }), _jsx("input", { className: "input w-full mb-2", placeholder: "Search models\u2026", value: search, onChange: (e) => setSearch(e.target.value) }), models.isLoading && (_jsx("p", { className: "text-spark-muted text-xs", children: "Fetching models\u2026" })), models.isError && (_jsx("p", { className: "text-red-400 text-xs", children: "Failed to fetch. Enter a model ID manually below." })), _jsxs("div", { className: "max-h-48 overflow-auto border border-spark-border rounded", children: [filtered.map((m) => (_jsxs("button", { className: `w-full text-left px-3 py-1.5 text-sm border-b border-spark-border last:border-0 hover:bg-spark-accent/5 ${model === m.id ? "bg-spark-accent/10 text-spark-accent" : ""}`, onClick: () => {
                                    onModelChange(m.id);
                                    setSearch("");
                                }, children: [_jsx("span", { className: "font-mono text-xs", children: m.id }), m.name !== m.id && (_jsx("span", { className: "text-spark-muted text-xs ml-2", children: m.name }))] }, m.id))), filtered.length === 0 && !models.isLoading && (_jsx("div", { className: "px-3 py-2 text-xs text-spark-muted", children: "No models match. Type the model ID:" }))] }), _jsx("input", { className: "input w-full mt-2 font-mono text-xs", placeholder: "Or type model ID directly", value: model, onChange: (e) => onModelChange(e.target.value) })] }), provider === "ollama" && onBaseUrlChange && (_jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Base URL" }), _jsx("input", { className: "input w-full", placeholder: "http://localhost:11434", value: baseUrl ?? "", onChange: (e) => onBaseUrlChange(e.target.value) })] })), _jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Temperature (", temperature, ")"] }), _jsx("input", { type: "range", min: "0", max: "2", step: "0.1", value: temperature, onChange: (e) => onTemperatureChange(parseFloat(e.target.value)), className: "w-full" })] }), _jsxs("p", { className: "text-xs text-spark-muted flex items-center gap-1.5", children: ["API key:", " ", _jsx("code", { className: "font-mono", children: PROVIDER_SECRET[provider] ?? "none needed" }), providerKeyConfigured(provider) ? (_jsxs("span", { className: "inline-flex items-center gap-1 text-green-500", children: [_jsx(CheckCircle2, { className: "w-3.5 h-3.5" }), " configured"] })) : (_jsxs("span", { className: "inline-flex items-center gap-1 text-amber-500", children: [_jsx(AlertCircle, { className: "w-3.5 h-3.5" }), " not set"] })), PROVIDER_SECRET[provider] && (_jsxs(_Fragment, { children: [" — ", _jsx("a", { href: "/provider", className: "text-spark-accent underline", children: "set in Provider Setup" })] }))] })] }));
}
export { PROVIDER_SECRET };
