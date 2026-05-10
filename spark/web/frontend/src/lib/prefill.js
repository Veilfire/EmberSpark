/**
 * Prefill schema — shared shape between the Python remediation
 * catalogue and the React pages that hydrate forms from it.
 *
 * The Failure Inspector's deep-link buttons embed a base64-encoded
 * JSON dict in `?prefill=...`. The target page reads it,
 * pre-populates the relevant form, and shows a "suggested by failure
 * inspector" banner with a discard link. The operator clicks Save
 * manually — this layer never auto-mutates.
 *
 * Add a new prefill kind in three places:
 *   1. Python: a new branch in `spark/errors/remediation.py` that
 *      builds a `prefill: dict[str, Any]` of the matching shape.
 *   2. TypeScript: extend `Prefill` below + the type guard.
 *   3. The target page: read it via `useSuggestedPrefill()` and
 *      hydrate.
 */
// ---------------------------------------------------------------------------
// URL-safe base64 encode/decode (no padding) — matches Python's
// urlsafe_b64encode().rstrip("=") in spark/errors/remediation.py.
// ---------------------------------------------------------------------------
export function encodePrefill(payload) {
    const json = JSON.stringify(payload);
    // btoa wants Latin-1 — JSON of these payloads is ASCII-safe.
    return btoa(json)
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=+$/, "");
}
export function decodePrefill(encoded) {
    if (!encoded)
        return null;
    try {
        const padded = encoded.replace(/-/g, "+").replace(/_/g, "/");
        const fill = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
        const json = atob(padded + fill);
        const parsed = JSON.parse(json);
        if (parsed && typeof parsed === "object" && typeof parsed.kind === "string") {
            return parsed;
        }
        return null;
    }
    catch {
        return null;
    }
}
// ---------------------------------------------------------------------------
// React helpers
// ---------------------------------------------------------------------------
import { useSearchParams } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
/**
 * Read a `?prefill=` payload of the matching kind from the current URL.
 *
 * Returns `[prefill, discard]` where `discard()` strips the param so
 * the highlight + banner clear without a full reload.
 *
 * Pages that handle multiple prefill shapes can call this once per
 * shape; only the matching kind activates.
 */
export function useSuggestedPrefill(expectedKind) {
    const [params, setParams] = useSearchParams();
    const raw = params.get("prefill");
    const decoded = useMemo(() => decodePrefill(raw), [raw]);
    const matched = decoded && decoded.kind === expectedKind
        ? decoded
        : null;
    const discard = () => {
        params.delete("prefill");
        setParams(params, { replace: true });
    };
    return [matched, discard];
}
/**
 * Banner shown above prefilled forms — "Suggested by failure inspector".
 * Tiny stateless component; pages compose it next to the highlighted
 * control. The amber ring on the field itself is each page's job.
 */
export function suggestionBannerProps(prefill, onDiscard) {
    return {
        show: prefill !== null,
        label: "Suggested by failure inspector — review before saving.",
        onDiscard,
    };
}
/**
 * One-shot "I just saw this prefill" hook for highlight effects that
 * should fade after first render (e.g. a flash on the prefilled row).
 */
export function usePrefillFlash(prefill, ms = 2000) {
    const [active, setActive] = useState(false);
    useEffect(() => {
        if (!prefill) {
            setActive(false);
            return;
        }
        setActive(true);
        const t = setTimeout(() => setActive(false), ms);
        return () => clearTimeout(t);
    }, [prefill, ms]);
    return active;
}
