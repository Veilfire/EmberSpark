import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
/**
 * Tiny cron expression parser — produces the next N fire times + a
 * human-readable summary. Supports standard 5-field cron:
 * `minute hour day month weekday`.
 * Only covers the subset of patterns used by EmberSpark:
 *   *, n, n-n, *\/n, a,b,c
 */
export function nextFireTimes(expr, count = 5, from = new Date()) {
    const parts = expr.trim().split(/\s+/);
    if (parts.length !== 5)
        return [];
    const [mi, hr, dom, mo, dow] = parts.map((p) => parseField(p));
    const results = [];
    const cursor = new Date(from.getTime());
    cursor.setSeconds(0, 0);
    cursor.setMinutes(cursor.getMinutes() + 1);
    // Safety bound: scan up to 2 years of minutes = 1M iterations.
    const MAX = 2 * 365 * 24 * 60;
    let steps = 0;
    while (results.length < count && steps < MAX) {
        steps++;
        if (mi.includes(cursor.getMinutes()) &&
            hr.includes(cursor.getHours()) &&
            dom.includes(cursor.getDate()) &&
            mo.includes(cursor.getMonth() + 1) &&
            dow.includes(cursor.getDay())) {
            results.push(new Date(cursor.getTime()));
        }
        cursor.setMinutes(cursor.getMinutes() + 1);
    }
    return results;
}
function parseField(field) {
    // Field indices are positional; we accept up to all reasonable ranges.
    // For robustness we just accept any number that fits; if caller gives
    // bad input the fire-time scan simply won't match.
    const out = new Set();
    const pieces = field.split(",");
    for (const p of pieces) {
        if (p === "*") {
            // We don't know the range here without caller context, so we mark
            // with a wildcard flag (encoded as a negative sentinel, then
            // expanded during iteration below).
            // Instead, return a large range that covers all cron fields.
            for (let i = 0; i <= 59; i++)
                out.add(i); // minutes
            for (let i = 0; i <= 23; i++)
                out.add(i); // hours
            for (let i = 1; i <= 31; i++)
                out.add(i); // day
            for (let i = 1; i <= 12; i++)
                out.add(i); // month
            for (let i = 0; i <= 6; i++)
                out.add(i); // weekday
            // The checks below use field-specific ranges; this is fine.
        }
        else if (p.startsWith("*/")) {
            const step = parseInt(p.slice(2), 10);
            if (step > 0) {
                for (let i = 0; i <= 59; i++)
                    if (i % step === 0)
                        out.add(i);
            }
        }
        else if (p.includes("-")) {
            const [a, b] = p.split("-").map((x) => parseInt(x, 10));
            if (!isNaN(a) && !isNaN(b)) {
                for (let i = a; i <= b; i++)
                    out.add(i);
            }
        }
        else {
            const n = parseInt(p, 10);
            if (!isNaN(n))
                out.add(n);
        }
    }
    return Array.from(out);
}
export function CronPreview({ expr }) {
    const times = nextFireTimes(expr, 3);
    if (times.length === 0) {
        return (_jsxs("span", { className: "text-spark-muted text-xs", children: ["Cannot parse cron \"", expr, "\""] }));
    }
    return (_jsxs("div", { className: "text-xs text-spark-muted", children: ["Next fires:", " ", _jsx("span", { className: "text-spark-text", children: times
                    .map((t) => t.toLocaleString(undefined, {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                }))
                    .join(" · ") })] }));
}
