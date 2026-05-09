import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  MessageSquare,
  Plus,
  Send,
  Settings2,
  Square,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { AgentSummary } from "../lib/types";
import { MarkdownView } from "../components/MarkdownView";
import { RelativeTime } from "../components/RelativeTime";
import { EmptyState } from "../components/primitives";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";

interface Citation {
  memory_id: string;
  summary: string;
  memory_type: string;
  score: number;
  scope: string;
  is_anti_pattern: boolean;
}

interface ChatMessage {
  kind: "user" | "assistant" | "system" | "tool";
  content: string;
  citations?: Citation[];
}

type SessionSummary = {
  session_id: string;
  name: string;
  agent_name: string;
  created_at: string;
  updated_at: string;
};

type HistoryEntry = { kind: string; content: string };

type ContextConfig = {
  max_history_messages: number;
  include_long_term_memory: boolean;
  ltm_top_k: number;
  ltm_min_score: number;
  include_global: boolean;
};

const DEFAULT_CONTEXT: ContextConfig = {
  max_history_messages: 20,
  include_long_term_memory: true,
  ltm_top_k: 6,
  ltm_min_score: 0.72,
  include_global: false,
};

function loadContext(sessionId: string): ContextConfig {
  if (!sessionId) return DEFAULT_CONTEXT;
  try {
    const raw = localStorage.getItem(`spark.chat.context.${sessionId}`);
    if (raw) return { ...DEFAULT_CONTEXT, ...JSON.parse(raw) };
  } catch {
    /* noop */
  }
  return DEFAULT_CONTEXT;
}

function saveContext(sessionId: string, cfg: ContextConfig) {
  if (!sessionId) return;
  try {
    localStorage.setItem(
      `spark.chat.context.${sessionId}`,
      JSON.stringify(cfg),
    );
  } catch {
    /* noop */
  }
}

