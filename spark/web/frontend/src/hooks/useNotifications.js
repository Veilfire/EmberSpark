// Notifications hook — polls /api/notifications/unread-count and subscribes
// to /api/stream/events for live `notification.created` events. Caches the
// recent list so the bell and drawer stay in sync without extra fetches.
import { useEffect, useState, useCallback, useRef } from "react";
import { api, sseConnect } from "../lib/api";
export function useNotifications() {
    const [notifications, setNotifications] = useState([]);
    const [unread, setUnread] = useState({ total: 0, by_kind: {} });
    const [preferences, setPreferences] = useState(null);
    const toastHandlerRef = useRef(null);
    const reload = useCallback(async () => {
        try {
            const [list, count, prefs] = await Promise.all([
                api.get("/api/notifications/?limit=50"),
                api.get("/api/notifications/unread-count"),
                api.get("/api/notifications/preferences"),
            ]);
            setNotifications(list);
            setUnread(count);
            setPreferences(prefs);
        }
        catch {
            /* silent — bell just won't update this tick */
        }
    }, []);
    useEffect(() => {
        reload();
    }, [reload]);
    // Subscribe to SSE for live notification.created events.
    useEffect(() => {
        const disconnect = sseConnect("/api/stream/events", {
            onMessage: (raw) => {
                if (typeof raw !== "object" || raw === null)
                    return;
                const envelope = raw;
                if (envelope.kind !== "notification.created")
                    return;
                // Server wraps the row in {kind, payload}. Older streams
                // flattened the row onto the envelope itself, so we fall back
                // to the envelope when payload is absent.
                const data = envelope.payload ?? envelope;
                // Reload to pick up the new row in the drawer + count badge.
                reload();
                // Fire a toast for visible feedback if the user opts in.
                const handler = toastHandlerRef.current;
                if (handler &&
                    preferences?.toast_on_create !== false &&
                    typeof data.id === "number") {
                    handler({
                        id: data.id,
                        kind: data.notification_kind || data.kind || "incident",
                        severity: data.severity || "info",
                        title: data.title || "Notification",
                        body: data.body || null,
                        target_kind: data.target_kind || null,
                        target_id: data.target_id || null,
                        action_url: data.action_url || null,
                        created_at: data.created_at || new Date().toISOString(),
                        read_at: null,
                        dismissed_at: null,
                    });
                }
            },
        });
        return disconnect;
    }, [reload, preferences?.toast_on_create]);
    const markRead = useCallback(async (id) => {
        await api.post(`/api/notifications/${id}/read`);
        await reload();
    }, [reload]);
    const dismiss = useCallback(async (id) => {
        await api.post(`/api/notifications/${id}/dismiss`);
        await reload();
    }, [reload]);
    const readAll = useCallback(async () => {
        await api.post("/api/notifications/read-all");
        await reload();
    }, [reload]);
    const setToastHandler = useCallback((handler) => {
        toastHandlerRef.current = handler;
    }, []);
    return {
        notifications,
        unread,
        reload,
        markRead,
        dismiss,
        readAll,
        preferences,
        setToastHandler,
    };
}
