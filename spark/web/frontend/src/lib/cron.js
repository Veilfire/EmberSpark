/**
 * Pure helpers for the visual cron-schedule builder.
 *
 * Two-way bind: ``parseCron`` detects which preset a saved expression
 * matches (so edit-task hydration shows the right radio + inputs),
 * ``buildCron`` regenerates a canonical expression from the preset
 * fields. Anything outside the preset vocabulary round-trips through
 * the ``custom`` mode unchanged.
 *
 * APScheduler's ``CronTrigger`` accepts both numeric (0–6) and named
 * (mon, tue, …) day-of-week values; we emit names because cron's
 * 0-vs-Sunday vs 0-vs-Monday ambiguity bites every couple of years.
 */
export const WEEKDAY_NAMES = [
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
];
const WEEKDAY_LABELS = {
    mon: "Mon",
    tue: "Tue",
    wed: "Wed",
    thu: "Thu",
    fri: "Fri",
    sat: "Sat",
    sun: "Sun",
};
const MONTH_LABELS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
];
export function weekdayLabel(d) {
    return WEEKDAY_LABELS[d];
}
export function monthLabel(m) {
    if (m < 1 || m > 12)
        return String(m);
    return MONTH_LABELS[m - 1];
}
const NUM = /^\d+$/;
const STEP = /^\*\/(\d+)$/;
function isInt(s) {
    return NUM.test(s);
}
function pad2(n) {
    return String(n).padStart(2, "0");
}
/** "08:30" → {hour: 8, minute: 30}. Returns null on bad shape. */
export function parseTimeOfDay(s) {
    const m = /^(\d{1,2}):(\d{2})$/.exec(s);
    if (!m)
        return null;
    const hour = parseInt(m[1], 10);
    const minute = parseInt(m[2], 10);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59)
        return null;
    return { hour, minute };
}
export function formatTimeOfDay(hour, minute) {
    return `${pad2(hour)}:${pad2(minute)}`;
}
/** "mon,wed,fri" → ["mon","wed","fri"]. Accepts numeric (0=mon..6=sun) too. */
function parseDow(field) {
    if (field === "*" || field === "")
        return null;
    const numericMap = {
        "0": "sun", "1": "mon", "2": "tue", "3": "wed",
        "4": "thu", "5": "fri", "6": "sat", "7": "sun",
    };
    const parts = field.toLowerCase().split(",").map((p) => p.trim());
    const out = [];
    for (const p of parts) {
        if (numericMap[p]) {
            out.push(numericMap[p]);
        }
        else if (WEEKDAY_NAMES.includes(p)) {
            out.push(p);
        }
        else {
            return null; // ranges (1-5), unknown name → punt to custom
        }
    }
    // Stable sort in canonical Mon..Sun order, dedup.
    const order = WEEKDAY_NAMES;
    const seen = new Set();
    return order.filter((d) => {
        if (out.includes(d) && !seen.has(d)) {
            seen.add(d);
            return true;
        }
        return false;
    });
}
function isWeekdayRange1to5(field) {
    // Detect "1-5" (Mon-Fri numeric) or "mon-fri".
    return /^(1-5|mon-fri)$/i.test(field.trim());
}
/**
 * Best-effort detection: which preset does this cron expression match?
 * Falls back to ``custom`` for anything outside the preset vocabulary.
 */
