import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Filter,
  KeyRound,
  Loader2,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  Wifi,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { Modal } from "../components/Modal";
import {
  FailureInspector,
  SparkErrorView,
} from "../components/FailureInspector";
import { useSuggestedPrefill } from "../lib/prefill";

/**
 * Live-introspection editor for the `home_assistant` plugin.
 *
 * Two design beats:
 *
 * 1. **Discovery-driven UI.** When the editor mounts (or the operator
 *    clicks "Test connection"), it calls
 *    `POST /api/plugin-config/home_assistant/discover` which hits HA's
 *    `/api/config`, `/api/services`, `/api/states` and returns the
 *    union: domains + services-per-domain + entities. The grids are
 *    populated from that — operators can't typo a domain name into
 *    existence.
 *
 * 2. **Failure Inspector deep-link prefill.** When a runtime call
 *    raises `SparkError(PERMISSION_MISSING, missing_domain="...")`,
 *    the inspector deep-links to `/plugins?plugin=home_assistant&prefill=...`.
 *    This editor reads the prefill on mount and ticks the matching
 *    checkbox / matrix cell with an amber ring + "Suggested by failure
 *    inspector" banner. Danger domains still require the typed-confirm
 *    before the change saves.
 */

// ---------------------------------------------------------------------------
// Discovery types — mirror spark/plugins/builtins/home_assistant.py
// ---------------------------------------------------------------------------

type Risk = "safe" | "elevated" | "danger";

interface DomainEntry {
  name: string;
  label: string;
  risk: Risk;
  entity_count: number;
}

interface ServiceEntry {
  name: string;
  risk: Risk;
  description: string | null;
}

interface EntityEntry {
  entity_id: string;
  domain: string;
  friendly_name: string | null;
  state: string | null;
}

interface Discovery {
  ok: boolean;
  error?: string | null;
  error_code?: string | null;
  error_detail?: Record<string, unknown> | null;
  domains: DomainEntry[];
  services_by_domain: Record<string, ServiceEntry[]>;
  entities: EntityEntry[];
  instance_url?: string | null;
  instance_version?: string | null;
}

// ---------------------------------------------------------------------------
// Config draft (mirror of HomeAssistantConfig in Python)
// ---------------------------------------------------------------------------

interface HaConfig {
  base_url: string;
  token_secret: string;
  read_only: boolean;
  allowed_domains: string[];
  allowed_services: Record<string, string[]>;
  entity_filter_glob: string[];
  verify_ssl: boolean;
  connect_timeout_seconds: number;
  read_timeout_seconds: number;
  max_response_bytes: number;
  max_states_returned: number;
}

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

interface PluginInfo {
  plugin_name: string;
  version: string;
  description: string;
  config: Record<string, unknown>;
  fresh: boolean;
}

