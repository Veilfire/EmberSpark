import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from "react";
import { Bell, X } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useNotifications } from "../hooks/useNotifications";
import { cn, formatTimestamp } from "../lib/utils";
/**
 * Top-bar bell icon with unread count badge and a slide-in drawer listing
 * recent notifications. Clicking a row marks it read and navigates to its
 * action_url if any.
 */
export function NotificationBell() {
    const { notifications, unread, markRead, dismiss, readAll } = useNotifications();
    const [open, setOpen] = useState(false);
    const drawerRef = useRef(null);
    useEffect(() => {
        if (!open)
            return;
        const handler = (e) => {
            if (!drawerRef.current)
                return;
            if (!drawerRef.current.contains(e.target)) {
                setOpen(false);
            }
        };
        window.addEventListener("mousedown", handler);
        return () => window.removeEventListener("mousedown", handler);
    }, [open]);
    return (_jsxs("div", { className: "relative", children: [_jsxs("button", { type: "button", onClick: () => setOpen((v) => !v), "aria-label": "Open notifications", className: cn("relative flex items-center justify-center w-8 h-8 rounded-md", "text-spark-muted hover:text-spark-text hover:bg-spark-border/50"), children: [_jsx(Bell, { className: "w-4 h-4" }), unread.total > 0 && (_jsx("span", { className: "absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-spark-danger text-white text-[10px] font-semibold flex items-center justify-center", "aria-label": `${unread.total} unread`, children: unread.total > 99 ? "99+" : unread.total }))] }), open && (_jsxs("div", { ref: drawerRef, className: "fixed md:absolute top-14 md:top-auto md:mt-2 left-2 md:left-full md:right-auto md:ml-2 w-96 max-w-[calc(100vw-1rem)] max-h-[70vh] bg-spark-panel border border-spark-border rounded-md shadow-xl overflow-hidden flex flex-col z-50", children: [_jsxs("header", { className: "px-3 py-2 border-b border-spark-border flex items-center justify-between", children: [_jsxs("div", { className: "font-semibold text-sm", children: ["Notifications", unread.total > 0 && (_jsxs("span", { className: "ml-2 text-xs text-spark-muted", children: [unread.total, " unread"] }))] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("button", { type: "button", onClick: () => void readAll(), className: "text-xs text-spark-muted hover:text-spark-text", children: "Read all" }), _jsx("button", { type: "button", onClick: () => setOpen(false), "aria-label": "Close notifications", className: "text-spark-muted hover:text-spark-text", children: _jsx(X, { className: "w-4 h-4" }) })] })] }), _jsx("div", { className: "overflow-y-auto flex-1", children: notifications.length === 0 ? (_jsx("div", { className: "p-4 text-xs text-spark-muted text-center", children: "No notifications." })) : (_jsx("ul", { className: "divide-y divide-spark-border", children: notifications.map((n) => (_jsx(NotificationRow, { notification: n, onRead: () => void markRead(n.id), onDismiss: () => void dismiss(n.id), onNavigate: () => setOpen(false) }, n.id))) })) }), _jsx("footer", { className: "px-3 py-2 border-t border-spark-border", children: _jsx(NavLink, { to: "/settings", className: "text-xs text-spark-muted hover:text-spark-text", onClick: () => setOpen(false), children: "Notification settings" }) })] }))] }));
}
// Belt + suspenders for action_url: the backend already refuses non-
// relative URLs at NotificationService.notify time, but we re-check here
// so any DB row that somehow bypassed the backend still cannot be
// rendered as an arbitrary-URL navigation target.
function safeActionHref(url) {
    if (!url)
        return null;
    const trimmed = url.trim();
    if (!trimmed.startsWith("/"))
        return null;
    if (trimmed.startsWith("//"))
        return null;
    if (/[\r\n\\]/.test(trimmed))
        return null;
    return trimmed;
}
function NotificationRow({ notification, onRead, onDismiss, onNavigate, }) {
    const severityClass = notification.severity === "critical"
        ? "bg-spark-danger/10"
        : notification.severity === "elevated"
            ? "bg-spark-warning/5"
            : "";
    const unread = notification.read_at === null;
    const actionHref = safeActionHref(notification.action_url);
    return (_jsx("li", { className: cn("p-3 hover:bg-spark-border/30 flex flex-col gap-1", severityClass, unread && "border-l-2 border-spark-accent"), children: _jsxs("div", { className: "flex items-start justify-between gap-2", children: [_jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [notification.kind.startsWith("gate_") && (_jsx("span", { className: "chip chip-warn text-[10px]", title: "Gate refused an operation. Click the title to tune.", children: "Gate" })), _jsx("span", { className: "text-xs uppercase tracking-wide text-spark-muted", children: notification.kind.replace(/_/g, " ") }), _jsx("span", { className: "text-xs text-spark-muted", children: formatTimestamp(notification.created_at) })] }), actionHref ? (_jsx(NavLink, { to: actionHref, onClick: () => {
                                onRead();
                                onNavigate();
                            }, className: "block font-semibold text-sm text-spark-text hover:underline truncate", children: notification.title })) : (_jsx("div", { className: "font-semibold text-sm text-spark-text truncate", children: notification.title })), notification.body && (_jsx("p", { className: "text-xs text-spark-muted mt-1 line-clamp-2", children: notification.body }))] }), _jsx("button", { type: "button", onClick: onDismiss, className: "text-spark-muted hover:text-spark-danger shrink-0", "aria-label": "Dismiss", children: _jsx(X, { className: "w-3.5 h-3.5" }) })] }) }));
}
