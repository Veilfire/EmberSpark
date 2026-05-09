import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../lib/api";

interface NotificationPreferences {
  download_ready: boolean;
  hitl_skill_review: boolean;
  hitl_approval: boolean;
  hitl_dlq: boolean;
  ip_grant_expiring: boolean;
  raw_logging_on: boolean;
  cost_soft_alert: boolean;
  cost_hard_stop: boolean;
  incident: boolean;
  plugin_hash_changed: boolean;
  memory_pruned: boolean;
  data_class_blocked: boolean;
  data_class_grant_expiring: boolean;
  play_sound: boolean;
  toast_on_create: boolean;
}

const NOTIFICATION_KINDS: {
  field: keyof NotificationPreferences;
  label: string;
  description: string;
}[] = [
  {
    field: "download_ready",
    label: "Download ready",
    description: "A plugin wrote a new file to the deliverables directory.",
  },
  {
    field: "hitl_skill_review",
    label: "Pending skill review",
    description: "An agent-discovered skill is waiting for your approval.",
  },
  {
    field: "hitl_approval",
    label: "Task approval required",
    description: "A scheduled task with an approval gate has been paused.",
  },
  {
    field: "hitl_dlq",
    label: "Task moved to DLQ",
    description:
      "A task has failed too many times and won't fire again until you ack it.",
  },
  {
    field: "ip_grant_expiring",
    label: "Internal-IP grant expiring",
    description: "An internal network grant is about to expire (< 1 hour).",
  },
  {
    field: "raw_logging_on",
    label: "Raw logging left on",
    description: "allow_raw_logging has been enabled for more than 24 hours.",
  },
  {
    field: "cost_soft_alert",
    label: "Cost soft alert",
    description: "A budget has crossed its soft-alert threshold.",
  },
  {
    field: "cost_hard_stop",
    label: "Cost hard stop",
    description:
      "A budget has crossed its hard ceiling and new runs are being refused.",
  },
  {
    field: "incident",
    label: "Incident",
    description: "A critical audit entry has been recorded.",
  },
  {
    field: "plugin_hash_changed",
    label: "Plugin hash changed",
    description: "A built-in plugin's module hash no longer matches the registry.",
  },
  {
    field: "memory_pruned",
    label: "Memory pruned",
    description:
      "A scheduled pruning sweep deleted long-term memory rows that aged past their retention window.",
  },
  {
    field: "data_class_blocked",
    label: "Data class blocked",
    description:
      "A data-classification guardrail refused an operation (tool output, chat turn, memory write, etc.). Surfaces the agent, class, and scope.",
  },
  {
    field: "data_class_grant_expiring",
    label: "Data-class grant expiring",
    description:
      "An unlimited data-class grant is within 24 hours of its expiry — extend or let it lapse.",
  },
];

export default function Settings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<NotificationPreferences>({
    queryKey: ["notification-preferences"],
    queryFn: () =>
      api.get<NotificationPreferences>("/api/notifications/preferences"),
  });

  const [dirty, setDirty] = useState<Partial<NotificationPreferences>>({});

  const save = useMutation({
    mutationFn: (patch: Partial<NotificationPreferences>) =>
      api.put<NotificationPreferences>("/api/notifications/preferences", patch),
    onSuccess: () => {
      setDirty({});
      qc.invalidateQueries({ queryKey: ["notification-preferences"] });
    },
  });

  const prefs: NotificationPreferences | undefined = data && {
    ...data,
    ...dirty,
  };

  if (isLoading || !prefs) {
    return <div className="p-4 text-spark-muted">Loading settings…</div>;
  }

  const toggle = (field: keyof NotificationPreferences) => {
    setDirty((d) => ({ ...d, [field]: !prefs[field] }));
  };

  return (
    <div className="p-4 space-y-6 max-w-2xl">
      <header>
        <h1 className="text-xl font-bold">Settings</h1>
        <p className="text-xs text-spark-muted mt-1">
          Per-category notification preferences. Turning a category off means
          no row is written and no bell/toast fires for that kind — the
          underlying event (skill review, DLQ, etc.) still happens.
        </p>
      </header>

      <section className="border border-spark-border rounded-md">
        <header className="px-3 py-2 border-b border-spark-border font-semibold text-sm">
          Notification categories
        </header>
        <ul className="divide-y divide-spark-border">
          {NOTIFICATION_KINDS.map((kind) => (
            <li
              key={kind.field}
              className="p-3 flex items-start justify-between gap-4"
            >
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm">{kind.label}</div>
                <div className="text-xs text-spark-muted mt-0.5">
                  {kind.description}
                </div>
              </div>
              <ToggleSwitch
                enabled={prefs[kind.field] as boolean}
                onChange={() => toggle(kind.field)}
              />
            </li>
          ))}
        </ul>
      </section>

      <SessionTimeoutSection />

      <section className="border border-spark-border rounded-md">
        <header className="px-3 py-2 border-b border-spark-border font-semibold text-sm">
          Delivery
        </header>
        <ul className="divide-y divide-spark-border">
          <li className="p-3 flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="font-medium text-sm">Toast on create</div>
              <div className="text-xs text-spark-muted mt-0.5">
                Show a transient toast in the web UI when a new notification is
                created.
              </div>
            </div>
            <ToggleSwitch
              enabled={prefs.toast_on_create}
              onChange={() => toggle("toast_on_create")}
            />
          </li>
          <li className="p-3 flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="font-medium text-sm">Play sound</div>
              <div className="text-xs text-spark-muted mt-0.5">
                Play a short ping on elevated or critical notifications only.
              </div>
            </div>
            <ToggleSwitch
              enabled={prefs.play_sound}
              onChange={() => toggle("play_sound")}
            />
          </li>
        </ul>
      </section>

      <div className="flex gap-2">
        <button
          type="button"
          className="btn"
          onClick={() => save.mutate(dirty)}
          disabled={save.isPending || Object.keys(dirty).length === 0}
        >
          {save.isPending ? "Saving…" : "Save changes"}
        </button>
        <button
          type="button"
          className="btn-ghost text-xs"
          onClick={() => setDirty({})}
          disabled={Object.keys(dirty).length === 0}
        >
          Discard
        </button>
        {Object.keys(dirty).length > 0 && (
          <span className="text-xs text-spark-muted self-center">
            {Object.keys(dirty).length} unsaved change
            {Object.keys(dirty).length === 1 ? "" : "s"}
          </span>
        )}
      </div>
    </div>
  );
}