export default function Chat() {
  const qc = useQueryClient();
  const agents = useQuery<AgentSummary[]>({
    queryKey: ["chat-agents"],
    queryFn: () => api.get("/api/scheduler/agents"),
  });
  const sessions = useQuery<SessionSummary[]>({
    queryKey: ["chat-sessions"],
    queryFn: () => api.get("/api/chat/sessions"),
    refetchInterval: 10_000,
  });

  const [agentName, setAgentName] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [sessionFilter, setSessionFilter] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<string | null>(
    null,
  );
  const [contextConfig, setContextConfig] = useState<ContextConfig>(
    DEFAULT_CONTEXT,
  );
  const [showContext, setShowContext] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  function connectWs(sid: string) {
    wsRef.current?.close();
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${protocol}//${location.host}/api/chat/ws/${sid}`,
    );
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.kind === "token") {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "assistant") {
              const updated = [...prev];
              updated[updated.length - 1] = {
                ...last,
                content: last.content + (data.content ?? ""),
              };
              return updated;
            }
            return [...prev, { kind: "assistant", content: data.content ?? "" }];
          });
        } else if (data.kind === "citations") {
          // Attach citations to the message being streamed (or upcoming).
          setMessages((prev) => {
            const updated = [...prev];
            // Find the most recent assistant message; if none, stash
            // on a new placeholder for the next token.
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].kind === "assistant") {
                updated[i] = {
                  ...updated[i],
                  citations: data.memories as Citation[],
                };
                return updated;
              }
            }
            return [
              ...updated,
              {
                kind: "assistant",
                content: "",
                citations: data.memories as Citation[],
              },
            ];
          });
        } else if (data.kind === "resume") {
          // Server is telling us a background turn is already running
          // for this session (e.g. the operator navigated away mid-
          // response and came back). Seed the partial assistant message
          // now; subsequent `token` frames append to it.
          const partial = String(data.data?.assistant_message ?? "");
          const citations = (data.data?.citations ?? []) as Citation[];
          setMessages((prev) => {
            // Avoid a double placeholder if we somehow already have an
            // empty assistant bubble at the tail.
            const last = prev[prev.length - 1];
            if (last && last.kind === "assistant" && last.content === "") {
              const updated = [...prev];
              updated[updated.length - 1] = {
                kind: "assistant",
                content: partial,
                citations: citations.length ? citations : last.citations,
              };
              return updated;
            }
            return [
              ...prev,
              {
                kind: "assistant",
                content: partial,
                citations: citations.length ? citations : undefined,
              },
            ];
          });
          setStreaming(true);
        } else if (data.kind === "started") {
          // Ack that the background task is running. Nothing to do —
          // `send()` already set streaming=true optimistically.
        } else if (data.kind === "done") {
          setStreaming(false);
        } else if (data.kind === "tool") {
          setMessages((m) => [
            ...m,
            { kind: "tool", content: JSON.stringify(data.data) },
          ]);
        } else if (data.kind === "tool_call") {
          // The model asked the runtime to invoke a plugin. Render it
          // as a "tool" message so the user sees what the agent decided
          // to do; the matching `tool_result` event arrives next.
          const plugin = data.data?.plugin ?? "?";
          const args = data.data?.args ?? {};
          setMessages((m) => [
            ...m,
            {
              kind: "tool",
              content: `→ ${plugin}(${JSON.stringify(args)})`,
            },
          ]);
        } else if (data.kind === "tool_result") {
          const plugin = data.data?.plugin ?? "?";
          const isErr = !!data.data?.is_error;
          const body = isErr
            ? data.data?.error || data.data?.error_class || "(error)"
            : (() => {
                const c = data.data?.content;
                return typeof c === "string" ? c : JSON.stringify(c);
              })();
          setMessages((m) => [
            ...m,
            {
              kind: "tool",
              content: `${isErr ? "✗" : "←"} ${plugin}: ${
                typeof body === "string" && body.length > 240
                  ? body.slice(0, 240) + "…"
                  : body
              }`,
            },
          ]);
        } else if (data.kind === "error") {
          setMessages((m) => [
            ...m,
            { kind: "system", content: `error: ${data.content}` },
          ]);
          setStreaming(false);
        }
      } catch {
        /* ignore */
      }
    };
    wsRef.current = ws;
  }

  async function startNewSession() {
    if (!agentName) return;
    const resp = await api.post<{ session_id: string }>(
      "/api/chat/sessions",
      {
        agent_name: agentName,
        name: `web-${Date.now()}`,
      },
    );
    setSessionId(resp.session_id);
    setMessages([]);
    setContextConfig(loadContext(resp.session_id));
    connectWs(resp.session_id);
    qc.invalidateQueries({ queryKey: ["chat-sessions"] });
    setTimeout(() => inputRef.current?.focus(), 100);
  }

  async function resumeSession(s: SessionSummary) {
    setAgentName(s.agent_name);
    setSessionId(s.session_id);
    setContextConfig(loadContext(s.session_id));
    try {
      const history = await api.get<HistoryEntry[]>(
        `/api/chat/sessions/${encodeURIComponent(s.session_id)}/history`,
      );
      setMessages(
        history.map((h) => ({
          kind: (h.kind === "user"
            ? "user"
            : "assistant") as ChatMessage["kind"],
          content: h.content,
        })),
      );
    } catch {
      setMessages([]);
    }
    connectWs(s.session_id);
    setTimeout(() => inputRef.current?.focus(), 100);
  }

  function endSession() {
    wsRef.current?.close();
    setSessionId("");
    setMessages([]);
    setConnected(false);
    setStreaming(false);
  }

  function stopStreaming() {
    wsRef.current?.close();
    setStreaming(false);
    setConnected(false);
    setTimeout(() => connectWs(sessionId), 200);
  }

  function send() {
    if (!wsRef.current || !input.trim() || !agentName || streaming) return;
    setMessages((m) => [...m, { kind: "user", content: input }]);
    wsRef.current.send(
      JSON.stringify({
        content: input,
        agent_name: agentName,
        context: contextConfig,
      }),
    );
    setInput("");
    setStreaming(true);
  }

  function updateContext(patch: Partial<ContextConfig>) {
    const next = { ...contextConfig, ...patch };
    setContextConfig(next);
    saveContext(sessionId, next);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    // Enter sends; Shift+Enter newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const filteredSessions = useMemo(() => {
    if (!sessionFilter) return sessions.data ?? [];
    const q = sessionFilter.toLowerCase();
    return (sessions.data ?? []).filter(
      (s) =>
        s.session_id.toLowerCase().includes(q) ||
        s.agent_name.toLowerCase().includes(q) ||
        s.name.toLowerCase().includes(q),
    );
  }, [sessions.data, sessionFilter]);

  const activeSession = sessions.data?.find((s) => s.session_id === sessionId);

  // Chat with sidebar layout.
  return (
    <div className="flex h-[calc(100vh-3rem)] gap-4">
      {/* Session sidebar */}
      <aside className="w-72 panel flex flex-col shrink-0 shadow-sm overflow-hidden">
        <div className="p-3 border-b border-spark-border">
          <div className="flex items-center gap-2 mb-2">
            <select
              className="input flex-1 text-xs"
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              disabled={!!sessionId}
            >
              <option value="">Choose agent…</option>
              {(agents.data ?? []).map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name}
                </option>
              ))}
            </select>
            <button
              className="btn btn-primary shrink-0"
              onClick={startNewSession}
              disabled={!agentName}
              title="New session"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
          <input
            className="input w-full text-xs"
            placeholder="Search sessions…"
            value={sessionFilter}
            onChange={(e) => setSessionFilter(e.target.value)}
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {filteredSessions.length === 0 ? (
            <p className="text-spark-muted text-xs text-center py-8 px-3">
              {sessions.data?.length === 0
                ? "No sessions yet. Pick an agent and click +."
                : "No matches."}
            </p>
          ) : (
            filteredSessions.map((s) => (
              <div
                key={s.session_id}
                className={`relative group border-b border-spark-border/50 ${
                  s.session_id === sessionId
                    ? "bg-spark-accent/10 border-l-2 border-l-spark-accent"
                    : "hover:bg-spark-border/30"
                }`}
              >
                <button
                  className="w-full text-left px-3 py-2 transition"
                  onClick={() => resumeSession(s)}
                >
                  <div className="font-mono text-xs truncate">
                    {s.session_id}
                  </div>
                  <div className="flex items-center justify-between mt-0.5">
                    <span className="text-xs text-spark-muted truncate">
                      {s.agent_name}
                    </span>
                    <span className="text-[10px] text-spark-muted shrink-0 ml-2">
                      <RelativeTime ts={s.updated_at} />
                    </span>
                  </div>
                </button>
              </div>
            ))
          )}
        </div>
      </aside>

      {/* Chat pane */}
      <div className="flex-1 flex flex-col min-w-0">
        {!sessionId ? (
          <div className="flex-1 flex items-center justify-center">
            <EmptyState
              icon={<MessageSquare className="w-10 h-10" />}
              title="Start a conversation"
              description="Pick an agent in the sidebar and start a new session to chat."
            />
          </div>
        ) : (
          <div className="panel flex-1 flex flex-col shadow-sm overflow-hidden">
            {/* Header */}
            <div className="border-b border-spark-border px-4 py-2 flex items-center justify-between text-sm shrink-0">
              <div className="flex items-center gap-2 min-w-0">
                <MessageSquare className="w-4 h-4 text-spark-accent shrink-0" />
                <span className="font-mono text-xs truncate">{sessionId}</span>
                <span className="chip text-[10px] shrink-0">{agentName}</span>
                {activeSession && (
                  <span className="text-xs text-spark-muted shrink-0">
                    • started <RelativeTime ts={activeSession.created_at} />
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span
                  className={`chip ${
                    connected ? "chip-good" : "chip-danger"
                  } text-[10px]`}
                >
                  {connected ? "connected" : "disconnected"}
                </span>
                <button
                  className="btn-icon"
                  onClick={() => setShowContext(true)}
                  title="Context settings"
                  aria-label="Context settings"
                >
                  <Settings2 className="w-4 h-4" />
                </button>
                <button
                  className="btn-icon"
                  onClick={endSession}
                  title="Close session"
                  aria-label="Close"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Messages */}
            <div
              ref={scrollRef}
              className="flex-1 overflow-auto px-6 py-4 space-y-4"
            >
              {messages.length === 0 && (
                <p className="text-spark-muted text-sm text-center py-8">
                  Send a message to start the conversation.
                </p>
              )}
              {messages.map((m, i) => (
                <MessageBubble key={i} message={m} />
              ))}
              {streaming && (
                <div className="flex items-center gap-2 text-spark-muted text-xs">
                  <span className="inline-flex gap-0.5">
                    <span className="w-1 h-1 rounded-full bg-spark-accent animate-pulse" />
                    <span className="w-1 h-1 rounded-full bg-spark-accent animate-pulse [animation-delay:200ms]" />
                    <span className="w-1 h-1 rounded-full bg-spark-accent animate-pulse [animation-delay:400ms]" />
                  </span>
                  streaming…
                </div>
              )}
            </div>

            {/* Input */}
            <div className="border-t border-spark-border p-3 shrink-0">
              <div className="flex gap-2 items-end">
                <textarea
                  ref={inputRef}
                  className="input flex-1 resize-none"
                  rows={1}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Send a message…   ·   Enter to send, Shift+Enter for newline"
                  disabled={streaming}
                  style={{ maxHeight: "200px" }}
                />
                {streaming ? (
                  <button
                    className="btn btn-danger"
                    onClick={stopStreaming}
                    title="Stop"
                  >
                    <Square className="w-4 h-4" fill="currentColor" />
                  </button>
                ) : (
                  <button
                    className="btn btn-primary"
                    onClick={send}
                    disabled={!input.trim()}
                    title="Send"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      <Modal open={showContext} onClose={() => setShowContext(false)}>
        <div className="bg-spark-panel border border-spark-border rounded-lg w-full max-w-md p-6 space-y-4 shadow-2xl">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold flex items-center gap-2">
              <Settings2 className="w-4 h-4 text-spark-accent" /> Context settings
            </h3>
            <button
              className="btn-icon"
              onClick={() => setShowContext(false)}
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <p className="text-xs text-spark-muted">
            These settings are stored locally per session and applied on the
            next turn you send.
          </p>

          <div>
            <label className="text-xs uppercase text-spark-muted block mb-1">
              Chat history ({contextConfig.max_history_messages} messages)
            </label>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={contextConfig.max_history_messages}
              onChange={(e) =>
                updateContext({
                  max_history_messages: parseInt(e.target.value, 10),
                })
              }
              className="w-full"
            />
            <p className="text-xs text-spark-muted mt-1">
              Past user/assistant turns included in the prompt. Lower = less
              cost, less context.
            </p>
          </div>

          <div className="border-t border-spark-border pt-3">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={contextConfig.include_long_term_memory}
                onChange={(e) =>
                  updateContext({ include_long_term_memory: e.target.checked })
                }
              />
              Include long-term memory
            </label>
            <p className="text-xs text-spark-muted mt-1 ml-6">
              Retrieve semantically-similar memories from the agent's vector
              store and add them to the system prompt.
            </p>
          </div>

          {contextConfig.include_long_term_memory && (
            <>
              <div>
                <label className="text-xs uppercase text-spark-muted block mb-1">
                  Memory top-K ({contextConfig.ltm_top_k})
                </label>
                <input
                  type="range"
                  min={0}
                  max={20}
                  step={1}
                  value={contextConfig.ltm_top_k}
                  onChange={(e) =>
                    updateContext({ ltm_top_k: parseInt(e.target.value, 10) })
                  }
                  className="w-full"
                />
              </div>
              <div>
                <label className="text-xs uppercase text-spark-muted block mb-1">
                  Min similarity ({contextConfig.ltm_min_score.toFixed(2)})
                </label>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={contextConfig.ltm_min_score}
                  onChange={(e) =>
                    updateContext({
                      ltm_min_score: parseFloat(e.target.value),
                    })
                  }
                  className="w-full"
                />
              </div>
              <div>
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={contextConfig.include_global}
                    onChange={(e) =>
                      updateContext({ include_global: e.target.checked })
                    }
                  />
                  Include global / shared memories
                </label>
                <p className="text-xs text-spark-muted mt-1 ml-6">
                  Agents can opt in to share long-term memories with each
                  other via the global pool. Requires the agent's
                  <code className="font-mono ml-1">memory.sharing.read_global</code>
                  config.
                </p>
              </div>
            </>
          )}

          <div className="flex justify-between gap-2 pt-2 border-t border-spark-border">
            <button
              className="btn"
              onClick={() => {
                setContextConfig(DEFAULT_CONTEXT);
                saveContext(sessionId, DEFAULT_CONTEXT);
              }}
            >
              Reset to defaults
            </button>
            <button
              className="btn btn-primary"
              onClick={() => setShowContext(false)}
            >
              Done
            </button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!showDeleteConfirm}
        title="Delete session?"
        description="Removes the session and all its messages."
        tone="danger"
        confirmLabel="Delete"
        onCancel={() => setShowDeleteConfirm(null)}
        onConfirm={() => {
          // For now we just hide locally — no backend delete route exists yet.
          toast.info("Session hidden from list");
          setShowDeleteConfirm(null);
        }}
      />
    </div>
  );
}

function MessageBubble({ message: m }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(m.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (m.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="relative group max-w-[85%]">
          <div className="bg-spark-accent/10 border border-spark-accent/20 rounded-lg px-3 py-2 whitespace-pre-wrap text-sm">
            {m.content}
          </div>
          <button
            className="absolute -left-8 top-1 opacity-0 group-hover:opacity-100 btn-icon text-spark-muted"
            onClick={copy}
            title="Copy"
          >
            {copied ? (
              <Check className="w-3 h-3 text-spark-good" />
            ) : (
              <Copy className="w-3 h-3" />
            )}
          </button>
        </div>
      </div>
    );
  }

  if (m.kind === "assistant") {
    return (
      <div className="relative group max-w-[90%]">
        <MarkdownView content={m.content} className="text-spark-text text-sm" />
        <button
          className="absolute -right-8 top-1 opacity-0 group-hover:opacity-100 btn-icon text-spark-muted"
          onClick={copy}
          title="Copy"
        >
          {copied ? (
            <Check className="w-3 h-3 text-spark-good" />
          ) : (
            <Copy className="w-3 h-3" />
          )}
        </button>
        {m.citations && m.citations.length > 0 && (
          <CitationsFooter citations={m.citations} />
        )}
      </div>
    );
  }

  if (m.kind === "tool") {
    return (
      <pre className="text-xs font-mono text-spark-accent bg-spark-bg border border-spark-border rounded p-2 overflow-x-auto whitespace-pre-wrap max-w-[90%]">
        {m.content}
      </pre>
    );
  }

  return <div className="text-spark-danger text-xs">{m.content}</div>;
}

function CitationsFooter({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 text-xs">
      <button
        className="text-spark-muted hover:text-spark-accent inline-flex items-center gap-1"
        onClick={() => setOpen(!open)}
      >
        <svg
          className="w-3 h-3"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
        {open ? "Hide" : "Show"} {citations.length} source{citations.length === 1 ? "" : "s"}
      </button>
      {open && (
        <div className="mt-1 border-l-2 border-spark-border pl-3 space-y-1">
          {citations.map((c, i) => (
            <div
              key={c.memory_id}
              className="flex items-start gap-2 text-spark-muted"
            >
              <span
                className={`chip text-[10px] shrink-0 ${
                  c.is_anti_pattern
                    ? "chip-danger"
                    : c.scope === "global"
                      ? "chip-warn"
                      : ""
                }`}
              >
                {c.is_anti_pattern ? `A${i + 1}` : `M${i + 1}`}
              </span>
              <div className="flex-1 min-w-0">
                <div className="truncate">{c.summary}</div>
                <div className="text-[10px]">
                  {c.memory_type} · {c.scope} · score{" "}
                  {c.score.toFixed(2)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
