import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { Activity, AlertTriangle, Blocks, Bot, Brain, Calendar, ChartBar, ChevronsLeft, ChevronsRight, Coins, Download, Eye, FileClock, Filter, KeyRound, LayoutDashboard, LogOut, MessageSquare, Package, Search, Settings as SettingsIcon, Shield, Sparkles, User2, Wrench, Zap, } from "lucide-react";
import { cn } from "../lib/utils";
import { useAuth } from "../hooks/useAuth";
import { NotificationBell } from "./NotificationBell";
const NAV_GROUPS = [
    {
        label: "Run",
        items: [
            { to: "/", label: "Overview", Icon: LayoutDashboard },
            { to: "/agents", label: "Agents", Icon: Bot },
            { to: "/chat", label: "Chat", Icon: MessageSquare },
            { to: "/runs", label: "Runs", Icon: Activity },
            { to: "/scheduler", label: "Scheduler", Icon: Calendar },
            { to: "/templates", label: "Templates", Icon: Package },
        ],
    },
    {
        label: "Observe",
        items: [
            { to: "/cost", label: "Cost", Icon: Coins },
            { to: "/memory", label: "Memory", Icon: Brain },
            { to: "/skills", label: "Skills", Icon: Sparkles },
            { to: "/stats", label: "Stats", Icon: ChartBar },
            { to: "/downloads", label: "Downloads", Icon: Download },
        ],
    },
    {
        label: "Secure",
        items: [
            { to: "/security", label: "Security", Icon: Shield },
            { to: "/secrets", label: "Secrets", Icon: KeyRound },
            { to: "/guardrails", label: "Guardrails", Icon: AlertTriangle },
            { to: "/filtering", label: "Filtering", Icon: Filter },
            { to: "/forensic", label: "Forensic", Icon: Eye },
            { to: "/audit", label: "Audit", Icon: FileClock },
        ],
    },
    {
        label: "System",
        items: [
            { to: "/provider", label: "Provider", Icon: Zap },
            { to: "/persona", label: "Persona", Icon: User2 },
            { to: "/plugins", label: "Plugins", Icon: Blocks },
            { to: "/ops", label: "Ops", Icon: Wrench },
            { to: "/settings", label: "Settings", Icon: SettingsIcon },
        ],
    },
];
export function Shell({ children }) {
    const { subject, role, logout } = useAuth();
    // Collapsed state persisted to localStorage.
    const [collapsed, setCollapsed] = useState(() => {
        if (typeof window === "undefined")
            return false;
        return window.localStorage.getItem("spark.sidebar.collapsed") === "1";
    });
    useEffect(() => {
        window.localStorage.setItem("spark.sidebar.collapsed", collapsed ? "1" : "0");
    }, [collapsed]);
    // Trigger command palette from the nav's search affordance.
    const openCommandPalette = () => {
        const e = new KeyboardEvent("keydown", {
            key: "k",
            metaKey: true,
            bubbles: true,
        });
        window.dispatchEvent(e);
    };
    const asideWidth = collapsed ? "md:w-14" : "md:w-56";
    return (_jsxs("div", { className: "flex min-h-screen flex-col md:flex-row bg-spark-bg", children: [_jsxs("aside", { className: cn("transition-all duration-200 bg-spark-panel border-r border-spark-border flex md:flex-col gap-1 overflow-x-auto md:overflow-visible", asideWidth), children: [_jsxs("div", { className: "flex items-center justify-between p-4 shrink-0 md:border-b md:border-spark-border", children: [_jsxs("div", { className: "flex items-center gap-2 min-w-0", children: [_jsx("img", { src: "/spark-icon.png", alt: "", className: "w-6 h-6 shrink-0 rounded", "aria-hidden": "true" }), !collapsed && (_jsx("h1", { className: "font-bold text-lg tracking-tight", children: "Spark" }))] }), !collapsed && _jsx(NotificationBell, {})] }), _jsxs("button", { className: cn("mx-2 mb-2 flex items-center gap-2 rounded-md border border-spark-border bg-spark-bg text-xs text-spark-muted hover:border-spark-accent/50 hover:text-spark-text transition px-2 py-1.5 shrink-0", collapsed && "justify-center px-1"), onClick: openCommandPalette, "aria-label": "Search", children: [_jsx(Search, { className: "w-3.5 h-3.5 shrink-0" }), !collapsed && (_jsxs(_Fragment, { children: [_jsx("span", { className: "flex-1 text-left", children: "Search\u2026" }), _jsx("span", { className: "kbd", children: "\u2318K" })] }))] }), _jsx("nav", { className: "flex-1 flex md:flex-col gap-0.5 overflow-y-auto px-2", children: NAV_GROUPS.map((group) => (_jsxs("div", { className: "mb-3", children: [!collapsed && (_jsx("div", { className: "text-[10px] uppercase tracking-wider text-spark-muted px-2 py-1.5", children: group.label })), group.items.map(({ to, label, Icon }) => (_jsx(NavLink, { to: to, end: to === "/", title: collapsed ? label : undefined, className: ({ isActive }) => cn("flex items-center gap-2 px-2 py-1.5 rounded-md text-sm shrink-0 transition-colors relative", collapsed && "justify-center", isActive
                                        ? "bg-spark-accent/10 text-spark-accent"
                                        : "text-spark-muted hover:bg-spark-border/50 hover:text-spark-text"), children: ({ isActive }) => (_jsxs(_Fragment, { children: [isActive && (_jsx("span", { className: "absolute left-0 top-1 bottom-1 w-0.5 bg-spark-accent rounded-full" })), _jsx(Icon, { className: "w-4 h-4 shrink-0" }), !collapsed && _jsx("span", { children: label })] })) }, to)))] }, group.label))) }), _jsxs("div", { className: "hidden md:flex flex-col border-t border-spark-border shrink-0 mt-2", children: [!collapsed && subject && (_jsxs("div", { className: "px-3 py-2 flex items-center gap-2 min-w-0", children: [_jsx("div", { className: "w-7 h-7 rounded-full bg-spark-accent/20 flex items-center justify-center text-spark-accent font-bold text-[10px] shrink-0", children: subject.slice(0, 2).toUpperCase() }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "truncate text-xs", children: subject }), _jsx("div", { className: "text-spark-muted text-[10px] leading-tight", children: role })] })] })), collapsed ? (_jsxs("div", { className: "flex flex-col gap-1 p-2", children: [_jsx("button", { className: "btn-icon w-full flex items-center justify-center", onClick: logout, title: "Sign out", "aria-label": "Sign out", children: _jsx(LogOut, { className: "w-4 h-4" }) }), _jsx("button", { className: "btn-icon w-full flex items-center justify-center", onClick: () => setCollapsed(false), title: "Expand sidebar", "aria-label": "Expand sidebar", children: _jsx(ChevronsRight, { className: "w-4 h-4" }) })] })) : (_jsxs("div", { className: "flex items-center gap-1 px-2 pb-2 pt-1", children: [_jsxs("button", { className: "btn flex-1 flex items-center justify-center gap-1.5 text-xs py-1", onClick: logout, "aria-label": "Sign out", children: [_jsx(LogOut, { className: "w-3.5 h-3.5" }), _jsx("span", { children: "Sign out" })] }), _jsx("button", { className: "btn-icon shrink-0", onClick: () => setCollapsed(true), title: "Collapse sidebar", "aria-label": "Collapse sidebar", children: _jsx(ChevronsLeft, { className: "w-4 h-4" }) })] }))] })] }), _jsx("main", { className: "flex-1 p-4 md:p-6 overflow-auto animate-enter", children: children })] }));
}
