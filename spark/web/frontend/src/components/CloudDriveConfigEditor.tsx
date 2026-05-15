import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Cloud,
  Lock,
  Plus,
  RotateCcw,
  Save,
  Sliders,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { useSuggestedPrefill } from "../lib/prefill";
import {
  PROVIDER_REGISTRY,
  PROVIDER_KINDS,
  type ProviderKind,
  providerLabel,
} from "./cloud_drive/ProviderTypeRegistry";
import {
  ProviderCard,
  type ProviderConfig,
  type ProviderHealth,
} from "./cloud_drive/ProviderCard";
import { FileTypeBucketPicker } from "./cloud_drive/FileTypeBucketPicker";

interface PluginInfo {
  plugin_name: string;
  version: string;
  description: string;
  config: Record<string, unknown>;
  fresh: boolean;
}

interface DiscoveryResponse {
  ok: boolean;
  error?: string | null;
  error_code?: string | null;
  rclone_available?: boolean;
  providers?: Array<{
    name: string;
    kind: ProviderKind;
    enabled: boolean;
    ok: boolean;
    error?: string | null;
    free_bytes?: number | null;
    total_bytes?: number | null;
  }>;
}

const DEFAULT_MAX_FILE_BYTES = 52_428_800; // 50 MB

export function CloudDriveConfigEditor({ info }: { info: PluginInfo }) {
  const qc = useQueryClient();

  const [draft, setDraft] = useState<Record<string, unknown>>(() => ({
    ...info.config,
  }));
  const [reason, setReason] = useState("");
  const [discovery, setDiscovery] = useState<DiscoveryResponse | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showAddProvider, setShowAddProvider] = useState(false);
  const flashedRef = useRef<{
    providers: Set<string>;
    fields: Record<string, string>; // provider name -> field
    fileType?: string;
    readOnly?: boolean;
  }>({ providers: new Set(), fields: {} });

  const providers: ProviderConfig[] = useMemo(
    () => (Array.isArray(draft.providers) ? (draft.providers as ProviderConfig[]) : []),
    [draft.providers],
  );
  const readOnly = draft.read_only !== false;
  const maxFileBytes =
    typeof draft.max_file_bytes === "number"
      ? draft.max_file_bytes
      : DEFAULT_MAX_FILE_BYTES;
  const fileTypeAllowlist: string[] = Array.isArray(draft.file_type_allowlist)
    ? (draft.file_type_allowlist as string[])
    : [];

  // Failure-Inspector deep-link prefill.
  const [prefill, discardPrefill] = useSuggestedPrefill("plugin_allowlist_grant");
  const prefillMatchesUs = prefill && prefill.plugin === "cloud_drive";

  useEffect(() => {
    if (!prefillMatchesUs || !prefill) return;
    if (prefill.toggle === "read_only") {
      setDraft((d) => ({ ...d, read_only: false }));
      flashedRef.current.readOnly = true;
      return;
    }
    if (prefill.field === "providers" && prefill.add_item) {
      // Enable a named provider.
      const name = prefill.add_item;
      setDraft((d) => ({
        ...d,
        providers: (Array.isArray(d.providers) ? d.providers : []).map((p) =>
          (p as ProviderConfig).name === name ? { ...p, enabled: true } : p,
        ),
      }));
      flashedRef.current.providers.add(name);
      return;
    }
    if (prefill.field === "allowed_paths" && prefill.provider && prefill.add_item) {
      const provName = prefill.provider;
      const path = prefill.add_item;
      setDraft((d) => ({
        ...d,
        providers: (Array.isArray(d.providers) ? d.providers : []).map((p) => {
          const pp = p as ProviderConfig;
          if (pp.name !== provName) return p;
          if (pp.allowed_paths.includes(path)) return pp;
          return { ...pp, allowed_paths: [...pp.allowed_paths, path] };
        }),
      }));
      flashedRef.current.fields[provName] = "allowed_paths";
      return;
    }
    if (prefill.field === "file_type_allowlist" && prefill.add_item) {
      const ext = prefill.add_item.toLowerCase().replace(/^\./, "");
      setDraft((d) => {
        const cur = Array.isArray(d.file_type_allowlist)
          ? (d.file_type_allowlist as string[])
          : [];
        if (cur.includes(ext)) return d;
        return { ...d, file_type_allowlist: [...cur, ext] };
      });
      flashedRef.current.fileType = ext;
    }
  }, [prefillMatchesUs, prefill]);

  useEffect(() => {
    void runDiscover();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runDiscover() {
    setDiscovering(true);
    try {
      const r = await api.post<DiscoveryResponse>(
        "/api/plugin-config/cloud_drive/discover",
        {},
      );
      setDiscovery(r);
    } catch (e) {
      const err = e as { message?: string };
      toast.error(`Discovery failed: ${err.message ?? "unknown error"}`);
    } finally {
      setDiscovering(false);
    }
  }

  function updateProviderAt(idx: number, next: ProviderConfig) {
    const list = providers.slice();
    list[idx] = next;
    setDraft((d) => ({ ...d, providers: list }));
  }

  function removeProvider(idx: number) {
    const list = providers.slice();
    list.splice(idx, 1);
    setDraft((d) => ({ ...d, providers: list }));
  }

  function addProvider(kind: ProviderKind) {
    // Generate a unique slug from the kind.
    const base = kind === "drive" ? "gdrive" : kind;
    let candidate = base;
    let i = 1;
    const taken = new Set(providers.map((p) => p.name));
    while (taken.has(candidate)) {
      candidate = `${base}_${i}`;
      i += 1;
    }
    const newProvider: ProviderConfig = {
      name: candidate,
      enabled: true,
      auth: PROVIDER_REGISTRY[kind].defaultAuth as ProviderConfig["auth"],
      allowed_paths: [],
      auto_share: { enabled: false, recipients: [], permission: "reader" },
    };
    setDraft((d) => ({ ...d, providers: [...providers, newProvider] }));
    setShowAddProvider(false);
  }

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(info.config),
    [draft, info.config],
  );

  async function handleSave() {
    setSaving(true);
    try {
      const sanitized = Object.fromEntries(
        Object.entries(draft).filter(([, v]) => v !== null && v !== undefined),
      );
      await api.put(`/api/plugin-config/${info.plugin_name}`, {
        config: sanitized,
        reason: reason || "cloud_drive config update",
      });
      qc.invalidateQueries({ queryKey: ["plugins"] });
      toast.success(`${info.plugin_name} saved`);
      setReason("");
      discardPrefill();
      flashedRef.current = { providers: new Set(), fields: {} };
      void runDiscover();
    } catch (e) {
      const err = e as { message?: string };
      toast.error(`Save failed: ${err.message ?? "unknown error"}`);
    } finally {
      setSaving(false);
    }
  }

  const healthByName = useMemo(() => {
    const m = new Map<string, ProviderHealth>();
    for (const p of discovery?.providers ?? []) {
      m.set(p.name, {
        ok: p.ok,
        error: p.error,
        free_bytes: p.free_bytes,
        total_bytes: p.total_bytes,
      });
    }
    return m;
  }, [discovery]);

  const rcloneMissing =
    discovery && discovery.rclone_available === false && !discovery.ok;

  return (
    <div className="space-y-4">
      <header>
        <div className="flex items-center gap-2 flex-wrap">
          <Cloud size={18} className="text-spark-accent" />
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

      {/* Prefill banner */}
      {prefillMatchesUs && prefill && (
        <div className="panel p-3 border-amber-400/60 bg-amber-400/5 flex items-start gap-3">
          <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 text-sm">
            <strong>Suggested by failure inspector.</strong>{" "}
            {prefill.toggle ? (
              <>
                <code>{prefill.toggle}</code> staged to flip. Review and Save.
              </>
            ) : prefill.field === "providers" ? (
              <>
                Enabling <code>{prefill.add_item}</code>. Review the
                highlighted card and Save.
              </>
            ) : prefill.field === "allowed_paths" ? (
              <>
                Adding <code>{prefill.add_item}</code> to{" "}
                <code>{prefill.provider}</code>'s allowed_paths. Review and
                Save.
              </>
            ) : prefill.field === "file_type_allowlist" ? (
              <>
                Adding <code>.{prefill.add_item}</code> to the file-type
                allowlist. Review and Save.
              </>
            ) : null}
          </div>
          <button
            className="btn btn-ghost text-xs"
            onClick={() => {
              discardPrefill();
              setDraft({ ...info.config });
            }}
          >
            Discard
          </button>
        </div>
      )}

      {rcloneMissing && (
        <div className="panel p-3 border-spark-danger/60 bg-spark-danger/5 flex items-start gap-3">
          <AlertTriangle size={16} className="text-spark-danger shrink-0 mt-0.5" />
          <div className="flex-1 text-sm">
            <strong>rclone binary missing.</strong> The plugin needs{" "}
            <code>rclone</code> on <code>$PATH</code>. Install it in the Spark
            image (already baked in the default image — rebuild may be needed).
          </div>
        </div>
      )}

      {/* Global policy */}
      <section className="panel p-4 space-y-4">
        <div className="flex items-center gap-2">
          <Sliders size={14} className="text-spark-muted" />
          <span className="label">Global policy</span>
        </div>

        <label
          className={`flex items-start gap-3 p-2 rounded-md ${
            flashedRef.current.readOnly ? "ring-2 ring-amber-400/70" : ""
          }`}
        >
          <input
            type="checkbox"
            checked={readOnly}
            onChange={(e) =>
              setDraft((d) => ({ ...d, read_only: e.target.checked }))
            }
            className="mt-1"
          />
          <span className="text-sm">
            <Lock size={12} className="inline mr-1 text-spark-muted" />
            <strong>Read-only mode</strong>{" "}
            {readOnly ? (
              <span className="chip chip-good text-[10px] ml-1">on</span>
            ) : (
              <span className="chip chip-warn text-[10px] ml-1">off</span>
            )}
            <span className="block text-xs text-spark-muted mt-0.5">
              When on, blocks <code>put</code> / <code>delete</code> across all
              providers. Reads still work.
            </span>
          </span>
        </label>

        <div>
          <span className="text-sm">
            <strong>Max file size</strong>
            <span className="block text-xs text-spark-muted mt-0.5">
              Per-file cap on <code>get</code> / <code>put</code>. Larger files
              refused with <code>SPK_E_FILE_TOO_LARGE</code>.
            </span>
          </span>
          <div className="flex items-center gap-2 mt-1">
            <input
              className="input font-mono text-sm flex-1"
              type="number"
              min={1}
              value={maxFileBytes}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === "") return;
                const n = parseInt(raw, 10);
                if (!Number.isNaN(n))
                  setDraft((d) => ({ ...d, max_file_bytes: n }));
              }}
            />
            <span className="chip text-xs">{formatBytes(maxFileBytes)}</span>
          </div>
        </div>

        <div>
          <div className="text-sm mb-1">
            <strong>Allowed file types</strong>{" "}
            <span className="text-spark-muted text-xs">
              ({fileTypeAllowlist.length} extension{fileTypeAllowlist.length === 1 ? "" : "s"})
            </span>
          </div>
          <p className="text-xs text-spark-muted mb-2">
            Pick whole buckets, then drill in to toggle individual extensions.
            Files outside the allowlist refused on <code>get</code>/<code>put</code>.
          </p>
          <FileTypeBucketPicker
            value={fileTypeAllowlist}
            onChange={(next) =>
              setDraft((d) => ({ ...d, file_type_allowlist: next }))
            }
            flashedExtension={flashedRef.current.fileType}
          />
        </div>
      </section>

      {/* Providers */}
      <section className="panel p-4 space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Cloud size={14} className="text-spark-muted" />
            <span className="label">Providers</span>
            <span className="text-spark-muted text-[11px]">
              ({providers.filter((p) => p.enabled).length}/{providers.length} enabled)
            </span>
          </div>
          <div className="relative">
            <button
              type="button"
              className="btn btn-ghost text-xs"
              onClick={() => setShowAddProvider((v) => !v)}
            >
              <Plus size={12} className="mr-1.5 inline" />
              Add provider
            </button>
            {showAddProvider && (
              <div className="absolute right-0 top-full mt-1 panel p-1 z-10 min-w-[14rem] shadow-lg">
                {PROVIDER_KINDS.map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    className="block w-full text-left px-3 py-2 text-sm hover:bg-spark-border/50 rounded"
                    onClick={() => addProvider(kind)}
                  >
                    <div className="font-medium">{providerLabel(kind)}</div>
                    <div className="text-[11px] text-spark-muted">
                      {PROVIDER_REGISTRY[kind].blurb}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {providers.length === 0 ? (
          <div className="border border-dashed border-spark-border rounded p-6 text-center">
            <Cloud size={20} className="mx-auto text-spark-muted mb-2" />
            <div className="text-sm">No providers yet.</div>
            <div className="text-xs text-spark-muted mt-1">
              Click <strong>Add provider</strong> above to pick from Google
              Drive, OneDrive, Dropbox, or Proton Drive.
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {providers.map((p, i) => (
              <ProviderCard
                key={`${p.name}-${i}`}
                config={p}
                health={healthByName.get(p.name)}
                flashed={flashedRef.current.providers.has(p.name)}
                flashedField={flashedRef.current.fields[p.name]}
                onChange={(next) => updateProviderAt(i, next)}
                onRemove={() => removeProvider(i)}
                onTest={() => {
                  void runDiscover();
                }}
                testing={discovering}
              />
            ))}
          </div>
        )}
      </section>

      {/* Save row */}
      <section className="panel p-4 space-y-3">
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
                setDraft({ ...info.config });
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
      </section>
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
