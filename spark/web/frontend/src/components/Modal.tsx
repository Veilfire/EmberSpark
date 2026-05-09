import { ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  /** When true, clicking the backdrop closes the modal. Default: true */
  closeOnBackdrop?: boolean;
}

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Portal-based modal that renders directly to `document.body`, guaranteeing
 * the backdrop covers the full viewport regardless of parent stacking
 * contexts or overflow containers. Includes focus trap + Escape + body
 * scroll lock + aria-modal semantics.
 */
export function Modal({
  open,
  onClose,
  children,
  closeOnBackdrop = true,
}: ModalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  // Close on Escape + focus trap.
  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "Tab" && containerRef.current) {
        const focusable =
          containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE);
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);

    // Focus the first focusable element on mount.
    setTimeout(() => {
      const first =
        containerRef.current?.querySelector<HTMLElement>(FOCUSABLE);
      first?.focus();
    }, 50);

    return () => {
      document.removeEventListener("keydown", handler);
      previouslyFocused.current?.focus();
    };
  }, [open, onClose]);

  // Lock body scroll while open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      className="fixed top-0 left-0 right-0 bottom-0 w-screen h-screen bg-black/70 backdrop-blur-sm flex items-center justify-center z-[100] p-4 animate-enter"
      onClick={closeOnBackdrop ? onClose : undefined}
    >
      <div onClick={(e) => e.stopPropagation()} className="contents">
        {children}
      </div>
    </div>,
    document.body,
  );
}
