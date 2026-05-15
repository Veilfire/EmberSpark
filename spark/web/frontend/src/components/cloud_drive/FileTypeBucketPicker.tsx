import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  FileType2,
  Image as ImageIcon,
  Archive,
  Music,
  Code2,
  Database,
} from "lucide-react";

/**
 * File-extension bucket picker.
 *
 * Buckets are a UI organization primitive only — the on-disk config
 * stores a flat list of extensions. The picker presents named groups
 * (office, images, …) with tri-state header checkboxes and an
 * expand-to-leaf view for per-extension control.
 */

export interface FileTypeBucket {
  id: string;
  label: string;
  icon: typeof FileText;
  extensions: string[];
}

export const BUCKETS: FileTypeBucket[] = [
  {
    id: "documents",
    label: "Documents",
    icon: FileText,
    extensions: ["pdf", "txt", "md", "rtf", "csv", "json", "yaml", "xml"],
  },
  {
    id: "office",
    label: "Microsoft Office",
    icon: FileType2,
    extensions: ["doc", "docx", "xls", "xlsx", "ppt", "pptx", "vsd", "vsdx", "one", "onepkg", "mpp"],
  },
  {
    id: "office_open",
    label: "OpenDocument (LibreOffice)",
    icon: FileType2,
    extensions: ["odt", "ods", "odp", "odg"],
  },
  {
    id: "images",
    label: "Images",
    icon: ImageIcon,
    extensions: ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tiff", "heic"],
  },
  {
    id: "archives",
    label: "Archives",
    icon: Archive,
    extensions: ["zip", "tar", "gz", "tgz", "bz2", "7z", "rar"],
  },
  {
    id: "media",
    label: "Audio / Video",
    icon: Music,
    extensions: ["mp3", "mp4", "mov", "wav", "flac", "m4a", "ogg", "webm"],
  },
  {
    id: "code",
    label: "Source code",
    icon: Code2,
    extensions: ["py", "js", "ts", "html", "css", "sh", "go", "rs", "java"],
  },
  {
    id: "data",
    label: "Data",
    icon: Database,
    extensions: ["parquet", "db", "sqlite"],
  },
];

type Tri = "all" | "some" | "none";

function bucketState(bucket: FileTypeBucket, selected: Set<string>): Tri {
  let count = 0;
  for (const ext of bucket.extensions) {
    if (selected.has(ext)) count += 1;
  }
  if (count === 0) return "none";
  if (count === bucket.extensions.length) return "all";
  return "some";
}

