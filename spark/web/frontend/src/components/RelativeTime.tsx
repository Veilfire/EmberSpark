import { useEffect, useReducer } from "react";
import { formatRelative, formatTimestamp, parseTs } from "../lib/utils";

interface RelativeTimeProps {
  ts: string | Date | null | undefined;
  className?: string;
}

function useTick(intervalMs: number): void {
  const [, tick] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    const id = setInterval(tick, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

/** Relative time with absolute time on hover. Re-renders every 30s. */
export function RelativeTime({ ts, className = "" }: RelativeTimeProps) {
  useTick(30_000);
  if (!ts) return <span className={className}>—</span>;
  const date = parseTs(ts);
  const absolute = isNaN(date.getTime()) ? "" : date.toISOString();
  const relative = formatRelative(ts) ?? "—";
  return (
    <span className={className} title={absolute}>
      {relative}
    </span>
  );
}

/** Absolute local-time timestamp. Use when you want an accurate reading. */
export function Timestamp({ ts, className = "" }: RelativeTimeProps) {
  if (!ts) return <span className={className}>—</span>;
  const date = parseTs(ts);
  const absolute = isNaN(date.getTime()) ? "" : date.toISOString();
  return (
    <span className={className} title={absolute}>
      {formatTimestamp(ts)}
    </span>
  );
}
