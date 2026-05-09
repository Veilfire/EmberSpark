import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { Bot, Calendar, ChevronDown, ChevronUp, Code, Layers, Shield, Zap, } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { ModelPicker, PROVIDER_SECRET } from "./ModelPicker";
import { Modal } from "./Modal";
const ALL_PLUGINS = [
    "web_search",
    "http_tool",
    "markdown_writer",
    "filesystem",
    "email_sender",
    "rss_reader",
    "git",
    "shell",
    "sqlite",
    "csv_io",
    "pdf_reader",
    "image_gen",
    "datetime_tool",
    "calculator",
    "json_transform",
];
const ALL_PERMISSIONS = [
    "fs.read",
    "fs.write",
    "fs.list",
    "net.http",
    "subprocess",
    "secrets.read",
];
const TASK_MODES = [
    { value: "one_shot", label: "One-shot", desc: "Runs once when triggered" },
    { value: "recurring", label: "Recurring", desc: "Runs on a cron schedule" },
    { value: "event", label: "Event", desc: "Fires on file/webhook events" },
    { value: "perpetual", label: "Perpetual", desc: "Runs continuously" },
];
const SCHEDULE_PRESETS = [
    { label: "Every hour", expr: "0 * * * *" },
    { label: "Daily 8am", expr: "0 8 * * *" },
    { label: "Daily 3am", expr: "0 3 * * *" },
    { label: "Weekly Monday 8am", expr: "0 8 * * 1" },
    { label: "Every 6 hours", expr: "0 */6 * * *" },
];
export function TemplateEditor({ editName, forkMode, onClose, onSaved }) {
    // Agent fields
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [providerType, setProviderType] = useState("openrouter");
    const [providerModel, setProviderModel] = useState("anthropic/claude-sonnet-4");
    const [providerTemp, setProviderTemp] = useState(0.2);
    const [providerBaseUrl, setProviderBaseUrl] = useState("");
    const [plugins, setPlugins] = useState(["web_search", "http_tool", "markdown_writer"]);
    const [grants, setGrants] = useState(["net.http", "fs.write", "secrets.read"]);
    const [maxIterations, setMaxIterations] = useState(12);
    const [maxModelCalls, setMaxModelCalls] = useState(30);
    const [maxToolCalls, setMaxToolCalls] = useState(25);
    const [maxRuntime, setMaxRuntime] = useState(900);
    const [reflection, setReflection] = useState(true);
    const [ltmEnabled, setLtmEnabled] = useState(true);
    // Task fields
    const [taskMode, setTaskMode] = useState("one_shot");
    const [objective, setObjective] = useState("");
    const [cronExpr, setCronExpr] = useState("0 8 * * 1");
    const [cronTz, setCronTz] = useState("UTC");
    const [taskMaxRuntime] = useState(1200);
    // UI state
    const [readme, setReadme] = useState("");
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [showYaml, setShowYaml] = useState(false);
    const [saving, setSaving] = useState(false);
    // Load existing template for editing.
    useEffect(() => {
        if (!editName)
            return;
        (async () => {
            try {
                const tpl = await api.get(`/api/templates/${encodeURIComponent(editName)}`);
                setName(forkMode ? `${tpl.name}-copy` : tpl.name);
                setDescription(tpl.description);
                setReadme(tpl.readme);
                // Parse agent YAML to extract fields.
                try {
                    // Simple YAML field extraction via regex (avoids needing a YAML parser in the browser).
                    const ay = tpl.agent_yaml;
                    const typeMatch = ay.match(/type:\s*(\w+)/);
                    const modelMatch = ay.match(/model:\s*([^\n]+)/);
                    const tempMatch = ay.match(/temperature:\s*([0-9.]+)/);
                    if (typeMatch)
                        setProviderType(typeMatch[1]);
                    if (modelMatch)
                        setProviderModel(modelMatch[1].trim());
                    if (tempMatch)
                        setProviderTemp(parseFloat(tempMatch[1]));
                    // Plugins
                    const pluginBlock = ay.match(/allow:\n((?:\s+-\s+\w+\n?)+)/);
                    if (pluginBlock) {
                        const ps = pluginBlock[1].match(/- (\w+)/g);
                        if (ps)
                            setPlugins(ps.map((p) => p.replace("- ", "")));
                    }
                    // Grants
                    const grantsBlock = ay.match(/grants:\n((?:\s+-\s+[\w.]+\n?)+)/);
                    if (grantsBlock) {
                        const gs = grantsBlock[1].match(/- ([\w.]+)/g);
                        if (gs)
                            setGrants(gs.map((g) => g.replace("- ", "")));
                    }
                    // Budgets
                    const iterMatch = ay.match(/max_iterations:\s*(\d+)/);
                    const mcMatch = ay.match(/max_model_calls:\s*(\d+)/);
                    const tcMatch = ay.match(/max_tool_calls:\s*(\d+)/);
                    const rtMatch = ay.match(/max_runtime_seconds:\s*(\d+)/);
                    if (iterMatch)
                        setMaxIterations(parseInt(iterMatch[1]));
                    if (mcMatch)
                        setMaxModelCalls(parseInt(mcMatch[1]));
                    if (tcMatch)
                        setMaxToolCalls(parseInt(tcMatch[1]));
                    if (rtMatch)
                        setMaxRuntime(parseInt(rtMatch[1]));
                    const reflMatch = ay.match(/reflection:\s*(true|false)/);
                    if (reflMatch)
                        setReflection(reflMatch[1] === "true");
                    const ltmMatch = ay.match(/long_term_memory:\n\s+enabled:\s*(true|false)/);
                    if (ltmMatch)
                        setLtmEnabled(ltmMatch[1] === "true");
                }
                catch {
                    /* best effort */
                }
                // Parse task YAML.
                try {
                    const ty = tpl.task_yaml;
                    const modeMatch = ty.match(/mode:\s*(\w+)/);
                    if (modeMatch)
                        setTaskMode(modeMatch[1]);
                    const objMatch = ty.match(/objective:\s*>\n([\s\S]*?)(?=\n\s*\w+:|$)/);
                    if (objMatch)
                        setObjective(objMatch[1].trim());
                    else {
                        const objSimple = ty.match(/objective:\s*(.+)/);
                        if (objSimple)
                            setObjective(objSimple[1].trim());
                    }
                    const cronMatch = ty.match(/expression:\s*"?([^"\n]+)"?/);
                    if (cronMatch)
                        setCronExpr(cronMatch[1]);
                    const tzMatch = ty.match(/timezone:\s*(\S+)/);
                    if (tzMatch)
                        setCronTz(tzMatch[1]);
                }
                catch {
                    /* best effort */
                }
            }
            catch (err) {
                toast.error(`Failed to load template: ${err}`);
            }
        })();
    }, [editName]);
    function generateAgentYaml() {
        const slug = name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
        const keyRef = PROVIDER_SECRET[providerType];
        const providerBlock = [
            `      type: ${providerType}`,
            `      model: ${providerModel}`,
            ...(keyRef ? [`      api_key_ref: ${keyRef}`] : []),
            ...(providerBaseUrl ? [`      base_url: ${providerBaseUrl}`] : []),
            `      temperature: ${providerTemp}`,
        ].join("\n");
        return `apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: ${slug}

spec:
  description: >
    ${description || "Custom agent."}

  runtime:
    provider:
${providerBlock}
    max_iterations: ${maxIterations}
    max_model_calls: ${maxModelCalls}
    max_tool_calls: ${maxToolCalls}
    max_runtime_seconds: ${maxRuntime}
    privacy_mode: strict
    reflection: ${reflection}

  memory:
    task_memory: true
    session_memory:
      enabled: true
      max_entries: 200
    long_term_memory:
      enabled: ${ltmEnabled}
      namespace: ${slug}
      backend: chroma
      collection: ${slug.replace(/-/g, "_")}_memory
      persist_path: ~/.spark/chroma
      embedder:
        provider: sentence_transformers
        model: BAAI/bge-small-en-v1.5
      retrieval:
        top_k: 6
        min_score: 0.72
      retention:
        default_class: review

  plugins:
    allow:
${plugins.map((p) => `      - ${p}`).join("\n")}

  permissions:
    filesystem:
      allow_paths: []
      deny_paths:
        - ~/.ssh
        - ~/.config
    network:
      allow_hosts: []
    sandbox:
      enabled: true
      backend: auto
      cpu_seconds: 60
      memory_mb: 1024
    grants:
${grants.map((g) => `      - ${g}`).join("\n")}

  logging:
    level: info
    raw_prompts: false
    raw_model_outputs: false
    local_path: ~/.spark/logs
`;
    }
    function generateTaskYaml() {
        const slug = name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
        const scheduleBlock = taskMode === "recurring"
            ? `
  schedule:
    type: cron
    expression: "${cronExpr}"
    timezone: ${cronTz}
`
            : "";
        return `apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: ${slug}

spec:
  agent: ${slug}
  mode: ${taskMode}
${scheduleBlock}
  objective: >
    ${objective || "Execute the configured task."}

  budgets:
    max_runtime_seconds: ${taskMaxRuntime}
    max_model_calls: ${maxModelCalls}
    max_tool_calls: ${maxToolCalls}
`;
    }
    async function save() {
        const slug = name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
        if (!slug || slug.length < 2) {
            toast.error("Name must be at least 2 characters (lowercase, hyphens)");
            return;
        }
        if (!providerModel) {
            toast.error("Select a model");
            return;
        }
        if (!objective.trim()) {
            toast.error("Write an objective for the task");
            return;
        }
        setSaving(true);
        try {
            await api.put(`/api/templates/${encodeURIComponent(slug)}`, {
                name: slug,
                agent_yaml: generateAgentYaml(),
                task_yaml: generateTaskYaml(),
                readme: readme || `# ${name}\n\n${description}\n`,
                plugin_config_hints: {},
            });
            toast.success(editName ? "Template updated" : "Template created");
            onSaved();
        }
        catch (err) {
            toast.error(`Save failed: ${err}`);
        }
        finally {
            setSaving(false);
        }
    }
    const agentYaml = generateAgentYaml();
    const taskYaml = generateTaskYaml();
    return (_jsx(Modal, { open: true, onClose: onClose, closeOnBackdrop: false, children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-4xl max-h-[95vh] overflow-auto shadow-2xl", children: [_jsxs("div", { className: "sticky top-0 bg-spark-panel border-b border-spark-border px-6 py-4 flex items-center justify-between z-10", children: [_jsx("h2", { className: "text-lg font-bold", children: forkMode
                                ? `Fork: ${editName}`
                                : editName
                                    ? `Edit: ${editName}`
                                    : "Create Template" }), _jsxs("div", { className: "flex gap-2", children: [_jsxs("button", { className: `btn ${showYaml ? "btn-primary" : ""}`, onClick: () => setShowYaml(!showYaml), children: [_jsx(Code, { className: "w-3 h-3 mr-1 inline" }), showYaml ? "Form" : "Preview YAML"] }), _jsx("button", { className: "text-spark-muted hover:text-spark-text text-xl", onClick: onClose, children: "\u00D7" })] })] }), showYaml ? (_jsxs("div", { className: "p-6 grid grid-cols-2 gap-4", children: [_jsxs("div", { children: [_jsx("h3", { className: "text-xs uppercase text-spark-muted mb-1", children: "agent.yaml" }), _jsx("pre", { className: "bg-spark-bg border border-spark-border rounded p-3 text-xs font-mono overflow-auto max-h-[70vh] whitespace-pre-wrap", children: agentYaml })] }), _jsxs("div", { children: [_jsx("h3", { className: "text-xs uppercase text-spark-muted mb-1", children: "task.yaml" }), _jsx("pre", { className: "bg-spark-bg border border-spark-border rounded p-3 text-xs font-mono overflow-auto max-h-[70vh] whitespace-pre-wrap", children: taskYaml })] })] })) : (_jsxs("div", { className: "p-6 space-y-6", children: [_jsxs("section", { className: "space-y-3", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Bot, { className: "w-4 h-4" }), " Identity"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-3", children: [_jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Name ", _jsx("span", { className: "text-spark-danger", children: "*" })] }), _jsx("input", { className: "input w-full", placeholder: "my-research-bot", value: name, onChange: (e) => setName(e.target.value), disabled: !!editName && !forkMode }), _jsx("p", { className: "text-xs text-spark-muted mt-1", children: "lowercase, hyphens only" })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Description" }), _jsx("input", { className: "input w-full", placeholder: "What does this agent do?", value: description, onChange: (e) => setDescription(e.target.value) })] })] })] }), _jsxs("section", { className: "space-y-3", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Zap, { className: "w-4 h-4" }), " Provider & Model"] }), _jsx(ModelPicker, { provider: providerType, model: providerModel, temperature: providerTemp, baseUrl: providerBaseUrl, onProviderChange: (p) => {
                                        setProviderType(p);
                                        setProviderModel("");
                                    }, onModelChange: setProviderModel, onTemperatureChange: setProviderTemp, onBaseUrlChange: setProviderBaseUrl })] }), _jsxs("section", { className: "space-y-3", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Calendar, { className: "w-4 h-4" }), " Task"] }), _jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Objective ", _jsx("span", { className: "text-spark-danger", children: "*" })] }), _jsx("textarea", { className: "input w-full", rows: 4, placeholder: "Describe what the agent should accomplish each run\u2026", value: objective, onChange: (e) => setObjective(e.target.value) })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Mode" }), _jsx("div", { className: "grid grid-cols-2 md:grid-cols-4 gap-2", children: TASK_MODES.map((m) => (_jsxs("button", { className: `border rounded px-3 py-2 text-left ${taskMode === m.value
                                                    ? "border-spark-accent bg-spark-accent/10"
                                                    : "border-spark-border"}`, onClick: () => setTaskMode(m.value), children: [_jsx("div", { className: "text-sm font-medium", children: m.label }), _jsx("div", { className: "text-xs text-spark-muted", children: m.desc })] }, m.value))) })] }), taskMode === "recurring" && (_jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-3", children: [_jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Cron schedule" }), _jsx("input", { className: "input w-full font-mono", value: cronExpr, onChange: (e) => setCronExpr(e.target.value) }), _jsx("div", { className: "flex flex-wrap gap-1 mt-1", children: SCHEDULE_PRESETS.map((p) => (_jsx("button", { className: "chip text-xs cursor-pointer hover:bg-spark-accent/10", onClick: () => setCronExpr(p.expr), children: p.label }, p.expr))) })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: "Timezone" }), _jsx("input", { className: "input w-full", value: cronTz, onChange: (e) => setCronTz(e.target.value), placeholder: "America/Vancouver" })] })] }))] }), _jsxs("section", { className: "space-y-3", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Layers, { className: "w-4 h-4" }), " Plugins"] }), _jsx("div", { className: "flex flex-wrap gap-2", children: ALL_PLUGINS.map((p) => {
                                        const on = plugins.includes(p);
                                        return (_jsxs("button", { className: `chip font-mono text-xs cursor-pointer border ${on
                                                ? "border-spark-accent bg-spark-accent/10 text-spark-accent"
                                                : "border-spark-border text-spark-muted"}`, onClick: () => setPlugins((prev) => on ? prev.filter((x) => x !== p) : [...prev, p]), children: [on ? "✓ " : "", p] }, p));
                                    }) })] }), _jsxs("section", { className: "space-y-3", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Shield, { className: "w-4 h-4" }), " Permission Grants"] }), _jsx("div", { className: "flex flex-wrap gap-2", children: ALL_PERMISSIONS.map((g) => {
                                        const on = grants.includes(g);
                                        return (_jsxs("button", { className: `chip font-mono text-xs cursor-pointer border ${on
                                                ? "border-spark-good bg-spark-good/10 text-spark-good"
                                                : "border-spark-border text-spark-muted"}`, onClick: () => setGrants((prev) => on ? prev.filter((x) => x !== g) : [...prev, g]), children: [on ? "✓ " : "", g] }, g));
                                    }) })] }), _jsxs("section", { children: [_jsxs("button", { className: "flex items-center gap-2 text-sm text-spark-muted hover:text-spark-text", onClick: () => setShowAdvanced(!showAdvanced), children: [showAdvanced ? (_jsx(ChevronUp, { className: "w-4 h-4" })) : (_jsx(ChevronDown, { className: "w-4 h-4" })), "Advanced options"] }), showAdvanced && (_jsxs("div", { className: "mt-3 space-y-3 border-t border-spark-border pt-3", children: [_jsxs("div", { className: "grid grid-cols-2 md:grid-cols-4 gap-3", children: [_jsxs("div", { children: [_jsx("label", { className: "text-xs text-spark-muted block mb-1", children: "Max iterations" }), _jsx("input", { type: "number", className: "input w-full", value: maxIterations, onChange: (e) => setMaxIterations(parseInt(e.target.value) || 12) })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs text-spark-muted block mb-1", children: "Max model calls" }), _jsx("input", { type: "number", className: "input w-full", value: maxModelCalls, onChange: (e) => setMaxModelCalls(parseInt(e.target.value) || 30) })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs text-spark-muted block mb-1", children: "Max tool calls" }), _jsx("input", { type: "number", className: "input w-full", value: maxToolCalls, onChange: (e) => setMaxToolCalls(parseInt(e.target.value) || 25) })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs text-spark-muted block mb-1", children: "Max runtime (s)" }), _jsx("input", { type: "number", className: "input w-full", value: maxRuntime, onChange: (e) => setMaxRuntime(parseInt(e.target.value) || 900) })] })] }), _jsxs("div", { className: "flex gap-6", children: [_jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: reflection, onChange: (e) => setReflection(e.target.checked) }), "Reflection (post-run learning)"] }), _jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: ltmEnabled, onChange: (e) => setLtmEnabled(e.target.checked) }), "Long-term memory (Chroma)"] })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs text-spark-muted block mb-1", children: "README (markdown)" }), _jsx("textarea", { className: "input w-full font-mono text-xs", rows: 4, value: readme, onChange: (e) => setReadme(e.target.value), placeholder: "# My Template\\n\\nDescribe your template here\u2026" })] })] }))] })] })), _jsxs("div", { className: "sticky bottom-0 bg-spark-panel border-t border-spark-border px-6 py-3 flex justify-end gap-2", children: [_jsx("button", { className: "btn", onClick: onClose, children: "Cancel" }), _jsx("button", { className: "btn btn-primary", onClick: save, disabled: saving || !name.trim() || !providerModel, children: saving
                                ? "Saving…"
                                : forkMode
                                    ? "Save as New"
                                    : editName
                                        ? "Update Template"
                                        : "Create Template" })] })] }) }));
}