interface Props {
  info: PluginInfo;
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

export function HomeAssistantConfigEditor({ info }: Props): JSX.Element {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<HaConfig>(() => readDraft(info.config));
  const [reason, setReason] = useState("");
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const [discoverError, setDiscoverError] = useState<SparkErrorView | null>(null);
  const [confirmFor, setConfirmFor] = useState<{
    kind: "domain" | "service";
    domain: string;
    service?: string;
    label: string;
  } | null>(null);
  const flashedRef = useRef<Record<string, boolean>>({});

  // Prefill from the Failure Inspector deep-links.
  const [grantPrefill, discardGrantPrefill] = useSuggestedPrefill(
    "home_assistant_grant",
  );

  // Run discovery once on mount when we have enough config to try.
  const discoverMutation = useMutation({
    mutationFn: async (): Promise<Discovery> =>
      api.post<Discovery>("/api/plugin-config/home_assistant/discover", {}),
    onSuccess: (r) => {
      if (!r.ok) {
        setDiscovery(null);
        const sparkErr: SparkErrorView | null = r.error_code
          ? {
              code: r.error_code,
              message: r.error || "Discovery failed",
              detail: (r.error_detail as Record<string, unknown>) ?? {},
              remediation: null,
              tuning: null,
            }
          : null;
        setDiscoverError(sparkErr);
      } else {
        setDiscovery(r);
        setDiscoverError(null);
      }
    },
    onError: (e: Error) => toast.error(`Discovery failed: ${e.message}`),
  });

  useEffect(() => {
    if (
      draft.base_url.trim().length > 0 &&
      draft.token_secret.trim().length > 0 &&
      discovery === null &&
      discoverError === null &&
      !discoverMutation.isPending
    ) {
      discoverMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apply prefill once we have discovery — danger domains need the
  // typed-confirm modal to actually flip the checkbox.
  useEffect(() => {
    if (!grantPrefill) return;
    if (grantPrefill.toggle === "read_only") {
      setDraft((d) => ({ ...d, read_only: false }));
      return;
    }
    if (grantPrefill.add_domain) {
      const dom = grantPrefill.add_domain;
      // Look up risk in discovery if available; danger → fire confirm modal.
      const risk =
        discovery?.domains.find((d) => d.name === dom)?.risk ??
        defaultRiskFor(dom);
      if (risk === "danger" && !draft.allowed_domains.includes(dom)) {
        setConfirmFor({ kind: "domain", domain: dom, label: dom });
      } else if (!draft.allowed_domains.includes(dom)) {
        setDraft((d) => ({
          ...d,
          allowed_domains: [...d.allowed_domains, dom],
        }));
      }
      flashedRef.current[`domain:${dom}`] = true;
      return;
    }
    if (grantPrefill.add_service) {
      const [dom, svc] = grantPrefill.add_service.split(".", 2);
      if (!dom || !svc) return;
      // Ensure the domain is allowed first (so the matrix cell isn't
      // greyed out). Danger domains still need confirm.
      const risk =
        discovery?.domains.find((d) => d.name === dom)?.risk ??
        defaultRiskFor(dom);
      if (risk === "danger" && !draft.allowed_domains.includes(dom)) {
        setConfirmFor({
          kind: "service",
          domain: dom,
          service: svc,
          label: `${dom}.${svc}`,
        });
      } else {
        setDraft((d) => {
          const services = { ...d.allowed_services };
          const cur = new Set(services[dom] ?? []);
          cur.add(svc);
          services[dom] = Array.from(cur).sort();
          return {
            ...d,
            allowed_domains: d.allowed_domains.includes(dom)
              ? d.allowed_domains
              : [...d.allowed_domains, dom],
            allowed_services: services,
          };
        });
      }
      flashedRef.current[`service:${dom}.${svc}`] = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grantPrefill, discovery]);

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(readDraft(info.config)),
    [draft, info.config],
  );

  const save = useMutation({
    mutationFn: async () =>
      api.put(`/api/plugin-config/${info.plugin_name}`, {
        config: serializeConfig(draft),
        reason,
      }),
    onSuccess: () => {
      toast.success(`${info.plugin_name} saved`);
      setReason("");
      qc.invalidateQueries({ queryKey: ["plugins"] });
      // Re-run discovery so the editor reflects any newly-allowed
      // domains' service rows.
      discoverMutation.mutate();
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  const groupedDomains = useMemo(
    () => groupDomains(discovery?.domains ?? []),
    [discovery],
  );

  return (
    <div className="panel p-4 space-y-5">
      <header>
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="font-bold text-lg font-mono">{info.plugin_name}</h3>
          <span className="chip text-xs">v{info.version}</span>
          {info.fresh && (
            <span className="chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30">
              operator-edited
            </span>
          )}
        </div>
        <p className="text-sm text-spark-muted mt-1">{info.description}</p>
      </header>

      {/* Connection */}
      <section className="space-y-3">
        <div className="label flex items-center gap-1.5">
          <Wifi size={12} /> Connection
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs text-spark-muted">Base URL</span>
            <input
              className="input w-full mt-1 font-mono text-sm"
              placeholder="http://ha.lan:8123"
              value={draft.base_url}
              onChange={(e) =>
                setDraft((d) => ({ ...d, base_url: e.target.value }))
              }
            />
          </label>
          <label className="block">
            <span className="text-xs text-spark-muted flex items-center gap-1">
              <KeyRound size={11} /> Token secret name
            </span>
            <input
              className="input w-full mt-1 font-mono text-sm"
              value={draft.token_secret}
              onChange={(e) =>
                setDraft((d) => ({ ...d, token_secret: e.target.value }))
              }
            />
            <span className="text-[10px] text-spark-muted mt-0.5 block">
              Run <code>spark secrets set {draft.token_secret}</code> first.
            </span>
          </label>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-xs flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.verify_ssl}
              onChange={(e) =>
                setDraft((d) => ({ ...d, verify_ssl: e.target.checked }))
              }
            />
            Verify SSL
          </label>
          <button
            className="btn btn-ghost text-xs ml-auto"
            disabled={discoverMutation.isPending}
            onClick={() => discoverMutation.mutate()}
          >
            {discoverMutation.isPending ? (
              <Loader2 size={12} className="animate-spin mr-1.5 inline" />
            ) : (
              <RefreshCw size={12} className="mr-1.5 inline" />
            )}
            {discovery ? "Re-discover" : "Test connection & discover"}
          </button>
        </div>
        {discovery?.ok && (
          <div className="flex items-center gap-2 text-xs text-spark-good">
            <CheckCircle2 size={14} />
            Connected to {discovery.instance_url} (HA{" "}
            <code>{discovery.instance_version}</code>) ·{" "}
            {discovery.domains.length} domains · {discovery.entities.length}{" "}
            entities
          </div>
        )}
        {discoverError && (
          <FailureInspector
            error={discoverError}
            variant="compact"
          />
        )}
      </section>

      {/* Suggestion banner */}
      {grantPrefill && (
        <div className="panel p-3 border-amber-400/60 bg-amber-400/5 flex items-start gap-3">
          <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 text-sm">
            <strong>Suggested by failure inspector.</strong>{" "}
            {grantPrefill.toggle === "read_only" ? (
              <>
                <code>read_only</code> staged off so call_service can fire.
              </>
            ) : grantPrefill.add_domain ? (
              <>
                Domain <code>{grantPrefill.add_domain}</code> staged for
                allow. Review the highlighted card and click Save.
              </>
            ) : grantPrefill.add_service ? (
              <>
                Service <code>{grantPrefill.add_service}</code> staged for
                allow. Review the highlighted matrix cell and click Save.
              </>
            ) : null}
          </div>
          <button
            className="btn btn-ghost text-xs"
            onClick={() => {
              discardGrantPrefill();
              // Roll back the staged change to whatever was saved.
              setDraft(readDraft(info.config));
            }}
          >
            Discard
          </button>
        </div>
      )}

      {/* Read-only toggle */}
      <section
        className={
          grantPrefill?.toggle === "read_only"
            ? "ring-2 ring-amber-400/70 rounded-md p-2 -m-2"
            : ""
        }
      >
        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={draft.read_only}
            onChange={(e) =>
              setDraft((d) => ({ ...d, read_only: e.target.checked }))
            }
          />
          <span className="text-sm">
            <strong>Read-only mode</strong>{" "}
            {draft.read_only ? (
              <span className="chip chip-good text-[10px] ml-1">on</span>
            ) : (
              <span className="chip chip-danger text-[10px] ml-1">
                off — services callable
              </span>
            )}
            <span className="block text-xs text-spark-muted mt-0.5">
              When on, the agent can read states and render templates but
              cannot call services. The per-service allowlist still applies
              when off.
            </span>
          </span>
        </label>
      </section>

      {/* Domain grid */}
      <section>
        <div className="label mb-2 flex items-center gap-2">
          <Filter size={12} /> Allowed domains
          <span className="text-spark-muted text-[11px] normal-case font-normal">
            ({draft.allowed_domains.length} selected)
          </span>
        </div>
        {!discovery?.ok ? (
          <p className="text-sm text-spark-muted">
            Run discovery to populate the domain list from your HA
            instance.
          </p>
        ) : (
          <div className="space-y-3">
            {Object.entries(groupedDomains).map(([groupLabel, domains]) => (
              <div key={groupLabel}>
                <div className="text-[11px] uppercase tracking-wide text-spark-muted mb-1.5">
                  {groupLabel}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                  {domains.map((d) => {
                    const checked = draft.allowed_domains.includes(d.name);
                    const flashed = flashedRef.current[`domain:${d.name}`];
                    return (
                      <label
                        key={d.name}
                        className={`flex items-center gap-2 px-2 py-1.5 border rounded-md text-sm cursor-pointer hover:border-spark-accent/40 transition-colors ${
                          checked
                            ? "border-spark-border bg-spark-bg/30"
                            : "border-spark-border"
                        } ${flashed ? "ring-2 ring-amber-400/70" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => {
                            if (!checked && d.risk === "danger") {
                              setConfirmFor({
                                kind: "domain",
                                domain: d.name,
                                label: d.name,
                              });
                              return;
                            }
                            toggleDomain(setDraft, d.name);
                          }}
                        />
                        <code className="font-mono text-xs flex-1 truncate">
                          {d.name}
                        </code>
                        {d.entity_count > 0 && (
                          <span className="text-[10px] text-spark-muted">
                            {d.entity_count}
                          </span>
                        )}
                        <span
                          className={`chip ${RISK_CHIP[d.risk]} text-[9px]`}
                        >
                          {RISK_LABEL[d.risk]}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Service matrix */}
      <section>
        <div className="label mb-2">
          Allowed services{" "}
          <span className="text-spark-muted text-[11px] normal-case font-normal">
            (per-domain matrix)
          </span>
        </div>
        {!discovery?.ok ? (
          <p className="text-sm text-spark-muted">
            Run discovery to populate the service matrix.
          </p>
        ) : draft.allowed_domains.length === 0 ? (
          <p className="text-sm text-spark-muted">
            Allow at least one domain above to see its services.
          </p>
        ) : (
          <div className="space-y-2">
            {draft.allowed_domains.map((dom) => {
              const services = discovery.services_by_domain[dom] ?? [];
              if (services.length === 0) return null;
              const allowedSet = new Set(draft.allowed_services[dom] ?? []);
              return (
                <div key={dom} className="border border-spark-border rounded-md p-2">
                  <div className="flex items-center gap-2 mb-1.5">
                    <code className="font-mono text-xs font-semibold">
                      {dom}
                    </code>
                    <span className="text-spark-muted text-[10px]">
                      {allowedSet.size}/{services.length} allowed
                    </span>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5">
                    {services.map((s) => {
                      const cellKey = `service:${dom}.${s.name}`;
                      const checked = allowedSet.has(s.name);
                      const flashed = flashedRef.current[cellKey];
                      return (
                        <label
                          key={s.name}
                          className={`flex items-center gap-2 px-2 py-1 border rounded text-xs cursor-pointer hover:border-spark-accent/40 transition-colors ${
                            checked
                              ? "border-spark-border bg-spark-bg/30"
                              : "border-spark-border"
                          } ${flashed ? "ring-2 ring-amber-400/70" : ""}`}
                          title={s.description ?? undefined}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              if (!checked && s.risk === "danger") {
                                setConfirmFor({
                                  kind: "service",
                                  domain: dom,
                                  service: s.name,
                                  label: `${dom}.${s.name}`,
                                });
                                return;
                              }
                              toggleService(setDraft, dom, s.name);
                            }}
                          />
                          <code className="font-mono flex-1 truncate">
                            {s.name}
                          </code>
                          {s.risk !== "safe" && (
                            <span
                              className={`chip ${RISK_CHIP[s.risk]} text-[9px]`}
                            >
                              {s.risk[0].toUpperCase()}
                            </span>
                          )}
                        </label>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Entity excludes */}
      <section>
        <div className="label mb-2">
          Entity excludes (glob patterns)
          <span className="text-spark-muted text-[11px] normal-case font-normal ml-1.5">
            ({draft.entity_filter_glob.length} active)
          </span>
        </div>
        <p className="text-xs text-spark-muted mb-2">
          Defense in depth on top of `allowed_domains`. Use{" "}
          <code className="font-mono">device_tracker.*</code>-style patterns
          to exclude an entire domain even when it's allowed; or pick
          specific entities below.
        </p>
        <div className="flex flex-wrap gap-1.5 mb-2">
          {draft.entity_filter_glob.map((g, i) => (
            <span
              key={i}
              className="chip chip-warn text-[10px] flex items-center gap-1"
            >
              <code className="font-mono">{g}</code>
              <button
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    entity_filter_glob: d.entity_filter_glob.filter(
                      (_, j) => j !== i,
                    ),
                  }))
                }
                aria-label="Remove"
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
        <GlobAdd
          discovery={discovery}
          existing={draft.entity_filter_glob}
          onAdd={(g) =>
            setDraft((d) => ({
              ...d,
              entity_filter_glob: [...d.entity_filter_glob, g],
            }))
          }
        />
      </section>

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
                setDraft(readDraft(info.config));
                setReason("");
                discardGrantPrefill();
              }}
            >
              <RotateCcw size={13} className="mr-1.5 inline" />
              Discard
            </button>
            <button
              className="btn btn-primary"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
            >
              <Save size={13} className="mr-1.5 inline" />
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>

      {confirmFor && (
        <DangerConfirmModal
          target={confirmFor}
          onCancel={() => setConfirmFor(null)}
          onConfirm={() => {
            if (confirmFor.kind === "domain") {
              toggleDomain(setDraft, confirmFor.domain);
            } else if (confirmFor.kind === "service" && confirmFor.service) {
              setDraft((d) => {
                const services = { ...d.allowed_services };
                const cur = new Set(services[confirmFor.domain] ?? []);
                cur.add(confirmFor.service!);
                services[confirmFor.domain] = Array.from(cur).sort();
                return {
                  ...d,
                  allowed_domains: d.allowed_domains.includes(confirmFor.domain)
                    ? d.allowed_domains
                    : [...d.allowed_domains, confirmFor.domain],
                  allowed_services: services,
                };
              });
            }
            setConfirmFor(null);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function GlobAdd({
  discovery,
  existing,
  onAdd,
}: {
  discovery: Discovery | null;
  existing: string[];
  onAdd: (g: string) => void;
}) {
  const [value, setValue] = useState("");
  const suggestions = useMemo(() => {
    if (!discovery?.entities || !value) return [];
    const v = value.toLowerCase();
    const out = new Set<string>();
    for (const e of discovery.entities) {
      if (existing.includes(e.entity_id)) continue;
      if (
        e.entity_id.toLowerCase().includes(v) ||
        (e.friendly_name ?? "").toLowerCase().includes(v)
      ) {
        out.add(e.entity_id);
        if (out.size >= 8) break;
      }
    }
    return Array.from(out);
  }, [discovery, value, existing]);

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <input
          className="input flex-1 font-mono text-xs"
          placeholder="device_tracker.* or sensor.power_meter"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && value.trim()) {
              onAdd(value.trim());
              setValue("");
            }
          }}
        />
        <button
          className="btn btn-ghost text-xs"
          disabled={!value.trim()}
          onClick={() => {
            onAdd(value.trim());
            setValue("");
          }}
        >
          Add
        </button>
      </div>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {suggestions.map((s) => (
            <button
              key={s}
              className="chip text-[10px] hover:chip-warn"
              onClick={() => {
                onAdd(s);
                setValue("");
              }}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function DangerConfirmModal({
  target,
  onCancel,
  onConfirm,
}: {
  target: { kind: "domain" | "service"; label: string };
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === target.label;
  return (
    <Modal open onClose={onCancel}>
      <div className="panel p-5 max-w-md">
        <div className="flex items-start gap-3">
          <ShieldAlert size={20} className="text-spark-danger shrink-0 mt-0.5" />
          <div className="flex-1">
            <h4 className="font-bold">Allow {target.kind}?</h4>
            <p className="text-sm text-spark-muted mt-1">
              {target.kind === "domain"
                ? `Allowing the "${target.label}" domain lets the agent see (and potentially act on) every entity in that domain. High-risk domains include locks, alarms, cameras, and location data.`
                : `Allowing "${target.label}" lets the agent call this mutating service. Pair with a tight scope where possible.`}
            </p>
            <p className="text-xs text-spark-muted mt-3">
              Type{" "}
              <code className="font-mono">{target.label}</code> to confirm:
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
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DEFAULT_DOMAINS = [
  "light",
  "switch",
  "sensor",
  "binary_sensor",
  "media_player",
  "climate",
  "weather",
  "fan",
  "scene",
  "input_boolean",
  "cover",
  "script",
];

function readDraft(cfg: Record<string, unknown>): HaConfig {
  return {
    base_url: typeof cfg.base_url === "string" ? cfg.base_url : "",
    token_secret:
      typeof cfg.token_secret === "string"
        ? cfg.token_secret
        : "home_assistant_token",
    read_only: cfg.read_only === false ? false : true,
    allowed_domains: Array.isArray(cfg.allowed_domains)
      ? (cfg.allowed_domains as string[])
      : DEFAULT_DOMAINS,
    allowed_services:
      cfg.allowed_services && typeof cfg.allowed_services === "object"
        ? (cfg.allowed_services as Record<string, string[]>)
        : {},
    entity_filter_glob: Array.isArray(cfg.entity_filter_glob)
      ? (cfg.entity_filter_glob as string[])
      : [],
    verify_ssl: cfg.verify_ssl === false ? false : true,
    connect_timeout_seconds:
      typeof cfg.connect_timeout_seconds === "number"
        ? cfg.connect_timeout_seconds
        : 5.0,
    read_timeout_seconds:
      typeof cfg.read_timeout_seconds === "number"
        ? cfg.read_timeout_seconds
        : 15.0,
    max_response_bytes:
      typeof cfg.max_response_bytes === "number"
        ? cfg.max_response_bytes
        : 1_048_576,
    max_states_returned:
      typeof cfg.max_states_returned === "number"
        ? cfg.max_states_returned
        : 200,
  };
}

function serializeConfig(d: HaConfig): Record<string, unknown> {
  return { ...d };
}

const DANGER_DOMAINS = new Set([
  "lock",
  "alarm_control_panel",
  "camera",
  "device_tracker",
  "person",
  "vacuum",
]);

const ELEVATED_DOMAINS = new Set([
  "cover",
  "script",
  "automation",
  "media_player",
]);

function defaultRiskFor(domain: string): Risk {
  if (DANGER_DOMAINS.has(domain)) return "danger";
  if (ELEVATED_DOMAINS.has(domain)) return "elevated";
  return "safe";
}

function groupDomains(domains: DomainEntry[]): Record<string, DomainEntry[]> {
  const groups: Record<string, DomainEntry[]> = {
    "Lights & switches": [],
    "Sensors": [],
    "Media": [],
    "Climate": [],
    "Security & access": [],
    "Location & people": [],
    "Other": [],
  };
  for (const d of domains) {
    if (["light", "switch", "input_boolean", "fan"].includes(d.name)) {
      groups["Lights & switches"].push(d);
    } else if (["sensor", "binary_sensor", "weather"].includes(d.name)) {
      groups["Sensors"].push(d);
    } else if (["media_player", "remote", "tv"].includes(d.name)) {
      groups["Media"].push(d);
    } else if (["climate", "humidifier", "fan"].includes(d.name)) {
      groups["Climate"].push(d);
    } else if (
      ["lock", "alarm_control_panel", "camera", "cover"].includes(d.name)
    ) {
      groups["Security & access"].push(d);
    } else if (["device_tracker", "person", "zone"].includes(d.name)) {
      groups["Location & people"].push(d);
    } else {
      groups["Other"].push(d);
    }
  }
  // Drop empty groups so the editor doesn't render orphan headers.
  return Object.fromEntries(Object.entries(groups).filter(([, v]) => v.length > 0));
}

function toggleDomain(
  setDraft: React.Dispatch<React.SetStateAction<HaConfig>>,
  domain: string,
) {
  setDraft((d) => {
    if (d.allowed_domains.includes(domain)) {
      const services = { ...d.allowed_services };
      delete services[domain];
      return {
        ...d,
        allowed_domains: d.allowed_domains.filter((x) => x !== domain),
        allowed_services: services,
      };
    }
    return {
      ...d,
      allowed_domains: [...d.allowed_domains, domain],
    };
  });
}

function toggleService(
  setDraft: React.Dispatch<React.SetStateAction<HaConfig>>,
  domain: string,
  service: string,
) {
  setDraft((d) => {
    const services = { ...d.allowed_services };
    const cur = new Set(services[domain] ?? []);
    if (cur.has(service)) cur.delete(service);
    else cur.add(service);
    services[domain] = Array.from(cur).sort();
    if (services[domain].length === 0) delete services[domain];
    return { ...d, allowed_services: services };
  });
}
