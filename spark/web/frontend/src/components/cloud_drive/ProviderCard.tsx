import { useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cloud,
  Copy,
  Loader2,
  Mail,
  Plug,
  Plus,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import {
  PROVIDER_REGISTRY,
  type ProviderKind,
  type AuthFieldSpec,
} from "./ProviderTypeRegistry";

/**
 * One operator-configured cloud provider. Tick to enable; expand to
 * edit. The card is self-contained: it owns its open/closed state and
 * dispatches every change up via ``onChange``.
 */

export interface ProviderConfig {
  name: string;
  enabled: boolean;
  auth: Record<string, unknown> & { kind: ProviderKind };
  allowed_paths: string[];
  auto_share: {
    enabled: boolean;
    recipients: string[];
    permission: "reader" | "writer" | "commenter";
  };
}

export interface ProviderHealth {
  ok: boolean;
  error?: string | null;
  free_bytes?: number | null;
  total_bytes?: number | null;
}

export function ProviderCard({
  config,
  health,
  flashed,
  flashedField,
  onChange,
  onRemove,
  onTest,
  testing,
}: {
  config: ProviderConfig;
  health?: ProviderHealth;
  /** Whole-card flash (provider just enabled by inspector deep-link) */
  flashed: boolean;
  /** Sub-field flash key — e.g. "allowed_paths" */
  flashedField?: string;
  onChange: (next: ProviderConfig) => void;
  onRemove: () => void;
  onTest: () => void;
  testing: boolean;
}) {
  const spec = PROVIDER_REGISTRY[config.auth.kind];
  const [open, setOpen] = useState(false);

  function setEnabled(v: boolean) {
    onChange({ ...config, enabled: v });
  }

  function setAuthField(key: string, value: unknown) {
    onChange({ ...config, auth: { ...config.auth, [key]: value } });
  }

  function setAllowedPaths(paths: string[]) {
    onChange({ ...config, allowed_paths: paths });
  }

  function setAutoShare(next: ProviderConfig["auto_share"]) {
    onChange({ ...config, auto_share: next });
  }

  const ringClass = flashed ? "ring-2 ring-amber-400/70" : "";
  const healthBadge = healthChip(health);

  return (
    <div
      className={`border rounded-md transition-colors ${
        config.enabled
          ? "border-spark-accent/40"
          : "border-spark-border"
      } ${ringClass}`}
    >
      <div className="flex items-center gap-3 px-3 py-2.5">
        <input
          type="checkbox"
          checked={config.enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          aria-label={`Enable ${config.name}`}
        />
        <button
          type="button"
          className="text-spark-muted hover:text-spark-text shrink-0"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? "Collapse" : "Expand"}
        >
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <Cloud size={16} className="text-spark-muted shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="font-mono text-sm truncate">{config.name}</div>
          <div className="text-[11px] text-spark-muted">
            {spec.label} · {spec.blurb}
          </div>
        </div>
        {healthBadge}
        <button
          type="button"
          className="text-spark-muted hover:text-spark-danger"
          onClick={onRemove}
          aria-label={`Remove ${config.name}`}
          title="Remove provider"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {open && (
        <div className="border-t border-spark-border bg-spark-bg/20 px-3 py-3 space-y-4">
          {/* Setup helper */}
          <SetupSteps kind={config.auth.kind} />

          {/* Auth fields */}
          <FieldGroup title="Auth" icon={<Plug size={12} />}>
            {spec.fields.map((field) => (
              <AuthFieldInput
                key={field.key}
                field={field}
                value={config.auth[field.key]}
                onChange={(v) => setAuthField(field.key, v)}
              />
            ))}
          </FieldGroup>

          {/* Allowed paths */}
          <FieldGroup
            title="Allowed paths"
            icon={<CheckCircle2 size={12} />}
            description="Root paths the agent may touch on this provider. Empty refuses all."
            flashed={flashedField === "allowed_paths"}
          >
            <PathListInput
              paths={config.allowed_paths}
              onChange={setAllowedPaths}
              providerName={config.name}
            />
          </FieldGroup>

          {/* Auto-share */}
          <FieldGroup
            title="Auto-share"
            icon={<Mail size={12} />}
            description={
              spec.autoShareImplemented
                ? "On every successful `put`, automatically grant access to these recipients."
                : `Auto-share isn't wired for ${spec.label} yet — config is preserved for when it lands in v2.`
            }
          >
            <AutoShareInput
              spec={config.auto_share}
              onChange={setAutoShare}
              warning={!spec.autoShareImplemented}
            />
          </FieldGroup>

          <div className="flex items-center justify-between pt-1 border-t border-spark-border">
            <div className="text-[11px] text-spark-muted">
              {health?.error && (
                <span className="text-spark-danger">{health.error}</span>
              )}
            </div>
            <button
              type="button"
              className="btn btn-ghost text-xs"
              disabled={testing}
              onClick={onTest}
            >
              {testing ? (
                <Loader2 size={12} className="animate-spin mr-1.5 inline" />
              ) : (
                <Plug size={12} className="mr-1.5 inline" />
              )}
              Test connection
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function healthChip(health: ProviderHealth | undefined) {
  if (!health) return null;
  if (health.ok) {
    return (
      <span
        className="chip chip-good text-[10px] flex items-center gap-1"
        title={
          health.total_bytes
            ? `${formatBytes(health.free_bytes ?? 0)} free of ${formatBytes(health.total_bytes)}`
            : "Connected"
        }
      >
        <CheckCircle2 size={10} />
        connected
      </span>
    );
  }
  return (
    <span
      className="chip chip-warn text-[10px] flex items-center gap-1"
      title={health.error ?? "Failed"}
    >
      <XCircle size={10} />
      error
    </span>
  );
}

function FieldGroup({
  title,
  icon,
  description,
  children,
  flashed,
}: {
  title: string;
  icon?: React.ReactNode;
  description?: string;
  children: React.ReactNode;
  flashed?: boolean;
}) {
  return (
    <div className={`space-y-2 ${flashed ? "ring-2 ring-amber-400/70 rounded p-2 -m-2" : ""}`}>
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-spark-muted">
        {icon}
        <span>{title}</span>
      </div>
      {description && (
        <p className="text-[11px] text-spark-muted">{description}</p>
      )}
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function AuthFieldInput({
  field,
  value,
  onChange,
}: {
  field: AuthFieldSpec;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const str = String(value ?? "");
  // Heuristic: warn if a *_secret field has a value that doesn't look
  // like a vault name (long + non-slug chars).
  const looksLikeCredential =
    field.type === "secret" &&
    str.length >= 24 &&
    !/^[a-zA-Z0-9._-]+$/.test(str);

  return (
    <label className="block">
      <span className="text-xs font-mono">{field.label}</span>
      {field.type === "enum" ? (
        <select
          className="input w-full mt-1 text-sm"
          value={str}
          onChange={(e) => onChange(e.target.value)}
        >
          {field.options?.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : field.type === "info" ? (
        <p className="text-xs text-spark-muted">{field.hint}</p>
      ) : (
        <input
          className="input w-full mt-1 font-mono text-sm"
          value={str}
          placeholder={field.placeholder}
          autoComplete={field.type === "secret" ? "off" : undefined}
          spellCheck={field.type === "secret" ? false : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {field.hint && field.type !== "info" && (
        <span className="text-[10px] text-spark-muted block mt-0.5">
          {field.hint}
        </span>
      )}
      {looksLikeCredential && (
        <div className="text-[11px] text-spark-danger border border-spark-danger/40 rounded p-2 mt-1">
          ⚠ This looks like a credential, not a vault name. Add it to
          the vault under a name (e.g. via{" "}
          <a className="text-spark-link hover:underline" href="/secrets">
            Secure → Secrets
          </a>
          ) and put the <em>name</em> here instead.
        </div>
      )}
    </label>
  );
}

function PathListInput({
  paths,
  onChange,
  providerName,
}: {
  paths: string[];
  onChange: (next: string[]) => void;
  providerName: string;
}) {
  const [draft, setDraft] = useState("");

  function add() {
    const clean = draft.trim().replace(/^\/+|\/+$/g, "");
    if (!clean) return;
    if (paths.includes(clean)) {
      setDraft("");
      return;
    }
    onChange([...paths, clean]);
    setDraft("");
  }

  return (
    <div className="space-y-1.5">
      {paths.length === 0 && (
        <div className="text-xs text-spark-danger border border-spark-danger/30 rounded p-2 bg-spark-danger/5">
          No paths allowed — the agent will be refused on every action.
          Add at least one root.
        </div>
      )}
      {paths.map((p, i) => (
        <div
          key={i}
          className="flex items-center gap-2 border border-spark-border rounded px-2 py-1 bg-spark-bg/40"
        >
          <code className="text-xs flex-1 font-mono">
            {providerName}:{p}
          </code>
          <button
            type="button"
            className="text-spark-muted hover:text-spark-danger"
            onClick={() => onChange(paths.filter((_, j) => j !== i))}
            aria-label="Remove path"
          >
            <X size={12} />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <input
          className="input flex-1 font-mono text-sm"
          placeholder="Spark-agent  (path under the remote root)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" className="btn text-xs" onClick={add} disabled={!draft.trim()}>
          <Plus size={12} className="mr-1 inline" />
          Add
        </button>
      </div>
    </div>
  );
}

function AutoShareInput({
  spec,
  onChange,
  warning,
}: {
  spec: ProviderConfig["auto_share"];
  onChange: (next: ProviderConfig["auto_share"]) => void;
  warning?: boolean;
}) {
  const [draft, setDraft] = useState("");

  function addRecipient() {
    const clean = draft.trim().toLowerCase();
    if (!clean) return;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(clean)) {
      toast.error("Looks like an invalid email address");
      return;
    }
    if (spec.recipients.includes(clean)) {
      setDraft("");
      return;
    }
    onChange({ ...spec, recipients: [...spec.recipients, clean] });
    setDraft("");
  }

  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={spec.enabled}
          onChange={(e) => onChange({ ...spec, enabled: e.target.checked })}
        />
        <span className="text-sm">
          Enable auto-share
          {warning && spec.enabled && (
            <span className="ml-2 chip chip-warn text-[10px] inline-flex items-center gap-1">
              <AlertTriangle size={10} />
              v2 only
            </span>
          )}
        </span>
      </label>

      {spec.enabled && (
        <>
          <div>
            <span className="text-xs font-mono">Permission</span>
            <select
              className="input w-full mt-1 text-sm"
              value={spec.permission}
              onChange={(e) =>
                onChange({
                  ...spec,
                  permission: e.target.value as ProviderConfig["auto_share"]["permission"],
                })
              }
            >
              <option value="reader">Reader (view only)</option>
              <option value="writer">Writer (view + edit)</option>
              <option value="commenter">Commenter (view + comment)</option>
            </select>
          </div>

          <div>
            <span className="text-xs font-mono">Recipients</span>
            <div className="space-y-1.5 mt-1">
              {spec.recipients.map((r, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 border border-spark-border rounded px-2 py-1 bg-spark-bg/40"
                >
                  <Mail size={12} className="text-spark-muted shrink-0" />
                  <code className="text-xs flex-1">{r}</code>
                  <button
                    type="button"
                    className="text-spark-muted hover:text-spark-danger"
                    onClick={() =>
                      onChange({
                        ...spec,
                        recipients: spec.recipients.filter((_, j) => j !== i),
                      })
                    }
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
              <div className="flex items-center gap-2">
                <input
                  className="input flex-1 text-sm"
                  placeholder="operator@example.com"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addRecipient();
                    }
                  }}
                />
                <button
                  type="button"
                  className="btn text-xs"
                  onClick={addRecipient}
                  disabled={!draft.trim()}
                >
                  <Plus size={12} className="mr-1 inline" />
                  Add
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SetupSteps({ kind }: { kind: ProviderKind }) {
  const spec = PROVIDER_REGISTRY[kind];
  return (
    <details className="border border-spark-border rounded-md bg-spark-bg/30">
      <summary className="px-3 py-2 text-xs cursor-pointer text-spark-muted select-none">
        How to pair {spec.label}
      </summary>
      <ol className="px-3 py-2 border-t border-spark-border space-y-2">
        {spec.setup.map((step, i) => (
          <li key={i} className="flex gap-2">
            <span className="shrink-0 w-4 h-4 rounded-full bg-spark-border text-[10px] flex items-center justify-center font-bold mt-0.5">
              {i + 1}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold">{step.title}</div>
              {step.cmd && <CodeLine cmd={step.cmd} />}
              {step.note && (
                <div className="text-[11px] text-spark-muted mt-1">
                  {step.note}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}

function CodeLine({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Clipboard unavailable");
    }
  }
  return (
    <div className="flex items-center gap-2 bg-spark-bg/70 border border-spark-border rounded px-2 py-1 font-mono text-xs mt-1">
      <span className="text-spark-muted">$</span>
      <code className="flex-1 truncate">{cmd}</code>
      <button
        type="button"
        className="text-spark-muted hover:text-spark-text shrink-0"
        onClick={copy}
        aria-label="Copy command"
      >
        {copied ? (
          <Check size={12} className="text-spark-good" />
        ) : (
          <Copy size={12} />
        )}
      </button>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n < 1024 * 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  return `${(n / (1024 * 1024 * 1024 * 1024)).toFixed(1)} TB`;
}
