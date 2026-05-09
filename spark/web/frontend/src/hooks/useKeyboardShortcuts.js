import { useEffect, useState } from "react";
/**
 * Global keyboard shortcut handler.
 *
 * Currently handles:
 * - `?` → toggle help overlay
 * - `/` → focus the first search input on the page (skipped if typing in a field)
 *
 * Cmd+K (command palette) is handled by CommandPalette itself.
 */
export function useKeyboardShortcuts() {
    const [helpOpen, setHelpOpen] = useState(false);
    useEffect(() => {
        function isEditable(target) {
            if (!(target instanceof HTMLElement))
                return false;
            const tag = target.tagName.toLowerCase();
            return (tag === "input" ||
                tag === "textarea" ||
                tag === "select" ||
                target.isContentEditable);
        }
        function onKey(e) {
            if (e.metaKey || e.ctrlKey || e.altKey)
                return;
            if (isEditable(e.target))
                return;
            if (e.key === "?") {
                e.preventDefault();
                setHelpOpen((v) => !v);
                return;
            }
            if (e.key === "/") {
                const input = document.querySelector('input[type="search"], input[placeholder*="Search" i], input[placeholder*="search" i]');
                if (input) {
                    e.preventDefault();
                    input.focus();
                }
            }
        }
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, []);
    return { helpOpen, setHelpOpen };
}
