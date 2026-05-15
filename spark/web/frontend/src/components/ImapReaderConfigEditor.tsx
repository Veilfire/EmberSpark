import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Inbox, KeyRound } from "lucide-react";
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

interface ImapDiscovery {
  ok: boolean;
  error?: string | null;
  error_code?: string | null;
  error_detail?: Record<string, unknown> | null;
  server?: string | null;
  capabilities: string[];
  mailboxes: { name: string; delimiter: string | null; attributes: string[]; risk: Risk }[];
}

export function ImapReaderConfigEditor({ info }: { info: PluginInfo }) {
  const qc = useQueryClient();
  const [config, setConfig] = useState(info.config);

  const [host, setHost] = useState(
    typeof config.host === "string" ? config.host : "",
  );
  const [port, setPort] = useState<number>(
    typeof config.port === "number" ? config.port : 993,
  );
  const [useSsl, setUseSsl] = useState(config.use_ssl !== false);
  const [username, setUsername] = useState(
    typeof config.username === "string" ? config.username : "",
  );
  const [tokenSecret, setTokenSecret] = useState(
    typeof config.password_secret === "string"
      ? config.password_secret
      : "imap_password",
  );

  async function discover(): Promise<DiscoveryEnvelope> {
    const cfgUpdate = {
      ...config,
      host,
      port,
      use_ssl: useSsl,
      username,
      password_secret: tokenSecret,
    };
    const dirty = JSON.stringify(cfgUpdate) !== JSON.stringify(info.config);
    if (dirty) {
      await api.put(`/api/plugin-config/${info.plugin_name}`, {
        config: cfgUpdate,
        reason: "auto-saved before imap discovery",
      });
      setConfig(cfgUpdate);
      qc.invalidateQueries({ queryKey: ["plugins"] });
    }
    const r = await api.post<ImapDiscovery>(
      "/api/plugin-config/imap_reader/discover",
      {},
    );
    return {
      ok: r.ok,
      error: r.error,
      error_code: r.error_code,
      error_detail: r.error_detail,
      badges: r.ok
        ? [
            ...(r.server ? [{ label: "Server", value: r.server }] : []),
            { label: "Mailboxes", value: String(r.mailboxes.length) },
          ]
        : undefined,
      sections: r.ok
        ? ([
            {
              field: "allowed_mailboxes",
              title: "Allowed mailboxes",
              description:
                "Pick which mailboxes the agent can search and read. Danger badge appears on `[Gmail]/All Mail`-style mailboxes that hold the full inbox content.",
              items: r.mailboxes.map((m) => ({
                id: m.name,
                label: m.name,
                risk: m.risk,
                hint: m.attributes.length > 0 ? m.attributes.join(" · ") : undefined,
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
          <Inbox size={18} className="text-spark-accent" />
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
        pluginName="imap_reader"
        config={config}
        onSave={async (next, reason) => {
          await api.put(`/api/plugin-config/${info.plugin_name}`, {
            config: { ...next, host, port, use_ssl: useSsl, username, password_secret: tokenSecret },
            reason: reason || "imap_reader config update via editor",
          });
          setConfig(next);
          qc.invalidateQueries({ queryKey: ["plugins"] });
          toast.success(`${info.plugin_name} saved`);
        }}
        discover={discover}
        toggles={[
          {
            field: "download_attachments",
            label: "Download attachments",
            description:
              "When on, attachments are written to the deliverables directory and surface in the Downloads page. Off by default — attachments are never read.",
          },
          {
            field: "mark_seen_on_read",
            label: "Mark read on fetch",
            description:
              "When on, `get_message` flags the message Seen on the server.",
          },
        ]}
        connectionPanel={
          <div className="space-y-3">
            <div className="label flex items-center gap-1.5">
              <KeyRound size={12} /> Connection
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs text-spark-muted">IMAP host</span>
                <input
                  className="input w-full mt-1 font-mono text-sm"
                  placeholder="imap.gmail.com"
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                />
              </label>
              <label className="block">
                <span className="text-xs text-spark-muted">Port</span>
                <input
                  type="number"
                  className="input w-full mt-1 font-mono text-sm"
                  value={port}
                  onChange={(e) =>
                    setPort(Math.max(1, parseInt(e.target.value, 10) || 993))
                  }
                />
              </label>
              <label className="block">
                <span className="text-xs text-spark-muted">Username</span>
                <input
                  className="input w-full mt-1 font-mono text-sm"
                  placeholder="alice@gmail.com"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label className="block">
                <span className="text-xs text-spark-muted">Password secret name</span>
                <input
                  className="input w-full mt-1 font-mono text-sm"
                  value={tokenSecret}
                  onChange={(e) => setTokenSecret(e.target.value)}
                />
                <span className="text-[10px] text-spark-muted mt-0.5 block">
                  Use an app-specific password, not your login.
                </span>
              </label>
              <label className="text-xs flex items-center gap-2 mt-6">
                <input
                  type="checkbox"
                  checked={useSsl}
                  onChange={(e) => setUseSsl(e.target.checked)}
                />
                Use SSL (port 993)
              </label>
            </div>
          </div>
        }
      />
    </div>
  );
}
