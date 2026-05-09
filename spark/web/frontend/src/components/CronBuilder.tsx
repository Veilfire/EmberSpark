/**
 * Visual cron-schedule builder.
 *
 * Wraps the raw cron-expression input with preset radio buttons +
 * preset-specific inputs (time picker, weekday checkboxes, …). The
 * generated cron string is always shown read-only so power users see
 * exactly what's emitted, and the ``Custom`` preset is an escape hatch
 * for expressions outside the preset vocabulary.
 *
 * Two-way bound: ``parseCron(value)`` re-detects the preset on every
 * external value change, which makes edit-task hydration work cleanly.
 */

import { useEffect, useMemo, useState } from "react";
import {
  CronFields,
  CronPreset,
  WeekdayName,
  WEEKDAY_NAMES,
  buildCron,
  formatTimeOfDay,
  monthLabel,
  parseCron,
  parseTimeOfDay,
  weekdayLabel,
} from "../lib/cron";

interface CronBuilderProps {
  value: string;
  onChange: (cron: string) => void;
}

const PRESETS: { value: CronPreset; label: string; hint: string }[] = [
  { value: "every_minutes", label: "Every N minutes", hint: "*/N * * * *" },
  { value: "every_hours", label: "Every N hours", hint: "0 */N * * *" },
  { value: "daily", label: "Daily at time", hint: "M H * * *" },
  { value: "every_weekday", label: "Every weekday at time", hint: "M H * * mon-fri" },
  { value: "weekly", label: "On selected weekdays at time", hint: "M H * * mon,wed,fri" },
  { value: "monthly", label: "Monthly on day at time", hint: "M H D * *" },
  { value: "yearly", label: "Yearly on month/day at time", hint: "M H D MO *" },
  { value: "custom", label: "Custom cron expression", hint: "raw 5-field" },
];

