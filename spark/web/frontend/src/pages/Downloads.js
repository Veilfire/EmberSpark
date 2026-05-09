import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { Download, Eye, FileText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MarkdownView } from "../components/MarkdownView";
import { Modal } from "../components/Modal";
import { api } from "../lib/api";
import { formatTimestamp } from "../lib/utils";
function formatSize(bytes) {
    if (bytes < 1024)
        return `${bytes} B`;
    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024)
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
function isMarkdown(path) {
    const lower = path.toLowerCase();
    return lower.endsWith(".md") || lower.endsWith(".markdown");
}
export default function Downloads() {
    const { data, isLoading, isError, error } = useQuery({
        queryKey: ["deliverables"],
        queryFn: () => api.get("/api/deliverables/"),
    });
    const [previewPath, setPreviewPath] = useState(null);
    if (isLoading) {
        return _jsx("div", { className: "p-4 text-spark-muted", children: "Loading deliverables\u2026" });
    }
    if (isError) {
        return (_jsxs("div", { className: "p-4", children: [_jsx("h1", { className: "text-xl font-bold mb-2", children: "Downloads" }), _jsx("div", { className: "text-spark-danger text-sm", children: error?.message ||
                        "Data volume not enabled. Set spec.data_volume.enabled in ~/.spark/spark.yaml and restart." })] }));
    }
    const files = data?.files ?? [];
    return (_jsxs("div", { className: "p-4 space-y-4", children: [_jsxs("header", { children: [_jsx("h1", { className: "text-xl font-bold", children: "Downloads" }), _jsxs("p", { className: "text-xs text-spark-muted mt-1", children: ["Files written by plugins to the data volume's deliverables directory. New files trigger a notification (unless you've disabled the \"download ready\" category in Settings).", _jsx("span", { className: "ml-1", children: "Markdown files have an inline preview \u2014 click the eye icon." })] }), data && (_jsxs("div", { className: "text-xs text-spark-muted mt-1", children: ["Root: ", _jsx("code", { children: data.root }), " \u00B7 ", files.length, " file", files.length === 1 ? "" : "s", " \u00B7 ", formatSize(data.total_size_bytes)] }))] }), files.length === 0 ? (_jsx("div", { className: "p-6 text-center text-spark-muted text-sm border border-spark-border rounded-md", children: "No deliverables yet. When a plugin writes a file to the deliverables directory it will appear here and the notification bell will light up." })) : (_jsx("ul", { className: "divide-y divide-spark-border border border-spark-border rounded-md overflow-hidden", children: files.map((file) => (_jsxs("li", { className: "p-3 flex items-center justify-between gap-3 hover:bg-spark-border/30", children: [_jsxs("div", { className: "flex items-center gap-3 min-w-0 flex-1", children: [_jsx(FileText, { className: "w-4 h-4 text-spark-muted shrink-0" }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "font-medium text-sm truncate", children: file.relative_path }), _jsxs("div", { className: "text-xs text-spark-muted flex items-center gap-2 flex-wrap", children: [_jsx("span", { children: formatSize(file.size_bytes) }), _jsx("span", { children: "\u00B7" }), _jsx("span", { children: formatTimestamp(file.modified_at) }), file.task_name && (_jsxs(_Fragment, { children: [_jsx("span", { children: "\u00B7" }), _jsxs("span", { children: ["task: ", file.task_name] })] })), file.run_id && (_jsxs(_Fragment, { children: [_jsx("span", { children: "\u00B7" }), _jsxs(Link, { to: `/runs/${encodeURIComponent(file.run_id)}/replay`, className: "text-spark-link hover:underline font-mono", children: ["from run ", file.run_id.slice(-8)] })] })), file.source && file.source !== "engine" && (_jsxs(_Fragment, { children: [_jsx("span", { children: "\u00B7" }), _jsx("span", { className: "italic", children: file.source })] }))] })] })] }), _jsxs("div", { className: "flex items-center gap-2 shrink-0", children: [isMarkdown(file.relative_path) && (_jsxs("button", { type: "button", onClick: () => setPreviewPath(file.relative_path), className: "btn text-xs flex items-center gap-1", title: "Preview rendered markdown", children: [_jsx(Eye, { className: "w-3.5 h-3.5" }), "Preview"] })), _jsxs("a", { href: `/api/deliverables/${encodeURIComponent(file.relative_path)}`, download: true, className: "btn text-xs flex items-center gap-1", children: [_jsx(Download, { className: "w-3.5 h-3.5" }), "Download"] })] })] }, file.relative_path))) })), previewPath && (_jsx(MarkdownPreviewModal, { relativePath: previewPath, onClose: () => setPreviewPath(null) }))] }));
}
function MarkdownPreviewModal({ relativePath, onClose, }) {
    // Hold the body in component state — the deliverables endpoint streams
    // the file as text/markdown, not JSON, so we can't use the typed
    // `api.get` helper. The fetch is keyed on the path and runs once per
    // open; closing the modal unmounts the component.
    const [content, setContent] = useState(null);
    const [loadError, setLoadError] = useState(null);
    useEffect(() => {
        let cancelled = false;
        setContent(null);
        setLoadError(null);
        fetch(`/api/deliverables/${encodeURIComponent(relativePath)}`, {
            credentials: "same-origin",
        })
            .then(async (r) => {
            if (!r.ok) {
                throw new Error(`fetch failed: ${r.status} ${r.statusText}`);
            }
            return r.text();
        })
            .then((text) => {
            if (!cancelled)
                setContent(text);
        })
            .catch((err) => {
            if (!cancelled)
                setLoadError(err.message);
        });
        return () => {
            cancelled = true;
        };
    }, [relativePath]);
    // Wrap the panel in the shared Modal component, which portals to
    // document.body so the backdrop blur covers the full viewport (no
    // top-bar gap caused by a parent stacking context). Modal handles
    // Esc, focus trap, body scroll lock, and ARIA semantics.
    return (_jsx(Modal, { open: true, onClose: onClose, children: _jsxs("div", { className: "panel max-w-3xl w-full max-h-[85vh] flex flex-col overflow-hidden", children: [_jsxs("header", { className: "px-4 py-3 border-b border-spark-border flex items-center justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "text-xs uppercase tracking-wide text-spark-muted", children: "Preview" }), _jsx("div", { className: "font-mono text-sm truncate", children: relativePath })] }), _jsxs("div", { className: "flex items-center gap-2 shrink-0", children: [_jsxs("a", { href: `/api/deliverables/${encodeURIComponent(relativePath)}`, download: true, className: "btn text-xs flex items-center gap-1", children: [_jsx(Download, { className: "w-3.5 h-3.5" }), "Download"] }), _jsx("button", { type: "button", onClick: onClose, className: "p-1.5 rounded-md border border-transparent hover:bg-spark-border/50 hover:border-spark-border text-spark-muted hover:text-spark-text transition-colors", "aria-label": "Close preview", children: _jsx(X, { className: "w-4 h-4" }) })] })] }), _jsx("div", { className: "flex-1 overflow-y-auto p-5", children: loadError ? (_jsxs("div", { className: "text-spark-danger text-sm", children: ["Failed to load file: ", loadError] })) : content === null ? (_jsx("div", { className: "text-spark-muted text-sm", children: "Loading\u2026" })) : (_jsx(MarkdownView, { content: content, className: "text-spark-text text-sm" })) })] }) }));
}
