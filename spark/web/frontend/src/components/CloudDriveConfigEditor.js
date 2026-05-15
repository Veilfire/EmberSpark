import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Cloud, Lock, Plus, RotateCcw, Save, Sliders, } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { useSuggestedPrefill } from "../lib/prefill";
import { PROVIDER_REGISTRY, PROVIDER_KINDS, providerLabel, } from "./cloud_drive/ProviderTypeRegistry";
import { ProviderCard, } from "./cloud_drive/ProviderCard";
import { FileTypeBucketPicker } from "./cloud_drive/FileTypeBucketPicker";
const DEFAULT_MAX_FILE_BYTES = 52_428_800; // 50 MB
export function CloudDriveConfigEditor({ info }) {
    const qc = useQueryClient();
    const [draft, setDraft] = useState(() => ({
        ...info.config,
    }));
    const [reason, setReason] = useState("");
    const [discovery, setDiscovery] = useState(null);
    const [discovering, setDiscovering] = useState(false);
    const [saving, setSaving] = useState(false);
    const [showAddProvider, setShowAddProvider] = useState(false);
    const flashedRef = useRef({ providers: new Set(), fields: {} });
    const providers = useMemo(() => (Array.isArray(draft.providers) ? draft.providers : []), [draft.providers]);
    const readOnly = draft.read_only !== false;
    const maxFileBytes = typeof draft.max_file_bytes === "number"
        ? draft.max_file_bytes
        : DEFAULT_MAX_FILE_BYTES;
    const fileTypeAllowlist = Array.isArray(draft.file_type_allowlist)
        ? draft.file_type_allowlist
        : [];
    // Failure-Inspector deep-link prefill.
    const [prefill, discardPrefill] = useSuggestedPrefill("plugin_allowlist_grant");
    const prefillMatchesUs = prefill && prefill.plugin === "cloud_drive";
    useEffect(() => {
        if (!prefillMatchesUs || !prefill)
            return;
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
                providers: (Array.isArray(d.providers) ? d.providers : []).map((p) => p.name === name ? { ...p, enabled: true } : p),
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
                    const pp = p;
                    if (pp.name !== provName)
                        return p;
                    if (pp.allowed_paths.includes(path))
                        return pp;
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
                    ? d.file_type_allowlist
                    : [];
                if (cur.includes(ext))
                    return d;
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
            const r = await api.post("/api/plugin-config/cloud_drive/discover", {});
            setDiscovery(r);
        }
        catch (e) {
            const err = e;
            toast.error(`Discovery failed: ${err.message ?? "unknown error"}`);
        }
        finally {
            setDiscovering(false);
        }
    }
    function updateProviderAt(idx, next) {
        const list = providers.slice();
        list[idx] = next;
        setDraft((d) => ({ ...d, providers: list }));
    }
    function removeProvider(idx) {
        const list = providers.slice();
        list.splice(idx, 1);
        setDraft((d) => ({ ...d, providers: list }));
    }
    function addProvider(kind) {
        // Generate a unique slug from the kind.
        const base = kind === "drive" ? "gdrive" : kind;
        let candidate = base;
        let i = 1;
        const taken = new Set(providers.map((p) => p.name));
        while (taken.has(candidate)) {
            candidate = `${base}_${i}`;
            i += 1;
        }
        const newProvider = {
            name: candidate,
            enabled: true,
            auth: PROVIDER_REGISTRY[kind].defaultAuth,
            allowed_paths: [],
            auto_share: { enabled: false, recipients: [], permission: "reader" },
        };
        setDraft((d) => ({ ...d, providers: [...providers, newProvider] }));
        setShowAddProvider(false);
    }
    const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(info.config), [draft, info.config]);
    async function handleSave() {
        setSaving(true);
        try {
            const sanitized = Object.fromEntries(Object.entries(draft).filter(([, v]) => v !== null && v !== undefined));
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
        }
        catch (e) {
            const err = e;
            toast.error(`Save failed: ${err.message ?? "unknown error"}`);
        }
        finally {
            setSaving(false);
        }
    }
    const healthByName = useMemo(() => {
        const m = new Map();
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
    const rcloneMissing = discovery && discovery.rclone_available === false && !discovery.ok;
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx(Cloud, { size: 18, className: "text-spark-accent" }), _jsx("h3", { className: "font-bold text-lg font-mono", children: info.plugin_name }), _jsxs("span", { className: "chip text-xs", children: ["v", info.version] }), info.fresh && (_jsx("span", { className: "chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30", children: "operator-edited" }))] }), _jsx("p", { className: "text-sm text-spark-muted mt-1", children: info.description })] }), prefillMatchesUs && prefill && (_jsxs("div", { className: "panel p-3 border-amber-400/60 bg-amber-400/5 flex items-start gap-3", children: [_jsx(AlertTriangle, { size: 16, className: "text-amber-400 shrink-0 mt-0.5" }), _jsxs("div", { className: "flex-1 text-sm", children: [_jsx("strong", { children: "Suggested by failure inspector." }), " ", prefill.toggle ? (_jsxs(_Fragment, { children: [_jsx("code", { children: prefill.toggle }), " staged to flip. Review and Save."] })) : prefill.field === "providers" ? (_jsxs(_Fragment, { children: ["Enabling ", _jsx("code", { children: prefill.add_item }), ". Review the highlighted card and Save."] })) : prefill.field === "allowed_paths" ? (_jsxs(_Fragment, { children: ["Adding ", _jsx("code", { children: prefill.add_item }), " to", " ", _jsx("code", { children: prefill.provider }), "'s allowed_paths. Review and Save."] })) : prefill.field === "file_type_allowlist" ? (_jsxs(_Fragment, { children: ["Adding ", _jsxs("code", { children: [".", prefill.add_item] }), " to the file-type allowlist. Review and Save."] })) : null] }), _jsx("button", { className: "btn btn-ghost text-xs", onClick: () => {
                            discardPrefill();
                            setDraft({ ...info.config });
                        }, children: "Discard" })] })), rcloneMissing && (_jsxs("div", { className: "panel p-3 border-spark-danger/60 bg-spark-danger/5 flex items-start gap-3", children: [_jsx(AlertTriangle, { size: 16, className: "text-spark-danger shrink-0 mt-0.5" }), _jsxs("div", { className: "flex-1 text-sm", children: [_jsx("strong", { children: "rclone binary missing." }), " The plugin needs", " ", _jsx("code", { children: "rclone" }), " on ", _jsx("code", { children: "$PATH" }), ". Install it in the Spark image (already baked in the default image \u2014 rebuild may be needed)."] })] })), _jsxs("section", { className: "panel p-4 space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Sliders, { size: 14, className: "text-spark-muted" }), _jsx("span", { className: "label", children: "Global policy" })] }), _jsxs("label", { className: `flex items-start gap-3 p-2 rounded-md ${flashedRef.current.readOnly ? "ring-2 ring-amber-400/70" : ""}`, children: [_jsx("input", { type: "checkbox", checked: readOnly, onChange: (e) => setDraft((d) => ({ ...d, read_only: e.target.checked })), className: "mt-1" }), _jsxs("span", { className: "text-sm", children: [_jsx(Lock, { size: 12, className: "inline mr-1 text-spark-muted" }), _jsx("strong", { children: "Read-only mode" }), " ", readOnly ? (_jsx("span", { className: "chip chip-good text-[10px] ml-1", children: "on" })) : (_jsx("span", { className: "chip chip-warn text-[10px] ml-1", children: "off" })), _jsxs("span", { className: "block text-xs text-spark-muted mt-0.5", children: ["When on, blocks ", _jsx("code", { children: "put" }), " / ", _jsx("code", { children: "delete" }), " across all providers. Reads still work."] })] })] }), _jsxs("div", { children: [_jsxs("span", { className: "text-sm", children: [_jsx("strong", { children: "Max file size" }), _jsxs("span", { className: "block text-xs text-spark-muted mt-0.5", children: ["Per-file cap on ", _jsx("code", { children: "get" }), " / ", _jsx("code", { children: "put" }), ". Larger files refused with ", _jsx("code", { children: "SPK_E_FILE_TOO_LARGE" }), "."] })] }), _jsxs("div", { className: "flex items-center gap-2 mt-1", children: [_jsx("input", { className: "input font-mono text-sm flex-1", type: "number", min: 1, value: maxFileBytes, onChange: (e) => {
                                            const raw = e.target.value;
                                            if (raw === "")
                                                return;
                                            const n = parseInt(raw, 10);
                                            if (!Number.isNaN(n))
                                                setDraft((d) => ({ ...d, max_file_bytes: n }));
                                        } }), _jsx("span", { className: "chip text-xs", children: formatBytes(maxFileBytes) })] })] }), _jsxs("div", { children: [_jsxs("div", { className: "text-sm mb-1", children: [_jsx("strong", { children: "Allowed file types" }), " ", _jsxs("span", { className: "text-spark-muted text-xs", children: ["(", fileTypeAllowlist.length, " extension", fileTypeAllowlist.length === 1 ? "" : "s", ")"] })] }), _jsxs("p", { className: "text-xs text-spark-muted mb-2", children: ["Pick whole buckets, then drill in to toggle individual extensions. Files outside the allowlist refused on ", _jsx("code", { children: "get" }), "/", _jsx("code", { children: "put" }), "."] }), _jsx(FileTypeBucketPicker, { value: fileTypeAllowlist, onChange: (next) => setDraft((d) => ({ ...d, file_type_allowlist: next })), flashedExtension: flashedRef.current.fileType })] })] }), _jsxs("section", { className: "panel p-4 space-y-3", children: [_jsxs("div", { className: "flex items-center justify-between flex-wrap gap-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Cloud, { size: 14, className: "text-spark-muted" }), _jsx("span", { className: "label", children: "Providers" }), _jsxs("span", { className: "text-spark-muted text-[11px]", children: ["(", providers.filter((p) => p.enabled).length, "/", providers.length, " enabled)"] })] }), _jsxs("div", { className: "relative", children: [_jsxs("button", { type: "button", className: "btn btn-ghost text-xs", onClick: () => setShowAddProvider((v) => !v), children: [_jsx(Plus, { size: 12, className: "mr-1.5 inline" }), "Add provider"] }), showAddProvider && (_jsx("div", { className: "absolute right-0 top-full mt-1 panel p-1 z-10 min-w-[14rem] shadow-lg", children: PROVIDER_KINDS.map((kind) => (_jsxs("button", { type: "button", className: "block w-full text-left px-3 py-2 text-sm hover:bg-spark-border/50 rounded", onClick: () => addProvider(kind), children: [_jsx("div", { className: "font-medium", children: providerLabel(kind) }), _jsx("div", { className: "text-[11px] text-spark-muted", children: PROVIDER_REGISTRY[kind].blurb })] }, kind))) }))] })] }), providers.length === 0 ? (_jsxs("div", { className: "border border-dashed border-spark-border rounded p-6 text-center", children: [_jsx(Cloud, { size: 20, className: "mx-auto text-spark-muted mb-2" }), _jsx("div", { className: "text-sm", children: "No providers yet." }), _jsxs("div", { className: "text-xs text-spark-muted mt-1", children: ["Click ", _jsx("strong", { children: "Add provider" }), " above to pick from Google Drive, OneDrive, Dropbox, or Proton Drive."] })] })) : (_jsx("div", { className: "space-y-2", children: providers.map((p, i) => (_jsx(ProviderCard, { config: p, health: healthByName.get(p.name), flashed: flashedRef.current.providers.has(p.name), flashedField: flashedRef.current.fields[p.name], onChange: (next) => updateProviderAt(i, next), onRemove: () => removeProvider(i), onTest: () => {
                                void runDiscover();
                            }, testing: discovering }, `${p.name}-${i}`))) }))] }), _jsxs("section", { className: "panel p-4 space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Reason (audited)" }), _jsx("input", { className: "input w-full", placeholder: "why are you changing this?", value: reason, onChange: (e) => setReason(e.target.value) })] }), _jsxs("div", { className: "flex items-center justify-between", children: [_jsx("div", { className: "text-xs text-spark-muted", children: dirty ? "Unsaved changes" : "In sync with stored config" }), _jsxs("div", { className: "flex gap-2", children: [_jsxs("button", { className: "btn", disabled: !dirty, onClick: () => {
                                            setDraft({ ...info.config });
                                            setReason("");
                                            discardPrefill();
                                        }, children: [_jsx(RotateCcw, { size: 13, className: "mr-1.5 inline" }), "Discard"] }), _jsxs("button", { className: "btn btn-primary", disabled: !dirty || saving, onClick: handleSave, children: [_jsx(Save, { size: 13, className: "mr-1.5 inline" }), saving ? "Saving…" : "Save"] })] })] })] })] }));
}
function formatBytes(n) {
    if (n < 1024)
        return `${n} B`;
    if (n < 1024 * 1024)
        return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024)
        return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (n < 1024 * 1024 * 1024 * 1024)
        return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    return `${(n / (1024 * 1024 * 1024 * 1024)).toFixed(1)} TB`;
}
