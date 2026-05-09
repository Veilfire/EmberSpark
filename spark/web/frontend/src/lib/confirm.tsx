import { useEffect, useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";

/**
 * Imperative, promise-based replacement for window.confirm — backed by the
 * existing ConfirmDialog component so it matches the app's look + feel.
 *
 * Usage:
 *   if (await confirmDialog({ title: "Delete foo?", tone: "danger" })) {
 *     await api.del("/foo");
 *   }
 *
 * Requires <ConfirmHost /> mounted somewhere above the caller (App.tsx).
 */

export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "warning" | "default";
  /** If set, the user must type this exact string to enable Confirm. */
  requireTypedName?: string;
}

interface DialogState extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

type Listener = (state: DialogState | null) => void;

const listeners = new Set<Listener>();
let current: DialogState | null = null;

function publish() {
  listeners.forEach((l) => l(current));
}

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    current = { ...opts, resolve };
    publish();
  });
}

export function ConfirmHost() {
  const [state, setState] = useState<DialogState | null>(null);

  useEffect(() => {
    const listener: Listener = (s) => setState(s);
    listeners.add(listener);
    // Sync any dialog already requested before the host mounted.
    listener(current);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  return (
    <ConfirmDialog
      open={state !== null}
      title={state?.title ?? ""}
      description={state?.description}
      tone={state?.tone ?? "default"}
      confirmLabel={state?.confirmLabel}
      cancelLabel={state?.cancelLabel}
      requireTypedName={state?.requireTypedName}
      onConfirm={() => {
        state?.resolve(true);
        current = null;
        publish();
      }}
      onCancel={() => {
        state?.resolve(false);
        current = null;
        publish();
      }}
    />
  );
}
