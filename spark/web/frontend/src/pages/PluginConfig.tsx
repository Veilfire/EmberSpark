import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Info, ExternalLink } from "lucide-react";
import { api } from "../lib/api";
import { confirmDialog } from "../lib/confirm";

interface PluginInfo {
  plugin_name: string;
  version: string;
  description: string;
  config: Record<string, unknown>;
  schema: Record<string, unknown>;
  schema_hash: string;
  fresh: boolean;
}

interface FieldSpec {
  name: string;
  type: "string" | "integer" | "number" | "boolean" | "array" | "object" | "enum";
  default?: unknown;
  description?: string;
  required?: boolean;
  enumValues?: string[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  examples?: unknown[];
  // For array fields, the shape of each item.
  itemsType?: "string" | "integer" | "number" | "boolean" | "object";
  itemsSchema?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Plugin-specific help hints. Keyed as "plugin_name.field_name". These fill
// in the gaps where a Pydantic schema alone can't capture "what does this
// actually look like?" — especially for complex list-of-object fields like
// http_tool.rules. Add entries here whenever a field needs more context than
// its description provides. Becomes the gold-standard reference operators
// see when configuring a plugin.
// ---------------------------------------------------------------------------
const FIELD_HELP: Record<
  string,
  { hint?: string; example?: string; docsHref?: string }
> = {
  "http_tool.rules": {
    hint:
      "List of per-host rules. Each rule pins one hostname and declares " +
      "which HTTP methods are allowed, size/timeout overrides, and whether " +
      "GET HTML responses should be stripped to readable-main-content.",
    example: JSON.stringify(
      [
        {
          host: "api.github.com",
          allowed_methods: ["GET"],
          allow_http: false,
          max_response_bytes: 5_000_000,
          note: "read-only GitHub API lookups",
        },
        {
          host: "example.com",
          allowed_methods: ["GET"],
          extract_main_content: true,
        },
      ],
      null,
      2,
    ),
    docsHref: "/wiki/Plugin-Reference-HTTP-Tool",
  },
  "http_tool.default_max_response_bytes": {
    hint: "Cap applied when a host rule does not set its own max_response_bytes.",
  },
  "http_tool.default_connect_timeout_seconds": {
    hint: "TCP connect timeout in seconds. 5s is usually enough for well-behaved APIs.",
  },
  "http_tool.default_read_timeout_seconds": {
    hint:
      "Read timeout in seconds. Increase for APIs that stream large responses; " +
      "decrease to fail fast on slow endpoints.",
  },
  "http_tool.user_agent": {
    hint: "User-Agent header sent with every request.",
  },
  "http_client.allow_hosts": {
    hint: "One hostname per line. Exact-match only; no wildcards.",
    example: "api.github.com\nwww.wikipedia.org",
  },
  "filesystem.allow_paths": {
    hint:
      "Absolute paths the plugin may read under. Each path is checked with " +
      "symlink resolution; traversal outside the list is refused.",
    example: "/data/spark-volume/deliverables\n/data/spark-volume/scratch",
  },
  "filesystem.deny_paths": {
    hint: "Deny-list applied after allow_paths. Useful for carving out subdirectories.",
  },
  "shell.allowed_commands": {
    hint:
      "Argv-first tokens this plugin may invoke. Matched strictly (no shell " +
      "interpolation, no PATH lookup — binaries must be pre-installed).",
    example: "ls\ngit\njq",
  },
  "web_search.provider": {
    hint: "Which search backend to use. Requires the matching secret.",
  },
  "image_gen.provider": {
    hint: "Which image-gen backend to use. Requires the matching secret.",
  },
  "email_sender.smtp_host": {
    hint: "SMTP server hostname. TLS is required; plaintext SMTP is refused.",
  },
};

function specsFromSchema(schema: Record<string, unknown>): FieldSpec[] {
  const props = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  const required = new Set((schema.required as string[] | undefined) ?? []);
  const out: FieldSpec[] = [];
  for (const [name, spec] of Object.entries(props)) {
    const t = (spec.type as string) || "string";
    const field: FieldSpec = {
      name,
      type: t as FieldSpec["type"],
      default: spec.default,
      required: required.has(name),
    };
    if (spec.description) field.description = String(spec.description);
    if (typeof spec.minimum === "number") field.minimum = spec.minimum;
    if (typeof spec.maximum === "number") field.maximum = spec.maximum;
    if (typeof spec.exclusiveMinimum === "number")
      field.exclusiveMinimum = spec.exclusiveMinimum;
    if (typeof spec.exclusiveMaximum === "number")
      field.exclusiveMaximum = spec.exclusiveMaximum;
    if (typeof spec.minLength === "number") field.minLength = spec.minLength;
    if (typeof spec.maxLength === "number") field.maxLength = spec.maxLength;
    if (typeof spec.pattern === "string") field.pattern = spec.pattern;
    if (Array.isArray(spec.examples)) field.examples = spec.examples;
    if (Array.isArray(spec.enum)) {
      field.type = "enum";
      field.enumValues = (spec.enum as unknown[]).map(String);
    }
    if (t === "array") {
      const items = spec.items as Record<string, unknown> | undefined;
      if (items) {
        const itype = (items.type as string) || "object";
        field.itemsType = itype as FieldSpec["itemsType"];
        field.itemsSchema = items;
      }
    }
    out.push(field);
  }
  return out;
}

function typeLabel(field: FieldSpec): string {
  if (field.type === "enum") return "enum";
  if (field.type === "array") {
    return field.itemsType === "object" ? "list[object]" : `list[${field.itemsType ?? "string"}]`;
  }
  return field.type;
}

function constraintHints(field: FieldSpec): string[] {
  const hints: string[] = [];
  if (field.minimum !== undefined && field.maximum !== undefined) {
    hints.push(`${field.minimum.toLocaleString()} ≤ n ≤ ${field.maximum.toLocaleString()}`);
  } else if (field.minimum !== undefined) {
    hints.push(`n ≥ ${field.minimum.toLocaleString()}`);
  } else if (field.maximum !== undefined) {
    hints.push(`n ≤ ${field.maximum.toLocaleString()}`);
  }
  if (field.exclusiveMinimum !== undefined) {
    hints.push(`n > ${field.exclusiveMinimum.toLocaleString()}`);
  }
  if (field.exclusiveMaximum !== undefined) {
    hints.push(`n < ${field.exclusiveMaximum.toLocaleString()}`);
  }
  if (field.minLength !== undefined && field.maxLength !== undefined) {
    hints.push(`${field.minLength}–${field.maxLength} chars`);
  } else if (field.maxLength !== undefined) {
    hints.push(`max ${field.maxLength} chars`);
  } else if (field.minLength !== undefined) {
    hints.push(`min ${field.minLength} chars`);
  }
  if (field.pattern) {
    hints.push(`pattern /${field.pattern}/`);
  }
  return hints;
}

function formatDefault(value: unknown): string {
  if (value === undefined) return "";
  if (value === null) return "null";
  if (Array.isArray(value)) return value.length === 0 ? "[]" : JSON.stringify(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function PluginConfigPage() {
  const plugins = useQuery<PluginInfo[]>({
    queryKey: ["plugins"],
    queryFn: () => api.get("/api/plugin-config/"),
  });
  const [selected, setSelected] = useState<string | null>(null);

  const active = useMemo(() => {
    if (!plugins.data) return null;
    if (selected === null && plugins.data.length > 0) return plugins.data[0];
    return plugins.data.find((p) => p.plugin_name === selected) ?? null;
  }, [plugins.data, selected]);

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-2xl font-bold">Plugins</h2>
        <p className="text-spark-muted text-sm">
          Configure built-in plugins without editing YAML. Operator-edited
          values override the agent YAML on overlapping fields. Every save is
          audited.
        </p>
      </header>

      <div className="flex gap-4">
        <div className="panel p-2 w-56 shrink-0">
          {(plugins.data ?? []).map((p) => (
            <button
              key={p.plugin_name}
              onClick={() => setSelected(p.plugin_name)}
              className={`block w-full text-left px-2 py-1.5 rounded-md text-sm ${
                active?.plugin_name === p.plugin_name
                  ? "bg-spark-border text-spark-text"
                  : "text-spark-muted hover:bg-spark-border/50"
              }`}
            >
              <div className="font-mono">{p.plugin_name}</div>
              <div className="text-xs">{p.version}</div>
            </button>
          ))}
        </div>
        <div className="flex-1 min-w-0">
          {active && (
            // ``key`` forces a fresh component instance per plugin so
            // the inner ``useState(info.config)`` re-initializes from
            // the new plugin's config. Without this, switching plugins
            // in the sidebar leaves ``draft`` holding the previous
            // plugin's fields — Save then sends the wrong shape and
            // the backend 422s with ``extra_forbidden`` on every field.
            <PluginEditor key={active.plugin_name} info={active} />
          )}
        </div>
      </div>
    </div>
  );
}

function PluginEditor({ info }: { info: PluginInfo }) {
  const client = useQueryClient();
  const fields = useMemo(() => specsFromSchema(info.schema), [info.schema]);
  const [draft, setDraft] = useState<Record<string, unknown>>(info.config);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      // Strip ``null`` / ``undefined`` keys before sending. Pydantic
      // refuses ``null`` for non-optional fields; omitting the key lets
      // the schema's default kick in instead. This protects against
      // stray nulls from any field renderer (cleared inputs, NaN
      // parses) and is a no-op on healthy drafts.
      const sanitized = Object.fromEntries(
        Object.entries(draft).filter(([, v]) => v !== null && v !== undefined),
      );
      return api.put(`/api/plugin-config/${encodeURIComponent(info.plugin_name)}`, {
        config: sanitized,
        reason,
      });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["plugins"] });
      setError(null);
      setReason("");
      toast.success(`${info.plugin_name} saved`);
    },
    onError: (e) => {
      const err = e as { message: string; status?: number; detail?: unknown };
      // Surface the field path + reason from FastAPI's 422 detail block
      // so operators see "max_results: input should be a valid integer"
      // instead of an opaque "422 Unprocessable Entity".
      let msg = err.message;
      const detail = err.detail as
        | { errors?: Array<{ loc?: unknown[]; msg?: string }> }
        | { detail?: Array<{ loc?: unknown[]; msg?: string }> }
        | undefined;
      const errs = (detail as { errors?: Array<{ loc?: unknown[]; msg?: string }> })?.errors
        ?? (detail as { detail?: Array<{ loc?: unknown[]; msg?: string }> })?.detail;
      if (Array.isArray(errs) && errs.length > 0) {
        msg = errs
          .map((e) => `${(e.loc ?? []).slice(1).join(".") || "config"}: ${e.msg}`)
          .join("; ");
      }
      setError(msg);
      toast.error(`Save failed: ${msg}`);
    },
  });

  const reset = useMutation({
    mutationFn: () =>
      api.post(
        `/api/plugin-config/${encodeURIComponent(info.plugin_name)}/reset`,
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["plugins"] });
      toast.success(`${info.plugin_name} reset to defaults`);
    },
  });

  async function handleReset() {
    const ok = await confirmDialog({
      title: `Reset ${info.plugin_name} to defaults?`,
      description:
        "This wipes your operator overrides and falls back to whatever the agent YAML specifies. The change is audited.",
      tone: "danger",
      confirmLabel: "Reset to defaults",
    });
    if (ok) reset.mutate();
  }

  const updateField = (name: string, value: unknown) =>
    setDraft((d) => ({ ...d, [name]: value }));

  const dirty = JSON.stringify(draft) !== JSON.stringify(info.config);

  return (
    <div className="panel p-4 space-y-5">
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="font-bold text-lg font-mono">{info.plugin_name}</h3>
          <span className="chip text-xs">v{info.version}</span>
          {info.fresh && (
            <span className="chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30">
              operator-edited
            </span>
          )}
        </div>
        <p className="text-sm text-spark-muted mt-1">{info.description}</p>
      </div>

      <div className="space-y-4">
        {fields.map((f) => (
          <FieldRenderer
            key={f.name}
            pluginName={info.plugin_name}
            field={f}
            value={draft[f.name]}
            onChange={(v) => updateField(f.name, v)}
          />
        ))}
        {fields.length === 0 && (
          <p className="text-sm text-spark-muted">
            This plugin has no operator-configurable fields.
          </p>
        )}
      </div>

      <div className="pt-3 border-t border-spark-border space-y-3">
        <label className="block">
          <span className="label">Reason (audited)</span>
          <input
            className="input w-full"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="why are you changing this?"
          />
          <span className="text-xs text-spark-muted mt-1 block">
            Recorded in the audit log alongside the diff. Keep it short and
            specific — future you will thank present you.
          </span>
        </label>
        {error && <div className="text-spark-danger text-sm">{error}</div>}
        <div className="flex justify-between items-center">
          <div className="text-xs text-spark-muted">
            {dirty ? "Unsaved changes" : "In sync with stored config"}
          </div>
          <div className="flex gap-2">
            <button className="btn btn-danger" onClick={handleReset}>
              Reset to defaults
            </button>
            <button
              className="btn btn-primary"
              onClick={() => save.mutate()}
              disabled={!dirty || save.isPending}
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function FieldHeader({
  field,
  help,
}: {
  field: FieldSpec;
  help: (typeof FIELD_HELP)[string] | undefined;
}) {
  const hints = constraintHints(field);
  return (
    <div>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-sm">{field.name}</span>
        <span className="chip text-[10px] uppercase tracking-wide">
          {typeLabel(field)}
        </span>
        {field.required && (
          <span className="chip text-[10px] bg-spark-danger/15 text-spark-danger border border-spark-danger/30 uppercase tracking-wide">
            required
          </span>
        )}
        {hints.length > 0 && (
          <span className="text-[11px] text-spark-muted font-mono">
            {hints.join(" · ")}
          </span>
        )}
      </div>
      {(field.description || help?.hint) && (
        <div className="flex items-start gap-1.5 text-xs text-spark-muted mt-1">
          <Info className="w-3 h-3 mt-0.5 shrink-0" />
          <span>
            {field.description}
            {field.description && help?.hint ? " " : ""}
            {help?.hint}
          </span>
        </div>
      )}
      {help?.docsHref && (
        <a
          href={help.docsHref}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-spark-accent inline-flex items-center gap-1 mt-1"
        >
          Reference <ExternalLink className="w-3 h-3" />
        </a>
      )}
    </div>
  );
}

function DefaultHint({ field }: { field: FieldSpec }) {
  if (field.default === undefined) return null;
  return (
    <div className="text-[11px] text-spark-muted mt-1 font-mono">
      default: {formatDefault(field.default)}
    </div>
  );
}

function FieldRenderer({
  pluginName,
  field,
  value,
  onChange,
}: {
  pluginName: string;
  field: FieldSpec;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const help = FIELD_HELP[`${pluginName}.${field.name}`];

  if (field.type === "boolean") {
    return (
      <div className="space-y-1">
        <FieldHeader field={field} help={help} />
        <label className="flex items-center gap-2 text-sm cursor-pointer mt-1">
          <input
            type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>{value ? "enabled" : "disabled"}</span>
        </label>
        <DefaultHint field={field} />
      </div>
    );
  }

  if (field.type === "enum") {
    return (
      <div className="space-y-1">
        <FieldHeader field={field} help={help} />
        <select
          className="input w-full mt-1"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          {field.enumValues?.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <DefaultHint field={field} />
      </div>
    );
  }

  if (field.type === "integer" || field.type === "number") {
    return (
      <div className="space-y-1">
        <FieldHeader field={field} help={help} />
        <input
          className="input w-full mt-1"
          type="number"
          value={String(value ?? "")}
          min={field.minimum}
          max={field.maximum}
          onChange={(e) => {
            const raw = e.target.value;
            // An empty input must NOT serialize as JSON ``null`` —
            // most plugin-config number fields are non-optional and
            // Pydantic refuses ``null`` for them. Fall back to the
            // schema default when known, else preserve the prior
            // value rather than poisoning the draft. The user can
            // always overwrite with a fresh number.
            if (raw === "") {
              if (field.default !== undefined && field.default !== null) {
                onChange(field.default);
              }
              return;
            }
            const parsed = field.type === "integer" ? parseInt(raw, 10) : parseFloat(raw);
            // NaN guard — Number.parse* yields NaN for "1e" mid-typing.
            if (Number.isNaN(parsed)) return;
            onChange(parsed);
          }}
          placeholder={field.default !== undefined ? String(field.default) : undefined}
        />
        <DefaultHint field={field} />
      </div>
    );
  }

  if (field.type === "array") {
    // list[object] → JSON editor with schema hint. list[string|int] → lines.
    if (field.itemsType === "object") {
      return <ComplexArrayEditor field={field} help={help} value={value} onChange={onChange} />;
    }
    const asText = Array.isArray(value)
      ? value.map((v) => String(v)).join("\n")
      : "";
    return (
      <div className="space-y-1">
        <FieldHeader field={field} help={help} />
        <textarea
          className="input w-full h-20 font-mono text-xs mt-1"
          value={asText}
          placeholder={help?.example ?? "one value per line"}
          onChange={(e) => {
            const items = e.target.value
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean)
              .map((s) => {
                if (field.itemsType === "integer" || field.itemsType === "number") {
                  const n = Number(s);
                  // Drop unparseable rows rather than emitting NaN —
                  // NaN serializes to JSON null and Pydantic 422s.
                  return Number.isNaN(n) ? undefined : n;
                }
                return s;
              })
              .filter((v) => v !== undefined);
            onChange(items);
          }}
        />
        <div className="text-[11px] text-spark-muted">
          One per line. Empty lines are stripped.
        </div>
        <DefaultHint field={field} />
      </div>
    );
  }

  // fallback: string / object
  const isSecretRef = field.name.endsWith("_secret");
  const stringValue = String(value ?? "");
  // ``*_secret`` fields hold the *name* of a vault entry, not the
  // credential itself. If the value doesn't match the slug pattern
  // (letters, digits, ``.``, ``_``, ``-``) and is suspiciously long,
  // it's almost certainly a pasted credential — a real footgun that
  // both poisons the agent (lookup misses) and persists cleartext to
  // disk. Surface a loud inline warning + a deep-link to /secrets.
  const looksLikeCredential =
    isSecretRef &&
    stringValue.length >= 24 &&
    !/^[a-zA-Z0-9._-]+$/.test(stringValue);
  return (
    <div className="space-y-1">
      <FieldHeader field={field} help={help} />
      <input
        className="input w-full mt-1"
        value={stringValue}
        type={isSecretRef ? "text" : "text"}
        autoComplete={isSecretRef ? "off" : undefined}
        spellCheck={isSecretRef ? false : undefined}
        placeholder={
          field.examples && field.examples.length > 0
            ? String(field.examples[0])
            : help?.example
        }
        onChange={(e) => onChange(e.target.value)}
      />
      {isSecretRef && (
        <div className="text-[11px] text-spark-muted">
          Holds the <em>name</em> of a vault entry, not the credential
          itself. Manage entries in{" "}
          <a className="text-spark-link hover:underline" href="/secrets">
            Secure → Secrets
          </a>
          .
        </div>
      )}
      {looksLikeCredential && (
        <div className="text-[11px] text-spark-danger border border-spark-danger/40 rounded p-2 mt-1">
          ⚠ This value looks like a credential, not a name. Pasting a
          raw API key here persists it cleartext on disk. Add it to the
          vault under a name (e.g. <code>{field.name.replace("_secret", "")}_key</code>) at{" "}
          <a className="text-spark-link hover:underline" href="/secrets">
            Secure → Secrets
          </a>{" "}
          and put the <em>name</em> here instead.
        </div>
      )}
      <DefaultHint field={field} />
    </div>
  );
}

function ComplexArrayEditor({
  field,
  help,
  value,
  onChange,
}: {
  field: FieldSpec;
  help: (typeof FIELD_HELP)[string] | undefined;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const initial = useMemo(
    () => (Array.isArray(value) ? JSON.stringify(value, null, 2) : "[]"),
    [value],
  );
  const [text, setText] = useState(initial);
  const [jsonError, setJsonError] = useState<string | null>(null);

  function commit(raw: string) {
    setText(raw);
    if (raw.trim() === "") {
      onChange([]);
      setJsonError(null);
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        setJsonError("Expected a JSON array");
        return;
      }
      setJsonError(null);
      onChange(parsed);
    } catch (err) {
      setJsonError((err as Error).message);
    }
  }

  const itemFields = (field.itemsSchema?.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const itemRequired = new Set(
    (field.itemsSchema?.required as string[] | undefined) ?? [],
  );

  return (
    <div className="space-y-2">
      <FieldHeader field={field} help={help} />

      {Object.keys(itemFields).length > 0 && (
        <details className="border border-spark-border rounded bg-spark-bg/40">
          <summary className="px-3 py-2 text-xs cursor-pointer text-spark-muted">
            Item schema ({Object.keys(itemFields).length} field
            {Object.keys(itemFields).length === 1 ? "" : "s"})
          </summary>
          <div className="px-3 py-2 border-t border-spark-border text-xs font-mono space-y-1">
            {Object.entries(itemFields).map(([k, v]) => {
              const t = (v.type as string) || (Array.isArray(v.enum) ? "enum" : "string");
              const desc = (v.description as string) || "";
              return (
                <div key={k} className="flex gap-2 items-baseline flex-wrap">
                  <span className="text-spark-text">{k}</span>
                  <span className="text-spark-muted">: {t}</span>
                  {itemRequired.has(k) && (
                    <span className="text-spark-danger">required</span>
                  )}
                  {v.default !== undefined && (
                    <span className="text-spark-muted">
                      default {JSON.stringify(v.default)}
                    </span>
                  )}
                  {desc && (
                    <span className="text-spark-muted font-sans">— {desc}</span>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      )}

      <textarea
        className="input w-full h-56 font-mono text-xs"
        value={text}
        placeholder={help?.example ?? "[]"}
        onChange={(e) => commit(e.target.value)}
        spellCheck={false}
      />
      {jsonError ? (
        <div className="text-xs text-spark-danger">JSON error: {jsonError}</div>
      ) : (
        <div className="text-[11px] text-spark-muted">
          Edit as JSON. Must be a JSON array. Invalid JSON is not saved.
        </div>
      )}
      {help?.example && (
        <details className="border border-spark-border rounded bg-spark-bg/40">
          <summary className="px-3 py-2 text-xs cursor-pointer text-spark-muted">
            Example
          </summary>
          <pre className="px-3 py-2 border-t border-spark-border text-xs font-mono overflow-auto">
            {help.example}
          </pre>
          <div className="px-3 pb-2">
            <button
              type="button"
              className="btn text-xs"
              onClick={() => commit(help.example!)}
            >
              Paste example
            </button>
          </div>
        </details>
      )}
      <DefaultHint field={field} />
    </div>
  );
}
