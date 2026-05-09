import { jsx as _jsx } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
const listeners = new Set();
let current = null;
function publish() {
    listeners.forEach((l) => l(current));
}
export function confirmDialog(opts) {
    return new Promise((resolve) => {
        current = { ...opts, resolve };
        publish();
    });
}
export function ConfirmHost() {
    const [state, setState] = useState(null);
    useEffect(() => {
        const listener = (s) => setState(s);
        listeners.add(listener);
        // Sync any dialog already requested before the host mounted.
        listener(current);
        return () => {
            listeners.delete(listener);
        };
    }, []);
    return (_jsx(ConfirmDialog, { open: state !== null, title: state?.title ?? "", description: state?.description, tone: state?.tone ?? "default", confirmLabel: state?.confirmLabel, cancelLabel: state?.cancelLabel, requireTypedName: state?.requireTypedName, onConfirm: () => {
            state?.resolve(true);
            current = null;
            publish();
        }, onCancel: () => {
            state?.resolve(false);
            current = null;
            publish();
        } }));
}
