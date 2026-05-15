import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Calendar, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { PluginAllowlistEditor, } from "./PluginAllowlistEditor";
export function CalendarConfigEditor({ info }) {
    const qc = useQueryClient();
    const [config, setConfig] = useState(info.config);
    // Track connection-panel fields separately so the operator can edit
    // base_url / username / token_secret / verify_ssl before re-running
    // discovery (which expects them to be saved first).
    const [baseUrl, setBaseUrl] = useState(typeof config.base_url === "string" ? config.base_url : "");
    const [username, setUsername] = useState(typeof config.username === "string" ? config.username : "");
    const [tokenSecret, setTokenSecret] = useState(typeof config.password_secret === "string"
        ? config.password_secret
        : "calendar_password");
    const [verifySsl, setVerifySsl] = useState(config.verify_ssl === false ? false : true);
    async function discover() {
        // Save connection-panel fields first so the backend discover()
        // call sees them. The shared editor's auto-discover-on-mount
        // would otherwise hit a stale cfg.
        const cfgUpdate = {
            ...config,
            base_url: baseUrl,
            username,
            password_secret: tokenSecret,
            verify_ssl: verifySsl,
        };
        const dirty = JSON.stringify(cfgUpdate) !== JSON.stringify(info.config);
        if (dirty) {
            await api.put(`/api/plugin-config/${info.plugin_name}`, {
                config: cfgUpdate,
                reason: "auto-saved before calendar discovery",
            });
            setConfig(cfgUpdate);
            qc.invalidateQueries({ queryKey: ["plugins"] });
        }
        const r = await api.post("/api/plugin-config/calendar/discover", {});
        return {
            ok: r.ok,
            error: r.error,
            error_code: r.error_code,
            error_detail: r.error_detail,
            badges: r.ok
                ? [
                    ...(r.instance_name
                        ? [{ label: "Server", value: r.instance_name }]
                        : []),
                    { label: "Calendars", value: String(r.calendars.length) },
                ]
                : undefined,
            sections: r.ok
                ? [
                    {
                        field: "allowed_calendars",
                        title: "Allowed calendars",
                        description: "Pick which calendars the agent can read or write. Danger badge appears on shared / public calendars.",
                        items: r.calendars.map((c) => ({
                            id: c.url,
                            label: c.name,
                            risk: c.risk,
                            hint: c.url,
                        })),
                    },
                ]
                : [],
        };
    }
    const save = useMutation({
        mutationFn: async (next) => api.put(`/api/plugin-config/${info.plugin_name}`, {
            config: next,
            reason: "calendar config update via editor",
        }),
        onSuccess: () => {
            toast.success(`${info.plugin_name} saved`);
            qc.invalidateQueries({ queryKey: ["plugins"] });
        },
        onError: (e) => toast.error(`Save failed: ${e.message}`),
    });
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx(Calendar, { size: 18, className: "text-spark-accent" }), _jsx("h3", { className: "font-bold text-lg font-mono", children: info.plugin_name }), _jsxs("span", { className: "chip text-xs", children: ["v", info.version] }), info.fresh && (_jsx("span", { className: "chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30", children: "operator-edited" }))] }), _jsx("p", { className: "text-sm text-spark-muted mt-1", children: info.description })] }), _jsx(PluginAllowlistEditor, { pluginName: "calendar", config: config, onSave: async (next, reason) => {
                    await api.put(`/api/plugin-config/${info.plugin_name}`, {
                        config: { ...next, base_url: baseUrl, username, password_secret: tokenSecret, verify_ssl: verifySsl },
                        reason: reason || "calendar config update via editor",
                    });
                    setConfig(next);
                    qc.invalidateQueries({ queryKey: ["plugins"] });
                    toast.success(`${info.plugin_name} saved`);
                }, discover: discover, toggles: [
                    {
                        field: "read_only",
                        label: "Read-only mode",
                        description: "When on, the agent can list and read events but cannot create / delete events. The Failure Inspector deep-links here to flip it.",
                    },
                ], connectionPanel: _jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "label flex items-center gap-1.5", children: [_jsx(KeyRound, { size: 12 }), " Connection"] }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs text-spark-muted", children: "CalDAV base URL" }), _jsx("input", { className: "input w-full mt-1 font-mono text-sm", placeholder: "https://caldav.icloud.com", value: baseUrl, onChange: (e) => setBaseUrl(e.target.value) })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs text-spark-muted", children: "Username" }), _jsx("input", { className: "input w-full mt-1 font-mono text-sm", placeholder: "alice@icloud.com", value: username, onChange: (e) => setUsername(e.target.value) })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs text-spark-muted", children: "Password secret name" }), _jsx("input", { className: "input w-full mt-1 font-mono text-sm", value: tokenSecret, onChange: (e) => setTokenSecret(e.target.value) }), _jsxs("span", { className: "text-[10px] text-spark-muted mt-0.5 block", children: ["Run ", _jsxs("code", { children: ["spark secrets set ", tokenSecret] }), " first. Use an", " ", _jsx("strong", { children: "app-specific password" }), ", not your login."] })] }), _jsxs("label", { className: "text-xs flex items-center gap-2 mt-6", children: [_jsx("input", { type: "checkbox", checked: verifySsl, onChange: (e) => setVerifySsl(e.target.checked) }), "Verify SSL"] })] })] }) }), _jsx("span", { hidden: true, children: save.isPending ? "" : "" })] }));
}
