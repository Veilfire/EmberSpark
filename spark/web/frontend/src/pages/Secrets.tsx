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

  const names = useQuery<string[]>({
    queryKey: ["security-secrets"],
    queryFn: () => api.get("/api/security/secrets"),
  });

  const [filter, setFilter] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const filtered = (names.data ?? []).filter((n) =>
    n.toLowerCase().includes(filter.toLowerCase()),
  );

  async function deleteSecret(name: string) {
    const ok = await confirmDialog({
      title: `Delete secret "${name}"?`,
      description:
        "This removes the value from the age vault permanently. Anything " +
        "configured to look up this secret name will fail until it's set " +
        "again. The deletion is audited at elevated severity.",
      tone: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await api.del(`/api/security/secrets/${encodeURIComponent(name)}`);
      toast.success(`Deleted "${name}"`);
      qc.invalidateQueries({ queryKey: ["security-secrets"] });
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    }
  }

  async function canaryTest(name: string) {
    try {
      const resp = await api.post<{ ok: boolean }>(
        "/api/security/secrets/canary",
        { name },
      );
      if (resp.ok) {
        toast.success(`"${name}" is reachable`);
      } else {
        toast.error(`"${name}" is NOT in the vault`);
      }
    } catch (e) {
      toast.error(`Canary failed: ${(e as Error).message}`);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        icon={<KeyRound className="w-5 h-5" />}
        title="Secrets"
        subtitle={
          "Names of credentials in the age-encrypted vault. Plugins " +
          "and triggers reference these by name; the value never leaves " +
          "the runtime. Adding or deleting a secret is audited."
        }
      />

      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="w-4 h-4 absolute left-2 top-1/2 -translate-y-1/2 text-spark-muted" />
          <input
            className="input w-full pl-8"
            placeholder="filter by name…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        {isAdmin && (
          <button
            className="btn btn-primary inline-flex items-center gap-1 whitespace-nowrap"
            onClick={() => setShowCreate(true)}
          >
            <Plus className="w-4 h-4" /> New secret
          </button>
        )}
      </div>

      {names.isLoading ? (
        <div className="text-spark-muted text-sm">Loading…</div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<KeyRound className="w-6 h-6" />}
          title={
            (names.data ?? []).length === 0
              ? "No secrets in the vault"
              : "No matches"
          }
          description={
            (names.data ?? []).length === 0
              ? "Plugins and webhook triggers reference vault entries by name. Click 'New secret' to add one."
              : "Try a different filter."
          }
        />
      ) : (
        <div className="panel divide-y divide-spark-border">
          {filtered.map((name) => (
            <div
              key={name}
              className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-spark-border/20"
            >
              <div className="flex items-center gap-3 min-w-0 flex-1">
                <KeyRound className="w-4 h-4 text-spark-muted shrink-0" />
                <code className="font-mono text-sm truncate">{name}</code>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  className="btn btn-ghost text-xs"
                  onClick={() => canaryTest(name)}
                  title="Verify the runtime can resolve this secret"
                >
                  Test
                </button>
                <button
                  className="btn btn-ghost text-xs"
                  onClick={() => {
                    void navigator.clipboard.writeText(name);
                    toast.success("Name copied");
                  }}
                  title="Copy name to clipboard"
                >
                  Copy name
                </button>
                {isAdmin && (
                  <button
                    className="btn btn-danger text-xs"
                    onClick={() => deleteSecret(name)}
                    title="Delete this secret from the vault"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <NewSecretModal
          onClose={() => setShowCreate(false)}
          onSaved={(name) => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: ["security-secrets"] });
            toast.success(`"${name}" stored in the vault`);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// New secret modal
// ---------------------------------------------------------------------------

function NewSecretModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [showValue, setShowValue] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [touched, setTouched] = useState(false);

  const nameValid = NAME_PATTERN.test(name);
  const valueValid = value.length > 0 && value.length <= 8192;
  const canSubmit = nameValid && valueValid && !submitting;

  const save = useMutation({
    mutationFn: () =>
      api.put<{ ok: boolean }>("/api/security/secrets", { name, value }),
    onSuccess: () => onSaved(name),
    onError: (e) => {
      toast.error(`Save failed: ${(e as Error).message}`);
      setSubmitting(false);
    },
  });

  async function submit() {
    setTouched(true);
    if (!canSubmit) return;
    setSubmitting(true);
    save.mutate();
  }

  return (
    <Modal open onClose={onClose}>
      <div className="w-full max-w-lg max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl">
        <header className="sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3">
          <h3 className="text-lg font-bold">New secret</h3>
          <p className="text-xs text-spark-muted mt-0.5">
            Stored encrypted-at-rest in the age vault. Cleartext is never
            re-displayed. The save is audited at elevated severity.
          </p>
        </header>

        <div className="p-4 space-y-4">
          <label className="block">
            <div className="label">Name</div>
            <input
              className="input w-full font-mono mt-1"
              placeholder="e.g. serper_api_key"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              autoComplete="off"
              spellCheck={false}
            />
            <div className="text-xs mt-1 text-spark-muted">
              Allowed: letters, digits, <code>.</code>, <code>_</code>,
              <code>-</code>. Max 128 chars. Plugins reference this name in
              their config — pick something you'll recognize.
            </div>
            {touched && !nameValid && (
              <div className="text-xs text-spark-danger mt-1">
                Invalid name. Must match <code>^[a-zA-Z0-9._-]{"{1,128}"}$</code>.
              </div>
            )}
          </label>

          <label className="block">
            <div className="flex items-center justify-between">
              <span className="label">Value</span>
              <button
                type="button"
                className="text-xs text-spark-muted hover:text-spark-text flex items-center gap-1"
                onClick={() => setShowValue((v) => !v)}
              >
                {showValue ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                {showValue ? "Hide" : "Show"}
              </button>
            </div>
            <input
              className="input w-full font-mono mt-1"
              type={showValue ? "text" : "password"}
              placeholder="paste credential…"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <div className="text-xs mt-1 text-spark-muted">
              The value goes straight to the vault. We never log or
              re-display it; once you click Save, it's only resolvable
              through the runtime by name.
            </div>
            {touched && !valueValid && (
              <div className="text-xs text-spark-danger mt-1">
                Value is required (max 8192 chars).
              </div>
            )}
          </label>

          <div className="bg-spark-bg border border-spark-border rounded p-3 text-xs text-spark-muted">
            <strong className="text-spark-text">Tip.</strong> Plugin config
            fields named <code>*_secret</code> (e.g.{" "}
            <code>web_search.api_key_secret</code>,{" "}
            <code>telegram_messenger.bot_token_secret</code>) want the{" "}
            <em>name</em> you set here, not the value itself. If you put a
            real credential into a plugin config, it gets persisted in
            cleartext and shows up in audit diffs — vault it instead.
          </div>
        </div>

        <footer className="sticky bottom-0 bg-spark-panel border-t border-spark-border px-4 py-3 flex justify-end gap-2">
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={!canSubmit}>
            {submitting ? "Saving…" : "Save to vault"}
          </button>
        </footer>
      </div>
    </Modal>
  );
}
