import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Hash, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import {
  PluginAllowlistEditor,
  type DiscoveryEnvelope,
  type AllowlistSection,
  type Risk,
} from "./PluginAllowlistEditor";

interface PluginInfo {
  plugin_name: string;
  version: string;
  description: string;
  config: Record<string, unknown>;
  fresh: boolean;
}

interface SlackDiscovery {
  ok: boolean;
  error?: string | null;
  error_code?: string | null;
  error_detail?: Record<string, unknown> | null;
  team?: string | null;
  user?: string | null;
  channels: {
    id: string;
    name: string;
    is_member: boolean;
    is_private: boolean;
    is_general: boolean;
    risk: Risk;
  }[];
  users: {
    id: string;
    name: string;
    real_name: string | null;
    is_bot: boolean;
    is_admin: boolean;
    risk: Risk;
  }[];
}

export function SlackConfigEditor({ info }: { info: PluginInfo }) {
  const qc = useQueryClient();
  const [config, setConfig] = useState(info.config);

  const [botSecret, setBotSecret] = useState(
    typeof config.bot_token_secret === "string"
      ? config.bot_token_secret
      : "slack_bot_token",
  );
  const [userSecret, setUserSecret] = useState(
    typeof config.user_token_secret === "string"
      ? config.user_token_secret
      : "",
  );

  async function discover(): Promise<DiscoveryEnvelope> {
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
    const r = await api.post<SlackDiscovery>(
      "/api/plugin-config/slack/discover",
      {},
    );
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
        ? ([
            {
              field: "allow_channel_ids",
              title: "Allowed channels",
              description:
                "Channels the bot can post to. Bot must be a member; non-member channels are filtered from discovery.",
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
              description:
                "Users the bot may DM directly. Admins / owners carry a danger chip.",
              items: r.users.map((u) => ({
                id: u.id,
                label: u.real_name ? `${u.real_name} (@${u.name})` : `@${u.name}`,
                risk: u.risk,
                hint: u.is_admin ? "admin" : u.id,
              })),
            },
          ] satisfies AllowlistSection[])
        : [],
    };
  }

  return (
    <div className="space-y-4">
      <header>
        <div className="flex items-center gap-2 flex-wrap">
          <Hash size={18} className="text-spark-accent" />
          <h3 className="font-bold text-lg font-mono">{info.plugin_name}</h3>
          <span className="chip text-xs">v{info.version}</span>
          {info.fresh && (
            <span className="chip text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30">
              operator-edited
            </span>
          )}
        </div>
        <p className="text-sm text-spark-muted mt-1">{info.description}</p>
      </header>

      <PluginAllowlistEditor
        pluginName="slack"
        config={config}
        onSave={async (next, reason) => {
          await api.put(`/api/plugin-config/${info.plugin_name}`, {
            config: { ...next, bot_token_secret: botSecret, user_token_secret: userSecret },
            reason: reason || "slack config update via editor",
          });
          setConfig(next);
          qc.invalidateQueries({ queryKey: ["plugins"] });
          toast.success(`${info.plugin_name} saved`);
        }}
        discover={discover}
        connectionPanel={
          <div className="space-y-3">
            <div className="label flex items-center gap-1.5">
              <KeyRound size={12} /> Tokens
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs text-spark-muted">
                  Bot token secret (xoxb-)
                </span>
                <input
                  className="input w-full mt-1 font-mono text-sm"
                  value={botSecret}
                  onChange={(e) => setBotSecret(e.target.value)}
                />
                <span className="text-[10px] text-spark-muted mt-0.5 block">
                  Run <code>spark secrets set {botSecret}</code> first.
                </span>
              </label>
              <label className="block">
                <span className="text-xs text-spark-muted">
                  User token secret (xoxp-, optional)
                </span>
                <input
                  className="input w-full mt-1 font-mono text-sm"
                  placeholder="(only needed for search_messages)"
                  value={userSecret}
                  onChange={(e) => setUserSecret(e.target.value)}
                />
                <span className="text-[10px] text-spark-muted mt-0.5 block">
                  Slack search.messages refuses bot tokens.
                </span>
              </label>
            </div>
          </div>
        }
      />
    </div>
  );
}
