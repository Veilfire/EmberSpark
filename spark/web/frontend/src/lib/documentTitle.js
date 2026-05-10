import { useEffect } from "react";
import { useLocation, useParams } from "react-router-dom";
/**
 * Per-route document title.
 *
 * Tabs / window-list / Cmd+T history show the page name alongside the
 * product name so an operator with multiple Spark tabs open can tell
 * Chat from Audit at a glance. The sidebar route → label map is the
 * source of truth — keep this dispatch in sync with
 * `Shell.tsx::NAV_GROUPS` when routes are added or relabeled.
 *
 * Format: ``Spark — Agent Runtime : <Page>``. The em-dash matches the
 * static title in `index.html` so both flavors render with the same
 * separator.
 */
const BASE = "Spark — Agent Runtime";
const ROUTE_TITLES = [
    // Run
    { match: /^\/$/, label: "Overview" },
    { match: /^\/agents\/[^/]+$/, label: "Agent" },
    { match: /^\/agents\/?$/, label: "Agents" },
    { match: /^\/chat\/?$/, label: "Chat" },
    { match: /^\/runs\/[^/]+\/replay\/?$/, label: "Run replay" },
    { match: /^\/runs\/?$/, label: "Runs" },
    { match: /^\/scheduler\/?$/, label: "Scheduler" },
    { match: /^\/templates\/?$/, label: "Templates" },
    // Observe
    { match: /^\/cost\/?$/, label: "Cost" },
    { match: /^\/memory\/?$/, label: "Memory" },
    { match: /^\/skills\/?$/, label: "Skills" },
    { match: /^\/stats\/?$/, label: "Stats" },
    { match: /^\/downloads\/?$/, label: "Downloads" },
    // Secure
    { match: /^\/security\/?$/, label: "Security" },
    { match: /^\/secrets\/?$/, label: "Secrets" },
    { match: /^\/guardrails\/?$/, label: "Guardrails" },
    { match: /^\/filtering\/?$/, label: "Filtering" },
    { match: /^\/forensic\/[^/]+\/?$/, label: "Forensic" },
    { match: /^\/forensic\/?$/, label: "Forensic" },
    { match: /^\/audit\/?$/, label: "Audit" },
    // System
    { match: /^\/provider\/?$/, label: "Provider" },
    { match: /^\/persona\/?$/, label: "Persona" },
    { match: /^\/plugins\/?$/, label: "Plugins" },
    { match: /^\/ops\/?$/, label: "Ops" },
    { match: /^\/settings\/?$/, label: "Settings" },
    // Auth
    { match: /^\/login\/?$/, label: "Sign in" },
];
function titleFor(pathname) {
    for (const entry of ROUTE_TITLES) {
        if (entry.match.test(pathname))
            return `${BASE} : ${entry.label}`;
    }
    return BASE;
}
/**
 * Mount once near the top of the React tree (inside the Router but
 * outside any auth gate). Updates ``document.title`` on every
 * navigation; renders nothing.
 */
export function DocumentTitle() {
    const location = useLocation();
    useEffect(() => {
        document.title = titleFor(location.pathname);
    }, [location.pathname]);
    return null;
}
/**
 * Sub-page override. Pages that want a more specific title (e.g. an
 * AgentDetail page rendering "Spark — Agent Runtime : Agent · my-bot")
 * call this with a fragment; null clears the override and the
 * route-based default reapplies.
 */
export function usePageTitle(detail) {
    const location = useLocation();
    const params = useParams();
    useEffect(() => {
        const base = titleFor(location.pathname);
        if (detail && detail.trim().length > 0) {
            document.title = `${base} · ${detail}`;
        }
        else {
            document.title = base;
        }
        // params is included so re-renders on route-param change fire even
        // if the pathname matched the same regex (e.g. /agents/:name).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [location.pathname, detail, JSON.stringify(params)]);
}
