import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Modal } from "./Modal";
const GROUPS = [
    {
        title: "Navigation",
        items: [
            ["⌘K", "Open command palette"],
            ["?", "Show this help"],
            ["/", "Focus search on current page"],
            ["Esc", "Close modal / dialog"],
        ],
    },
    {
        title: "Chat",
        items: [
            ["Enter", "Send message"],
            ["Shift+Enter", "New line"],
        ],
    },
];
export function ShortcutHelp({ open, onClose }) {
    return (_jsx(Modal, { open: open, onClose: onClose, children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[80vh] overflow-auto p-6 shadow-2xl", children: [_jsx("h2", { className: "text-lg font-bold mb-4", children: "Keyboard shortcuts" }), _jsx("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-6", children: GROUPS.map((g) => (_jsxs("div", { children: [_jsx("h3", { className: "text-xs uppercase tracking-wide text-spark-muted mb-2", children: g.title }), _jsx("dl", { className: "space-y-1.5", children: g.items.map(([k, label]) => (_jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsx("dd", { className: "text-sm", children: label }), _jsx("dt", { className: "kbd whitespace-nowrap", children: k })] }, k))) })] }, g.title))) }), _jsx("div", { className: "mt-4 pt-4 border-t border-spark-border flex justify-end", children: _jsx("button", { className: "btn", onClick: onClose, children: "Close (Esc)" }) })] }) }));
}
