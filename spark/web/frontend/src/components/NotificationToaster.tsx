import { useEffect } from "react";
import { Toaster, toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { useNotifications, NotificationView } from "../hooks/useNotifications";

/**
 * Mounts sonner's <Toaster /> and wires live SSE notification events into
 * transient toasts. Click anywhere on a toast: marks the notification
 * read AND navigates to its action_url. Auto-dismiss (the duration
 * timeout) intentionally does NOT navigate — that would yank the
 * operator off the page they're working on every few seconds.
 */
export function NotificationToaster() {
  const navigate = useNavigate();
  const { setToastHandler, markRead, preferences } = useNotifications();

  useEffect(() => {
    const handler = (n: NotificationView) => {
      if (!preferences?.toast_on_create) return;
      const description = n.body || undefined;
      // sonner 1.x doesn't have a whole-toast onClick prop; render an
      // explicit "Open" action button when there's somewhere to go.
      // Clicking the action button auto-acks the row (so the bell
      // badge decrements immediately) and navigates. Auto-dismiss
      // intentionally does NOT navigate or ack — that would yank
      // operators off whatever they're working on every few seconds.
      const action = n.action_url
        ? {
            label: "Open",
            onClick: () => {
              void markRead(n.id);
              navigate(n.action_url as string);
            },
          }
        : undefined;
      const opts = { description, action };
      if (n.severity === "critical") {
        toast.error(n.title, { ...opts, duration: 8000 });
      } else if (n.severity === "elevated") {
        toast.warning(n.title, { ...opts, duration: 6000 });
      } else {
        toast(n.title, { ...opts, duration: 5000 });
      }
    };
    setToastHandler(handler);
    return () => setToastHandler(null);
  }, [setToastHandler, preferences?.toast_on_create, navigate, markRead]);

  return (
    <Toaster
      position="bottom-right"
      theme="dark"
      richColors
      closeButton
      expand
      visibleToasts={4}
      toastOptions={{
        classNames: {
          toast: "!bg-spark-panel !border-spark-border !shadow-lg",
        },
      }}
    />
  );
}
