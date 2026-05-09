import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  FlaskConical,
  Save,
  RotateCcw,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import { api } from "../lib/api";
import { toast } from "sonner";
import {
  MaskStyleSelector,
  MaskStyleOption,
  MaskStyleValue,
} from "../components/MaskStyleSelector";
import { Modal } from "../components/Modal";

// ---------------------------------------------------------------------------
// Types — mirror /api/filtering/policy
// ---------------------------------------------------------------------------

type Level = "allow" | "warn" | "redact" | "shadow_block" | "block";
type Scope =
  | "user_input"
  | "tool_output"
  | "model_output"
  | "memory_write"
  | "shell_args";

interface DetectorEntry {
  rule_id: string;
  label: string;
  description: string;
  tier: "tier1" | "tier2";
}

interface CategoryView {
  data_class: string;
  family: string;
  description: string;
  default_level: Level;
  default_scopes: Scope[];
  default_mask_style: MaskStyleValue;
  default_min_confidence: number;
  default_require_consensus: boolean;
  global_override: GlobalRow | null;
  detectors: DetectorEntry[];
}

interface GlobalRow {
  id: number;
  scope_kind: "global";
  data_class: string;
  level: Level;
  scopes: Scope[];
  reason: string;
  mask_style: MaskStyleValue | null;
  min_confidence: number | null;
  require_consensus: boolean | null;
  detector_overrides: Record<string, { enabled?: boolean; threshold?: number }>;
  updated_at: string | null;
  updated_by: string | null;
}

interface FamilyDef {
  id: string;
  label: string;
  members: string[];
}

