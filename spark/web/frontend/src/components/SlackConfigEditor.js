import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Hash, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { PluginAllowlistEditor, } from "./PluginAllowlistEditor";
export function SlackConfigEditor({ info }) {
    const qc = useQueryClient();
    const [config, setConfig] = useState(info.config);
    const [botSecret, setBotSecret] = useState(typeof config.bot_token_secret === "string"
        ? config.bot_token_secret
        : "slack_bot_token");
    const [userSecret, setUserSecret] = useState(typeof config.user_token_secret === "string"
        ? config.user_token_secret
        : "");
    async function discover() {
        const cfgUpdate = {
            ...config,
            bot_token_secret: botSecret,
            user_token_secret: userSecret,
        };
        const dirty = JSON.stringify(cfgUpdate) !== JSON.stringify(info.config);
        if (dirty) {
            await api.put(`/api/plugin-config/${info.plugin_name}`, {
                config: cfgUpdate,
                reason: "auto-saved before slack discovery",
            });
            setConfig(cfgUpdate);
            qc.invalidateQueries({ queryKey: ["plugins"] });
        }
        const r = await api.post("/api/plugin-config/slack/discover", {});
        return {
            ok: r.ok,
            error: r.error,
            error_code: r.error_code,
            error_detail: r.error_detail,
            badges: r.ok
                ? [
                    ...(r.team ? [{ label: "Team", value: r.team }] : []),
                    ...(r.user ? [{ label: "Bot", value: r.user }] : []),
                    { label: "Channels", value: String(r.channels.length) },
                    { label: "Users", value: String(r.users.length) },
                ]
                : undefined,
            sections: r.ok
                ? [
                    {
                        field: "allow_channel_ids",
                        title: "Allowed channels",
                        description: "Channels the bot can post to. Bot must be a member; non-member channels are filtered from discovery.",
                        items: r.channels.map((c) => ({
                            id: c.id,
                            label: `#${c.name}`,
                            risk: c.risk,
                            hint: c.is_private ? "private" : c.is_general ? "general" : c.id,
                        })),
                    },
                    {
                        field: "allow_dm_user_ids",
                        title: "Allowed DM recipients",
                        description: "Users the bot may DM directly. Admins / owners carry a danger chip.",
                        items: r.users.map((u) => ({
                            id: u.id,
                            label: u.real_name ? `${u.real_name} (@${u.name})` : `@${u.name}`,
                            risk: u.risk,
                            hint: u.is_admin ? "admin" : u.id,
                        })),
                    },
                ]
                : [],
        };
    }
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("header", { children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx(Hash, { size: 18, className: "text-spark-accent" }), _jsx("h3", { className: "font-bold text-lg font-mono", children: info.plugin_name }), _jsxs("span", { className: "chip text-xs", children: ["v", info.version] }), info.fresh && (_jsx("span", { className: "chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30", children: "operator-edited" }))] }), _jsx("p", { className: "text-sm text-spark-muted mt-1", children: info.description })] }), _jsx(PluginAllowlistEditor, { pluginName: "slack", config: config, onSave: async (next, reason) => {
                    await api.put(`/api/plugin-config/${info.plugin_name}`, {
                        config: { ...next, bot_token_secret: botSecret, user_token_secret: userSecret },
                        reason: reason || "slack config update via editor",
                    });
                    setConfig(next);
                    qc.invalidateQueries({ queryKey: ["plugins"] });
                    toast.success(`${info.plugin_name} saved`);
                }, discover: discover, connectionPanel: _jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "label flex items-center gap-1.5", children: [_jsx(KeyRound, { size: 12 }), " Tokens"] }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs text-spark-muted", children: "Bot token secret (xoxb-)" }), _jsx("input", { className: "input w-full mt-1 font-mono text-sm", value: botSecret, onChange: (e) => setBotSecret(e.target.value) }), _jsxs("span", { className: "text-[10px] text-spark-muted mt-0.5 block", children: ["Run ", _jsxs("code", { children: ["spark secrets set ", botSecret] }), " first."] })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "text-xs text-spark-muted", children: "User token secret (xoxp-, optional)" }), _jsx("input", { className: "input w-full mt-1 font-mono text-sm", placeholder: "(only needed for search_messages)", value: userSecret, onChange: (e) => setUserSecret(e.target.value) }), _jsx("span", { className: "text-[10px] text-spark-muted mt-0.5 block", children: "Slack search.messages refuses bot tokens." })] })] })] }) })] }));
}
