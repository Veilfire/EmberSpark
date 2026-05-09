import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Blocks, Check, Download, GitFork, Key, Package, Pencil, Plus, Search, Star, Trash2, X, } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { MarkdownView } from "../components/MarkdownView";
import { TemplateEditor } from "../components/TemplateEditor";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/primitives";
import { ConfirmDialog } from "../components/ConfirmDialog";
/**
 * Agent template gallery (H1.1). Lets the operator browse ready-to-run
 * templates, preview the README + YAMLs, and install with one click.
 */
export default function Templates() {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const list = useQuery({
        queryKey: ["templates"],
        queryFn: () => api.get("/api/templates/"),
    });
    const [selected, setSelected] = useState(null);
    const [editorOpen, setEditorOpen] = useState(false);
    const [forkSource, setForkSource] = useState(null);
    const [filter, setFilter] = useState("");
    const [deleteConfirm, setDeleteConfirm] = useState(null);
    const [starred, setStarred] = useState(() => {
        if (typeof window === "undefined")
            return new Set();
        try {
            return new Set(JSON.parse(localStorage.getItem("spark.templates.starred") || "[]"));
        }
        catch {
            return new Set();
        }
    });
    // false = closed, null = create new, string = edit existing
    function toggleStar(name) {
        const next = new Set(starred);
        if (next.has(name))
            next.delete(name);
        else
            next.add(name);
        setStarred(next);
        localStorage.setItem("spark.templates.starred", JSON.stringify([...next]));
    }
    const filteredTemplates = useMemo(() => {
        const items = list.data ?? [];
        const q = filter.toLowerCase();
        const matches = items.filter((t) => !q ||
            t.name.toLowerCase().includes(q) ||
            t.description.toLowerCase().includes(q) ||
            t.plugins_required.some((p) => p.toLowerCase().includes(q)));
        // Sort: starred first, then alphabetical
        return matches.sort((a, b) => {
            const as = starred.has(a.name);
            const bs = starred.has(b.name);
            if (as !== bs)
                return as ? -1 : 1;
            return a.name.localeCompare(b.name);
        });
    }, [list.data, filter, starred]);
    async function deleteTemplate(name) {
        try {
            await api.del(`/api/templates/${encodeURIComponent(name)}`);
            toast.success(`Template "${name}" deleted`);
            qc.invalidateQueries({ queryKey: ["templates"] });
        }
        catch (err) {
            toast.error(`Delete failed: ${err}`);
        }
        finally {
            setDeleteConfirm(null);
        }
    }
    const detail = useQuery({
        queryKey: ["templates", selected],
        queryFn: () => api.get(`/api/templates/${encodeURIComponent(selected ?? "")}`),
        enabled: !!selected,
    });
    const install = useMutation({
        mutationFn: async ({ name, overwrite }) => {
            return api.post(`/api/templates/${encodeURIComponent(name)}/install`, { overwrite });
        },
        onSuccess: (result) => {
            qc.invalidateQueries({ queryKey: ["plugin-configs"] });
            qc.invalidateQueries({ queryKey: ["agents"] });
            // Surface any follow-up work as a toast instead of hijacking the
            // navigation — the operator likely wants to see the newly-installed
            // agent first.
            const pending = result.plugins_still_to_configure;
            const secrets = result.secrets_still_to_populate;
            if (pending.length > 0) {
                toast.message(`${pending.length} plugin${pending.length === 1 ? "" : "s"} still need configuration`, {
                    description: pending.join(", "),
                    action: {
                        label: "Configure",
                        onClick: () => navigate(`/plugins?focus=${encodeURIComponent(pending[0])}`),
                    },
                });
            }
            else if (secrets.length > 0) {
                toast.message(`${secrets.length} secret${secrets.length === 1 ? "" : "s"} still missing`, {
                    description: secrets.join(", "),
                    action: {
                        label: "Open secrets",
                        onClick: () => navigate("/security?tab=secrets"),
                    },
                });
            }
            else {
                toast.success(`Installed ${result.agent_name}`);
            }
            navigate(`/agents/${encodeURIComponent(result.agent_name)}`);
        },
    });
    return (_jsxs("div", { className: "space-y-4", children: [_jsx(PageHeader, { icon: _jsx(Package, { className: "w-6 h-6" }), title: "Templates", subtitle: "Ready-to-run agent + task pairs. Install one, configure the plugins it needs, and you're done.", actions: _jsxs("button", { className: "btn btn-primary flex items-center gap-1", onClick: () => setEditorOpen(null), children: [_jsx(Plus, { className: "w-4 h-4" }), " Create Template"] }) }), (list.data ?? []).length > 0 && (_jsxs("div", { className: "relative max-w-md", children: [_jsx(Search, { className: "w-4 h-4 text-spark-muted absolute left-3 top-1/2 -translate-y-1/2" }), _jsx("input", { className: "input w-full pl-9", placeholder: `Search ${list.data?.length ?? 0} templates…`, value: filter, onChange: (e) => setFilter(e.target.value) })] })), list.isLoading && (_jsx("div", { className: "text-spark-muted text-sm", children: "Loading templates\u2026" })), list.isError && (_jsxs("div", { className: "text-spark-danger text-sm", children: ["Failed to load templates: ", list.error?.message] })), list.data && list.data.length === 0 && (_jsx(EmptyState, { icon: _jsx(Package, { className: "w-10 h-10" }), title: "No templates available", description: "Create your first custom template to share across agents.", action: {
                    label: "Create Template",
                    onClick: () => setEditorOpen(null),
                } })), _jsx("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3", children: filteredTemplates.map((tpl) => (_jsx(TemplateCard, { tpl: tpl, starred: starred.has(tpl.name), onPreview: () => setSelected(tpl.name), onEdit: () => setEditorOpen(tpl.name), onFork: () => setForkSource(tpl.name), onQuickInstall: () => install.mutate({ name: tpl.name, overwrite: false }), onToggleStar: () => toggleStar(tpl.name), onDelete: () => setDeleteConfirm(tpl.name), installing: install.isPending && install.variables?.name === tpl.name }, tpl.name))) }), selected && detail.data && (_jsx(TemplateDrawer, { detail: detail.data, onClose: () => setSelected(null), onInstall: (overwrite) => install.mutate({ name: detail.data.name, overwrite }), installing: install.isPending, installResult: install.data ?? null, installError: install.error })), editorOpen !== false && (_jsx(TemplateEditor, { editName: editorOpen, onClose: () => setEditorOpen(false), onSaved: () => {
                    setEditorOpen(false);
                    qc.invalidateQueries({ queryKey: ["templates"] });
                } })), forkSource && (_jsx(TemplateEditor, { editName: forkSource, forkMode: true, onClose: () => setForkSource(null), onSaved: () => {
                    setForkSource(null);
                    qc.invalidateQueries({ queryKey: ["templates"] });
                } })), _jsx(ConfirmDialog, { open: !!deleteConfirm, title: `Delete template "${deleteConfirm}"?`, description: "This removes the template from disk. Installed agents are unaffected.", tone: "danger", confirmLabel: "Delete", requireTypedName: deleteConfirm ?? undefined, onCancel: () => setDeleteConfirm(null), onConfirm: () => deleteConfirm && deleteTemplate(deleteConfirm) })] }));
}
function TemplateCard({ tpl, starred, onPreview, onEdit, onFork, onQuickInstall, onToggleStar, onDelete, installing, }) {
    const stop = (e) => e.stopPropagation();
    return (_jsxs("div", { className: "panel-interactive text-left p-4 flex flex-col gap-3 cursor-pointer relative group", onClick: onPreview, children: [_jsx("button", { className: `absolute top-3 left-3 transition ${starred
                    ? "text-spark-accent"
                    : "text-spark-muted opacity-0 group-hover:opacity-100 hover:text-spark-accent"}`, onClick: (e) => {
                    stop(e);
                    onToggleStar();
                }, title: starred ? "Unstar" : "Star", children: _jsx(Star, { className: "w-4 h-4", fill: starred ? "currentColor" : "none" }) }), _jsxs("div", { className: "absolute top-2 right-2 flex items-center opacity-0 group-hover:opacity-100 transition", children: [_jsx("button", { className: "btn-icon", onClick: (e) => {
                            stop(e);
                            onFork();
                        }, title: "Fork template", children: _jsx(GitFork, { className: "w-3.5 h-3.5" }) }), _jsx("button", { className: "btn-icon", onClick: (e) => {
                            stop(e);
                            onEdit();
                        }, title: "Edit template", children: _jsx(Pencil, { className: "w-3.5 h-3.5" }) }), _jsx("button", { className: "btn-icon hover:text-spark-danger", onClick: (e) => {
                            stop(e);
                            onDelete();
                        }, title: "Delete template", children: _jsx(Trash2, { className: "w-3.5 h-3.5" }) })] }), _jsxs("div", { className: "flex items-center gap-2 pl-6", children: [_jsx(Package, { className: "w-4 h-4 text-spark-accent" }), _jsx("h3", { className: "font-semibold text-spark-text", children: tpl.name })] }), _jsx("p", { className: "text-xs text-spark-muted line-clamp-3 flex-1", children: tpl.description }), _jsxs("div", { className: "flex flex-wrap gap-1", children: [tpl.plugins_required.slice(0, 4).map((p) => (_jsxs("span", { className: "chip text-[10px] gap-1 flex items-center", children: [_jsx(Blocks, { className: "w-3 h-3" }), p] }, p))), tpl.plugins_required.length > 4 && (_jsxs("span", { className: "chip text-[10px]", children: ["+", tpl.plugins_required.length - 4] }))] }), tpl.secrets_required.length > 0 && (_jsx("div", { className: "flex flex-wrap gap-1", children: tpl.secrets_required.map((s) => (_jsxs("span", { className: "chip chip-warn text-[10px] gap-1 flex items-center", children: [_jsx(Key, { className: "w-3 h-3" }), s] }, s))) })), _jsxs("div", { className: "flex gap-2 pt-1 border-t border-spark-border", children: [_jsxs("button", { className: "btn btn-primary flex-1 flex items-center justify-center gap-1 text-xs", onClick: (e) => {
                            stop(e);
                            onQuickInstall();
                        }, disabled: installing, children: [_jsx(Download, { className: "w-3 h-3" }), installing ? "Installing…" : "Quick Install"] }), _jsx("button", { className: "btn text-xs", onClick: (e) => {
                            stop(e);
                            onPreview();
                        }, children: "Preview" })] })] }));
}
function TemplateDrawer({ detail, onClose, onInstall, installing, installResult, installError, }) {
    const [overwrite, setOverwrite] = useState(false);
    return (_jsx(Modal, { open: true, onClose: onClose, children: _jsxs("div", { className: "w-full max-w-3xl max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl ml-auto", children: [_jsxs("header", { className: "sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3 flex items-center justify-between z-10", children: [_jsxs("div", { children: [_jsx("h3", { className: "text-lg font-bold", children: detail.name }), _jsx("p", { className: "text-xs text-spark-muted", children: detail.description })] }), _jsx("button", { type: "button", onClick: onClose, className: "text-spark-muted hover:text-spark-text", "aria-label": "Close", children: _jsx(X, { className: "w-4 h-4" }) })] }), _jsxs("div", { className: "p-4 space-y-4", children: [_jsxs("section", { children: [_jsx("h4", { className: "label mb-2", children: "Required plugins" }), _jsx("div", { className: "flex flex-wrap gap-1", children: detail.plugins_required.map((p) => (_jsx("span", { className: "chip", children: p }, p))) })] }), detail.secrets_required.length > 0 && (_jsxs("section", { children: [_jsx("h4", { className: "label mb-2", children: "Required secrets" }), _jsx("div", { className: "flex flex-wrap gap-1", children: detail.secrets_required.map((s) => (_jsx("span", { className: "chip chip-warning", children: s }, s))) }), _jsxs("p", { className: "text-xs text-spark-muted mt-2", children: ["Populate via", " ", _jsx("code", { className: "text-spark-accent", children: "spark secrets set <name>" }), " ", "after install."] })] })), _jsxs("section", { children: [_jsx("h4", { className: "label mb-2", children: "README" }), _jsx("div", { className: "panel p-3", children: _jsx(MarkdownView, { content: detail.readme }) })] }), _jsxs("section", { children: [_jsx("h4", { className: "label mb-2", children: "agent.yaml" }), _jsx("pre", { className: "panel p-3 text-xs font-mono overflow-x-auto whitespace-pre", children: detail.agent_yaml })] }), _jsxs("section", { children: [_jsx("h4", { className: "label mb-2", children: "task.yaml" }), _jsx("pre", { className: "panel p-3 text-xs font-mono overflow-x-auto whitespace-pre", children: detail.task_yaml })] }), _jsxs("section", { children: [_jsx("h4", { className: "label mb-2", children: "plugin-config hints" }), _jsx("pre", { className: "panel p-3 text-xs font-mono overflow-x-auto whitespace-pre", children: JSON.stringify(detail.plugin_config_hints, null, 2) }), _jsx("p", { className: "text-xs text-spark-muted mt-2", children: "Not auto-applied. Copy into the Plugins page after install." })] }), installResult && (_jsxs("section", { className: "panel border-spark-accent p-3 space-y-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-spark-accent", children: [_jsx(Check, { className: "w-4 h-4" }), "Installed"] }), _jsxs("div", { className: "text-xs space-y-1", children: [_jsxs("div", { children: ["agent: ", _jsx("code", { children: installResult.agent_path })] }), _jsxs("div", { children: ["task: ", _jsx("code", { children: installResult.task_path })] })] }), installResult.plugins_still_to_configure.length > 0 && (_jsxs("div", { className: "text-xs", children: [_jsx("span", { className: "text-spark-muted", children: "Plugins still needing config:" }), " ", installResult.plugins_still_to_configure.join(", ")] })), installResult.secrets_still_to_populate.length > 0 && (_jsxs("div", { className: "text-xs", children: [_jsx("span", { className: "text-spark-muted", children: "Secrets still needing population:" }), " ", installResult.secrets_still_to_populate.join(", ")] }))] })), installError && (_jsxs("section", { className: "panel border-spark-danger p-3 text-sm text-spark-danger", children: [installError.message, installError.status === 409 && (_jsx("div", { className: "mt-2", children: _jsxs("label", { className: "flex items-center gap-2 text-xs", children: [_jsx("input", { type: "checkbox", checked: overwrite, onChange: (e) => setOverwrite(e.target.checked) }), "Overwrite existing files"] }) }))] }))] }), _jsxs("footer", { className: "sticky bottom-0 bg-spark-panel border-t border-spark-border px-4 py-3 flex items-center justify-between", children: [_jsxs("label", { className: "flex items-center gap-2 text-xs text-spark-muted", children: [_jsx("input", { type: "checkbox", checked: overwrite, onChange: (e) => setOverwrite(e.target.checked) }), "Overwrite existing"] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { type: "button", onClick: onClose, className: "btn text-xs", children: "Close" }), _jsx("button", { type: "button", onClick: () => onInstall(overwrite), disabled: installing, className: "btn btn-primary text-xs", children: installing ? "Installing…" : "Install" })] })] })] }) }));
}
