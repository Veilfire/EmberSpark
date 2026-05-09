import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
/** Persistent banner that surfaces the most recent critical audit entry. */
export function IncidentBanner() {
    const [incident, setIncident] = useState(null);
    useEffect(() => {
        let cancelled = false;
        async function load() {
            try {
                const rows = await api.get("/api/audit/?limit=20&min_severity=critical");
                if (!cancelled)
                    setIncident(rows[0] ?? null);
            }
            catch {
                /* silent */
            }
        }
        load();
        const interval = window.setInterval(load, 30_000);
        return () => {
            cancelled = true;
            window.clearInterval(interval);
        };
    }, []);
    if (!incident)
        return null;
    return (_jsxs("div", { className: "bg-spark-danger/10 border-b border-spark-danger text-spark-danger px-4 py-2 text-sm flex items-center justify-between", children: [_jsxs("div", { children: [_jsx("span", { className: "font-bold", children: "\u26A0 Incident" }), " \u2014 ", incident.kind, " \u00B7 ", incident.target, incident.reason && _jsxs("span", { className: "text-xs ml-2", children: ["(", incident.reason, ")"] })] }), _jsx("button", { className: "btn btn-danger text-xs", onClick: () => setIncident(null), children: "Dismiss" })] }));
}
