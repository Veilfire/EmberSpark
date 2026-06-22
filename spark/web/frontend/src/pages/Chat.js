import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, MessageSquare, Pencil, Pin, PinOff, Plus, Send, Settings2, Square, Trash2, X, } from "lucide-react";
import { toast } from "sonner";
import { api, sseConnect } from "../lib/api";
import { MarkdownView } from "../components/MarkdownView";
import { RelativeTime } from "../components/RelativeTime";
import { EmptyState } from "../components/primitives";
import { confirmDialog } from "../lib/confirm";
import { Modal } from "../components/Modal";
import { FailureInspector, WhyToggle, isSparkError, } from "../components/FailureInspector";
const DEFAULT_CONTEXT = {
    max_history_messages: 20,
    include_long_term_memory: true,
    ltm_top_k: 6,
    ltm_min_score: 0.72,
    include_global: false,
};
function loadContext(sessionId) {
    if (!sessionId)
        return DEFAULT_CONTEXT;
    try {
        const raw = localStorage.getItem(`spark.chat.context.${sessionId}`);
        if (raw)
            return { ...DEFAULT_CONTEXT, ...JSON.parse(raw) };
    }
    catch {
        /* noop */
    }
    return DEFAULT_CONTEXT;
}
function saveContext(sessionId, cfg) {
    if (!sessionId)
        return;
    try {
        localStorage.setItem(`spark.chat.context.${sessionId}`, JSON.stringify(cfg));
    }
    catch {
        /* noop */
    }
}
export default function Chat() {
    const qc = useQueryClient();
    const agents = useQuery({
        queryKey: ["chat-agents"],
        queryFn: () => api.get("/api/scheduler/agents"),
    });
    const sessions = useQuery({
        queryKey: ["chat-sessions"],
        queryFn: () => api.get("/api/chat/sessions"),
        refetchInterval: 10_000,
    });
    // Live updates from the SSE bus. The runtime publishes
    // ``chat.session_updated`` when a session's title or other metadata
    // changes (most commonly: first-turn title generation finished).
    // Without this, the sidebar wouldn't reflect the new title until
    // the next 10-s refetchInterval tick. Survives WS reconnects and
    // works across multiple tabs.
    useEffect(() => {
        const disconnect = sseConnect("/api/stream/events", {
            onMessage: (raw) => {
                if (typeof raw !== "object" || raw === null)
                    return;
                const env = raw;
                if (env.kind !== "chat.session_updated" &&
                    env.kind !== "chat.session_deleted")
                    return;
                qc.invalidateQueries({ queryKey: ["chat-sessions"] });
            },
        });
        return disconnect;
    }, [qc]);
    const [agentName, setAgentName] = useState("");
    const [sessionId, setSessionId] = useState("");
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState("");
    const [connected, setConnected] = useState(false);
    const [streaming, setStreaming] = useState(false);
    const [sessionFilter, setSessionFilter] = useState("");
    const [renamingId, setRenamingId] = useState(null);
    const [contextConfig, setContextConfig] = useState(DEFAULT_CONTEXT);
    const [showContext, setShowContext] = useState(false);
    const wsRef = useRef(null);
    const scrollRef = useRef(null);
    const inputRef = useRef(null);
    // Guards inline-rename against a double commit (Enter, then blur as the
    // input unmounts) — see commitRename.
    const renamingRef = useRef(null);
    useEffect(() => {
        return () => {
            wsRef.current?.close();
        };
    }, []);
    useEffect(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }, [messages]);
    function connectWs(sid) {
        wsRef.current?.close();
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(`${protocol}//${location.host}/api/chat/ws/${sid}`);
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
                }
                else if (data.kind === "citations") {
                    // Attach citations to the message being streamed (or upcoming).
                    setMessages((prev) => {
                        const updated = [...prev];
                        // Find the most recent assistant message; if none, stash
                        // on a new placeholder for the next token.
                        for (let i = updated.length - 1; i >= 0; i--) {
                            if (updated[i].kind === "assistant") {
                                updated[i] = {
                                    ...updated[i],
                                    citations: data.memories,
                                };
                                return updated;
                            }
                        }
                        return [
                            ...updated,
                            {
                                kind: "assistant",
                                content: "",
                                citations: data.memories,
                            },
                        ];
                    });
                }
                else if (data.kind === "resume") {
                    // Server is telling us a background turn is already running
                    // for this session (e.g. the operator navigated away mid-
                    // response and came back). Seed the partial assistant message
                    // now; subsequent `token` frames append to it.
                    const partial = String(data.data?.assistant_message ?? "");
                    const citations = (data.data?.citations ?? []);
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
                }
                else if (data.kind === "started") {
                    // Ack that the background task is running. Nothing to do —
                    // `send()` already set streaming=true optimistically.
                }
                else if (data.kind === "done") {
                    setStreaming(false);
                    // Refresh the sessions list so a freshly-generated title (or
                    // updated_at bump) lands in the sidebar without a manual reload.
                    qc.invalidateQueries({ queryKey: ["chat-sessions"] });
                }
                else if (data.kind === "tool") {
                    setMessages((m) => [
                        ...m,
                        { kind: "tool", content: JSON.stringify(data.data) },
                    ]);
                }
                else if (data.kind === "tool_call") {
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
                }
                else if (data.kind === "tool_result") {
                    const plugin = data.data?.plugin ?? "?";
                    const isErr = !!data.data?.is_error;
                    const body = isErr
                        ? data.data?.error || data.data?.error_class || "(error)"
                        : (() => {
                            const c = data.data?.content;
                            return typeof c === "string" ? c : JSON.stringify(c);
                        })();
                    // The backend now ships `error_payload` carrying the full
                    // SparkError.to_dict() shape. Stash it on the message so the
                    // FailureInspector can render beneath the thin error line.
                    const errorPayload = isErr && isSparkError(data.data?.error_payload)
                        ? data.data.error_payload
                        : undefined;
                    setMessages((m) => [
                        ...m,
                        {
                            kind: "tool",
                            content: `${isErr ? "✗" : "←"} ${plugin}: ${typeof body === "string" && body.length > 240
                                ? body.slice(0, 240) + "…"
                                : body}`,
                            errorPayload,
                        },
                    ]);
                }
                else if (data.kind === "error") {
                    // Input guardrail block + similar one-shot errors. Backend
                    // sends `error: SparkError.to_dict()` alongside the legacy
                    // `content` string.
                    const errorPayload = isSparkError(data.error)
                        ? data.error
                        : undefined;
                    setMessages((m) => [
                        ...m,
                        {
                            kind: "system",
                            content: `error: ${data.content}`,
                            errorPayload,
                        },
                    ]);
                    setStreaming(false);
                }
            }
            catch {
                /* ignore */
            }
        };
        wsRef.current = ws;
    }
    async function startNewSession() {
        if (!agentName)
            return;
        const resp = await api.post("/api/chat/sessions", {
            agent_name: agentName,
            name: `web-${Date.now()}`,
        });
        setSessionId(resp.session_id);
        setMessages([]);
        setContextConfig(loadContext(resp.session_id));
        connectWs(resp.session_id);
        qc.invalidateQueries({ queryKey: ["chat-sessions"] });
        setTimeout(() => inputRef.current?.focus(), 100);
    }
    async function resumeSession(s) {
        setAgentName(s.agent_name);
        setSessionId(s.session_id);
        setContextConfig(loadContext(s.session_id));
        try {
            const history = await api.get(`/api/chat/sessions/${encodeURIComponent(s.session_id)}/history`);
            setMessages(history.map((h) => ({
                kind: (h.kind === "user"
                    ? "user"
                    : "assistant"),
                content: h.content,
            })));
        }
        catch {
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
    async function commitRename(s, nextTitle) {
        // Only the first commit for this row wins — pressing Enter can be
        // followed by a blur event as the input unmounts.
        if (renamingRef.current !== s.session_id)
            return;
        renamingRef.current = null;
        setRenamingId(null);
        const trimmed = nextTitle.trim();
        if (!trimmed || trimmed === (s.title ?? ""))
            return;
        try {
            await api.put(`/api/chat/sessions/${encodeURIComponent(s.session_id)}`, {
                title: trimmed,
            });
            toast.success("Chat renamed");
            qc.invalidateQueries({ queryKey: ["chat-sessions"] });
        }
        catch (e) {
            toast.error(`Rename failed: ${e.message}`);
        }
    }
    async function togglePin(s) {
        try {
            await api.put(`/api/chat/sessions/${encodeURIComponent(s.session_id)}`, {
                pinned: !s.pinned,
            });
            qc.invalidateQueries({ queryKey: ["chat-sessions"] });
        }
        catch (e) {
            toast.error(`${s.pinned ? "Unpin" : "Pin"} failed: ${e.message}`);
        }
    }
    async function deleteSession(s) {
        const label = s.title ?? s.name ?? s.session_id;
        const ok = await confirmDialog({
            title: "Delete chat?",
            description: `"${label}" and all of its messages will be permanently removed.`,
            tone: "danger",
            confirmLabel: "Delete",
        });
        if (!ok)
            return;
        try {
            await api.del(`/api/chat/sessions/${encodeURIComponent(s.session_id)}`);
            toast.success("Chat deleted");
            if (s.session_id === sessionId)
                endSession();
            qc.invalidateQueries({ queryKey: ["chat-sessions"] });
        }
        catch (e) {
            toast.error(`Delete failed: ${e.message}`);
        }
    }
    function stopStreaming() {
        wsRef.current?.close();
        setStreaming(false);
        setConnected(false);
        setTimeout(() => connectWs(sessionId), 200);
    }
    function send() {
        if (!wsRef.current || !input.trim() || !agentName || streaming)
            return;
        setMessages((m) => [...m, { kind: "user", content: input }]);
        wsRef.current.send(JSON.stringify({
            content: input,
            agent_name: agentName,
            context: contextConfig,
        }));
        setInput("");
        setStreaming(true);
    }
    function updateContext(patch) {
        const next = { ...contextConfig, ...patch };
        setContextConfig(next);
        saveContext(sessionId, next);
    }
    function handleKeyDown(e) {
        // Enter sends; Shift+Enter newline.
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
        }
    }
    const filteredSessions = useMemo(() => {
        const all = sessions.data ?? [];
        const q = sessionFilter.trim().toLowerCase();
        const matched = !q
            ? all
            : all.filter((s) => s.session_id.toLowerCase().includes(q) ||
                s.agent_name.toLowerCase().includes(q) ||
                s.name.toLowerCase().includes(q) ||
                (s.title ?? "").toLowerCase().includes(q));
        // Pinned chats float to the top; recency within each group.
        return [...matched].sort((a, b) => Number(b.pinned) - Number(a.pinned) ||
            b.updated_at.localeCompare(a.updated_at));
    }, [sessions.data, sessionFilter]);
    const activeSession = sessions.data?.find((s) => s.session_id === sessionId);
    // Chat with sidebar layout.
    return (_jsxs("div", { className: "flex h-[calc(100vh-3rem)] gap-4", children: [_jsxs("aside", { className: "w-72 panel flex flex-col shrink-0 shadow-sm overflow-hidden", children: [_jsxs("div", { className: "p-3 border-b border-spark-border", children: [_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [_jsxs("select", { className: "input flex-1 text-xs", value: agentName, onChange: (e) => setAgentName(e.target.value), disabled: !!sessionId, children: [_jsx("option", { value: "", children: "Choose agent\u2026" }), (agents.data ?? []).map((a) => (_jsx("option", { value: a.name, children: a.name }, a.name)))] }), _jsx("button", { className: "btn btn-primary shrink-0", onClick: startNewSession, disabled: !agentName, title: "New session", children: _jsx(Plus, { className: "w-4 h-4" }) })] }), _jsx("input", { className: "input w-full text-xs", placeholder: "Search sessions\u2026", value: sessionFilter, onChange: (e) => setSessionFilter(e.target.value) })] }), _jsx("div", { className: "flex-1 overflow-y-auto", children: filteredSessions.length === 0 ? (_jsx("p", { className: "text-spark-muted text-xs text-center py-8 px-3", children: sessions.data?.length === 0
                                ? "No sessions yet. Pick an agent and click +."
                                : "No matches." })) : (filteredSessions.map((s) => (_jsx("div", { className: `relative group border-b border-spark-border/50 ${s.session_id === sessionId
                                ? "bg-spark-accent/10 border-l-2 border-l-spark-accent"
                                : "hover:bg-spark-border/30"}`, children: renamingId === s.session_id ? (_jsx("div", { className: "px-3 py-2", children: _jsx("input", { className: "input w-full text-sm", autoFocus: true, defaultValue: s.title ?? "", placeholder: "Chat name\u2026", onKeyDown: (e) => {
                                        if (e.key === "Enter") {
                                            e.preventDefault();
                                            commitRename(s, e.currentTarget.value);
                                        }
                                        else if (e.key === "Escape") {
                                            e.preventDefault();
                                            renamingRef.current = null;
                                            setRenamingId(null);
                                        }
                                    }, onBlur: (e) => commitRename(s, e.currentTarget.value) }) })) : (_jsxs(_Fragment, { children: [_jsxs("button", { className: "w-full text-left pl-3 pr-20 py-2 transition", onClick: () => resumeSession(s), children: [_jsxs("div", { className: "flex items-center gap-1.5", children: [s.pinned && (_jsx(Pin, { className: "w-3 h-3 text-spark-accent fill-spark-accent shrink-0" })), _jsx("div", { className: "text-sm truncate text-spark-text", children: s.title ?? (_jsx("span", { className: "font-mono text-xs", children: s.session_id })) })] }), _jsxs("div", { className: "flex items-center gap-2 mt-0.5 text-[10px] text-spark-muted", children: [_jsx("span", { className: "truncate", children: s.agent_name }), s.title && (_jsx("span", { className: "font-mono truncate", children: s.session_id })), _jsx("span", { className: "shrink-0 ml-auto", children: _jsx(RelativeTime, { ts: s.updated_at }) })] })] }), _jsxs("div", { className: "absolute right-1 top-1 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition", children: [_jsx("button", { className: "btn-icon", title: s.pinned ? "Unpin" : "Pin", "aria-label": s.pinned ? "Unpin chat" : "Pin chat", onClick: (e) => {
                                                    e.stopPropagation();
                                                    togglePin(s);
                                                }, children: s.pinned ? (_jsx(PinOff, { className: "w-3.5 h-3.5" })) : (_jsx(Pin, { className: "w-3.5 h-3.5" })) }), _jsx("button", { className: "btn-icon", title: "Rename", "aria-label": "Rename chat", onClick: (e) => {
                                                    e.stopPropagation();
                                                    renamingRef.current = s.session_id;
                                                    setRenamingId(s.session_id);
                                                }, children: _jsx(Pencil, { className: "w-3.5 h-3.5" }) }), _jsx("button", { className: "btn-icon text-spark-muted hover:text-spark-danger", title: "Delete", "aria-label": "Delete chat", onClick: (e) => {
                                                    e.stopPropagation();
                                                    deleteSession(s);
                                                }, children: _jsx(Trash2, { className: "w-3.5 h-3.5" }) })] })] })) }, s.session_id)))) })] }), _jsx("div", { className: "flex-1 flex flex-col min-w-0", children: !sessionId ? (_jsx("div", { className: "flex-1 flex items-center justify-center", children: _jsx(EmptyState, { icon: _jsx(MessageSquare, { className: "w-10 h-10" }), title: "Start a conversation", description: "Pick an agent in the sidebar and start a new session to chat." }) })) : (_jsxs("div", { className: "panel flex-1 flex flex-col shadow-sm overflow-hidden", children: [_jsxs("div", { className: "border-b border-spark-border px-4 py-2 flex items-center justify-between text-sm shrink-0", children: [_jsxs("div", { className: "flex items-center gap-2 min-w-0", children: [_jsx(MessageSquare, { className: "w-4 h-4 text-spark-accent shrink-0" }), activeSession?.title ? (_jsxs(_Fragment, { children: [_jsx("span", { className: "font-medium text-sm truncate", children: activeSession.title }), _jsx("span", { className: "font-mono text-xs text-spark-muted truncate shrink-0", children: sessionId })] })) : (_jsx("span", { className: "font-mono text-xs truncate", children: sessionId })), _jsx("span", { className: "chip text-[10px] shrink-0", children: agentName }), activeSession && (_jsxs("span", { className: "text-xs text-spark-muted shrink-0", children: ["\u2022 started ", _jsx(RelativeTime, { ts: activeSession.created_at })] }))] }), _jsxs("div", { className: "flex items-center gap-2 shrink-0", children: [_jsx("span", { className: `chip ${connected ? "chip-good" : "chip-danger"} text-[10px]`, children: connected ? "connected" : "disconnected" }), _jsx("button", { className: "btn-icon", onClick: () => setShowContext(true), title: "Context settings", "aria-label": "Context settings", children: _jsx(Settings2, { className: "w-4 h-4" }) }), _jsx("button", { className: "btn-icon", onClick: endSession, title: "Close session", "aria-label": "Close", children: _jsx(X, { className: "w-4 h-4" }) })] })] }), _jsxs("div", { ref: scrollRef, className: "flex-1 overflow-auto px-6 py-4 space-y-4", children: [messages.length === 0 && (_jsx("p", { className: "text-spark-muted text-sm text-center py-8", children: "Send a message to start the conversation." })), messages.map((m, i) => (_jsx(MessageBubble, { message: m }, i))), streaming && (_jsxs("div", { className: "flex items-center gap-2 text-spark-muted text-xs", children: [_jsxs("span", { className: "inline-flex gap-0.5", children: [_jsx("span", { className: "w-1 h-1 rounded-full bg-spark-accent animate-pulse" }), _jsx("span", { className: "w-1 h-1 rounded-full bg-spark-accent animate-pulse [animation-delay:200ms]" }), _jsx("span", { className: "w-1 h-1 rounded-full bg-spark-accent animate-pulse [animation-delay:400ms]" })] }), "streaming\u2026"] }))] }), _jsx("div", { className: "border-t border-spark-border p-3 shrink-0", children: _jsxs("div", { className: "flex gap-2 items-end", children: [_jsx("textarea", { ref: inputRef, className: "input flex-1 resize-none", rows: 1, value: input, onChange: (e) => setInput(e.target.value), onKeyDown: handleKeyDown, placeholder: "Send a message\u2026   \u00B7   Enter to send, Shift+Enter for newline", disabled: streaming, style: { maxHeight: "200px" } }), streaming ? (_jsx("button", { className: "btn btn-danger", onClick: stopStreaming, title: "Stop", children: _jsx(Square, { className: "w-4 h-4", fill: "currentColor" }) })) : (_jsx("button", { className: "btn btn-primary", onClick: send, disabled: !input.trim(), title: "Send", children: _jsx(Send, { className: "w-4 h-4" }) }))] }) })] })) }), _jsx(Modal, { open: showContext, onClose: () => setShowContext(false), children: _jsxs("div", { className: "bg-spark-panel border border-spark-border rounded-lg w-full max-w-md p-6 space-y-4 shadow-2xl", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("h3", { className: "font-semibold flex items-center gap-2", children: [_jsx(Settings2, { className: "w-4 h-4 text-spark-accent" }), " Context settings"] }), _jsx("button", { className: "btn-icon", onClick: () => setShowContext(false), "aria-label": "Close", children: _jsx(X, { className: "w-4 h-4" }) })] }), _jsx("p", { className: "text-xs text-spark-muted", children: "These settings are stored locally per session and applied on the next turn you send." }), _jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Chat history (", contextConfig.max_history_messages, " messages)"] }), _jsx("input", { type: "range", min: 0, max: 100, step: 1, value: contextConfig.max_history_messages, onChange: (e) => updateContext({
                                        max_history_messages: parseInt(e.target.value, 10),
                                    }), className: "w-full" }), _jsx("p", { className: "text-xs text-spark-muted mt-1", children: "Past user/assistant turns included in the prompt. Lower = less cost, less context." })] }), _jsxs("div", { className: "border-t border-spark-border pt-3", children: [_jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: contextConfig.include_long_term_memory, onChange: (e) => updateContext({ include_long_term_memory: e.target.checked }) }), "Include long-term memory"] }), _jsx("p", { className: "text-xs text-spark-muted mt-1 ml-6", children: "Retrieve semantically-similar memories from the agent's vector store and add them to the system prompt." })] }), contextConfig.include_long_term_memory && (_jsxs(_Fragment, { children: [_jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Memory top-K (", contextConfig.ltm_top_k, ")"] }), _jsx("input", { type: "range", min: 0, max: 20, step: 1, value: contextConfig.ltm_top_k, onChange: (e) => updateContext({ ltm_top_k: parseInt(e.target.value, 10) }), className: "w-full" })] }), _jsxs("div", { children: [_jsxs("label", { className: "text-xs uppercase text-spark-muted block mb-1", children: ["Min similarity (", contextConfig.ltm_min_score.toFixed(2), ")"] }), _jsx("input", { type: "range", min: 0, max: 1, step: 0.01, value: contextConfig.ltm_min_score, onChange: (e) => updateContext({
                                                ltm_min_score: parseFloat(e.target.value),
                                            }), className: "w-full" })] }), _jsxs("div", { children: [_jsxs("label", { className: "flex items-center gap-2 text-sm cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: contextConfig.include_global, onChange: (e) => updateContext({ include_global: e.target.checked }) }), "Include global / shared memories"] }), _jsxs("p", { className: "text-xs text-spark-muted mt-1 ml-6", children: ["Agents can opt in to share long-term memories with each other via the global pool. Requires the agent's", _jsx("code", { className: "font-mono ml-1", children: "memory.sharing.read_global" }), "config."] })] })] })), _jsxs("div", { className: "flex justify-between gap-2 pt-2 border-t border-spark-border", children: [_jsx("button", { className: "btn", onClick: () => {
                                        setContextConfig(DEFAULT_CONTEXT);
                                        saveContext(sessionId, DEFAULT_CONTEXT);
                                    }, children: "Reset to defaults" }), _jsx("button", { className: "btn btn-primary", onClick: () => setShowContext(false), children: "Done" })] })] }) })] }));
}
function MessageBubble({ message: m }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        await navigator.clipboard.writeText(m.content);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
    };
    if (m.kind === "user") {
        return (_jsx("div", { className: "flex justify-end", children: _jsxs("div", { className: "relative group max-w-[85%]", children: [_jsx("div", { className: "bg-spark-accent/10 border border-spark-accent/20 rounded-lg px-3 py-2 whitespace-pre-wrap text-sm", children: m.content }), _jsx("button", { className: "absolute -left-8 top-1 opacity-0 group-hover:opacity-100 btn-icon text-spark-muted", onClick: copy, title: "Copy", children: copied ? (_jsx(Check, { className: "w-3 h-3 text-spark-good" })) : (_jsx(Copy, { className: "w-3 h-3" })) })] }) }));
    }
    if (m.kind === "assistant") {
        return (_jsxs("div", { className: "relative group max-w-[90%]", children: [_jsx(MarkdownView, { content: m.content, className: "text-spark-text text-sm" }), _jsx("button", { className: "absolute -right-8 top-1 opacity-0 group-hover:opacity-100 btn-icon text-spark-muted", onClick: copy, title: "Copy", children: copied ? (_jsx(Check, { className: "w-3 h-3 text-spark-good" })) : (_jsx(Copy, { className: "w-3 h-3" })) }), m.citations && m.citations.length > 0 && (_jsx(CitationsFooter, { citations: m.citations }))] }));
    }
    if (m.kind === "tool") {
        return (_jsxs("div", { className: "max-w-[90%] space-y-1.5", children: [_jsx("pre", { className: "text-xs font-mono text-spark-accent bg-spark-bg border border-spark-border rounded p-2 overflow-x-auto whitespace-pre-wrap", children: m.content }), m.errorPayload && _jsx(ChatFailurePanel, { error: m.errorPayload })] }));
    }
    // Fallback: system messages and unknown kinds. Render thin error
    // line + the FailureInspector if a structured payload was attached.
    return (_jsxs("div", { className: "max-w-[90%] space-y-1.5", children: [_jsx("div", { className: "text-spark-danger text-xs", children: m.content }), m.errorPayload && _jsx(ChatFailurePanel, { error: m.errorPayload })] }));
}
function ChatFailurePanel({ error }) {
    const [open, setOpen] = useState(false);
    return (_jsxs("div", { children: [_jsx(WhyToggle, { open: open, onClick: () => setOpen((o) => !o) }), open && _jsx(FailureInspector, { error: error, variant: "inline" })] }));
}
function CitationsFooter({ citations }) {
    const [open, setOpen] = useState(false);
    return (_jsxs("div", { className: "mt-2 text-xs", children: [_jsxs("button", { className: "text-spark-muted hover:text-spark-accent inline-flex items-center gap-1", onClick: () => setOpen(!open), children: [_jsx("svg", { className: "w-3 h-3", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: "2", d: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" }) }), open ? "Hide" : "Show", " ", citations.length, " source", citations.length === 1 ? "" : "s"] }), open && (_jsx("div", { className: "mt-1 border-l-2 border-spark-border pl-3 space-y-1", children: citations.map((c, i) => (_jsxs("div", { className: "flex items-start gap-2 text-spark-muted", children: [_jsx("span", { className: `chip text-[10px] shrink-0 ${c.is_anti_pattern
                                ? "chip-danger"
                                : c.scope === "global"
                                    ? "chip-warn"
                                    : ""}`, children: c.is_anti_pattern ? `A${i + 1}` : `M${i + 1}` }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("div", { className: "truncate", children: c.summary }), _jsxs("div", { className: "text-[10px]", children: [c.memory_type, " \u00B7 ", c.scope, " \u00B7 score", " ", c.score.toFixed(2)] })] })] }, c.memory_id))) }))] }));
}
