import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
export default function PersonaPage() {
    const client = useQueryClient();
    const personas = useQuery({
        queryKey: ["personas"],
        queryFn: () => api.get("/api/persona/"),
    });
    const [selectedId, setSelectedId] = useState(null);
    const active = useMemo(() => {
        const list = personas.data ?? [];
        if (selectedId === null)
            return list[0] ?? null;
        return list.find((p) => p.persona_id === selectedId) ?? list[0] ?? null;
    }, [personas.data, selectedId]);
    const createDraft = useMutation({
        mutationFn: () => api.post("/api/persona/", {
            name: "New persona",
            description: "",
            system_prompt: "You are a helpful assistant.",
            tone: null,
            tags: [],
        }),
        onSuccess: (row) => {
            client.invalidateQueries({ queryKey: ["personas"] });
            setSelectedId(row.persona_id);
        },
    });
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Persona" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Edit the agent's system prompt live. The next model call picks up the active persona with no restart required." })] }), _jsx("button", { className: "btn btn-primary", onClick: () => createDraft.mutate(), children: "New persona" })] }), _jsxs("div", { className: "flex gap-4", children: [_jsx("div", { className: "panel p-2 w-64 shrink-0 space-y-1", children: (personas.data ?? []).map((p) => (_jsxs("button", { onClick: () => setSelectedId(p.persona_id), className: `block w-full text-left px-2 py-1.5 rounded-md text-sm ${active?.persona_id === p.persona_id
                                ? "bg-spark-border text-spark-text"
                                : "text-spark-muted hover:bg-spark-border/50"}`, children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "font-semibold", children: p.name }), p.is_active && _jsx("span", { className: "chip chip-good", children: "active" })] }), p.description && (_jsx("div", { className: "text-xs text-spark-muted truncate", children: p.description }))] }, p.persona_id))) }), _jsx("div", { className: "flex-1", children: active && _jsx(PersonaEditor, { persona: active }) })] })] }));
}
function PersonaEditor({ persona }) {
    const client = useQueryClient();
    const [name, setName] = useState(persona.name);
    const [description, setDescription] = useState(persona.description);
    const [systemPrompt, setSystemPrompt] = useState(persona.system_prompt);
    const [tone, setTone] = useState(persona.tone ?? "");
    const [tags, setTags] = useState((persona.tags ?? []).join(", "));
    const [preview, setPreview] = useState(null);
    useEffect(() => {
        setName(persona.name);
        setDescription(persona.description);
        setSystemPrompt(persona.system_prompt);
        setTone(persona.tone ?? "");
        setTags((persona.tags ?? []).join(", "));
    }, [persona.persona_id]);
    const save = useMutation({
        mutationFn: () => api.put(`/api/persona/${encodeURIComponent(persona.persona_id)}`, {
            name,
            description,
            system_prompt: systemPrompt,
            tone: tone || null,
            tags: tags
                .split(",")
                .map((t) => t.trim())
                .filter(Boolean),
        }),
        onSuccess: () => client.invalidateQueries({ queryKey: ["personas"] }),
    });
    const activate = useMutation({
        mutationFn: () => api.post(`/api/persona/${encodeURIComponent(persona.persona_id)}/activate`),
        onSuccess: () => client.invalidateQueries({ queryKey: ["personas"] }),
    });
    const del = useMutation({
        mutationFn: () => api.del(`/api/persona/${encodeURIComponent(persona.persona_id)}`),
        onSuccess: () => client.invalidateQueries({ queryKey: ["personas"] }),
    });
    async function runPreview() {
        const resp = await api.post(`/api/persona/${encodeURIComponent(persona.persona_id)}/preview`, { objective: "" });
        setPreview(resp.system_prompt);
    }
    return (_jsxs("div", { className: "panel p-4 space-y-3", children: [_jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Name" }), _jsx("input", { className: "input w-full mt-1", value: name, onChange: (e) => setName(e.target.value) })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Tone (optional)" }), _jsx("input", { className: "input w-full mt-1", value: tone, onChange: (e) => setTone(e.target.value), placeholder: "e.g. direct, operator-focused" })] })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Description" }), _jsx("input", { className: "input w-full mt-1", value: description, onChange: (e) => setDescription(e.target.value) })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Tags (comma separated)" }), _jsx("input", { className: "input w-full mt-1", value: tags, onChange: (e) => setTags(e.target.value) })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "System prompt" }), _jsx("textarea", { className: "input w-full h-64 font-mono text-xs", value: systemPrompt, onChange: (e) => setSystemPrompt(e.target.value) })] }), preview && (_jsxs("div", { className: "panel p-3 bg-spark-bg", children: [_jsx("div", { className: "label mb-1", children: "Preview (what the model will see)" }), _jsx("pre", { className: "text-xs text-spark-muted whitespace-pre-wrap", children: preview })] })), _jsxs("div", { className: "flex items-center justify-between", children: [_jsx("button", { className: "btn btn-danger", onClick: async () => {
                            const ok = await confirmDialog({
                                title: `Delete persona "${persona.name}"?`,
                                description: "This removes the persona record. Active agents won't be affected until you select a different persona. The change is audited.",
                                tone: "danger",
                                confirmLabel: "Delete persona",
                            });
                            if (ok)
                                del.mutate();
                        }, disabled: persona.is_active, children: "Delete" }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { className: "btn", onClick: runPreview, children: "Preview" }), _jsx("button", { className: "btn", onClick: () => save.mutate(), children: "Save" }), _jsx("button", { className: "btn btn-primary", onClick: async () => {
                                    await save.mutateAsync();
                                    await activate.mutateAsync();
                                }, children: "Save & Activate" })] })] })] }));
}
