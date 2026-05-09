import { jsx as _jsx } from "react/jsx-runtime";
import { useEffect, useReducer } from "react";
import { formatRelative, formatTimestamp, parseTs } from "../lib/utils";
function useTick(intervalMs) {
    const [, tick] = useReducer((x) => x + 1, 0);
    useEffect(() => {
        const id = setInterval(tick, intervalMs);
        return () => clearInterval(id);
    }, [intervalMs]);
}
/** Relative time with absolute time on hover. Re-renders every 30s. */
export function RelativeTime({ ts, className = "" }) {
    useTick(30_000);
    if (!ts)
        return _jsx("span", { className: className, children: "\u2014" });
    const date = parseTs(ts);
    const absolute = isNaN(date.getTime()) ? "" : date.toISOString();
    const relative = formatRelative(ts) ?? "—";
    return (_jsx("span", { className: className, title: absolute, children: relative }));
}
/** Absolute local-time timestamp. Use when you want an accurate reading. */
export function Timestamp({ ts, className = "" }) {
    if (!ts)
        return _jsx("span", { className: className, children: "\u2014" });
    const date = parseTs(ts);
    const absolute = isNaN(date.getTime()) ? "" : date.toISOString();
    return (_jsx("span", { className: className, title: absolute, children: formatTimestamp(ts) }));
}
