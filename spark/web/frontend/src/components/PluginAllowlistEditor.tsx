import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  Wifi,
  X,
} from "lucide-react";
import { Modal } from "./Modal";
import {
  FailureInspector,
  SparkErrorView,
} from "./FailureInspector";
import { useSuggestedPrefill } from "../lib/prefill";

/**
 * Shared live-introspection editor for plugins that ship a
 * "checkbox-grid driven by a discovery endpoint" config experience —
 * calendar, imap_reader, slack, cloud_drive, etc. Each plugin's
 * thin custom editor delegates here, passing:
 *
 * - the saved config + a setter
 * - the discovery callback (returns a typed payload)
 * - a list of "allowlist sections" — each a {field, items[]} block
 *   that renders as one checkbox grid
 * - optional bool toggles ("read_only", etc.) the editor can flash
 *   when the failure inspector deep-links to them
 *
 * The shared shape replicates `HomeAssistantConfigEditor`'s patterns
 * — discovery panel, danger-typed-confirm, prefill-flashing,
 * inline FailureInspector for discovery errors — but without
 * bolting in HA-specific behavior.
 */

// ---------------------------------------------------------------------------
// Public contract
// ---------------------------------------------------------------------------

export type Risk = "safe" | "elevated" | "danger";

export interface AllowlistItem {
  /** Stable id stored in the config list (URL, mailbox name, channel
   * id, remote name, …). */
  id: string;
  /** Operator-friendly label rendered next to the checkbox. */
  label: string;
  /** Risk band — drives chip color + typed-confirm gate when
   * `danger`. */
  risk: Risk;
  /** Optional secondary text (free-busy info, owner, type). */
  hint?: string;
}

export interface AllowlistSection {
  /** Config field name written back on save (e.g.
   * `allowed_calendars`). */
  field: string;
  /** Section header. */
  title: string;
  /** Optional explanation rendered under the title. */
  description?: string;
  /** Live-discovery items. Order is preserved. */
  items: AllowlistItem[];
}

export interface ToggleSpec {
  /** Config field name (e.g. `read_only`). */
  field: string;
  /** Label shown next to the toggle. */
  label: string;
  /** Inline description. */
  description?: string;
  /** Pretty values: "When `on`: …; When `off`: …" */
  on_label?: string;
  off_label?: string;
}

export interface DiscoveryEnvelope {
  ok: boolean;
  error?: string | null;
  error_code?: string | null;
  error_detail?: Record<string, unknown> | null;
  /** Bullet-list of `{label, value}` items shown above the grids when
   * discovery succeeded (e.g. "Connected to server X · 12 calendars"). */
  badges?: { label: string; value: string }[];
  /** Allowlist sections rendered as checkbox grids. */
  sections: AllowlistSection[];
}

export interface PluginAllowlistEditorProps {
  /** Plugin name — used for prefill matching + the deep-link URL. */
  pluginName: string;
  /** Saved config — used as the initial draft. */
  config: Record<string, unknown>;
  /** Called when the operator clicks Save. The editor passes the
   * merged config; the caller persists via `/api/plugin-config/<name>`. */
  onSave: (next: Record<string, unknown>, reason: string) => Promise<void>;
  /** Discovery call: usually `() => api.post(`/api/plugin-config/<name>/discover`)`. */
  discover: () => Promise<DiscoveryEnvelope>;
  /** Optional boolean toggles rendered above the grids (read_only,
   * etc.). Each can be the target of a `toggle` prefill. */
  toggles?: ToggleSpec[];
  /** Optional top-of-page connection panel (text inputs for
   * base_url / token_secret etc.). The shared component doesn't try
   * to introspect these; the per-plugin editor wires them itself. */
  connectionPanel?: React.ReactNode;
}

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

const RISK_CHIP: Record<Risk, string> = {
  safe: "chip-good",
  elevated: "chip-warn",
  danger: "chip-danger",
};

const RISK_LABEL: Record<Risk, string> = {
  safe: "safe",
  elevated: "elevated",
  danger: "danger",
};

