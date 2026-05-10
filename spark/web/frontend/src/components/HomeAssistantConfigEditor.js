import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Filter, KeyRound, Loader2, RefreshCw, RotateCcw, Save, ShieldAlert, Wifi, X, } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { Modal } from "../components/Modal";
import { FailureInspector, } from "../components/FailureInspector";
import { useSuggestedPrefill } from "../lib/prefill";
const RISK_CHIP = {
    safe: "chip-good",
    elevated: "chip-warn",
    danger: "chip-danger",
};
const RISK_LABEL = {
    safe: "safe",
    elevated: "elevated",
    danger: "danger",
};
// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------
export function HomeAssistantConfigEditor({ info }) {
    const qc = useQueryClient();
    const [draft, setDraft] = useState(() => readDraft(info.config));
    const [reason, setReason] = useState("");
    const [discovery, setDiscovery] = useState(null);
    const [discoverError, setDiscoverError] = useState(null);
    const [confirmFor, setConfirmFor] = useState(null);
    const flashedRef = useRef({});
    // Prefill from the Failure Inspector deep-links.
    const [grantPrefill, discardGrantPrefill] = useSuggestedPrefill("home_assistant_grant");
    // Run discovery once on mount when we have enough config to try.
    const discoverMutation = useMutation({
        mutationFn: async () => api.post("/api/plugin-config/home_assistant/discover", {}),
        onSuccess: (r) => {
            if (!r.ok) {
                setDiscovery(null);
                const sparkErr = r.error_code
                    ? {
                        code: r.error_code,
                        message: r.error || "Discovery failed",
                        detail: r.error_detail ?? {},
                        remediation: null,
                        tuning: null,
                    }
                    : null;
                setDiscoverError(sparkErr);
            }
            else {
                setDiscovery(r);
                setDiscoverError(null);
            }
        },
        onError: (e) => toast.error(`Discovery failed: ${e.message}`),
    });
    useEffect(() => {
        if (draft.base_url.trim().length > 0 &&
            draft.token_secret.trim().length > 0 &&
            discovery === null &&
            discoverError === null &&
            !discoverMutation.isPending) {
            discoverMutation.mutate();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    // Apply prefill once we have discovery — danger domains need the
    // typed-confirm modal to actually flip the checkbox.
    useEffect(() => {
        if (!grantPrefill)
            return;
        if (grantPrefill.toggle === "read_only") {
            setDraft((d) => ({ ...d, read_only: false }));
            return;
        }
        if (grantPrefill.add_domain) {
            const dom = grantPrefill.add_domain;
            // Look up risk in discovery if available; danger → fire confirm modal.
            const risk = discovery?.domains.find((d) => d.name === dom)?.risk ??
                defaultRiskFor(dom);
            if (risk === "danger" && !draft.allowed_domains.includes(dom)) {
                setConfirmFor({ kind: "domain", domain: dom, label: dom });
            }
            else if (!draft.allowed_domains.includes(dom)) {
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
            if (!dom || !svc)
                return;
            // Ensure the domain is allowed first (so the matrix cell isn't
            // greyed out). Danger domains still need confirm.
            const risk = discovery?.domains.find((d) => d.name === dom)?.risk ??
                defaultRiskFor(dom);
            if (risk === "danger" && !draft.allowed_domains.includes(dom)) {
                setConfirmFor({
                    kind: "service",
                    domain: dom,
                    service: svc,
                    label: `${dom}.${svc}`,
                });
            }
            else {
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
    const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(readDraft(info.config)), [draft, info.config]);
    const save = useMutation({
        mutationFn: async () => api.put(`/api/plugin-config/${info.plugin_name}`, {
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
        onError: (e) => toast.error(`Save failed: ${e.message}`),
    });
    const groupedDomains = useMemo(() => groupDomains(discovery?.domains ?? []), [discovery]);
    return (_jsxs("div", { className: "panel p-4 space-y-5", children: [_jsxs("header", { children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("h3", { className: "font-bold text-lg font-mono", children: info.plugin_name }), _jsxs("span", { className: "chip text-xs", children: ["v", info.version] }), info.fresh && (_jsx("span", { className: "chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30", children: "operator-edited" }))] }), _jsx("p", { className: "text-sm text-spark-muted mt-1", children: info.description })] }), _jsxs("section", { className: "space-y-3", children: [_jsxs("div", { className: "label flex items-center gap-1.5", children: [_jsx(Wifi, { size: 12 }), " Connection"] }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs text-spark-muted", children: "Base URL" }), _jsx("input", { className: "input w-full mt-1 font-mono text-sm", placeholder: "http://ha.lan:8123", value: draft.base_url, onChange: (e) => setDraft((d) => ({ ...d, base_url: e.target.value })) })] }), _jsxs("label", { className: "block", children: [_jsxs("span", { className: "text-xs text-spark-muted flex items-center gap-1", children: [_jsx(KeyRound, { size: 11 }), " Token secret name"] }), _jsx("input", { className: "input w-full mt-1 font-mono text-sm", value: draft.token_secret, onChange: (e) => setDraft((d) => ({ ...d, token_secret: e.target.value })) }), _jsxs("span", { className: "text-[10px] text-spark-muted mt-0.5 block", children: ["Run ", _jsxs("code", { children: ["spark secrets set ", draft.token_secret] }), " first."] })] })] }), _jsxs("div", { className: "flex items-center gap-3 flex-wrap", children: [_jsxs("label", { className: "text-xs flex items-center gap-2", children: [_jsx("input", { type: "checkbox", checked: draft.verify_ssl, onChange: (e) => setDraft((d) => ({ ...d, verify_ssl: e.target.checked })) }), "Verify SSL"] }), _jsxs("button", { className: "btn btn-ghost text-xs ml-auto", disabled: discoverMutation.isPending, onClick: () => discoverMutation.mutate(), children: [discoverMutation.isPending ? (_jsx(Loader2, { size: 12, className: "animate-spin mr-1.5 inline" })) : (_jsx(RefreshCw, { size: 12, className: "mr-1.5 inline" })), discovery ? "Re-discover" : "Test connection & discover"] })] }), discovery?.ok && (_jsxs("div", { className: "flex items-center gap-2 text-xs text-spark-good", children: [_jsx(CheckCircle2, { size: 14 }), "Connected to ", discovery.instance_url, " (HA", " ", _jsx("code", { children: discovery.instance_version }), ") \u00B7", " ", discovery.domains.length, " domains \u00B7 ", discovery.entities.length, " ", "entities"] })), discoverError && (_jsx(FailureInspector, { error: discoverError, variant: "compact" }))] }), grantPrefill && (_jsxs("div", { className: "panel p-3 border-amber-400/60 bg-amber-400/5 flex items-start gap-3", children: [_jsx(AlertTriangle, { size: 16, className: "text-amber-400 shrink-0 mt-0.5" }), _jsxs("div", { className: "flex-1 text-sm", children: [_jsx("strong", { children: "Suggested by failure inspector." }), " ", grantPrefill.toggle === "read_only" ? (_jsxs(_Fragment, { children: [_jsx("code", { children: "read_only" }), " staged off so call_service can fire."] })) : grantPrefill.add_domain ? (_jsxs(_Fragment, { children: ["Domain ", _jsx("code", { children: grantPrefill.add_domain }), " staged for allow. Review the highlighted card and click Save."] })) : grantPrefill.add_service ? (_jsxs(_Fragment, { children: ["Service ", _jsx("code", { children: grantPrefill.add_service }), " staged for allow. Review the highlighted matrix cell and click Save."] })) : null] }), _jsx("button", { className: "btn btn-ghost text-xs", onClick: () => {
                            discardGrantPrefill();
                            // Roll back the staged change to whatever was saved.
                            setDraft(readDraft(info.config));
                        }, children: "Discard" })] })), _jsx("section", { className: grantPrefill?.toggle === "read_only"
                    ? "ring-2 ring-amber-400/70 rounded-md p-2 -m-2"
                    : "", children: _jsxs("label", { className: "flex items-center gap-3", children: [_jsx("input", { type: "checkbox", checked: draft.read_only, onChange: (e) => setDraft((d) => ({ ...d, read_only: e.target.checked })) }), _jsxs("span", { className: "text-sm", children: [_jsx("strong", { children: "Read-only mode" }), " ", draft.read_only ? (_jsx("span", { className: "chip chip-good text-[10px] ml-1", children: "on" })) : (_jsx("span", { className: "chip chip-danger text-[10px] ml-1", children: "off \u2014 services callable" })), _jsx("span", { className: "block text-xs text-spark-muted mt-0.5", children: "When on, the agent can read states and render templates but cannot call services. The per-service allowlist still applies when off." })] })] }) }), _jsxs("section", { children: [_jsxs("div", { className: "label mb-2 flex items-center gap-2", children: [_jsx(Filter, { size: 12 }), " Allowed domains", _jsxs("span", { className: "text-spark-muted text-[11px] normal-case font-normal", children: ["(", draft.allowed_domains.length, " selected)"] })] }), !discovery?.ok ? (_jsx("p", { className: "text-sm text-spark-muted", children: "Run discovery to populate the domain list from your HA instance." })) : (_jsx("div", { className: "space-y-3", children: Object.entries(groupedDomains).map(([groupLabel, domains]) => (_jsxs("div", { children: [_jsx("div", { className: "text-[11px] uppercase tracking-wide text-spark-muted mb-1.5", children: groupLabel }), _jsx("div", { className: "grid grid-cols-2 md:grid-cols-3 gap-2", children: domains.map((d) => {
                                        const checked = draft.allowed_domains.includes(d.name);
                                        const flashed = flashedRef.current[`domain:${d.name}`];
                                        return (_jsxs("label", { className: `flex items-center gap-2 px-2 py-1.5 border rounded-md text-sm cursor-pointer hover:border-spark-accent/40 transition-colors ${checked
                                                ? "border-spark-border bg-spark-bg/30"
                                                : "border-spark-border"} ${flashed ? "ring-2 ring-amber-400/70" : ""}`, children: [_jsx("input", { type: "checkbox", checked: checked, onChange: () => {
                                                        if (!checked && d.risk === "danger") {
                                                            setConfirmFor({
                                                                kind: "domain",
                                                                domain: d.name,
                                                                label: d.name,
                                                            });
                                                            return;
                                                        }
                                                        toggleDomain(setDraft, d.name);
                                                    } }), _jsx("code", { className: "font-mono text-xs flex-1 truncate", children: d.name }), d.entity_count > 0 && (_jsx("span", { className: "text-[10px] text-spark-muted", children: d.entity_count })), _jsx("span", { className: `chip ${RISK_CHIP[d.risk]} text-[9px]`, children: RISK_LABEL[d.risk] })] }, d.name));
                                    }) })] }, groupLabel))) }))] }), _jsxs("section", { children: [_jsxs("div", { className: "label mb-2", children: ["Allowed services", " ", _jsx("span", { className: "text-spark-muted text-[11px] normal-case font-normal", children: "(per-domain matrix)" })] }), !discovery?.ok ? (_jsx("p", { className: "text-sm text-spark-muted", children: "Run discovery to populate the service matrix." })) : draft.allowed_domains.length === 0 ? (_jsx("p", { className: "text-sm text-spark-muted", children: "Allow at least one domain above to see its services." })) : (_jsx("div", { className: "space-y-2", children: draft.allowed_domains.map((dom) => {
                            const services = discovery.services_by_domain[dom] ?? [];
                            if (services.length === 0)
                                return null;
                            const allowedSet = new Set(draft.allowed_services[dom] ?? []);
                            return (_jsxs("div", { className: "border border-spark-border rounded-md p-2", children: [_jsxs("div", { className: "flex items-center gap-2 mb-1.5", children: [_jsx("code", { className: "font-mono text-xs font-semibold", children: dom }), _jsxs("span", { className: "text-spark-muted text-[10px]", children: [allowedSet.size, "/", services.length, " allowed"] })] }), _jsx("div", { className: "grid grid-cols-2 md:grid-cols-3 gap-1.5", children: services.map((s) => {
                                            const cellKey = `service:${dom}.${s.name}`;
                                            const checked = allowedSet.has(s.name);
                                            const flashed = flashedRef.current[cellKey];
                                            return (_jsxs("label", { className: `flex items-center gap-2 px-2 py-1 border rounded text-xs cursor-pointer hover:border-spark-accent/40 transition-colors ${checked
                                                    ? "border-spark-border bg-spark-bg/30"
                                                    : "border-spark-border"} ${flashed ? "ring-2 ring-amber-400/70" : ""}`, title: s.description ?? undefined, children: [_jsx("input", { type: "checkbox", checked: checked, onChange: () => {
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
                                                        } }), _jsx("code", { className: "font-mono flex-1 truncate", children: s.name }), s.risk !== "safe" && (_jsx("span", { className: `chip ${RISK_CHIP[s.risk]} text-[9px]`, children: s.risk[0].toUpperCase() }))] }, s.name));
                                        }) })] }, dom));
                        }) }))] }), _jsxs("section", { children: [_jsxs("div", { className: "label mb-2", children: ["Entity excludes (glob patterns)", _jsxs("span", { className: "text-spark-muted text-[11px] normal-case font-normal ml-1.5", children: ["(", draft.entity_filter_glob.length, " active)"] })] }), _jsxs("p", { className: "text-xs text-spark-muted mb-2", children: ["Defense in depth on top of `allowed_domains`. Use", " ", _jsx("code", { className: "font-mono", children: "device_tracker.*" }), "-style patterns to exclude an entire domain even when it's allowed; or pick specific entities below."] }), _jsx("div", { className: "flex flex-wrap gap-1.5 mb-2", children: draft.entity_filter_glob.map((g, i) => (_jsxs("span", { className: "chip chip-warn text-[10px] flex items-center gap-1", children: [_jsx("code", { className: "font-mono", children: g }), _jsx("button", { onClick: () => setDraft((d) => ({
                                        ...d,
                                        entity_filter_glob: d.entity_filter_glob.filter((_, j) => j !== i),
                                    })), "aria-label": "Remove", children: _jsx(X, { size: 10 }) })] }, i))) }), _jsx(GlobAdd, { discovery: discovery, existing: draft.entity_filter_glob, onAdd: (g) => setDraft((d) => ({
                            ...d,
                            entity_filter_glob: [...d.entity_filter_glob, g],
                        })) })] }), _jsxs("div", { className: "pt-3 border-t border-spark-border space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Reason (audited)" }), _jsx("input", { className: "input w-full", placeholder: "why are you changing this?", value: reason, onChange: (e) => setReason(e.target.value) })] }), _jsxs("div", { className: "flex items-center justify-between", children: [_jsx("div", { className: "text-xs text-spark-muted", children: dirty ? "Unsaved changes" : "In sync with stored config" }), _jsxs("div", { className: "flex gap-2", children: [_jsxs("button", { className: "btn", disabled: !dirty, onClick: () => {
                                            setDraft(readDraft(info.config));
                                            setReason("");
                                            discardGrantPrefill();
                                        }, children: [_jsx(RotateCcw, { size: 13, className: "mr-1.5 inline" }), "Discard"] }), _jsxs("button", { className: "btn btn-primary", disabled: !dirty || save.isPending, onClick: () => save.mutate(), children: [_jsx(Save, { size: 13, className: "mr-1.5 inline" }), save.isPending ? "Saving…" : "Save"] })] })] })] }), confirmFor && (_jsx(DangerConfirmModal, { target: confirmFor, onCancel: () => setConfirmFor(null), onConfirm: () => {
                    if (confirmFor.kind === "domain") {
                        toggleDomain(setDraft, confirmFor.domain);
                    }
                    else if (confirmFor.kind === "service" && confirmFor.service) {
                        setDraft((d) => {
                            const services = { ...d.allowed_services };
                            const cur = new Set(services[confirmFor.domain] ?? []);
                            cur.add(confirmFor.service);
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
                } }))] }));
}
// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
function GlobAdd({ discovery, existing, onAdd, }) {
    const [value, setValue] = useState("");
    const suggestions = useMemo(() => {
        if (!discovery?.entities || !value)
            return [];
        const v = value.toLowerCase();
        const out = new Set();
        for (const e of discovery.entities) {
            if (existing.includes(e.entity_id))
                continue;
            if (e.entity_id.toLowerCase().includes(v) ||
                (e.friendly_name ?? "").toLowerCase().includes(v)) {
                out.add(e.entity_id);
                if (out.size >= 8)
                    break;
            }
        }
        return Array.from(out);
    }, [discovery, value, existing]);
    return (_jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("input", { className: "input flex-1 font-mono text-xs", placeholder: "device_tracker.* or sensor.power_meter", value: value, onChange: (e) => setValue(e.target.value), onKeyDown: (e) => {
                            if (e.key === "Enter" && value.trim()) {
                                onAdd(value.trim());
                                setValue("");
                            }
                        } }), _jsx("button", { className: "btn btn-ghost text-xs", disabled: !value.trim(), onClick: () => {
                            onAdd(value.trim());
                            setValue("");
                        }, children: "Add" })] }), suggestions.length > 0 && (_jsx("div", { className: "flex flex-wrap gap-1 mt-1", children: suggestions.map((s) => (_jsx("button", { className: "chip text-[10px] hover:chip-warn", onClick: () => {
                        onAdd(s);
                        setValue("");
                    }, children: s }, s))) }))] }));
}
function DangerConfirmModal({ target, onCancel, onConfirm, }) {
    const [typed, setTyped] = useState("");
    const matches = typed === target.label;
    return (_jsx(Modal, { open: true, onClose: onCancel, children: _jsxs("div", { className: "panel p-5 max-w-md", children: [_jsxs("div", { className: "flex items-start gap-3", children: [_jsx(ShieldAlert, { size: 20, className: "text-spark-danger shrink-0 mt-0.5" }), _jsxs("div", { className: "flex-1", children: [_jsxs("h4", { className: "font-bold", children: ["Allow ", target.kind, "?"] }), _jsx("p", { className: "text-sm text-spark-muted mt-1", children: target.kind === "domain"
                                        ? `Allowing the "${target.label}" domain lets the agent see (and potentially act on) every entity in that domain. High-risk domains include locks, alarms, cameras, and location data.`
                                        : `Allowing "${target.label}" lets the agent call this mutating service. Pair with a tight scope where possible.` }), _jsxs("p", { className: "text-xs text-spark-muted mt-3", children: ["Type", " ", _jsx("code", { className: "font-mono", children: target.label }), " to confirm:"] }), _jsx("input", { className: "input w-full mt-2 font-mono text-sm", autoFocus: true, value: typed, onChange: (e) => setTyped(e.target.value), onKeyDown: (e) => {
                                        if (e.key === "Enter" && matches)
                                            onConfirm();
                                    } })] })] }), _jsxs("div", { className: "flex justify-end gap-2 mt-4", children: [_jsx("button", { className: "btn", onClick: onCancel, children: "Cancel" }), _jsx("button", { className: "btn btn-danger", disabled: !matches, onClick: onConfirm, children: "Allow" })] })] }) }));
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
function readDraft(cfg) {
    return {
        base_url: typeof cfg.base_url === "string" ? cfg.base_url : "",
        token_secret: typeof cfg.token_secret === "string"
            ? cfg.token_secret
            : "home_assistant_token",
        read_only: cfg.read_only === false ? false : true,
        allowed_domains: Array.isArray(cfg.allowed_domains)
            ? cfg.allowed_domains
            : DEFAULT_DOMAINS,
        allowed_services: cfg.allowed_services && typeof cfg.allowed_services === "object"
            ? cfg.allowed_services
            : {},
        entity_filter_glob: Array.isArray(cfg.entity_filter_glob)
            ? cfg.entity_filter_glob
            : [],
        verify_ssl: cfg.verify_ssl === false ? false : true,
        connect_timeout_seconds: typeof cfg.connect_timeout_seconds === "number"
            ? cfg.connect_timeout_seconds
            : 5.0,
        read_timeout_seconds: typeof cfg.read_timeout_seconds === "number"
            ? cfg.read_timeout_seconds
            : 15.0,
        max_response_bytes: typeof cfg.max_response_bytes === "number"
            ? cfg.max_response_bytes
            : 1_048_576,
        max_states_returned: typeof cfg.max_states_returned === "number"
            ? cfg.max_states_returned
            : 200,
    };
}
function serializeConfig(d) {
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
function defaultRiskFor(domain) {
    if (DANGER_DOMAINS.has(domain))
        return "danger";
    if (ELEVATED_DOMAINS.has(domain))
        return "elevated";
    return "safe";
}
function groupDomains(domains) {
    const groups = {
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
        }
        else if (["sensor", "binary_sensor", "weather"].includes(d.name)) {
            groups["Sensors"].push(d);
        }
        else if (["media_player", "remote", "tv"].includes(d.name)) {
            groups["Media"].push(d);
        }
        else if (["climate", "humidifier", "fan"].includes(d.name)) {
            groups["Climate"].push(d);
        }
        else if (["lock", "alarm_control_panel", "camera", "cover"].includes(d.name)) {
            groups["Security & access"].push(d);
        }
        else if (["device_tracker", "person", "zone"].includes(d.name)) {
            groups["Location & people"].push(d);
        }
        else {
            groups["Other"].push(d);
        }
    }
    // Drop empty groups so the editor doesn't render orphan headers.
    return Object.fromEntries(Object.entries(groups).filter(([, v]) => v.length > 0));
}
function toggleDomain(setDraft, domain) {
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
function toggleService(setDraft, domain, service) {
    setDraft((d) => {
        const services = { ...d.allowed_services };
        const cur = new Set(services[domain] ?? []);
        if (cur.has(service))
            cur.delete(service);
        else
            cur.add(service);
        services[domain] = Array.from(cur).sort();
        if (services[domain].length === 0)
            delete services[domain];
        return { ...d, allowed_services: services };
    });
}
