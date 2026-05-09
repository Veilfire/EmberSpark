import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Modal } from "./Modal";
export function ConfirmDialog({ open, title, description, requireTypedName, confirmLabel = "Confirm", cancelLabel = "Cancel", tone = "default", onConfirm, onCancel, }) {
    const [typed, setTyped] = useState("");
    const canConfirm = !requireTypedName || typed === requireTypedName;
    const toneClass = tone === "danger"
        ? "btn-danger"
        : tone === "warning"
            ? "btn-primary"
            : "btn-primary";
    const iconTone = tone === "danger"
        ? "text-spark-danger"
        : tone === "warning"
            ? "text-spark-accent"
            : "text-spark-accent";
    return (_jsx(Modal, { open: open, onClose: () => {
            setTyped("");
            onCancel();
        }, children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-md p-6 shadow-xl", children: [_jsxs("div", { className: "flex items-start gap-3 mb-4", children: [(tone === "danger" || tone === "warning") && (_jsx(AlertTriangle, { className: `w-5 h-5 shrink-0 mt-0.5 ${iconTone}` })), _jsxs("div", { className: "flex-1", children: [_jsx("h3", { className: "font-semibold", children: title }), description && (_jsx("p", { className: "text-sm text-spark-muted mt-1", children: description }))] })] }), requireTypedName && (_jsxs("div", { className: "mb-4", children: [_jsxs("label", { className: "block text-xs text-spark-muted mb-1", children: ["Type", " ", _jsx("code", { className: "font-mono bg-spark-bg px-1 py-0.5 rounded", children: requireTypedName }), " ", "to confirm:"] }), _jsx("input", { autoFocus: true, className: "input w-full font-mono", value: typed, onChange: (e) => setTyped(e.target.value), placeholder: requireTypedName })] })), _jsxs("div", { className: "flex justify-end gap-2", children: [_jsx("button", { className: "btn", onClick: () => {
                                setTyped("");
                                onCancel();
                            }, children: cancelLabel }), _jsx("button", { className: `btn ${toneClass}`, disabled: !canConfirm, onClick: () => {
                                setTyped("");
                                onConfirm();
                            }, children: confirmLabel })] })] }) }));
}