export function FileTypeBucketPicker({
  value,
  onChange,
  flashedExtension,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  flashedExtension?: string;
}) {
  const selected = useMemo(
    () => new Set(value.map((v) => v.toLowerCase().replace(/^\./, ""))),
    [value],
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Anything in `value` that isn't in any bucket is shown in an
  // "Other" row so the operator can see + remove arbitrary entries.
  const knownExts = useMemo(() => {
    const s = new Set<string>();
    for (const b of BUCKETS) for (const e of b.extensions) s.add(e);
    return s;
  }, []);
  const otherExts = useMemo(
    () => Array.from(selected).filter((e) => !knownExts.has(e)).sort(),
    [selected, knownExts],
  );

  // Auto-expand the bucket containing the flashed extension.
  const effectiveExpanded = useMemo(() => {
    if (!flashedExtension) return expanded;
    const e = flashedExtension.toLowerCase().replace(/^\./, "");
    const owner = BUCKETS.find((b) => b.extensions.includes(e));
    if (!owner) return expanded;
    const next = new Set(expanded);
    next.add(owner.id);
    return next;
  }, [flashedExtension, expanded]);

  function emit(next: Set<string>) {
    onChange(Array.from(next).sort());
  }

  function toggleBucket(bucket: FileTypeBucket) {
    const state = bucketState(bucket, selected);
    const next = new Set(selected);
    if (state === "all") {
      for (const e of bucket.extensions) next.delete(e);
    } else {
      for (const e of bucket.extensions) next.add(e);
    }
    emit(next);
  }

  function toggleExt(ext: string) {
    const next = new Set(selected);
    if (next.has(ext)) next.delete(ext);
    else next.add(ext);
    emit(next);
  }

  function toggleExpanded(id: string) {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExpanded(next);
  }

  function addOther(ext: string) {
    const clean = ext.toLowerCase().trim().replace(/^\./, "");
    if (!clean) return;
    const next = new Set(selected);
    next.add(clean);
    emit(next);
  }

  return (
    <div className="space-y-1.5">
      {BUCKETS.map((bucket) => {
        const state = bucketState(bucket, selected);
        const Icon = bucket.icon;
        const open = effectiveExpanded.has(bucket.id);
        return (
          <div
            key={bucket.id}
            className="border border-spark-border rounded-md overflow-hidden"
          >
            <div className="flex items-center gap-2 px-3 py-2 hover:bg-spark-bg/40 cursor-pointer">
              <button
                type="button"
                onClick={() => toggleExpanded(bucket.id)}
                className="text-spark-muted shrink-0"
                aria-label={open ? "Collapse" : "Expand"}
              >
                {open ? (
                  <ChevronDown size={14} />
                ) : (
                  <ChevronRight size={14} />
                )}
              </button>
              <input
                type="checkbox"
                checked={state === "all"}
                ref={(el) => {
                  if (el) el.indeterminate = state === "some";
                }}
                onChange={() => toggleBucket(bucket)}
                aria-label={`Toggle ${bucket.label} bucket`}
              />
              <Icon size={14} className="text-spark-muted shrink-0" />
              <span className="text-sm flex-1">{bucket.label}</span>
              <span className="text-[11px] text-spark-muted">
                {state === "all"
                  ? `all ${bucket.extensions.length}`
                  : state === "some"
                    ? `${bucket.extensions.filter((e) => selected.has(e)).length}/${bucket.extensions.length}`
                    : "none"}
              </span>
            </div>
            {open && (
              <div className="border-t border-spark-border bg-spark-bg/20 px-3 py-2 flex flex-wrap gap-1.5">
                {bucket.extensions.map((ext) => {
                  const on = selected.has(ext);
                  const flashed =
                    flashedExtension &&
                    flashedExtension.toLowerCase().replace(/^\./, "") === ext;
                  return (
                    <label
                      key={ext}
                      className={`flex items-center gap-1.5 px-2 py-1 border rounded text-xs font-mono cursor-pointer transition-colors ${
                        on
                          ? "border-spark-accent/40 bg-spark-bg/40"
                          : "border-spark-border"
                      } ${flashed ? "ring-2 ring-amber-400/70" : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => toggleExt(ext)}
                      />
                      <span>.{ext}</span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      <div className="border border-spark-border rounded-md px-3 py-2 space-y-2">
        <div className="text-xs text-spark-muted">
          Custom extensions ({otherExts.length})
        </div>
        <div className="flex flex-wrap gap-1.5">
          {otherExts.map((ext) => (
            <span
              key={ext}
              className="chip text-xs flex items-center gap-1.5 font-mono"
            >
              .{ext}
              <button
                type="button"
                onClick={() => toggleExt(ext)}
                className="text-spark-muted hover:text-spark-danger"
                aria-label={`Remove ${ext}`}
              >
                ×
              </button>
            </span>
          ))}
          <AddCustomExt onAdd={addOther} />
        </div>
      </div>
    </div>
  );
}

function AddCustomExt({ onAdd }: { onAdd: (ext: string) => void }) {
  const [v, setV] = useState("");
  return (
    <input
      className="input text-xs font-mono px-2 py-1 w-32"
      placeholder="add .ext"
      value={v}
      onChange={(e) => setV(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          onAdd(v);
          setV("");
        }
      }}
      onBlur={() => {
        if (v) {
          onAdd(v);
          setV("");
        }
      }}
    />
  );
}
