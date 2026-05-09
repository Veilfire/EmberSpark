import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";

interface Persona {
  persona_id: string;
  name: string;
  description: string;
  system_prompt: string;
  tone: string | null;
  tags: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export default function PersonaPage() {
  const client = useQueryClient();
  const personas = useQuery<Persona[]>({
    queryKey: ["personas"],
    queryFn: () => api.get("/api/persona/"),
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const active = useMemo(() => {
    const list = personas.data ?? [];
    if (selectedId === null) return list[0] ?? null;
    return list.find((p) => p.persona_id === selectedId) ?? list[0] ?? null;
  }, [personas.data, selectedId]);

  const createDraft = useMutation({
    mutationFn: () =>
      api.post<Persona>("/api/persona/", {
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

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Persona</h2>
          <p className="text-spark-muted text-sm">
            Edit the agent's system prompt live. The next model call picks up the
            active persona with no restart required.
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => createDraft.mutate()}>
          New persona
        </button>
      </header>

      <div className="flex gap-4">
        <div className="panel p-2 w-64 shrink-0 space-y-1">
          {(personas.data ?? []).map((p) => (
            <button
              key={p.persona_id}
              onClick={() => setSelectedId(p.persona_id)}
              className={`block w-full text-left px-2 py-1.5 rounded-md text-sm ${
                active?.persona_id === p.persona_id
                  ? "bg-spark-border text-spark-text"
                  : "text-spark-muted hover:bg-spark-border/50"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold">{p.name}</span>
                {p.is_active && <span className="chip chip-good">active</span>}
              </div>
              {p.description && (
                <div className="text-xs text-spark-muted truncate">{p.description}</div>
              )}
            </button>
          ))}
        </div>

        <div className="flex-1">{active && <PersonaEditor persona={active} />}</div>
      </div>
    </div>
  );
}

function PersonaEditor({ persona }: { persona: Persona }) {
  const client = useQueryClient();
  const [name, setName] = useState(persona.name);
  const [description, setDescription] = useState(persona.description);
  const [systemPrompt, setSystemPrompt] = useState(persona.system_prompt);
  const [tone, setTone] = useState(persona.tone ?? "");
  const [tags, setTags] = useState((persona.tags ?? []).join(", "));
  const [preview, setPreview] = useState<string | null>(null);

  useEffect(() => {
    setName(persona.name);
    setDescription(persona.description);
    setSystemPrompt(persona.system_prompt);
    setTone(persona.tone ?? "");
    setTags((persona.tags ?? []).join(", "));
  }, [persona.persona_id]);

  const save = useMutation({
    mutationFn: () =>
      api.put<Persona>(`/api/persona/${encodeURIComponent(persona.persona_id)}`, {
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
    mutationFn: () =>
      api.post<Persona>(
        `/api/persona/${encodeURIComponent(persona.persona_id)}/activate`
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["personas"] }),
  });

  const del = useMutation({
    mutationFn: () =>
      api.del(`/api/persona/${encodeURIComponent(persona.persona_id)}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["personas"] }),
  });

  async function runPreview() {
    const resp = await api.post<{ system_prompt: string; char_count: number }>(
      `/api/persona/${encodeURIComponent(persona.persona_id)}/preview`,
      { objective: "" }
    );
    setPreview(resp.system_prompt);
  }

  return (
    <div className="panel p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="label">Name</span>
          <input
            className="input w-full mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Tone (optional)</span>
          <input
            className="input w-full mt-1"
            value={tone}
            onChange={(e) => setTone(e.target.value)}
            placeholder="e.g. direct, operator-focused"
          />
        </label>
      </div>
      <label className="block">
        <span className="label">Description</span>
        <input
          className="input w-full mt-1"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="label">Tags (comma separated)</span>
        <input
          className="input w-full mt-1"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="label">System prompt</span>
        <textarea
          className="input w-full h-64 font-mono text-xs"
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
        />
      </label>

      {preview && (
        <div className="panel p-3 bg-spark-bg">
          <div className="label mb-1">Preview (what the model will see)</div>
          <pre className="text-xs text-spark-muted whitespace-pre-wrap">{preview}</pre>
        </div>
      )}

      <div className="flex items-center justify-between">
        <button
          className="btn btn-danger"
          onClick={async () => {
            const ok = await confirmDialog({
              title: `Delete persona "${persona.name}"?`,
              description:
                "This removes the persona record. Active agents won't be affected until you select a different persona. The change is audited.",
              tone: "danger",
              confirmLabel: "Delete persona",
            });
            if (ok) del.mutate();
          }}
          disabled={persona.is_active}
        >
          Delete
        </button>
        <div className="flex gap-2">
          <button className="btn" onClick={runPreview}>
            Preview
          </button>
          <button className="btn" onClick={() => save.mutate()}>
            Save
          </button>
          <button
            className="btn btn-primary"
            onClick={async () => {
              await save.mutateAsync();
              await activate.mutateAsync();
            }}
          >
            Save & Activate
          </button>
        </div>
      </div>
    </div>
  );
}
