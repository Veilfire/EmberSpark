import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatTimestamp } from "../lib/utils";
export default function Ops() {
    const health = useQuery({
        queryKey: ["ops-health"],
        queryFn: () => api.get("/api/ops/health"),
    });
    const residency = useQuery({
        queryKey: ["ops-residency"],
        queryFn: () => api.get("/api/ops/data-residency"),
    });
    const plugins = useQuery({
        queryKey: ["ops-plugins"],
        queryFn: () => api.get("/api/ops/plugins"),
    });
    const [logs, setLogs] = useState([]);
    useEffect(() => {
        const source = new EventSource("/api/stream/logs", { withCredentials: true });
        source.onmessage = (e) => {
            try {
                const payload = JSON.parse(e.data);
                setLogs((prev) => [...prev.slice(-400), payload]);
            }
            catch {
                /* ignore */
            }
        };
        return () => source.close();
    }, []);
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsx("h2", { className: "text-2xl font-bold", children: "Ops" }), _jsx("p", { className: "text-spark-muted text-sm", children: "Host health, data residency, plugin registry, live log tail." })] }), _jsxs("section", { className: "grid grid-cols-1 md:grid-cols-3 gap-4", children: [_jsxs("div", { className: "panel p-4", children: [_jsx("div", { className: "label", children: "Sandbox" }), _jsx("div", { className: `text-lg font-semibold ${health.data?.ok ? "text-spark-good" : "text-spark-danger"}`, children: health.data?.ok ? health.data.sandbox_backend : "unavailable" }), health.data?.sandbox_error && (_jsx("div", { className: "text-xs text-spark-danger mt-1", children: health.data.sandbox_error }))] }), _jsxs("div", { className: "panel p-4", children: [_jsx("div", { className: "label", children: "Disk free" }), _jsx("div", { className: "text-lg font-semibold", children: residency.data ? formatBytes(residency.data.disk.free) : "…" }), _jsxs("div", { className: "text-xs text-spark-muted", children: ["of ", residency.data ? formatBytes(residency.data.disk.total) : "…"] })] }), _jsxs("div", { className: "panel p-4", children: [_jsx("div", { className: "label", children: "Registered plugins" }), _jsx("div", { className: "text-lg font-semibold", children: plugins.data?.length ?? 0 })] })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Data residency" }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "what" }), _jsx("th", { className: "text-left", children: "path" }), _jsx("th", { className: "text-left", children: "exists" }), _jsx("th", { className: "text-left", children: "size" })] }) }), _jsx("tbody", { children: residency.data &&
                                    ["db", "chroma", "logs", "scheduler", "web_token"].map((k) => {
                                        const info = residency.data?.[k];
                                        if (!info)
                                            return null;
                                        return (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1", children: k }), _jsx("td", { className: "font-mono text-xs", children: info.path }), _jsx("td", { children: info.exists ? "yes" : "no" }), _jsx("td", { children: formatBytes(info.size_bytes) })] }, k));
                                    }) })] })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Plugin registry" }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { className: "text-spark-muted text-xs uppercase", children: _jsxs("tr", { children: [_jsx("th", { className: "text-left", children: "name" }), _jsx("th", { className: "text-left", children: "version" }), _jsx("th", { className: "text-left", children: "hash" }), _jsx("th", { className: "text-left", children: "first seen" }), _jsx("th", { className: "text-left", children: "last seen" })] }) }), _jsx("tbody", { children: (plugins.data ?? []).map((p) => (_jsxs("tr", { className: "border-t border-spark-border", children: [_jsx("td", { className: "py-1 font-mono", children: p.name }), _jsx("td", { children: p.version }), _jsx("td", { className: "font-mono text-xs truncate max-w-xs", children: p.module_hash.slice(0, 16) }), _jsx("td", { className: "text-xs", children: formatTimestamp(p.first_seen_at) }), _jsx("td", { className: "text-xs", children: formatTimestamp(p.last_seen_at) })] }, p.name))) })] })] }), _jsxs("section", { className: "panel p-4", children: [_jsx("h3", { className: "font-semibold mb-2", children: "Log tail (live)" }), _jsx("div", { className: "bg-spark-bg border border-spark-border rounded p-2 h-96 overflow-auto font-mono text-xs", children: logs.map((line, i) => (_jsx("div", { children: JSON.stringify(line) }, i))) })] })] }));
}
function formatBytes(b) {
    if (b < 1024)
        return `${b} B`;
    if (b < 1024 ** 2)
        return `${(b / 1024).toFixed(1)} KiB`;
    if (b < 1024 ** 3)
        return `${(b / 1024 ** 2).toFixed(1)} MiB`;
    return `${(b / 1024 ** 3).toFixed(2)} GiB`;
}
