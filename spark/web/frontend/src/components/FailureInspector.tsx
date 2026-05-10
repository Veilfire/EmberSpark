/**
 * Failure Inspector — render a SparkError.to_dict() payload as a
 * "what gate, what element, how to tune, what risk" panel.
 *
 * Two variants:
 *   - "inline" — used beneath a failed turn in Chat or a span in
 *     Replay. Full layout: gate header chip, element table, tuning
 *     option list, risk tooltips.
 *   - "compact" — used in narrow rows (Audit log expand,
 *     NotificationBell drawer). One-row layout with the first
 *     tuning option as a button.
 *
 * The component is data-driven from the backend `tuning` field; the
 * frontend has no per-code switch statements.
 */

import { ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertCircle,
  ChevronRight,
  Info,
  ShieldAlert,
} from "lucide-react";

export type SparkErrorView = {
  code: string;
  message: string;
  detail?: Record<string, unknown> | null;
  remediation?: string | null;
  tuning?: TuningOption[] | null;
};

export type TuningOption = {
  label: string;
  description: string;
  risk: string;
  severity: "low" | "medium" | "high" | "critical";
  deep_link: string | null;
  prefill: Record<string, unknown> | null;
  audit_kind: string | null;
};

type Props = {
  error: SparkErrorView;
  context?: {
    agent_name?: string;
    plugin?: string;
    run_id?: string;
  };
  variant: "inline" | "compact";
};

// ---------------------------------------------------------------------------
// Code → human family + element-table copy.
// ---------------------------------------------------------------------------

const FAMILY_BY_PREFIX: Array<[string[], string, string]> = [
  // [code prefixes, family label, family icon-bg color hint]
  [["SPK_E_PLUGIN_NOT_ALLOWED", "SPK_E_PERMISSION_MISSING"], "Permission", "warn"],
  [["SPK_E_BUDGET_"], "Budget", "warn"],
  [["SPK_E_PATH_", "SPK_E_FILE_"], "Filesystem", "info"],
  [["SPK_E_URL_", "SPK_E_METHOD_NOT_ALLOWED", "SPK_E_RESPONSE_TOO_LARGE"], "Network", "info"],
  [["SPK_E_SANDBOX_"], "Sandbox", "warn"],
  [["SPK_E_DATA_CLASS_"], "Data class", "danger"],
  [["SPK_E_FROZEN", "SPK_E_APPROVAL_", "SPK_E_RUN_WINDOW_", "SPK_E_DLQ_"], "Lifecycle", "warn"],
  [["SPK_E_INPUT_SCHEMA_", "SPK_E_OUTPUT_SCHEMA_", "SPK_E_OPERATOR_OVERRIDE_"], "Validation", "info"],
  [["SPK_E_SECRET_"], "Secrets", "warn"],
  [["SPK_E_PLUGIN_RAISED"], "Plugin internal", "info"],
];

function familyForCode(code: string): { label: string; tone: string } {
  for (const [prefixes, label, tone] of FAMILY_BY_PREFIX) {
    if (prefixes.some((p) => code.startsWith(p) || code === p)) {
      return { label, tone };
    }
  }
  return { label: "Failure", tone: "info" };
}

const SEVERITY_CHIP: Record<TuningOption["severity"], string> = {
  low: "chip-good",
  medium: "chip-warn",
  high: "chip-danger",
  critical: "chip-danger",
};

const SEVERITY_LABEL: Record<TuningOption["severity"], string> = {
  low: "low risk",
  medium: "medium risk",
  high: "high risk",
  critical: "critical",
};

// Detail keys we hide from the operator (internal markers).
const INTERNAL_KEYS = new Set(["_message"]);

// ---------------------------------------------------------------------------
// Inline variant
// ---------------------------------------------------------------------------

