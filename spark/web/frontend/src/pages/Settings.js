import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../lib/api";
const NOTIFICATION_KINDS = [
    {
        field: "download_ready",
        label: "Download ready",
        description: "A plugin wrote a new file to the deliverables directory.",
    },
    {
        field: "hitl_skill_review",
        label: "Pending skill review",
        description: "An agent-discovered skill is waiting for your approval.",
    },
    {
        field: "hitl_approval",
        label: "Task approval required",
        description: "A scheduled task with an approval gate has been paused.",
    },
    {
        field: "hitl_dlq",
        label: "Task moved to DLQ",
        description: "A task has failed too many times and won't fire again until you ack it.",
    },
    {
        field: "ip_grant_expiring",
        label: "Internal-IP grant expiring",
        description: "An internal network grant is about to expire (< 1 hour).",
    },
    {
        field: "raw_logging_on",
        label: "Raw logging left on",
        description: "allow_raw_logging has been enabled for more than 24 hours.",
    },
    {
        field: "cost_soft_alert",
        label: "Cost soft alert",
        description: "A budget has crossed its soft-alert threshold.",
    },
    {
        field: "cost_hard_stop",
        label: "Cost hard stop",
        description: "A budget has crossed its hard ceiling and new runs are being refused.",
    },
    {
        field: "incident",
        label: "Incident",
        description: "A critical audit entry has been recorded.",
    },
    {
        field: "plugin_hash_changed",
        label: "Plugin hash changed",
        description: "A built-in plugin's module hash no longer matches the registry.",
    },
    {
        field: "memory_pruned",
        label: "Memory pruned",
        description: "A scheduled pruning sweep deleted long-term memory rows that aged past their retention window.",
    },
    {
        field: "data_class_blocked",
        label: "Data class blocked",
        description: "A data-classification guardrail refused an operation (tool output, chat turn, memory write, etc.). Surfaces the agent, class, and scope.",
    },
    {
        field: "data_class_grant_expiring",
        label: "Data-class grant expiring",
        description: "An unlimited data-class grant is within 24 hours of its expiry — extend or let it lapse.",
    },
];
export default function Settings() {
    const qc = useQueryClient();
    const { data, isLoading } = useQuery({
        queryKey: ["notification-preferences"],
        queryFn: () => api.get("/api/notifications/preferences"),
    });
    const [dirty, setDirty] = useState({});
    const save = useMutation({
        mutationFn: (patch) => api.put("/api/notifications/preferences", patch),
        onSuccess: () => {
            setDirty({});
            qc.invalidateQueries({ queryKey: ["notification-preferences"] });
        },
    });
    const prefs = data && {
        ...data,
        ...dirty,
    };
    if (isLoading || !prefs) {
        return _jsx("div", { className: "p-4 text-spark-muted", children: "Loading settings\u2026" });
    }
    const toggle = (field) => {
        setDirty((d) => ({ ...d, [field]: !prefs[field] }));
    };
    return (_jsxs("div", { className: "p-4 space-y-6 max-w-2xl", children: [_jsxs("header", { children: [_jsx("h1", { className: "text-xl font-bold", children: "Settings" }), _jsx("p", { className: "text-xs text-spark-muted mt-1", children: "Per-category notification preferences. Turning a category off means no row is written and no bell/toast fires for that kind \u2014 the underlying event (skill review, DLQ, etc.) still happens." })] }), _jsxs("section", { className: "border border-spark-border rounded-md", children: [_jsx("header", { className: "px-3 py-2 border-b border-spark-border font-semibold text-sm", children: "Notification categories" }), _jsx("ul", { className: "divide-y divide-spark-border", children: NOTIFICATION_KINDS.map((kind) => (_jsxs("li", { className: "p-3 flex items-start justify-between gap-4", children: [_jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "font-medium text-sm", children: kind.label }), _jsx("div", { className: "text-xs text-spark-muted mt-0.5", children: kind.description })] }), _jsx(ToggleSwitch, { enabled: prefs[kind.field], onChange: () => toggle(kind.field) })] }, kind.field))) })] }), _jsx(SessionTimeoutSection, {}), _jsxs("section", { className: "border border-spark-border rounded-md", children: [_jsx("header", { className: "px-3 py-2 border-b border-spark-border font-semibold text-sm", children: "Delivery" }), _jsxs("ul", { className: "divide-y divide-spark-border", children: [_jsxs("li", { className: "p-3 flex items-start justify-between gap-4", children: [_jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "font-medium text-sm", children: "Toast on create" }), _jsx("div", { className: "text-xs text-spark-muted mt-0.5", children: "Show a transient toast in the web UI when a new notification is created." })] }), _jsx(ToggleSwitch, { enabled: prefs.toast_on_create, onChange: () => toggle("toast_on_create") })] }), _jsxs("li", { className: "p-3 flex items-start justify-between gap-4", children: [_jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "font-medium text-sm", children: "Play sound" }), _jsx("div", { className: "text-xs text-spark-muted mt-0.5", children: "Play a short ping on elevated or critical notifications only." })] }), _jsx(ToggleSwitch, { enabled: prefs.play_sound, onChange: () => toggle("play_sound") })] })] })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { type: "button", className: "btn", onClick: () => save.mutate(dirty), disabled: save.isPending || Object.keys(dirty).length === 0, children: save.isPending ? "Saving…" : "Save changes" }), _jsx("button", { type: "button", className: "btn-ghost text-xs", onClick: () => setDirty({}), disabled: Object.keys(dirty).length === 0, children: "Discard" }), Object.keys(dirty).length > 0 && (_jsxs("span", { className: "text-xs text-spark-muted self-center", children: [Object.keys(dirty).length, " unsaved change", Object.keys(dirty).length === 1 ? "" : "s"] }))] })] }));
}
const MIN_TIMEOUT_SECONDS = 60;
const MAX_TIMEOUT_SECONDS = 30 * 86_400;
function toDHM(seconds) {
    const days = Math.floor(seconds / 86_400);
    const afterDays = seconds - days * 86_400;
    const hours = Math.floor(afterDays / 3600);
    const minutes = Math.floor((afterDays - hours * 3600) / 60);
    return { days, hours, minutes };
}
function fromDHM(d, h, m) {
    return d * 86_400 + h * 3600 + m * 60;
}
function SessionTimeoutSection() {
    const qc = useQueryClient();
    const { data, isLoading } = useQuery({
        queryKey: ["session-settings"],
        queryFn: () => api.get("/api/settings/session"),
    });
    const [enabled, setEnabled] = useState(true);
    const [days, setDays] = useState(0);
    const [hours, setHours] = useState(1);
    const [minutes, setMinutes] = useState(0);
    const [hydrated, setHydrated] = useState(false);
    useEffect(() => {
        if (data && !hydrated) {
            setEnabled(data.enabled);
            const parts = toDHM(data.timeout_seconds ?? 3600);
            setDays(parts.days);
            setHours(parts.hours);
            setMinutes(parts.minutes);
            setHydrated(true);
        }
    }, [data, hydrated]);
    const totalSeconds = fromDHM(days, hours, minutes);
    const tooShort = enabled && totalSeconds < MIN_TIMEOUT_SECONDS;
    const tooLong = enabled && totalSeconds > MAX_TIMEOUT_SECONDS;
    const save = useMutation({
        mutationFn: (body) => api.put("/api/settings/session", body),
        onSuccess: () => {
            toast.success("Session settings saved");
            qc.invalidateQueries({ queryKey: ["session-settings"] });
        },
        onError: (err) => {
            const msg = err instanceof Error ? err.message : "Failed to save session settings";
            toast.error(msg);
        },
    });
    const onSave = () => {
        if (enabled && (tooShort || tooLong))
            return;
        save.mutate({
            enabled,
            timeout_seconds: enabled ? totalSeconds : null,
        });
    };
    const dirty = hydrated &&
        data !== undefined &&
        (enabled !== data.enabled ||
            (enabled && totalSeconds !== (data.timeout_seconds ?? 0)));
    return (_jsxs("section", { className: "border border-spark-border rounded-md", children: [_jsx("header", { className: "px-3 py-2 border-b border-spark-border font-semibold text-sm", children: "Security \u2014 session timeout" }), _jsxs("div", { className: "p-3 space-y-4", children: [_jsxs("div", { className: "flex items-start justify-between gap-4", children: [_jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "font-medium text-sm", children: "Session timeout enabled" }), _jsx("div", { className: "text-xs text-spark-muted mt-0.5", children: "When disabled, signed-in browsers stay authenticated indefinitely. Applies immediately to new and existing sessions." })] }), _jsx(ToggleSwitch, { enabled: enabled, onChange: () => setEnabled((e) => !e) })] }), _jsxs("div", { className: `grid grid-cols-3 gap-3 ${enabled ? "" : "opacity-40 pointer-events-none select-none"}`, "aria-disabled": !enabled, children: [_jsx(NumberField, { label: "Days", value: days, min: 0, max: 30, onChange: setDays, disabled: !enabled }), _jsx(NumberField, { label: "Hours", value: hours, min: 0, max: 23, onChange: setHours, disabled: !enabled }), _jsx(NumberField, { label: "Minutes", value: minutes, min: 0, max: 59, onChange: setMinutes, disabled: !enabled })] }), enabled && tooShort && (_jsx("p", { className: "text-xs text-red-400", children: "Minimum timeout is 1 minute." })), enabled && tooLong && (_jsx("p", { className: "text-xs text-red-400", children: "Maximum timeout is 30 days." })), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("button", { type: "button", className: "btn", onClick: onSave, disabled: save.isPending || isLoading || !dirty || tooShort || tooLong, children: save.isPending ? "Saving…" : "Save" }), dirty && (_jsx("span", { className: "text-xs text-spark-muted", children: "Unsaved changes" }))] })] })] }));
}
function NumberField({ label, value, min, max, onChange, disabled, }) {
    return (_jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs uppercase tracking-wide text-spark-muted", children: label }), _jsx("input", { type: "number", min: min, max: max, value: value, disabled: disabled, onChange: (e) => {
                    const raw = Number(e.target.value);
                    if (Number.isNaN(raw))
                        return;
                    onChange(Math.max(min, Math.min(max, Math.floor(raw))));
                }, className: "mt-1 w-full px-2 py-1 bg-spark-bg border border-spark-border rounded text-sm tabular-nums disabled:cursor-not-allowed" })] }));
}
function ToggleSwitch({ enabled, onChange, }) {
    return (_jsx("button", { type: "button", role: "switch", "aria-checked": enabled, onClick: onChange, className: `relative inline-flex h-5 w-9 items-center rounded-full transition-colors shrink-0 ${enabled ? "bg-spark-accent" : "bg-spark-border"}`, children: _jsx("span", { className: `inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${enabled ? "translate-x-4" : "translate-x-0.5"}` }) }));
}
