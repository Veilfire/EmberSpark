import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Secrets — manage the age-encrypted vault.
 *
 * The vault holds named credentials (API keys, bot tokens, signing
 * keys) that plugins reference *by name* in their config. The actual
 * cleartext never leaves the runtime: it goes in here, lives encrypted
 * on disk, and is only ever resolved at tool-call time. This page
 * never renders a value, only names + canary results.
 *
 * Roles:
 *   - viewer: list secret names + canary test (audited at info)
 *   - admin:  set + delete (audited at elevated)
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Eye, EyeOff, KeyRound, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";
import { useAuth } from "../hooks/useAuth";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/primitives";
const NAME_PATTERN = /^[a-zA-Z0-9._-]{1,128}$/;
export default function Secrets() {
    const qc = useQueryClient();
    const { role } = useAuth();
    const isAdmin = role === "admin";
    const names = useQuery({
        queryKey: ["security-secrets"],
        queryFn: () => api.get("/api/security/secrets"),
    });
    const [filter, setFilter] = useState("");
    const [showCreate, setShowCreate] = useState(false);
    const filtered = (names.data ?? []).filter((n) => n.toLowerCase().includes(filter.toLowerCase()));
    async function deleteSecret(name) {
        const ok = await confirmDialog({
            title: `Delete secret "${name}"?`,
            description: "This removes the value from the age vault permanently. Anything " +
                "configured to look up this secret name will fail until it's set " +
                "again. The deletion is audited at elevated severity.",
            tone: "danger",
            confirmLabel: "Delete",
        });
        if (!ok)
            return;
        try {
            await api.del(`/api/security/secrets/${encodeURIComponent(name)}`);
            toast.success(`Deleted "${name}"`);
            qc.invalidateQueries({ queryKey: ["security-secrets"] });
        }
        catch (e) {
            toast.error(`Delete failed: ${e.message}`);
        }
    }
    async function canaryTest(name) {
        try {
            const resp = await api.post("/api/security/secrets/canary", { name });
            if (resp.ok) {
                toast.success(`"${name}" is reachable`);
            }
            else {
                toast.error(`"${name}" is NOT in the vault`);
            }
        }
        catch (e) {
            toast.error(`Canary failed: ${e.message}`);
        }
    }
    return (_jsxs("div", { className: "space-y-4", children: [_jsx(PageHeader, { icon: _jsx(KeyRound, { className: "w-5 h-5" }), title: "Secrets", subtitle: "Names of credentials in the age-encrypted vault. Plugins " +
                    "and triggers reference these by name; the value never leaves " +
                    "the runtime. Adding or deleting a secret is audited." }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: "relative flex-1 max-w-md", children: [_jsx(Search, { className: "w-4 h-4 absolute left-2 top-1/2 -translate-y-1/2 text-spark-muted" }), _jsx("input", { className: "input w-full pl-8", placeholder: "filter by name\u2026", value: filter, onChange: (e) => setFilter(e.target.value) })] }), isAdmin && (_jsxs("button", { className: "btn btn-primary inline-flex items-center gap-1 whitespace-nowrap", onClick: () => setShowCreate(true), children: [_jsx(Plus, { className: "w-4 h-4" }), " New secret"] }))] }), names.isLoading ? (_jsx("div", { className: "text-spark-muted text-sm", children: "Loading\u2026" })) : filtered.length === 0 ? (_jsx(EmptyState, { icon: _jsx(KeyRound, { className: "w-6 h-6" }), title: (names.data ?? []).length === 0
                    ? "No secrets in the vault"
                    : "No matches", description: (names.data ?? []).length === 0
                    ? "Plugins and webhook triggers reference vault entries by name. Click 'New secret' to add one."
                    : "Try a different filter." })) : (_jsx("div", { className: "panel divide-y divide-spark-border", children: filtered.map((name) => (_jsxs("div", { className: "flex items-center justify-between gap-3 px-4 py-3 hover:bg-spark-border/20", children: [_jsxs("div", { className: "flex items-center gap-3 min-w-0 flex-1", children: [_jsx(KeyRound, { className: "w-4 h-4 text-spark-muted shrink-0" }), _jsx("code", { className: "font-mono text-sm truncate", children: name })] }), _jsxs("div", { className: "flex items-center gap-2 shrink-0", children: [_jsx("button", { className: "btn btn-ghost text-xs", onClick: () => canaryTest(name), title: "Verify the runtime can resolve this secret", children: "Test" }), _jsx("button", { className: "btn btn-ghost text-xs", onClick: () => {
                                        void navigator.clipboard.writeText(name);
                                        toast.success("Name copied");
                                    }, title: "Copy name to clipboard", children: "Copy name" }), isAdmin && (_jsx("button", { className: "btn btn-danger text-xs", onClick: () => deleteSecret(name), title: "Delete this secret from the vault", children: _jsx(Trash2, { className: "w-3.5 h-3.5" }) }))] })] }, name))) })), showCreate && (_jsx(NewSecretModal, { onClose: () => setShowCreate(false), onSaved: (name) => {
                    setShowCreate(false);
                    qc.invalidateQueries({ queryKey: ["security-secrets"] });
                    toast.success(`"${name}" stored in the vault`);
                } }))] }));
}
// ---------------------------------------------------------------------------
// New secret modal
// ---------------------------------------------------------------------------
function NewSecretModal({ onClose, onSaved, }) {
    const [name, setName] = useState("");
    const [value, setValue] = useState("");
    const [showValue, setShowValue] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [touched, setTouched] = useState(false);
    const nameValid = NAME_PATTERN.test(name);
    const valueValid = value.length > 0 && value.length <= 8192;
    const canSubmit = nameValid && valueValid && !submitting;
    const save = useMutation({
        mutationFn: () => api.put("/api/security/secrets", { name, value }),
        onSuccess: () => onSaved(name),
        onError: (e) => {
            toast.error(`Save failed: ${e.message}`);
            setSubmitting(false);
        },
    });
    async function submit() {
        setTouched(true);
        if (!canSubmit)
            return;
        setSubmitting(true);
        save.mutate();
    }
    return (_jsx(Modal, { open: true, onClose: onClose, children: _jsxs("div", { className: "w-full max-w-lg max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl", children: [_jsxs("header", { className: "sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3", children: [_jsx("h3", { className: "text-lg font-bold", children: "New secret" }), _jsx("p", { className: "text-xs text-spark-muted mt-0.5", children: "Stored encrypted-at-rest in the age vault. Cleartext is never re-displayed. The save is audited at elevated severity." })] }), _jsxs("div", { className: "p-4 space-y-4", children: [_jsxs("label", { className: "block", children: [_jsx("div", { className: "label", children: "Name" }), _jsx("input", { className: "input w-full font-mono mt-1", placeholder: "e.g. serper_api_key", value: name, onChange: (e) => setName(e.target.value), autoFocus: true, autoComplete: "off", spellCheck: false }), _jsxs("div", { className: "text-xs mt-1 text-spark-muted", children: ["Allowed: letters, digits, ", _jsx("code", { children: "." }), ", ", _jsx("code", { children: "_" }), ",", _jsx("code", { children: "-" }), ". Max 128 chars. Plugins reference this name in their config \u2014 pick something you'll recognize."] }), touched && !nameValid && (_jsxs("div", { className: "text-xs text-spark-danger mt-1", children: ["Invalid name. Must match ", _jsxs("code", { children: ["^[a-zA-Z0-9._-]", "{1,128}", "$"] }), "."] }))] }), _jsxs("label", { className: "block", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "label", children: "Value" }), _jsxs("button", { type: "button", className: "text-xs text-spark-muted hover:text-spark-text flex items-center gap-1", onClick: () => setShowValue((v) => !v), children: [showValue ? _jsx(EyeOff, { className: "w-3 h-3" }) : _jsx(Eye, { className: "w-3 h-3" }), showValue ? "Hide" : "Show"] })] }), _jsx("input", { className: "input w-full font-mono mt-1", type: showValue ? "text" : "password", placeholder: "paste credential\u2026", value: value, onChange: (e) => setValue(e.target.value), autoComplete: "off", spellCheck: false }), _jsx("div", { className: "text-xs mt-1 text-spark-muted", children: "The value goes straight to the vault. We never log or re-display it; once you click Save, it's only resolvable through the runtime by name." }), touched && !valueValid && (_jsx("div", { className: "text-xs text-spark-danger mt-1", children: "Value is required (max 8192 chars)." }))] }), _jsxs("div", { className: "bg-spark-bg border border-spark-border rounded p-3 text-xs text-spark-muted", children: [_jsx("strong", { className: "text-spark-text", children: "Tip." }), " Plugin config fields named ", _jsx("code", { children: "*_secret" }), " (e.g.", " ", _jsx("code", { children: "web_search.api_key_secret" }), ",", " ", _jsx("code", { children: "telegram_messenger.bot_token_secret" }), ") want the", " ", _jsx("em", { children: "name" }), " you set here, not the value itself. If you put a real credential into a plugin config, it gets persisted in cleartext and shows up in audit diffs \u2014 vault it instead."] })] }), _jsxs("footer", { className: "sticky bottom-0 bg-spark-panel border-t border-spark-border px-4 py-3 flex justify-end gap-2", children: [_jsx("button", { className: "btn btn-ghost", onClick: onClose, disabled: submitting, children: "Cancel" }), _jsx("button", { className: "btn btn-primary", onClick: submit, disabled: !canSubmit, children: submitting ? "Saving…" : "Save to vault" })] })] }) }));
}