function FailureInspectorInline({ error, context }: Omit<Props, "variant">) {
  const family = familyForCode(error.code);
  const tuning = error.tuning ?? [];
  const detailEntries = Object.entries(error.detail ?? {}).filter(
    ([k]) => !INTERNAL_KEYS.has(k),
  );

  return (
    <div className="border border-spark-border rounded-md bg-spark-panel/40 mt-2 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-spark-border bg-spark-panel">
        <ShieldAlert
          size={14}
          className={
            family.tone === "danger"
              ? "text-spark-danger"
              : family.tone === "warn"
                ? "text-spark-accent"
                : "text-spark-muted"
          }
        />
        <span className="text-xs font-semibold uppercase tracking-wide">
          {family.label}
        </span>
        <code className="ml-auto font-mono text-[10px] text-spark-muted">
          {error.code}
        </code>
      </div>

      <div className="px-3 py-2 space-y-3 text-sm">
        <p className="text-spark-text">{error.message}</p>

        {(detailEntries.length > 0 || context) && (
          <ElementTable
            entries={detailEntries}
            context={context ?? null}
          />
        )}

        {tuning.length > 0 && (
          <div>
            <div className="label mb-1.5">Tune</div>
            <div className="space-y-2">
              {tuning.map((opt, i) => (
                <TuningOptionCard key={i} option={opt} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compact variant — one row, first action only
// ---------------------------------------------------------------------------

function FailureInspectorCompact({ error }: Omit<Props, "variant">) {
  const family = familyForCode(error.code);
  const firstAction = (error.tuning ?? []).find((o) => o.deep_link);

  return (
    <div className="flex items-center gap-3 text-xs">
      <span
        className={`chip ${
          family.tone === "danger"
            ? "chip-danger"
            : family.tone === "warn"
              ? "chip-warn"
              : "chip-info"
        }`}
      >
        {family.label}
      </span>
      <code className="font-mono text-[10px] text-spark-muted">
        {error.code}
      </code>
      <span className="text-spark-text truncate flex-1">{error.message}</span>
      {firstAction && firstAction.deep_link && (
        <Link
          to={firstAction.deep_link}
          className="btn btn-ghost text-xs whitespace-nowrap"
        >
          {firstAction.label} <ChevronRight size={12} className="ml-1 inline" />
        </Link>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Element table — what triggered this gate
// ---------------------------------------------------------------------------

function ElementTable({
  entries,
  context,
}: {
  entries: [string, unknown][];
  context: Props["context"] | null;
}) {
  const ctxRows: [string, string][] = [];
  if (context?.agent_name) ctxRows.push(["agent", context.agent_name]);
  if (context?.plugin) ctxRows.push(["plugin", context.plugin]);

  if (ctxRows.length === 0 && entries.length === 0) return null;

  return (
    <div>
      <div className="label mb-1">Element</div>
      <table className="text-xs w-full">
        <tbody className="divide-y divide-spark-border/50">
          {ctxRows.map(([k, v]) => (
            <tr key={k}>
              <td className="py-1 pr-3 font-mono text-spark-muted w-32 align-top">
                {k}
              </td>
              <td className="py-1 break-all">{v}</td>
            </tr>
          ))}
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td className="py-1 pr-3 font-mono text-spark-muted w-32 align-top">
                {k}
              </td>
              <td className="py-1 break-all">
                {Array.isArray(v) ? (
                  v.length === 0 ? (
                    <span className="text-spark-muted">(empty)</span>
                  ) : (
                    v.map((x) => String(x)).join(", ")
                  )
                ) : v === null || v === undefined ? (
                  <span className="text-spark-muted">—</span>
                ) : typeof v === "object" ? (
                  <code className="font-mono text-[10px]">{JSON.stringify(v)}</code>
                ) : (
                  String(v)
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TuningOptionCard — one row per option
// ---------------------------------------------------------------------------

function TuningOptionCard({ option }: { option: TuningOption }) {
  const [showRisk, setShowRisk] = useState(false);
  const isAdvice = option.deep_link === null;

  return (
    <div
      className={`p-2.5 border rounded-md ${
        isAdvice
          ? "border-spark-border/60 bg-spark-bg/40"
          : "border-spark-border hover:border-spark-accent/40 transition-colors"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={isAdvice ? "text-spark-muted" : "font-medium"}
            >
              {option.label}
            </span>
            <span className={`chip ${SEVERITY_CHIP[option.severity]} text-[10px]`}>
              {SEVERITY_LABEL[option.severity]}
            </span>
          </div>
          <p className="text-xs text-spark-muted mt-1">{option.description}</p>
          <div className="mt-1.5 flex items-center gap-1.5 text-xs">
            <button
              onClick={() => setShowRisk((s) => !s)}
              className="btn-ghost btn-icon"
              aria-label={showRisk ? "Hide risk" : "Show risk"}
              title={option.risk}
            >
              <Info size={12} />
            </button>
            <span className="text-spark-muted">
              <strong>Risk:</strong>{" "}
              {showRisk ? option.risk : option.risk.split(".")[0] + "."}
            </span>
          </div>
        </div>
        {!isAdvice && option.deep_link && (
          <Link
            to={option.deep_link}
            className="btn btn-primary text-xs whitespace-nowrap"
          >
            Open <ChevronRight size={12} className="ml-1 inline" />
          </Link>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public dispatcher
// ---------------------------------------------------------------------------

export function FailureInspector(props: Props): ReactNode {
  if (!props.error || !props.error.code) {
    // Defensive: don't render an empty inspector if the WS frame had no
    // structured error. Caller is expected to feature-detect.
    return null;
  }
  if (props.variant === "compact") {
    return <FailureInspectorCompact error={props.error} context={props.context} />;
  }
  return <FailureInspectorInline error={props.error} context={props.context} />;
}

/** Best-effort SparkError feature detection — used by call sites that
 * receive `error` as `string | object`. */
export function isSparkError(value: unknown): value is SparkErrorView {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return typeof v.code === "string" && typeof v.message === "string" && v.code.startsWith("SPK_E_");
}

/** A small "Why?" toggle button for inline use beneath a thin error line. */
export function WhyToggle({
  open,
  onClick,
}: {
  open: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="btn-ghost text-[11px] inline-flex items-center gap-1 px-1.5 py-0.5"
      aria-expanded={open}
    >
      <AlertCircle size={11} />
      {open ? "Hide" : "Why?"}
    </button>
  );
}
