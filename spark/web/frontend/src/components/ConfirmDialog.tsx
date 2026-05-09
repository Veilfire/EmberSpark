import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Modal } from "./Modal";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  /** If provided, user must type this exact string to enable the confirm button. */
  requireTypedName?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "warning" | "default";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  requireTypedName,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");
  const canConfirm = !requireTypedName || typed === requireTypedName;
  const toneClass =
    tone === "danger"
      ? "btn-danger"
      : tone === "warning"
        ? "btn-primary"
        : "btn-primary";
  const iconTone =
    tone === "danger"
      ? "text-spark-danger"
      : tone === "warning"
        ? "text-spark-accent"
        : "text-spark-accent";

  return (
    <Modal
      open={open}
      onClose={() => {
        setTyped("");
        onCancel();
      }}
    >
      <div className="bg-spark-panel border border-spark-border rounded-lg w-full max-w-md p-6 shadow-xl">
        <div className="flex items-start gap-3 mb-4">
          {(tone === "danger" || tone === "warning") && (
            <AlertTriangle className={`w-5 h-5 shrink-0 mt-0.5 ${iconTone}`} />
          )}
          <div className="flex-1">
            <h3 className="font-semibold">{title}</h3>
            {description && (
              <p className="text-sm text-spark-muted mt-1">{description}</p>
            )}
          </div>
        </div>
        {requireTypedName && (
          <div className="mb-4">
            <label className="block text-xs text-spark-muted mb-1">
              Type{" "}
              <code className="font-mono bg-spark-bg px-1 py-0.5 rounded">
                {requireTypedName}
              </code>{" "}
              to confirm:
            </label>
            <input
              autoFocus
              className="input w-full font-mono"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={requireTypedName}
            />
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            className="btn"
            onClick={() => {
              setTyped("");
              onCancel();
            }}
          >
            {cancelLabel}
          </button>
          <button
            className={`btn ${toneClass}`}
            disabled={!canConfirm}
            onClick={() => {
              setTyped("");
              onConfirm();
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