interface PolicyResponse {
  families: FamilyDef[];
  categories: CategoryView[];
  agent_overrides: Record<string, Record<string, GlobalRow>>;
  mask_styles: MaskStyleOption[];
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const ALL_SCOPES: Scope[] = [
  "user_input",
  "tool_output",
  "model_output",
  "memory_write",
  "shell_args",
];

const LEVEL_LABEL: Record<Level, string> = {
  allow: "Allow",
  warn: "Warn",
  redact: "Redact",
  shadow_block: "Shadow",
  block: "Block",
};

const FAMILY_ICON: Record<string, string> = {
  pii: "🪪",
  financial: "💳",
  credentials: "🔐",
  cli: "💻",
  prompt: "🧠",
};

export default function Filtering() {
  const qc = useQueryClient();
  const policy = useQuery<PolicyResponse>({
    queryKey: ["filtering", "policy"],
    queryFn: () => api.get("/api/filtering/policy"),
  });

  if (policy.isLoading) {
    return <div className="text-spark-muted">Loading filtering policy…</div>;
  }
  if (policy.error || !policy.data) {
    return (
      <div className="panel p-4 text-sm text-spark-danger">
        Failed to load filtering policy.
      </div>
    );
  }
  return <PolicyEditor data={policy.data} qc={qc} />;
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

interface PendingEdit {
  level?: Level;
  scopes?: Scope[];
  mask_style?: MaskStyleValue | null;
  min_confidence?: number | null;
  require_consensus?: boolean | null;
  reason?: string;
}

function PolicyEditor({
  data,
  qc,
}: {
  data: PolicyResponse;
  qc: ReturnType<typeof useQueryClient>;
}) {
  const [pending, setPending] = useState<Record<string, PendingEdit>>({});
  const [drawerOpenFor, setDrawerOpenFor] = useState<string | null>(null);
  const [dryRunOpen, setDryRunOpen] = useState(false);

  const setEdit = (dataClass: string, patch: PendingEdit) => {
    setPending((p) => ({
      ...p,
      [dataClass]: { ...(p[dataClass] || {}), ...patch },
    }));
  };

  const discardEdit = (dataClass: string) =>
    setPending((p) => {
      const next = { ...p };
      delete next[dataClass];
      return next;
    });

  const dirtyClasses = Object.keys(pending);
  const isDirty = dirtyClasses.length > 0;

  const saveMutation = useMutation({
    mutationFn: async () => {
      // Save each dirty category sequentially. The audit trail wants
      // one row per change, and the operator picks edits one card at a
      // time, so we never expect huge batches.
      for (const cls of dirtyClasses) {
        const cat = data.categories.find((c) => c.data_class === cls)!;
        const cur = effectiveCategory(cat);
        const next = { ...cur, ...pending[cls] };
        await api.put(`/api/filtering/policy/category/${cls}`, {
          level: next.level,
          scopes: next.scopes,
          reason: next.reason || "edited via Filtering page",
          mask_style: next.mask_style ?? null,
          min_confidence: next.min_confidence ?? null,
          require_consensus: next.require_consensus ?? null,
        });
      }
    },
    onSuccess: () => {
      toast.success(
        `Saved ${dirtyClasses.length} categor${dirtyClasses.length === 1 ? "y" : "ies"}`,
      );
      setPending({});
      qc.invalidateQueries({ queryKey: ["filtering", "policy"] });
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  const drawerCategory = drawerOpenFor
    ? data.categories.find((c) => c.data_class === drawerOpenFor) ?? null
    : null;

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <ShieldCheck size={22} className="text-spark-accent" />
            Filtering
          </h2>
          <p className="text-spark-muted text-sm mt-1 max-w-3xl">
            Per-category control over the data-class guardrails. Pick a
            redaction style, choose which detectors run, and dry-run
            sample text before saving. Every save is audited at
            elevated severity.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn btn-ghost text-sm"
            onClick={() => setDryRunOpen(true)}
          >
            <FlaskConical size={14} className="mr-1.5 inline" />
            Dry-run
          </button>
          {isDirty && (
            <button
              className="btn text-sm"
              onClick={() => setPending({})}
            >
              <RotateCcw size={14} className="mr-1.5 inline" />
              Discard ({dirtyClasses.length})
            </button>
          )}
          <button
            className="btn btn-primary text-sm"
            disabled={!isDirty || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            <Save size={14} className="mr-1.5 inline" />
            {saveMutation.isPending
              ? "Saving…"
              : isDirty
                ? `Save ${dirtyClasses.length}`
                : "Saved"}
          </button>
        </div>
      </header>

      {data.families.map((fam) => {
        const cats = data.categories.filter(
          (c) => c.family === fam.id,
        );
        if (cats.length === 0) return null;
        return (
          <section key={fam.id}>
            <h3 className="text-sm font-semibold tracking-wide uppercase text-spark-muted mb-3 flex items-center gap-2">
              <span aria-hidden>{FAMILY_ICON[fam.id] ?? "•"}</span>
              {fam.label}
            </h3>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {cats.map((cat) => (
                <CategoryCard
                  key={cat.data_class}
                  cat={cat}
                  pending={pending[cat.data_class] || null}
                  maskStyles={data.mask_styles}
                  onChange={(patch) => setEdit(cat.data_class, patch)}
                  onDiscard={() => discardEdit(cat.data_class)}
                  onAdvanced={() => setDrawerOpenFor(cat.data_class)}
                />
              ))}
            </div>
          </section>
        );
      })}

      {drawerCategory && (
        <DetectorDrawer
          cat={drawerCategory}
          onClose={() => setDrawerOpenFor(null)}
          onChanged={() => qc.invalidateQueries({ queryKey: ["filtering", "policy"] })}
        />
      )}

      {dryRunOpen && (
        <DryRunSandbox
          categories={data.categories}
          onClose={() => setDryRunOpen(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Effective values — merge global-override with built-in default
// ---------------------------------------------------------------------------

interface Effective {
  level: Level;
  scopes: Scope[];
  mask_style: MaskStyleValue | null;
  min_confidence: number | null;
  require_consensus: boolean | null;
  reason: string;
}

function effectiveCategory(cat: CategoryView): Effective {
  const o = cat.global_override;
  return {
    level: (o?.level as Level) ?? cat.default_level,
    scopes: o?.scopes ?? cat.default_scopes,
    mask_style: o?.mask_style ?? null,
    min_confidence: o?.min_confidence ?? null,
    require_consensus: o?.require_consensus ?? null,
    reason: o?.reason ?? "",
  };
}

// ---------------------------------------------------------------------------
// Category card
// ---------------------------------------------------------------------------

function CategoryCard({
  cat,
  pending,
  maskStyles,
  onChange,
  onDiscard,
  onAdvanced,
}: {
  cat: CategoryView;
  pending: PendingEdit | null;
  maskStyles: MaskStyleOption[];
  onChange: (patch: PendingEdit) => void;
  onDiscard: () => void;
  onAdvanced: () => void;
}) {
  const base = effectiveCategory(cat);
  const view: Effective = { ...base, ...(pending || {}) };
  const isDirty = pending !== null;
  const overrideCount = Object.keys(
    cat.global_override?.detector_overrides ?? {},
  ).length;
  const totalDetectors = cat.detectors.length;

  return (
    <div
      className={`panel p-4 ${isDirty ? "ring-1 ring-amber-400/60" : ""}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <code className="font-mono text-sm font-semibold text-spark-text">
              {cat.data_class}
            </code>
            <LevelChip level={view.level} />
            {isDirty && (
              <span className="chip chip-warn text-[10px]">unsaved</span>
            )}
          </div>
          <p className="text-xs text-spark-muted mt-1 line-clamp-2">
            {cat.description}
          </p>
        </div>
        {isDirty && (
          <button
            className="btn-ghost btn-icon"
            onClick={onDiscard}
            title="Discard"
          >
            <RotateCcw size={14} />
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 mt-4">
        <div>
          <label className="label block mb-1">Level</label>
          <select
            className="input w-full text-sm"
            value={view.level}
            onChange={(e) => onChange({ level: e.target.value as Level })}
          >
            {(["allow", "warn", "redact", "shadow_block", "block"] as Level[]).map(
              (l) => (
                <option key={l} value={l}>
                  {LEVEL_LABEL[l]}
                </option>
              ),
            )}
          </select>
        </div>
        <div>
          <label className="label block mb-1">Mask style</label>
          <MaskStyleSelector
            options={maskStyles}
            value={view.mask_style}
            dataClass={cat.data_class}
            defaultStyle={cat.default_mask_style}
            onChange={(v) => onChange({ mask_style: v })}
          />
        </div>
      </div>

      <div className="mt-4">
        <label className="label block mb-1.5">Scopes</label>
        <div className="flex flex-wrap gap-2">
          {ALL_SCOPES.map((s) => {
            const active = view.scopes.includes(s);
            return (
              <button
                key={s}
                onClick={() =>
                  onChange({
                    scopes: active
                      ? view.scopes.filter((x) => x !== s)
                      : [...view.scopes, s],
                  })
                }
                className={`chip text-[11px] ${active ? "chip-info" : ""}`}
              >
                {s}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mt-4">
        <div>
          <label className="label flex items-center justify-between mb-1">
            <span>Min confidence</span>
            <span className="font-mono text-spark-muted">
              {(view.min_confidence ?? cat.default_min_confidence).toFixed(2)}
            </span>
          </label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={view.min_confidence ?? cat.default_min_confidence}
            onChange={(e) =>
              onChange({ min_confidence: Number(e.target.value) })
            }
            className="w-full"
          />
        </div>
        <div>
          <label className="label block mb-1">Consensus</label>
          <select
            className="input w-full text-sm"
            value={
              view.require_consensus === null
                ? "default"
                : view.require_consensus
                  ? "require"
                  : "off"
            }
            onChange={(e) => {
              const v = e.target.value;
              onChange({
                require_consensus:
                  v === "default" ? null : v === "require" ? true : false,
              });
            }}
          >
            <option value="default">
              Default ({cat.default_require_consensus ? "required" : "off"})
            </option>
            <option value="require">Require 2+ detectors</option>
            <option value="off">Single detector OK</option>
          </select>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between">
        <button
          className="btn-ghost btn-icon text-xs flex items-center gap-1.5"
          onClick={onAdvanced}
        >
          <Settings2 size={13} />
          Advanced — {totalDetectors} detector{totalDetectors === 1 ? "" : "s"}
          {overrideCount > 0 && (
            <span className="chip chip-warn text-[10px]">
              {overrideCount} override{overrideCount === 1 ? "" : "s"}
            </span>
          )}
        </button>
        {cat.global_override?.updated_by && (
          <span className="text-[11px] text-spark-muted">
            edited by {cat.global_override.updated_by}
          </span>
        )}
      </div>
    </div>
  );
}

function LevelChip({ level }: { level: Level }) {
  const className = ((): string => {
    switch (level) {
      case "block":
        return "chip-danger";
      case "shadow_block":
        return "chip-danger";
      case "redact":
        return "chip-warn";
      case "warn":
        return "chip-info";
      case "allow":
        return "chip-good";
    }
  })();
  return <span className={`chip ${className} text-[11px]`}>{LEVEL_LABEL[level]}</span>;
}

// ---------------------------------------------------------------------------
// Advanced drawer — per-detector toggles
// ---------------------------------------------------------------------------

function DetectorDrawer({
  cat,
  onClose,
  onChanged,
}: {
  cat: CategoryView;
  onClose: () => void;
  onChanged: () => void;
}) {
  const overrides = cat.global_override?.detector_overrides ?? {};

  const toggle = useMutation({
    mutationFn: async ({
      ruleId,
      enabled,
    }: {
      ruleId: string;
      enabled: boolean | null;
    }) => {
      await api.put(
        `/api/filtering/policy/category/${cat.data_class}/detector/${encodeURIComponent(ruleId)}`,
        { enabled },
      );
    },
    onSuccess: () => {
      onChanged();
    },
    onError: (e: Error) => toast.error(`Update failed: ${e.message}`),
  });

  return (
    <Modal open onClose={onClose}>
      <div className="panel w-[640px] max-w-full max-h-[80vh] overflow-hidden flex flex-col">
        <div className="p-4 border-b border-spark-border flex items-start justify-between">
          <div>
            <div className="text-xs text-spark-muted uppercase tracking-wide">
              Advanced
            </div>
            <h3 className="text-lg font-semibold mt-0.5">
              <code className="font-mono">{cat.data_class}</code> detectors
            </h3>
            <p className="text-xs text-spark-muted mt-1 max-w-md">
              Per-detector toggles. Disabling a detector here suppresses
              its hits across every scope this category covers, no
              matter the level.
            </p>
          </div>
          <button className="btn btn-ghost text-sm" onClick={onClose}>
            Done
          </button>
        </div>
        <div className="overflow-y-auto p-4 space-y-2">
          {cat.detectors.length === 0 && (
            <div className="text-sm text-spark-muted">
              No detectors registered for this category.
            </div>
          )}
          {cat.detectors.map((d) => {
            const ov = overrides[d.rule_id];
            const enabled = ov?.enabled !== false;
            return (
              <div
                key={d.rule_id}
                className="flex items-start justify-between gap-3 p-2.5 border border-spark-border rounded-md hover:border-spark-accent/40 transition-colors"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{d.label}</span>
                    {d.tier === "tier2" && (
                      <span className="chip text-[9px]">tier 2</span>
                    )}
                  </div>
                  <code className="block font-mono text-[10px] text-spark-muted">
                    {d.rule_id}
                  </code>
                  <p className="text-xs text-spark-muted mt-0.5">
                    {d.description}
                  </p>
                </div>
                <label className="flex items-center gap-2 text-xs whitespace-nowrap pt-1">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() =>
                      toggle.mutate({
                        ruleId: d.rule_id,
                        enabled: enabled ? false : null,
                      })
                    }
                  />
                  {enabled ? (
                    <span className="text-spark-text">Enabled</span>
                  ) : (
                    <span className="text-spark-muted">Disabled</span>
                  )}
                </label>
              </div>
            );
          })}
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Dry-run sandbox
// ---------------------------------------------------------------------------

interface DryRunResponse {
  blocked: boolean;
  error_code?: string;
  message?: string;
  input: string;
  output: string | null;
  hits: Array<{
    data_class: string;
    rule_id: string;
    matched: string;
    confidence: number;
    tier: string;
    start: number;
    end: number;
  }>;
  levels_applied?: Array<{ data_class: string; level: Level }>;
}

function DryRunSandbox({
  categories,
  onClose,
}: {
  categories: CategoryView[];
  onClose: () => void;
}) {
  const [text, setText] = useState(
    "Hi, I'm Jane Doe. My card is 4111-1111-1111-1234 and my AWS key is AKIAIOSFODNN7EXAMPLE.",
  );
  const [scope, setScope] = useState<Scope>("model_output");
  const [agent, setAgent] = useState<string>("");
  const [result, setResult] = useState<DryRunResponse | null>(null);

  const run = useMutation({
    mutationFn: async () =>
      api.post<DryRunResponse>("/api/filtering/dry-run", {
        text,
        scope,
        agent_name: agent.trim() || null,
      }),
    onSuccess: (r) => setResult(r),
    onError: (e: Error) => toast.error(`Dry-run failed: ${e.message}`),
  });

  const ruleLabel = useMemo(() => {
    const map: Record<string, string> = {};
    for (const c of categories) {
      for (const d of c.detectors) map[d.rule_id] = d.label;
    }
    return map;
  }, [categories]);

  return (
    <Modal open onClose={onClose}>
      <div className="panel w-[920px] max-w-full max-h-[85vh] overflow-hidden flex flex-col">
        <div className="p-4 border-b border-spark-border flex items-start justify-between">
          <div>
            <div className="text-xs text-spark-muted uppercase tracking-wide flex items-center gap-1.5">
              <FlaskConical size={12} /> Sandbox
            </div>
            <h3 className="text-lg font-semibold mt-0.5">Dry-run filtering</h3>
            <p className="text-xs text-spark-muted mt-1 max-w-2xl">
              Paste sample text, pick a scope and (optionally) an agent.
              Runs the resolved policy without persisting anything — the
              run itself is recorded as info-severity audit so we can
              spot abusive use.
            </p>
          </div>
          <button className="btn btn-ghost text-sm" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label block mb-1">Scope</label>
              <select
                className="input w-full text-sm"
                value={scope}
                onChange={(e) => setScope(e.target.value as Scope)}
              >
                {ALL_SCOPES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label block mb-1">
                Agent (optional — leave blank for global)
              </label>
              <input
                className="input w-full text-sm"
                placeholder="my-agent"
                value={agent}
                onChange={(e) => setAgent(e.target.value)}
              />
            </div>
          </div>
          <div>
            <label className="label block mb-1">Input</label>
            <textarea
              className="input w-full text-sm font-mono"
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <button
            className="btn btn-primary text-sm"
            disabled={run.isPending || text.trim().length === 0}
            onClick={() => run.mutate()}
          >
            {run.isPending ? "Running…" : "Run"}
          </button>

          {result && (
            <div className="space-y-3">
              {result.blocked ? (
                <div className="panel p-3 border-spark-danger/50 bg-spark-danger/5">
                  <div className="flex items-center gap-2 text-spark-danger">
                    <AlertTriangle size={14} />
                    <strong className="text-sm">Blocked</strong>
                    <code className="text-xs">{result.error_code}</code>
                  </div>
                  <p className="text-sm mt-1">{result.message}</p>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="label mb-1">Input</div>
                    <pre className="panel p-2 text-xs font-mono whitespace-pre-wrap break-words">
                      {result.input}
                    </pre>
                  </div>
                  <div>
                    <div className="label mb-1">Redacted output</div>
                    <pre className="panel p-2 text-xs font-mono whitespace-pre-wrap break-words">
                      {result.output ?? "(blocked)"}
                    </pre>
                  </div>
                </div>
              )}
              <div>
                <div className="label mb-1">
                  Hits ({result.hits.length})
                </div>
                {result.hits.length === 0 ? (
                  <p className="text-sm text-spark-muted">
                    Nothing matched. The category levels you have set
                    didn't fire on this input.
                  </p>
                ) : (
                  <table className="w-full text-xs">
                    <thead className="text-spark-muted">
                      <tr>
                        <th className="text-left py-1.5 pr-3">Class</th>
                        <th className="text-left py-1.5 pr-3">Detector</th>
                        <th className="text-left py-1.5 pr-3">Match</th>
                        <th className="text-left py-1.5 pr-3">Tier</th>
                        <th className="text-right py-1.5">Confidence</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-spark-border">
                      {result.hits.map((h, i) => (
                        <tr key={i}>
                          <td className="py-1.5 pr-3 font-mono">{h.data_class}</td>
                          <td className="py-1.5 pr-3">
                            {ruleLabel[h.rule_id] ?? h.rule_id}
                          </td>
                          <td className="py-1.5 pr-3 font-mono text-spark-muted truncate max-w-[180px]">
                            {h.matched}
                          </td>
                          <td className="py-1.5 pr-3">
                            <span className="chip text-[10px]">{h.tier}</span>
                          </td>
                          <td className="py-1.5 text-right font-mono">
                            {h.confidence.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
