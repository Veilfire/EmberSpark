// Notifications hook — polls /api/notifications/unread-count and subscribes
// to /api/stream/events for live `notification.created` events. Caches the
// recent list so the bell and drawer stay in sync without extra fetches.

import { useEffect, useState, useCallback, useRef } from "react";
import { api, sseConnect } from "../lib/api";

export interface NotificationView {
  id: number;
  kind: string;
  severity: "info" | "elevated" | "critical";
  title: string;
  body: string | null;
  target_kind: string | null;
  target_id: string | null;
  action_url: string | null;
  created_at: string;
  read_at: string | null;
  dismissed_at: string | null;
}

export interface UnreadCount {
  total: number;
  by_kind: Record<string, number>;
}

export interface NotificationPreferences {
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
  play_sound: boolean;
  toast_on_create: boolean;
}

// Server-side bus envelope: { kind: "notification.created", payload: {...} }.
// We unwrap once before reading the typed fields below.
interface BusEnvelope {
  kind?: string;
  payload?: BusPayload;
  // Some legacy emitters flattened payload onto the envelope; keep the
  // optional flat fields so older streams keep parsing.
  id?: number;
  title?: string;
  severity?: string;
  action_url?: string | null;
  body?: string | null;
}

interface BusPayload {
  id?: number;
  // The notification's *kind* (e.g. ``download_ready``,
  // ``hitl_skill_review``). The bus envelope's own ``kind`` is the
  // *event-name* (``notification.created``); the per-row category lives
  // here under ``notification_kind`` to avoid colliding with the bus's
  // positional kwarg. ``kind`` is kept as a fallback for older streams.
  notification_kind?: string;
  kind?: string;
  title?: string;
  severity?: string;
  action_url?: string | null;
  body?: string | null;
  target_kind?: string | null;
  target_id?: string | null;
  created_at?: string;
}

type ToastHandler = (notification: NotificationView) => void;

interface UseNotificationsResult {
  notifications: NotificationView[];
  unread: UnreadCount;
  reload: () => Promise<void>;
  markRead: (id: number) => Promise<void>;
  dismiss: (id: number) => Promise<void>;
  readAll: () => Promise<void>;
  preferences: NotificationPreferences | null;
  setToastHandler: (handler: ToastHandler | null) => void;
}

export function useNotifications(): UseNotificationsResult {
  const [notifications, setNotifications] = useState<NotificationView[]>([]);
  const [unread, setUnread] = useState<UnreadCount>({ total: 0, by_kind: {} });
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const toastHandlerRef = useRef<ToastHandler | null>(null);

  const reload = useCallback(async () => {
    try {
      const [list, count, prefs] = await Promise.all([
        api.get<NotificationView[]>("/api/notifications/?limit=50"),
        api.get<UnreadCount>("/api/notifications/unread-count"),
        api.get<NotificationPreferences>("/api/notifications/preferences"),
      ]);
      setNotifications(list);
      setUnread(count);
      setPreferences(prefs);
    } catch {
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
        if (typeof raw !== "object" || raw === null) return;
        const envelope = raw as BusEnvelope;
        if (envelope.kind !== "notification.created") return;
        // Server wraps the row in {kind, payload}. Older streams
        // flattened the row onto the envelope itself, so we fall back
        // to the envelope when payload is absent.
        const data: BusPayload = envelope.payload ?? (envelope as BusPayload);

        // Reload to pick up the new row in the drawer + count badge.
        reload();

        // Fire a toast for visible feedback if the user opts in.
        const handler = toastHandlerRef.current;
        if (
          handler &&
          preferences?.toast_on_create !== false &&
          typeof data.id === "number"
        ) {
          handler({
            id: data.id,
            kind: data.notification_kind || data.kind || "incident",
            severity: (data.severity as NotificationView["severity"]) || "info",
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

  const markRead = useCallback(
    async (id: number) => {
      await api.post(`/api/notifications/${id}/read`);
      await reload();
    },
    [reload]
  );

  const dismiss = useCallback(
    async (id: number) => {
      await api.post(`/api/notifications/${id}/dismiss`);
      await reload();
    },
    [reload]
  );

  const readAll = useCallback(async () => {
    await api.post("/api/notifications/read-all");
    await reload();
  }, [reload]);

  const setToastHandler = useCallback((handler: ToastHandler | null) => {
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
