import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatUsd(n: number): string {
  return `$${n.toFixed(4).replace(/\.?0+$/, (m) => (m === "." ? "" : m))}`;
}

/**
 * Parse an ISO timestamp. If the string carries no timezone, assume UTC —
 * the Spark server always writes UTC but SQLite round-trips can drop tzinfo.
 */
export function parseTs(ts: string | Date): Date {
  if (ts instanceof Date) return ts;
  const hasTz = /(Z|[+-]\d{2}:?\d{2})$/.test(ts);
  return new Date(hasTz ? ts : ts + "Z");
}

export function formatRelative(ts: string | Date | null | undefined): string {
  if (!ts) return "never";
  const date = parseTs(ts);
  if (isNaN(date.getTime())) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/**
 * Absolute local-time timestamp, e.g. "2026-04-17 14:32".
 * Use when you want a stable, accurate reading rather than "N seconds ago".
 */
export function formatTimestamp(ts: string | Date | null | undefined): string {
  if (!ts) return "—";
  const date = parseTs(ts);
  if (isNaN(date.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  const y = date.getFullYear();
  const m = pad(date.getMonth() + 1);
  const d = pad(date.getDate());
  const hh = pad(date.getHours());
  const mm = pad(date.getMinutes());
  return `${y}-${m}-${d} ${hh}:${mm}`;
}

/**
 * Relative phrase that flips across the present moment: "in 5d" for a
 * future timestamp, "5d ago" for a past one. Use for expiry/deadline
 * fields where the sign matters semantically.
 */
export function formatUntil(ts: string | Date | null | undefined): string {
  if (!ts) return "never";
  const date = parseTs(ts);
  if (isNaN(date.getTime())) return "—";
  const deltaSec = Math.floor((date.getTime() - Date.now()) / 1000);
  const future = deltaSec >= 0;
  const abs = Math.abs(deltaSec);
  let unit: string;
  if (abs < 60) unit = `${abs}s`;
  else if (abs < 3600) unit = `${Math.floor(abs / 60)}m`;
  else if (abs < 86400) unit = `${Math.floor(abs / 3600)}h`;
  else unit = `${Math.floor(abs / 86400)}d`;
  return future ? `in ${unit}` : `${unit} ago`;
}

export function severityColor(severity: string): string {
  switch (severity) {
    case "critical":
      return "chip-danger";
    case "elevated":
      return "chip-warn";
    default:
      return "";
  }
}
