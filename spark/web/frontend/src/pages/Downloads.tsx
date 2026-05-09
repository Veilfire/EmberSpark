import { useQuery } from "@tanstack/react-query";
import { Download, Eye, FileText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MarkdownView } from "../components/MarkdownView";
import { Modal } from "../components/Modal";
import { api } from "../lib/api";
import { formatTimestamp } from "../lib/utils";

interface DeliverableFile {
  relative_path: string;
  size_bytes: number;
  modified_at: string;
  run_id?: string | null;
  task_name?: string | null;
  source?: string | null;
  kind?: string | null;
}

interface DeliverableListing {
  root: string;
  files: DeliverableFile[];
  total_size_bytes: number;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function isMarkdown(path: string): boolean {
  const lower = path.toLowerCase();
  return lower.endsWith(".md") || lower.endsWith(".markdown");
}

export default function Downloads() {
  const { data, isLoading, isError, error } = useQuery<DeliverableListing>({
    queryKey: ["deliverables"],
    queryFn: () => api.get<DeliverableListing>("/api/deliverables/"),
  });

  const [previewPath, setPreviewPath] = useState<string | null>(null);

  if (isLoading) {
    return <div className="p-4 text-spark-muted">Loading deliverables…</div>;
  }
  if (isError) {
    return (
      <div className="p-4">
        <h1 className="text-xl font-bold mb-2">Downloads</h1>
        <div className="text-spark-danger text-sm">
          {(error as Error)?.message ||
            "Data volume not enabled. Set spec.data_volume.enabled in ~/.spark/spark.yaml and restart."}
        </div>
      </div>
    );
  }

  const files = data?.files ?? [];

  return (
    <div className="p-4 space-y-4">
      <header>
        <h1 className="text-xl font-bold">Downloads</h1>
        <p className="text-xs text-spark-muted mt-1">
          Files written by plugins to the data volume's deliverables directory.
          New files trigger a notification (unless you've disabled the "download
          ready" category in Settings).
          <span className="ml-1">
            Markdown files have an inline preview — click the eye icon.
          </span>
        </p>
        {data && (
          <div className="text-xs text-spark-muted mt-1">
            Root: <code>{data.root}</code> · {files.length} file
            {files.length === 1 ? "" : "s"} · {formatSize(data.total_size_bytes)}
          </div>
        )}
      </header>

      {files.length === 0 ? (
        <div className="p-6 text-center text-spark-muted text-sm border border-spark-border rounded-md">
          No deliverables yet. When a plugin writes a file to the deliverables
          directory it will appear here and the notification bell will light up.
        </div>
      ) : (
        <ul className="divide-y divide-spark-border border border-spark-border rounded-md overflow-hidden">
          {files.map((file) => (
            <li
              key={file.relative_path}
              className="p-3 flex items-center justify-between gap-3 hover:bg-spark-border/30"
            >
              <div className="flex items-center gap-3 min-w-0 flex-1">
                <FileText className="w-4 h-4 text-spark-muted shrink-0" />
                <div className="min-w-0">
                  <div className="font-medium text-sm truncate">
                    {file.relative_path}
                  </div>
                  <div className="text-xs text-spark-muted flex items-center gap-2 flex-wrap">
                    <span>{formatSize(file.size_bytes)}</span>
                    <span>·</span>
                    <span>{formatTimestamp(file.modified_at)}</span>
                    {file.task_name && (
                      <>
                        <span>·</span>
                        <span>task: {file.task_name}</span>
                      </>
                    )}
                    {file.run_id && (
                      <>
                        <span>·</span>
                        <Link
                          to={`/runs/${encodeURIComponent(file.run_id)}/replay`}
                          className="text-spark-link hover:underline font-mono"
                        >
                          from run {file.run_id.slice(-8)}
                        </Link>
                      </>
                    )}
                    {file.source && file.source !== "engine" && (
                      <>
                        <span>·</span>
                        <span className="italic">{file.source}</span>
                      </>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {isMarkdown(file.relative_path) && (
                  <button
                    type="button"
                    onClick={() => setPreviewPath(file.relative_path)}
                    className="btn text-xs flex items-center gap-1"
                    title="Preview rendered markdown"
                  >
                    <Eye className="w-3.5 h-3.5" />
                    Preview
                  </button>
                )}
                <a
                  href={`/api/deliverables/${encodeURIComponent(file.relative_path)}`}
                  download
                  className="btn text-xs flex items-center gap-1"
                >
                  <Download className="w-3.5 h-3.5" />
                  Download
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}

      {previewPath && (
        <MarkdownPreviewModal
          relativePath={previewPath}
          onClose={() => setPreviewPath(null)}
        />
      )}
    </div>
  );
}

interface MarkdownPreviewModalProps {
  relativePath: string;
  onClose: () => void;
}

function MarkdownPreviewModal({
  relativePath,
  onClose,
}: MarkdownPreviewModalProps) {
  // Hold the body in component state — the deliverables endpoint streams
  // the file as text/markdown, not JSON, so we can't use the typed
  // `api.get` helper. The fetch is keyed on the path and runs once per
  // open; closing the modal unmounts the component.
  const [content, setContent] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

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
        if (!cancelled) setContent(text);
      })
      .catch((err: Error) => {
        if (!cancelled) setLoadError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [relativePath]);

  // Wrap the panel in the shared Modal component, which portals to
  // document.body so the backdrop blur covers the full viewport (no
  // top-bar gap caused by a parent stacking context). Modal handles
  // Esc, focus trap, body scroll lock, and ARIA semantics.
  return (
    <Modal open={true} onClose={onClose}>
      <div className="panel max-w-3xl w-full max-h-[85vh] flex flex-col overflow-hidden">
        <header className="px-4 py-3 border-b border-spark-border flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wide text-spark-muted">
              Preview
            </div>
            <div className="font-mono text-sm truncate">{relativePath}</div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <a
              href={`/api/deliverables/${encodeURIComponent(relativePath)}`}
              download
              className="btn text-xs flex items-center gap-1"
            >
              <Download className="w-3.5 h-3.5" />
              Download
            </a>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-md border border-transparent hover:bg-spark-border/50 hover:border-spark-border text-spark-muted hover:text-spark-text transition-colors"
              aria-label="Close preview"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto p-5">
          {loadError ? (
            <div className="text-spark-danger text-sm">
              Failed to load file: {loadError}
            </div>
          ) : content === null ? (
            <div className="text-spark-muted text-sm">Loading…</div>
          ) : (
            <MarkdownView
              content={content}
              className="text-spark-text text-sm"
            />
          )}
        </div>
      </div>
    </Modal>
  );
}
