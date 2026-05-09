import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Link, useLocation } from "react-router-dom";
import { ChevronRight } from "lucide-react";
export function PageHeader({ icon, title, subtitle, actions, breadcrumbs, }) {
    return (_jsxs("header", { className: "flex flex-col gap-1 mb-6", children: [breadcrumbs && breadcrumbs.length > 0 && (_jsx("nav", { className: "flex items-center gap-1 text-xs text-spark-muted mb-1", children: breadcrumbs.map((crumb, i) => (_jsxs("span", { className: "flex items-center gap-1", children: [i > 0 && _jsx(ChevronRight, { className: "w-3 h-3" }), crumb.to ? (_jsx(Link, { to: crumb.to, className: "hover:text-spark-text transition", children: crumb.label })) : (_jsx("span", { children: crumb.label }))] }, i))) })), _jsxs("div", { className: "flex items-start justify-between gap-4", children: [_jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("h1", { className: "text-2xl font-bold tracking-tight flex items-center gap-2", children: [icon && _jsx("span", { className: "text-spark-accent", children: icon }), title] }), subtitle && (_jsx("p", { className: "text-spark-muted text-sm mt-1", children: subtitle }))] }), actions && _jsx("div", { className: "flex gap-2 shrink-0", children: actions })] })] }));
}
/** Auto-generates a breadcrumb trail from the current URL path. */
export function useAutoBreadcrumbs(override) {
    const location = useLocation();
    if (override)
        return override;
    const parts = location.pathname.split("/").filter(Boolean);
    if (parts.length === 0)
        return [];
    const crumbs = [];
    let path = "";
    parts.forEach((p, i) => {
        path += "/" + p;
        crumbs.push({
            label: p.charAt(0).toUpperCase() + p.slice(1).replace(/-/g, " "),
            to: i === parts.length - 1 ? undefined : path,
        });
    });
    return crumbs;
}
