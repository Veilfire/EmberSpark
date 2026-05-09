import { Modal } from "./Modal";

interface ShortcutHelpProps {
  open: boolean;
  onClose: () => void;
}

const GROUPS: { title: string; items: [string, string][] }[] = [
  {
    title: "Navigation",
    items: [
      ["⌘K", "Open command palette"],
      ["?", "Show this help"],
      ["/", "Focus search on current page"],
      ["Esc", "Close modal / dialog"],
    ],
  },
  {
    title: "Chat",
    items: [
      ["Enter", "Send message"],
      ["Shift+Enter", "New line"],
    ],
  },
];

export function ShortcutHelp({ open, onClose }: ShortcutHelpProps) {
  return (
    <Modal open={open} onClose={onClose}>
      <div className="bg-spark-panel border border-spark-border rounded-lg w-full max-w-2xl max-h-[80vh] overflow-auto p-6 shadow-2xl">
        <h2 className="text-lg font-bold mb-4">Keyboard shortcuts</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <h3 className="text-xs uppercase tracking-wide text-spark-muted mb-2">
                {g.title}
              </h3>
              <dl className="space-y-1.5">
                {g.items.map(([k, label]) => (
                  <div key={k} className="flex items-center justify-between gap-3">
                    <dd className="text-sm">{label}</dd>
                    <dt className="kbd whitespace-nowrap">{k}</dt>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
        <div className="mt-4 pt-4 border-t border-spark-border flex justify-end">
          <button className="btn" onClick={onClose}>
            Close (Esc)
          </button>
        </div>
      </div>
    </Modal>
  );
}
