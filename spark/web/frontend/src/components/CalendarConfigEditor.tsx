import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Calendar, KeyRound } from "lucide-react";
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

/**
 * Calendar (CalDAV) plugin editor. Connection panel for
 * URL/username/secret + Verify SSL toggle; allowlist grid + danger
 * typed-confirm + prefill flashing live in the shared
 * `PluginAllowlistEditor`.
 *
 * Backend discovery payload (`CalendarDiscovery`) is translated into
 * the shared `DiscoveryEnvelope` shape so the operator-facing UX is
 * identical across calendar / imap / slack / cloud_drive.
 */

interface CalendarDiscovery {
  ok: boolean;
  error?: string | null;
  error_code?: string | null;
  error_detail?: Record<string, unknown> | null;
  principal_url?: string | null;
  instance_name?: string | null;
  calendars: {
    name: string;
    url: string;
    color: string | null;
    can_write: boolean;
    risk: Risk;
  }[];
}

export function CalendarConfigEditor({ info }: { info: PluginInfo }) {
  const qc = useQueryClient();
  const [config, setConfig] = useState(info.config);

  // Track connection-panel fields separately so the operator can edit
  // base_url / username / token_secret / verify_ssl before re-running
  // discovery (which expects them to be saved first).
  const [baseUrl, setBaseUrl] = useState(
    typeof config.base_url === "string" ? config.base_url : "",
  );
  const [username, setUsername] = useState(
    typeof config.username === "string" ? config.username : "",
  );
  const [tokenSecret, setTokenSecret] = useState(
    typeof config.password_secret === "string"
      ? config.password_secret
      : "calendar_password",
  );
  const [verifySsl, setVerifySsl] = useState(
    config.verify_ssl === false ? false : true,
  );

  async function discover(): Promise<DiscoveryEnvelope> {
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
    const r = await api.post<CalendarDiscovery>(
      "/api/plugin-config/calendar/discover",
      {},
    );
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
        ? ([
            {
              field: "allowed_calendars",
              title: "Allowed calendars",
              description:
                "Pick which calendars the agent can read or write. Danger badge appears on shared / public calendars.",
              items: r.calendars.map((c) => ({
                id: c.url,
                label: c.name,
                risk: c.risk,
                hint: c.url,
              })),
            },
          ] satisfies AllowlistSection[])
        : [],
    };
  }

  const save = useMutation({
    mutationFn: async (next: Record<string, unknown>) =>
      api.put(`/api/plugin-config/${info.plugin_name}`, {
        config: next,
        reason: "calendar config update via editor",
      }),
    onSuccess: () => {
      toast.success(`${info.plugin_name} saved`);
      qc.invalidateQueries({ queryKey: ["plugins"] });
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  return (
    <div className="space-y-4">
      <header>
        <div className="flex items-center gap-2 flex-wrap">
          <Calendar size={18} className="text-spark-accent" />
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
        pluginName="calendar"
        config={config}
        onSave={async (next, reason) => {
          await api.put(`/api/plugin-config/${info.plugin_name}`, {
            config: { ...next, base_url: baseUrl, username, password_secret: tokenSecret, verify_ssl: verifySsl },
            reason: reason || "calendar config update via editor",
          });
          setConfig(next);
          qc.invalidateQueries({ queryKey: ["plugins"] });
          toast.success(`${info.plugin_name} saved`);
        }}
        discover={discover}
        toggles={[
          {
            field: "read_only",
            label: "Read-only mode",
            description:
              "When on, the agent can list and read events but cannot create / delete events. The Failure Inspector deep-links here to flip it.",
          },
        ]}
        connectionPanel={
          <div className="space-y-3">
            <div className="label flex items-center gap-1.5">
              <KeyRound size={12} /> Connection
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs text-spark-muted">CalDAV base URL</span>
                <input
                  className="input w-full mt-1 font-mono text-sm"
                  placeholder="https://caldav.icloud.com"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </label>
              <label className="block">
                <span className="text-xs text-spark-muted">Username</span>
                <input
                  className="input w-full mt-1 font-mono text-sm"
                  placeholder="alice@icloud.com"
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
                  Run <code>spark secrets set {tokenSecret}</code> first. Use an{" "}
                  <strong>app-specific password</strong>, not your login.
                </span>
              </label>
              <label className="text-xs flex items-center gap-2 mt-6">
                <input
                  type="checkbox"
                  checked={verifySsl}
                  onChange={(e) => setVerifySsl(e.target.checked)}
                />
                Verify SSL
              </label>
            </div>
          </div>
        }
      />
      {/* Silence the unused save mutation — kept for parity with the
          home_assistant flow so we can re-add a "Save connection
          panel only" button later. */}
      <span hidden>{save.isPending ? "" : ""}</span>
    </div>
  );
}
