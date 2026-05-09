import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Blocks,
  Check,
  Download,
  GitFork,
  Key,
  Package,
  Pencil,
  Plus,
  Search,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "../lib/api";
import { MarkdownView } from "../components/MarkdownView";
import { TemplateEditor } from "../components/TemplateEditor";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/primitives";
import { ConfirmDialog } from "../components/ConfirmDialog";

interface TemplateSummary {
  name: string;
  description: string;
  plugins_required: string[];
  permissions_required: string[];
  secrets_required: string[];
}

interface TemplateDetail extends TemplateSummary {
  readme: string;
  agent_yaml: string;
  task_yaml: string;
  plugin_config_hints: Record<string, unknown>;
}

interface InstallResponse {
  agent_name: string;
  task_name: string;
  agent_path: string;
  task_path: string;
  plugins_still_to_configure: string[];
  secrets_still_to_populate: string[];
}

/**
 * Agent template gallery (H1.1). Lets the operator browse ready-to-run
 * templates, preview the README + YAMLs, and install with one click.
 */
export default function Templates() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const list = useQuery<TemplateSummary[]>({
    queryKey: ["templates"],
    queryFn: () => api.get<TemplateSummary[]>("/api/templates/"),
  });

  const [selected, setSelected] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState<string | null | false>(false);
  const [forkSource, setForkSource] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [starred, setStarred] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      return new Set(
        JSON.parse(localStorage.getItem("spark.templates.starred") || "[]"),
      );
    } catch {
      return new Set();
    }
  });
  // false = closed, null = create new, string = edit existing

  function toggleStar(name: string) {
    const next = new Set(starred);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setStarred(next);
    localStorage.setItem(
      "spark.templates.starred",
      JSON.stringify([...next]),
    );
  }

  const filteredTemplates = useMemo(() => {
    const items = list.data ?? [];
    const q = filter.toLowerCase();
    const matches = items.filter(
      (t) =>
        !q ||
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.plugins_required.some((p) => p.toLowerCase().includes(q)),
    );
    // Sort: starred first, then alphabetical
    return matches.sort((a, b) => {
      const as = starred.has(a.name);
      const bs = starred.has(b.name);
      if (as !== bs) return as ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [list.data, filter, starred]);

  async function deleteTemplate(name: string) {
    try {
      await api.del(`/api/templates/${encodeURIComponent(name)}`);
      toast.success(`Template "${name}" deleted`);
      qc.invalidateQueries({ queryKey: ["templates"] });
    } catch (err) {
      toast.error(`Delete failed: ${err}`);
    } finally {
      setDeleteConfirm(null);
    }
  }

  const detail = useQuery<TemplateDetail>({
    queryKey: ["templates", selected],
    queryFn: () =>
      api.get<TemplateDetail>(`/api/templates/${encodeURIComponent(selected ?? "")}`),
    enabled: !!selected,
  });

  const install = useMutation({
    mutationFn: async ({ name, overwrite }: { name: string; overwrite: boolean }) => {
      return api.post<InstallResponse>(
        `/api/templates/${encodeURIComponent(name)}/install`,
        { overwrite },
      );
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
        toast.message(
          `${pending.length} plugin${pending.length === 1 ? "" : "s"} still need configuration`,
          {
            description: pending.join(", "),
            action: {
              label: "Configure",
              onClick: () =>
                navigate(`/plugins?focus=${encodeURIComponent(pending[0])}`),
            },
          },
        );
      } else if (secrets.length > 0) {
        toast.message(
          `${secrets.length} secret${secrets.length === 1 ? "" : "s"} still missing`,
          {
            description: secrets.join(", "),
            action: {
              label: "Open secrets",
              onClick: () => navigate("/security?tab=secrets"),
            },
          },
        );
      } else {
        toast.success(`Installed ${result.agent_name}`);
      }
      navigate(`/agents/${encodeURIComponent(result.agent_name)}`);
    },
  });

  return (
    <div className="space-y-4">
      <PageHeader
        icon={<Package className="w-6 h-6" />}
        title="Templates"
        subtitle="Ready-to-run agent + task pairs. Install one, configure the plugins it needs, and you're done."
        actions={
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={() => setEditorOpen(null)}
          >
            <Plus className="w-4 h-4" /> Create Template
          </button>
        }
      />

      {(list.data ?? []).length > 0 && (
        <div className="relative max-w-md">
          <Search className="w-4 h-4 text-spark-muted absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            className="input w-full pl-9"
            placeholder={`Search ${list.data?.length ?? 0} templates…`}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      )}

      {list.isLoading && (
        <div className="text-spark-muted text-sm">Loading templates…</div>
      )}
      {list.isError && (
        <div className="text-spark-danger text-sm">
          Failed to load templates: {(list.error as Error)?.message}
        </div>
      )}

      {list.data && list.data.length === 0 && (
        <EmptyState
          icon={<Package className="w-10 h-10" />}
          title="No templates available"
          description="Create your first custom template to share across agents."
          action={{
            label: "Create Template",
            onClick: () => setEditorOpen(null),
          }}
        />
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {filteredTemplates.map((tpl) => (
          <TemplateCard
            key={tpl.name}
            tpl={tpl}
            starred={starred.has(tpl.name)}
            onPreview={() => setSelected(tpl.name)}
            onEdit={() => setEditorOpen(tpl.name)}
            onFork={() => setForkSource(tpl.name)}
            onQuickInstall={() => install.mutate({ name: tpl.name, overwrite: false })}
            onToggleStar={() => toggleStar(tpl.name)}
            onDelete={() => setDeleteConfirm(tpl.name)}
            installing={install.isPending && install.variables?.name === tpl.name}
          />
        ))}
      </div>

      {selected && detail.data && (
        <TemplateDrawer
          detail={detail.data}
          onClose={() => setSelected(null)}
          onInstall={(overwrite) =>
            install.mutate({ name: detail.data!.name, overwrite })
          }
          installing={install.isPending}
          installResult={install.data ?? null}
          installError={install.error as ApiError | null}
        />
      )}

      {editorOpen !== false && (
        <TemplateEditor
          editName={editorOpen}
          onClose={() => setEditorOpen(false)}
          onSaved={() => {
            setEditorOpen(false);
            qc.invalidateQueries({ queryKey: ["templates"] });
          }}
        />
      )}

      {forkSource && (
        <TemplateEditor
          editName={forkSource}
          forkMode
          onClose={() => setForkSource(null)}
          onSaved={() => {
            setForkSource(null);
            qc.invalidateQueries({ queryKey: ["templates"] });
          }}
        />
      )}

      <ConfirmDialog
        open={!!deleteConfirm}
        title={`Delete template "${deleteConfirm}"?`}
        description="This removes the template from disk. Installed agents are unaffected."
        tone="danger"
        confirmLabel="Delete"
        requireTypedName={deleteConfirm ?? undefined}
        onCancel={() => setDeleteConfirm(null)}
        onConfirm={() => deleteConfirm && deleteTemplate(deleteConfirm)}
      />
    </div>
  );
}

function TemplateCard({
  tpl,
  starred,
  onPreview,
  onEdit,
  onFork,
  onQuickInstall,
  onToggleStar,
  onDelete,
  installing,
}: {
  tpl: TemplateSummary;
  starred: boolean;
  onPreview: () => void;
  onEdit: () => void;
  onFork: () => void;
  onQuickInstall: () => void;
  onToggleStar: () => void;
  onDelete: () => void;
  installing: boolean;
}) {
  const stop = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <div
      className="panel-interactive text-left p-4 flex flex-col gap-3 cursor-pointer relative group"
      onClick={onPreview}
    >
      {/* Star top-left */}
      <button
        className={`absolute top-3 left-3 transition ${
          starred
            ? "text-spark-accent"
            : "text-spark-muted opacity-0 group-hover:opacity-100 hover:text-spark-accent"
        }`}
        onClick={(e) => {
          stop(e);
          onToggleStar();
        }}
        title={starred ? "Unstar" : "Star"}
      >
        <Star className="w-4 h-4" fill={starred ? "currentColor" : "none"} />
      </button>

      {/* Action icons top-right */}
      <div className="absolute top-2 right-2 flex items-center opacity-0 group-hover:opacity-100 transition">
        <button
          className="btn-icon"
          onClick={(e) => {
            stop(e);
            onFork();
          }}
          title="Fork template"
        >
          <GitFork className="w-3.5 h-3.5" />
        </button>
        <button
          className="btn-icon"
          onClick={(e) => {
            stop(e);
            onEdit();
          }}
          title="Edit template"
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
        <button
          className="btn-icon hover:text-spark-danger"
          onClick={(e) => {
            stop(e);
            onDelete();
          }}
          title="Delete template"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex items-center gap-2 pl-6">
        <Package className="w-4 h-4 text-spark-accent" />
        <h3 className="font-semibold text-spark-text">{tpl.name}</h3>
      </div>
      <p className="text-xs text-spark-muted line-clamp-3 flex-1">
        {tpl.description}
      </p>
      <div className="flex flex-wrap gap-1">
        {tpl.plugins_required.slice(0, 4).map((p) => (
          <span key={p} className="chip text-[10px] gap-1 flex items-center">
            <Blocks className="w-3 h-3" />
            {p}
          </span>
        ))}
        {tpl.plugins_required.length > 4 && (
          <span className="chip text-[10px]">
            +{tpl.plugins_required.length - 4}
          </span>
        )}
      </div>
      {tpl.secrets_required.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {tpl.secrets_required.map((s) => (
            <span
              key={s}
              className="chip chip-warn text-[10px] gap-1 flex items-center"
            >
              <Key className="w-3 h-3" />
              {s}
            </span>
          ))}
        </div>
      )}

      {/* Quick install row */}
      <div className="flex gap-2 pt-1 border-t border-spark-border">
        <button
          className="btn btn-primary flex-1 flex items-center justify-center gap-1 text-xs"
          onClick={(e) => {
            stop(e);
            onQuickInstall();
          }}
          disabled={installing}
        >
          <Download className="w-3 h-3" />
          {installing ? "Installing…" : "Quick Install"}
        </button>
        <button
          className="btn text-xs"
          onClick={(e) => {
            stop(e);
            onPreview();
          }}
        >
          Preview
        </button>
      </div>
    </div>
  );
}

function TemplateDrawer({
  detail,
  onClose,
  onInstall,
  installing,
  installResult,
  installError,
}: {
  detail: TemplateDetail;
  onClose: () => void;
  onInstall: (overwrite: boolean) => void;
  installing: boolean;
  installResult: InstallResponse | null;
  installError: ApiError | null;
}) {
  const [overwrite, setOverwrite] = useState(false);

  return (
    <Modal open={true} onClose={onClose}>
      <div className="w-full max-w-3xl max-h-[92vh] bg-spark-panel border border-spark-border rounded-lg overflow-y-auto shadow-2xl ml-auto">
        <header className="sticky top-0 bg-spark-panel border-b border-spark-border px-4 py-3 flex items-center justify-between z-10">
          <div>
            <h3 className="text-lg font-bold">{detail.name}</h3>
            <p className="text-xs text-spark-muted">{detail.description}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-spark-muted hover:text-spark-text"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </header>

        <div className="p-4 space-y-4">
          <section>
            <h4 className="label mb-2">Required plugins</h4>
            <div className="flex flex-wrap gap-1">
              {detail.plugins_required.map((p) => (
                <span key={p} className="chip">
                  {p}
                </span>
              ))}
            </div>
          </section>

          {detail.secrets_required.length > 0 && (
            <section>
              <h4 className="label mb-2">Required secrets</h4>
              <div className="flex flex-wrap gap-1">
                {detail.secrets_required.map((s) => (
                  <span key={s} className="chip chip-warning">
                    {s}
                  </span>
                ))}
              </div>
              <p className="text-xs text-spark-muted mt-2">
                Populate via{" "}
                <code className="text-spark-accent">spark secrets set &lt;name&gt;</code>{" "}
                after install.
              </p>
            </section>
          )}

          <section>
            <h4 className="label mb-2">README</h4>
            <div className="panel p-3">
              <MarkdownView content={detail.readme} />
            </div>
          </section>

          <section>
            <h4 className="label mb-2">agent.yaml</h4>
            <pre className="panel p-3 text-xs font-mono overflow-x-auto whitespace-pre">
              {detail.agent_yaml}
            </pre>
          </section>

          <section>
            <h4 className="label mb-2">task.yaml</h4>
            <pre className="panel p-3 text-xs font-mono overflow-x-auto whitespace-pre">
              {detail.task_yaml}
            </pre>
          </section>

          <section>
            <h4 className="label mb-2">plugin-config hints</h4>
            <pre className="panel p-3 text-xs font-mono overflow-x-auto whitespace-pre">
              {JSON.stringify(detail.plugin_config_hints, null, 2)}
            </pre>
            <p className="text-xs text-spark-muted mt-2">
              Not auto-applied. Copy into the Plugins page after install.
            </p>
          </section>

          {installResult && (
            <section className="panel border-spark-accent p-3 space-y-2">
              <div className="flex items-center gap-2 text-sm font-semibold text-spark-accent">
                <Check className="w-4 h-4" />
                Installed
              </div>
              <div className="text-xs space-y-1">
                <div>
                  agent: <code>{installResult.agent_path}</code>
                </div>
                <div>
                  task: <code>{installResult.task_path}</code>
                </div>
              </div>
              {installResult.plugins_still_to_configure.length > 0 && (
                <div className="text-xs">
                  <span className="text-spark-muted">
                    Plugins still needing config:
                  </span>{" "}
                  {installResult.plugins_still_to_configure.join(", ")}
                </div>
              )}
              {installResult.secrets_still_to_populate.length > 0 && (
                <div className="text-xs">
                  <span className="text-spark-muted">
                    Secrets still needing population:
                  </span>{" "}
                  {installResult.secrets_still_to_populate.join(", ")}
                </div>
              )}
            </section>
          )}

          {installError && (
            <section className="panel border-spark-danger p-3 text-sm text-spark-danger">
              {installError.message}
              {installError.status === 409 && (
                <div className="mt-2">
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={overwrite}
                      onChange={(e) => setOverwrite(e.target.checked)}
                    />
                    Overwrite existing files
                  </label>
                </div>
              )}
            </section>
          )}
        </div>

        <footer className="sticky bottom-0 bg-spark-panel border-t border-spark-border px-4 py-3 flex items-center justify-between">
          <label className="flex items-center gap-2 text-xs text-spark-muted">
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(e) => setOverwrite(e.target.checked)}
            />
            Overwrite existing
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="btn text-xs"
            >
              Close
            </button>
            <button
              type="button"
              onClick={() => onInstall(overwrite)}
              disabled={installing}
              className="btn btn-primary text-xs"
            >
              {installing ? "Installing…" : "Install"}
            </button>
          </div>
        </footer>
      </div>
    </Modal>
  );
}