export function PluginAllowlistEditor(
  props: PluginAllowlistEditorProps,
): JSX.Element {
  const {
    pluginName,
    config,
    onSave,
    discover,
    toggles = [],
    connectionPanel,
  } = props;

  const [draft, setDraft] = useState<Record<string, unknown>>(() => ({ ...config }));
  const [reason, setReason] = useState("");
  const [envelope, setEnvelope] = useState<DiscoveryEnvelope | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [confirmFor, setConfirmFor] = useState<{
    field: string;
    item: AllowlistItem;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const flashedRef = useRef<Record<string, boolean>>({});

  // Prefill handler — `plugin_allowlist_grant` shape from the
  // Failure Inspector deep-link. Only acts when `plugin` matches.
  const [prefill, discardPrefill] = useSuggestedPrefill(
    "plugin_allowlist_grant",
  );
  const prefillMatchesUs = prefill && prefill.plugin === pluginName;

  useEffect(() => {
    if (!prefillMatchesUs || !prefill) return;
    if (prefill.toggle) {
      // Flip the matching boolean on.
      setDraft((d) => ({ ...d, [prefill.toggle as string]: false }));
      flashedRef.current[`toggle:${prefill.toggle}`] = true;
      return;
    }
    if (prefill.add_item && prefill.field) {
      const field = prefill.field;
      setDraft((d) => {
        const cur = new Set<string>(
          Array.isArray(d[field]) ? (d[field] as string[]) : [],
        );
        cur.add(prefill.add_item!);
        return { ...d, [field]: Array.from(cur) };
      });
      flashedRef.current[`item:${prefill.field}:${prefill.add_item}`] = true;
    }
  }, [prefillMatchesUs, prefill]);

  // Auto-discover on mount when we have anything to introspect.
  useEffect(() => {
    runDiscover().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runDiscover() {
    setDiscovering(true);
    try {
      const r = await discover();
      setEnvelope(r);
    } finally {
      setDiscovering(false);
    }
  }

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(config),
    [draft, config],
  );

  async function handleSave() {
    setSaving(true);
    try {
      await onSave(draft, reason);
      setReason("");
      discardPrefill();
      runDiscover().catch(() => undefined);
    } finally {
      setSaving(false);
    }
  }

  function toggleItem(field: string, item: AllowlistItem) {
    const cur = new Set<string>(
      Array.isArray(draft[field]) ? (draft[field] as string[]) : [],
    );
    if (cur.has(item.id)) {
      cur.delete(item.id);
      setDraft((d) => ({ ...d, [field]: Array.from(cur) }));
      return;
    }
    if (item.risk === "danger") {
      setConfirmFor({ field, item });
      return;
    }
    cur.add(item.id);
    setDraft((d) => ({ ...d, [field]: Array.from(cur) }));
  }

  function toggleBool(field: string) {
    setDraft((d) => ({ ...d, [field]: !d[field] }));
  }

  const sparkErr: SparkErrorView | null =
    envelope && !envelope.ok && envelope.error_code
      ? {
          code: envelope.error_code,
          message: envelope.error || "Discovery failed",
          detail: (envelope.error_detail as Record<string, unknown>) ?? {},
          remediation: null,
          tuning: null,
        }
      : null;

  return (
    <div className="panel p-4 space-y-5">
      {/* Optional connection panel */}
      {connectionPanel && <section>{connectionPanel}</section>}

      {/* Discover button row */}
      <section className="flex items-center gap-3 flex-wrap">
        <button
          className="btn btn-ghost text-xs"
          disabled={discovering}
          onClick={() => runDiscover()}
        >
          {discovering ? (
            <Loader2 size={12} className="animate-spin mr-1.5 inline" />
          ) : (
            <RefreshCw size={12} className="mr-1.5 inline" />
          )}
          {envelope ? "Re-discover" : "Test connection & discover"}
        </button>
        {envelope?.ok && envelope.badges && (
          <div className="flex items-center gap-2 text-xs text-spark-good flex-wrap">
            <CheckCircle2 size={14} />
            {envelope.badges.map((b, i) => (
              <span key={i}>
                {b.label}: <code>{b.value}</code>
              </span>
            ))}
          </div>
        )}
        {sparkErr && (
          <div className="w-full">
            <FailureInspector error={sparkErr} variant="compact" />
          </div>
        )}
      </section>

      {/* Suggestion banner */}
      {prefillMatchesUs && prefill && (
        <div className="panel p-3 border-amber-400/60 bg-amber-400/5 flex items-start gap-3">
          <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 text-sm">
            <strong>Suggested by failure inspector.</strong>{" "}
            {prefill.toggle ? (
              <>
                <code>{prefill.toggle}</code> staged to flip. Review and
                Save.
              </>
            ) : prefill.add_item && prefill.field ? (
              <>
                <code>{prefill.add_item}</code> staged for{" "}
                <code>{prefill.field}</code>. Review the highlighted
                checkbox and Save.
              </>
            ) : null}
          </div>
          <button
            className="btn btn-ghost text-xs"
            onClick={() => {
              discardPrefill();
              setDraft({ ...config });
            }}
          >
            Discard
          </button>
        </div>
      )}

      {/* Toggles */}
      {toggles.length > 0 && (
        <section className="space-y-2">
          {toggles.map((t) => {
            const flashed = flashedRef.current[`toggle:${t.field}`];
            const checked = Boolean(draft[t.field]);
            return (
              <label
                key={t.field}
                className={`flex items-start gap-3 p-2 rounded-md ${
                  flashed ? "ring-2 ring-amber-400/70" : ""
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleBool(t.field)}
                  className="mt-1"
                />
                <span className="text-sm">
                  <strong>{t.label}</strong>{" "}
                  {checked ? (
                    <span className="chip chip-good text-[10px] ml-1">
                      {t.on_label ?? "on"}
                    </span>
                  ) : (
                    <span className="chip chip-warn text-[10px] ml-1">
                      {t.off_label ?? "off"}
                    </span>
                  )}
                  {t.description && (
                    <span className="block text-xs text-spark-muted mt-0.5">
                      {t.description}
                    </span>
                  )}
                </span>
              </label>
            );
          })}
        </section>
      )}

      {/* Allowlist sections */}
      {envelope?.ok &&
        envelope.sections.map((sec) => (
          <section key={sec.field}>
            <div className="label mb-1">
              {sec.title}{" "}
              <span className="text-spark-muted text-[11px] normal-case font-normal">
                (
                {
                  (Array.isArray(draft[sec.field])
                    ? (draft[sec.field] as string[])
                    : []
                  ).length
                }
                /{sec.items.length} selected)
              </span>
            </div>
            {sec.description && (
              <p className="text-xs text-spark-muted mb-2">{sec.description}</p>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
              {sec.items.map((item) => {
                const checked = Array.isArray(draft[sec.field])
                  ? (draft[sec.field] as string[]).includes(item.id)
                  : false;
                const flashKey = `item:${sec.field}:${item.id}`;
                const flashed = flashedRef.current[flashKey];
                return (
                  <label
                    key={item.id}
                    className={`flex items-center gap-2 px-2 py-1.5 border rounded-md text-sm cursor-pointer hover:border-spark-accent/40 transition-colors ${
                      checked
                        ? "border-spark-border bg-spark-bg/30"
                        : "border-spark-border"
                    } ${flashed ? "ring-2 ring-amber-400/70" : ""}`}
                    title={item.hint ?? undefined}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleItem(sec.field, item)}
                    />
                    <span className="flex-1 min-w-0 truncate">
                      <span className="text-sm">{item.label}</span>
                      {item.hint && (
                        <span className="block text-[10px] text-spark-muted truncate">
                          {item.hint}
                        </span>
                      )}
                    </span>
                    {item.risk !== "safe" && (
                      <span
                        className={`chip ${RISK_CHIP[item.risk]} text-[9px]`}
                      >
                        {RISK_LABEL[item.risk]}
                      </span>
                    )}
                  </label>
                );
              })}
            </div>
          </section>
        ))}

      {/* Save row */}
      <div className="pt-3 border-t border-spark-border space-y-3">
        <label className="block">
          <span className="label">Reason (audited)</span>
          <input
            className="input w-full"
            placeholder="why are you changing this?"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <div className="flex items-center justify-between">
          <div className="text-xs text-spark-muted">
            {dirty ? "Unsaved changes" : "In sync with stored config"}
          </div>
          <div className="flex gap-2">
            <button
              className="btn"
              disabled={!dirty}
              onClick={() => {
                setDraft({ ...config });
                setReason("");
                discardPrefill();
              }}
            >
              <RotateCcw size={13} className="mr-1.5 inline" />
              Discard
            </button>
            <button
              className="btn btn-primary"
              disabled={!dirty || saving}
              onClick={handleSave}
            >
              <Save size={13} className="mr-1.5 inline" />
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>

      {confirmFor && (
        <Modal open onClose={() => setConfirmFor(null)}>
          <DangerConfirm
            target={confirmFor}
            onCancel={() => setConfirmFor(null)}
            onConfirm={() => {
              const { field, item } = confirmFor;
              setDraft((d) => {
                const cur = new Set<string>(
                  Array.isArray(d[field]) ? (d[field] as string[]) : [],
                );
                cur.add(item.id);
                return { ...d, [field]: Array.from(cur) };
              });
              setConfirmFor(null);
            }}
          />
        </Modal>
      )}
    </div>
  );
}

function DangerConfirm({
  target,
  onCancel,
  onConfirm,
}: {
  target: { field: string; item: AllowlistItem };
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === target.item.id || typed === target.item.label;
  return (
    <div className="panel p-5 max-w-md">
      <div className="flex items-start gap-3">
        <ShieldAlert size={20} className="text-spark-danger shrink-0 mt-0.5" />
        <div className="flex-1">
          <h4 className="font-bold">Allow `{target.item.label}`?</h4>
          <p className="text-sm text-spark-muted mt-1">
            This is a high-risk item. Allowing it lets the agent
            interact with it through the plugin.
          </p>
          <p className="text-xs text-spark-muted mt-3">
            Type <code className="font-mono">{target.item.id}</code> or{" "}
            <code className="font-mono">{target.item.label}</code> to
            confirm:
          </p>
          <input
            className="input w-full mt-2 font-mono text-sm"
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && matches) onConfirm();
            }}
          />
        </div>
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="btn btn-danger"
          disabled={!matches}
          onClick={onConfirm}
        >
          Allow
        </button>
      </div>
    </div>
  );
}

// Suppress unused-import warning for X (chips render their own remove
// buttons in per-plugin wrappers, not in the shared component).
void X;
void Wifi;