export function CronBuilder({ value, onChange }: CronBuilderProps) {
  // Detect preset + populate inputs from the externally-supplied value.
  const detected = useMemo(() => parseCron(value), [value]);

  const [preset, setPreset] = useState<CronPreset>(detected.preset);
  const [every, setEvery] = useState<number>(detected.fields.every ?? 15);
  const [time, setTime] = useState<string>(
    formatTimeOfDay(detected.fields.hour ?? 8, detected.fields.minute ?? 0),
  );
  const [weekdays, setWeekdays] = useState<WeekdayName[]>(
    detected.fields.weekdays ?? ["mon"],
  );
  const [day, setDay] = useState<number>(detected.fields.day ?? 1);
  const [month, setMonth] = useState<number>(detected.fields.month ?? 1);
  const [custom, setCustom] = useState<string>(
    detected.fields.custom ?? value ?? "",
  );

  // When the parent ``value`` changes from outside (e.g. operator
  // swapped between create and edit), re-hydrate. Compare-by-equality
  // on the detection result avoids spamming setters when the parent is
  // just echoing back what we emitted.
  useEffect(() => {
    const next = parseCron(value);
    setPreset(next.preset);
    if (next.fields.every !== undefined) setEvery(next.fields.every);
    if (next.fields.hour !== undefined && next.fields.minute !== undefined) {
      setTime(formatTimeOfDay(next.fields.hour, next.fields.minute));
    }
    if (next.fields.weekdays !== undefined) setWeekdays(next.fields.weekdays);
    if (next.fields.day !== undefined) setDay(next.fields.day);
    if (next.fields.month !== undefined) setMonth(next.fields.month);
    if (next.fields.custom !== undefined) setCustom(next.fields.custom);
    // We deliberately depend only on ``value`` — the local state
    // setters are the secondary effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // Compose the canonical cron from the active preset + inputs.
  const composed = useMemo(() => {
    const t = parseTimeOfDay(time);
    const fields: CronFields = {
      every,
      hour: t?.hour ?? 0,
      minute: t?.minute ?? 0,
      weekdays,
      day,
      month,
      custom,
    };
    try {
      return { value: buildCron(preset, fields), error: null as string | null };
    } catch (e) {
      return { value: "", error: (e as Error).message };
    }
  }, [preset, every, time, weekdays, day, month, custom]);

  // Push composed value upward whenever it changes. Skip the
  // round-trip when the value is unchanged to avoid effect loops.
  useEffect(() => {
    if (!composed.value) return;
    if (composed.value === value) return;
    onChange(composed.value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [composed.value]);

  function toggleWeekday(d: WeekdayName) {
    setWeekdays((prev) =>
      prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d],
    );
  }

  return (
    <div className="space-y-2">
      {/* Preset selector */}
      <div>
        <span className="label text-xs">Repeat</span>
        <div className="grid grid-cols-2 gap-1 mt-1">
          {PRESETS.map((p) => (
            <label
              key={p.value}
              className={`flex items-center gap-2 px-2 py-1 rounded text-xs cursor-pointer border ${
                preset === p.value
                  ? "border-spark-accent bg-spark-accent/5"
                  : "border-spark-border hover:border-spark-accent/50"
              }`}
            >
              <input
                type="radio"
                name="cron-preset"
                checked={preset === p.value}
                onChange={() => setPreset(p.value)}
              />
              <span>{p.label}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Preset-specific inputs */}
      <div className="border border-spark-border rounded p-3 space-y-2 bg-spark-bg/40">
        {(preset === "every_minutes" || preset === "every_hours") && (
          <label className="flex items-center gap-2 text-sm">
            Every
            <input
              type="number"
              className="input w-20 text-xs"
              min={1}
              max={preset === "every_minutes" ? 59 : 23}
              value={every}
              onChange={(e) => setEvery(parseInt(e.target.value, 10) || 1)}
            />
            {preset === "every_minutes" ? "minute(s)" : "hour(s)"}
          </label>
        )}

        {(preset === "daily"
          || preset === "weekly"
          || preset === "every_weekday"
          || preset === "monthly"
          || preset === "yearly") && (
          <label className="flex items-center gap-2 text-sm">
            <span className="w-12">at</span>
            <input
              type="time"
              className="input w-32 text-xs"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
          </label>
        )}

        {preset === "weekly" && (
          <div>
            <span className="label text-xs">On</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {WEEKDAY_NAMES.map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`px-2.5 py-1 rounded text-xs border ${
                    weekdays.includes(d)
                      ? "border-spark-accent bg-spark-accent/15 text-spark-accent"
                      : "border-spark-border hover:border-spark-accent/50"
                  }`}
                  onClick={() => toggleWeekday(d)}
                >
                  {weekdayLabel(d)}
                </button>
              ))}
            </div>
            {weekdays.length === 0 && (
              <p className="text-[10px] text-spark-danger mt-1">
                Pick at least one weekday.
              </p>
            )}
          </div>
        )}

        {preset === "monthly" && (
          <label className="flex items-center gap-2 text-sm">
            <span className="w-12">on day</span>
            <select
              className="input w-20 text-xs"
              value={day}
              onChange={(e) => setDay(parseInt(e.target.value, 10))}
            >
              {Array.from({ length: 31 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <span className="text-[10px] text-spark-muted">
              days 29–31 are skipped in months that don't have them.
            </span>
          </label>
        )}

        {preset === "yearly" && (
          <div className="flex items-center gap-2 text-sm">
            <span className="w-12">on</span>
            <select
              className="input w-32 text-xs"
              value={month}
              onChange={(e) => setMonth(parseInt(e.target.value, 10))}
            >
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                <option key={m} value={m}>
                  {monthLabel(m)}
                </option>
              ))}
            </select>
            <select
              className="input w-20 text-xs"
              value={day}
              onChange={(e) => setDay(parseInt(e.target.value, 10))}
            >
              {Array.from({ length: 31 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        )}

        {preset === "custom" && (
          <label className="block text-sm">
            <span className="label text-xs">Cron expression (5 fields)</span>
            <input
              type="text"
              className="input w-full font-mono text-xs"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              placeholder="0 8 * * mon-fri"
            />
            <span className="text-[10px] text-spark-muted">
              minute hour day-of-month month day-of-week
            </span>
          </label>
        )}
      </div>

      {/* Generated cron — always visible. */}
      <div className="text-xs text-spark-muted flex items-center gap-2">
        Generated:
        <code className="font-mono text-spark-text bg-spark-bg/60 px-2 py-0.5 rounded">
          {composed.value || "(invalid)"}
        </code>
      </div>
      {composed.error && (
        <div className="text-[11px] text-spark-danger">{composed.error}</div>
      )}
    </div>
  );
}