export function parseCron(expr) {
    if (!expr || typeof expr !== "string") {
        return { preset: "custom", fields: { custom: expr ?? "" } };
    }
    const parts = expr.trim().split(/\s+/);
    if (parts.length !== 5) {
        return { preset: "custom", fields: { custom: expr } };
    }
    const [m, h, d, mo, dow] = parts;
    // Every N minutes: */N * * * *
    if (mo === "*" && d === "*" && dow === "*" && h === "*") {
        const step = STEP.exec(m);
        if (step) {
            const n = parseInt(step[1], 10);
            if (n >= 1 && n <= 59)
                return { preset: "every_minutes", fields: { every: n } };
        }
    }
    // Every N hours: M */N * * *  (M usually 0)
    if (mo === "*" && d === "*" && dow === "*" && isInt(m)) {
        const step = STEP.exec(h);
        if (step) {
            const n = parseInt(step[1], 10);
            if (n >= 1 && n <= 23)
                return { preset: "every_hours", fields: { every: n } };
        }
    }
    // Daily at H:M  →  M H * * *
    if (mo === "*" && d === "*" && dow === "*" && isInt(m) && isInt(h)) {
        return {
            preset: "daily",
            fields: { hour: parseInt(h, 10), minute: parseInt(m, 10) },
        };
    }
    // Every weekday: M H * * 1-5  (or mon-fri)
    if (mo === "*" && d === "*" && isInt(m) && isInt(h) && isWeekdayRange1to5(dow)) {
        return {
            preset: "every_weekday",
            fields: { hour: parseInt(h, 10), minute: parseInt(m, 10) },
        };
    }
    // Weekly on selected weekdays: M H * * mon,wed,fri (or 1,3,5)
    if (mo === "*" && d === "*" && isInt(m) && isInt(h)) {
        const days = parseDow(dow);
        if (days !== null && days.length > 0) {
            return {
                preset: "weekly",
                fields: {
                    hour: parseInt(h, 10),
                    minute: parseInt(m, 10),
                    weekdays: days,
                },
            };
        }
    }
    // Monthly on day D: M H D * *
    if (mo === "*" && dow === "*" && isInt(m) && isInt(h) && isInt(d)) {
        return {
            preset: "monthly",
            fields: {
                day: parseInt(d, 10),
                hour: parseInt(h, 10),
                minute: parseInt(m, 10),
            },
        };
    }
    // Yearly: M H D MO *
    if (dow === "*" && isInt(m) && isInt(h) && isInt(d) && isInt(mo)) {
        const monthN = parseInt(mo, 10);
        if (monthN >= 1 && monthN <= 12) {
            return {
                preset: "yearly",
                fields: {
                    month: monthN,
                    day: parseInt(d, 10),
                    hour: parseInt(h, 10),
                    minute: parseInt(m, 10),
                },
            };
        }
    }
    return { preset: "custom", fields: { custom: expr } };
}
/**
 * Generate a canonical cron expression from the preset fields. Throws
 * for malformed inputs so the UI surfaces validation errors before the
 * value reaches the API.
 */
export function buildCron(preset, fields) {
    switch (preset) {
        case "every_minutes": {
            const n = fields.every ?? 0;
            if (n < 1 || n > 59)
                throw new Error("every_minutes N must be 1..59");
            return `*/${n} * * * *`;
        }
        case "every_hours": {
            const n = fields.every ?? 0;
            if (n < 1 || n > 23)
                throw new Error("every_hours N must be 1..23");
            return `0 */${n} * * *`;
        }
        case "daily": {
            const h = fields.hour ?? 0;
            const m = fields.minute ?? 0;
            return `${m} ${h} * * *`;
        }
        case "every_weekday": {
            const h = fields.hour ?? 0;
            const m = fields.minute ?? 0;
            return `${m} ${h} * * mon-fri`;
        }
        case "weekly": {
            const h = fields.hour ?? 0;
            const m = fields.minute ?? 0;
            const days = fields.weekdays ?? [];
            if (days.length === 0) {
                throw new Error("weekly: pick at least one weekday");
            }
            return `${m} ${h} * * ${days.join(",")}`;
        }
        case "monthly": {
            const h = fields.hour ?? 0;
            const m = fields.minute ?? 0;
            const d = fields.day ?? 1;
            if (d < 1 || d > 31)
                throw new Error("monthly day must be 1..31");
            return `${m} ${h} ${d} * *`;
        }
        case "yearly": {
            const h = fields.hour ?? 0;
            const m = fields.minute ?? 0;
            const d = fields.day ?? 1;
            const mo = fields.month ?? 1;
            if (d < 1 || d > 31)
                throw new Error("yearly day must be 1..31");
            if (mo < 1 || mo > 12)
                throw new Error("yearly month must be 1..12");
            return `${m} ${h} ${d} ${mo} *`;
        }
        case "custom": {
            const c = (fields.custom ?? "").trim();
            if (!c)
                throw new Error("custom: expression is empty");
            const parts = c.split(/\s+/);
            if (parts.length !== 5) {
                throw new Error("custom: cron must have 5 whitespace-separated fields");
            }
            return c;
        }
    }
}