interface SessionSettings {
  timeout_seconds: number | null;
  enabled: boolean;
}

const MIN_TIMEOUT_SECONDS = 60;
const MAX_TIMEOUT_SECONDS = 30 * 86_400;

function toDHM(seconds: number): { days: number; hours: number; minutes: number } {
  const days = Math.floor(seconds / 86_400);
  const afterDays = seconds - days * 86_400;
  const hours = Math.floor(afterDays / 3600);
  const minutes = Math.floor((afterDays - hours * 3600) / 60);
  return { days, hours, minutes };
}

function fromDHM(d: number, h: number, m: number): number {
  return d * 86_400 + h * 3600 + m * 60;
}

function SessionTimeoutSection() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<SessionSettings>({
    queryKey: ["session-settings"],
    queryFn: () => api.get<SessionSettings>("/api/settings/session"),
  });

  const [enabled, setEnabled] = useState(true);
  const [days, setDays] = useState(0);
  const [hours, setHours] = useState(1);
  const [minutes, setMinutes] = useState(0);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (data && !hydrated) {
      setEnabled(data.enabled);
      const parts = toDHM(data.timeout_seconds ?? 3600);
      setDays(parts.days);
      setHours(parts.hours);
      setMinutes(parts.minutes);
      setHydrated(true);
    }
  }, [data, hydrated]);

  const totalSeconds = fromDHM(days, hours, minutes);
  const tooShort = enabled && totalSeconds < MIN_TIMEOUT_SECONDS;
  const tooLong = enabled && totalSeconds > MAX_TIMEOUT_SECONDS;

  const save = useMutation({
    mutationFn: (body: { enabled: boolean; timeout_seconds: number | null }) =>
      api.put<SessionSettings>("/api/settings/session", body),
    onSuccess: () => {
      toast.success("Session settings saved");
      qc.invalidateQueries({ queryKey: ["session-settings"] });
    },
    onError: (err: unknown) => {
      const msg =
        err instanceof Error ? err.message : "Failed to save session settings";
      toast.error(msg);
    },
  });

  const onSave = () => {
    if (enabled && (tooShort || tooLong)) return;
    save.mutate({
      enabled,
      timeout_seconds: enabled ? totalSeconds : null,
    });
  };

  const dirty =
    hydrated &&
    data !== undefined &&
    (enabled !== data.enabled ||
      (enabled && totalSeconds !== (data.timeout_seconds ?? 0)));

  return (
    <section className="border border-spark-border rounded-md">
      <header className="px-3 py-2 border-b border-spark-border font-semibold text-sm">
        Security — session timeout
      </header>
      <div className="p-3 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="font-medium text-sm">Session timeout enabled</div>
            <div className="text-xs text-spark-muted mt-0.5">
              When disabled, signed-in browsers stay authenticated indefinitely.
              Applies immediately to new and existing sessions.
            </div>
          </div>
          <ToggleSwitch
            enabled={enabled}
            onChange={() => setEnabled((e) => !e)}
          />
        </div>

        <div
          className={`grid grid-cols-3 gap-3 ${
            enabled ? "" : "opacity-40 pointer-events-none select-none"
          }`}
          aria-disabled={!enabled}
        >
          <NumberField
            label="Days"
            value={days}
            min={0}
            max={30}
            onChange={setDays}
            disabled={!enabled}
          />
          <NumberField
            label="Hours"
            value={hours}
            min={0}
            max={23}
            onChange={setHours}
            disabled={!enabled}
          />
          <NumberField
            label="Minutes"
            value={minutes}
            min={0}
            max={59}
            onChange={setMinutes}
            disabled={!enabled}
          />
        </div>

        {enabled && tooShort && (
          <p className="text-xs text-red-400">
            Minimum timeout is 1 minute.
          </p>
        )}
        {enabled && tooLong && (
          <p className="text-xs text-red-400">Maximum timeout is 30 days.</p>
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn"
            onClick={onSave}
            disabled={
              save.isPending || isLoading || !dirty || tooShort || tooLong
            }
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          {dirty && (
            <span className="text-xs text-spark-muted">Unsaved changes</span>
          )}
        </div>
      </div>
    </section>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (n: number) => void;
  disabled: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wide text-spark-muted">
        {label}
      </span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          const raw = Number(e.target.value);
          if (Number.isNaN(raw)) return;
          onChange(Math.max(min, Math.min(max, Math.floor(raw))));
        }}
        className="mt-1 w-full px-2 py-1 bg-spark-bg border border-spark-border rounded text-sm tabular-nums disabled:cursor-not-allowed"
      />
    </label>
  );
}

function ToggleSwitch({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={onChange}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors shrink-0 ${
        enabled ? "bg-spark-accent" : "bg-spark-border"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${
          enabled ? "translate-x-4" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}
