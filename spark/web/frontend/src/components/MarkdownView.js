import { jsx as _jsx } from "react/jsx-runtime";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
/**
 * Render model/agent output as Markdown.
 *
 * Security notes:
 * - react-markdown does NOT render raw HTML unless you explicitly enable
 *   `rehype-raw`. We don't. Any `<script>` / `<style>` in the content is
 *   rendered as literal text.
 * - Link elements are force-`target="_blank"` + `rel="noopener noreferrer"`
 *   so a click on a rendered link can't navigate the whole app away.
 * - Only relative URLs or `http(s):` / `mailto:` URLs are honored for the
 *   `href`. Anything else (e.g. `javascript:`, `data:`, `vbscript:`) is
 *   rendered as plain text.
 * - GFM is enabled via `remark-gfm` for tables, strikethrough, task lists,
 *   and autolinks — the Markdown flavor most models emit.
 *
 * Styling is done via Tailwind classes on the default elements so we get
 * the Spark dark theme without pulling in `@tailwindcss/typography`. The
 * classes match the rest of the web UI (spark-text / spark-muted / border).
 */
export function MarkdownView({ content, className = "", }) {
    return (_jsx("div", { className: `emberspark-md ${className}`, children: _jsx(ReactMarkdown, { remarkPlugins: [remarkGfm], components: MD_COMPONENTS, children: content }) }));
}
/** Only these URL schemes are rendered as live links. */
function isSafeHref(url) {
    if (!url)
        return false;
    const trimmed = url.trim();
    if (!trimmed)
        return false;
    // Relative paths and fragment links are fine.
    if (trimmed.startsWith("/") ||
        trimmed.startsWith("#") ||
        trimmed.startsWith("./") ||
        trimmed.startsWith("../")) {
        // Reject `//evil.com` protocol-relative URLs.
        return !trimmed.startsWith("//");
    }
    // Everything else must parse as a URL with a safe scheme.
    try {
        const parsed = new URL(trimmed);
        return ["http:", "https:", "mailto:"].includes(parsed.protocol);
    }
    catch {
        return false;
    }
}
const MD_COMPONENTS = {
    h1: ({ children }) => (_jsx("h1", { className: "text-xl font-bold text-spark-text mt-4 mb-2 first:mt-0", children: children })),
    h2: ({ children }) => (_jsx("h2", { className: "text-lg font-bold text-spark-text mt-4 mb-2 first:mt-0", children: children })),
    h3: ({ children }) => (_jsx("h3", { className: "text-base font-bold text-spark-text mt-3 mb-1 first:mt-0", children: children })),
    h4: ({ children }) => (_jsx("h4", { className: "text-sm font-bold text-spark-text mt-3 mb-1 first:mt-0", children: children })),
    p: ({ children }) => (_jsx("p", { className: "text-sm leading-relaxed my-2 first:mt-0 last:mb-0", children: children })),
    ul: ({ children }) => (_jsx("ul", { className: "list-disc list-outside ml-5 my-2 space-y-1 text-sm", children: children })),
    ol: ({ children }) => (_jsx("ol", { className: "list-decimal list-outside ml-5 my-2 space-y-1 text-sm", children: children })),
    li: ({ children }) => _jsx("li", { className: "leading-relaxed", children: children }),
    a: ({ href, children }) => {
        if (!isSafeHref(href)) {
            return _jsx("span", { className: "underline decoration-dotted", children: children });
        }
        return (_jsx("a", { href: href, target: "_blank", rel: "noopener noreferrer", className: "text-spark-accent underline hover:no-underline", children: children }));
    },
    blockquote: ({ children }) => (_jsx("blockquote", { className: "border-l-2 border-spark-border pl-3 my-2 text-spark-muted italic", children: children })),
    hr: () => _jsx("hr", { className: "border-spark-border my-4" }),
    code: ({ className, children, ...rest }) => {
        // react-markdown's default: `inline` prop was removed in v9 — we
        // detect inline code by the absence of a language class (block code
        // always has `language-*`).
        const isBlock = className?.startsWith("language-");
        if (isBlock) {
            return (_jsx("code", { className: `${className} block whitespace-pre`, ...rest, children: children }));
        }
        return (_jsx("code", { className: "bg-spark-border/40 text-spark-accent px-1 py-0.5 rounded text-[0.85em] font-mono", ...rest, children: children }));
    },
    pre: ({ children }) => (_jsx("pre", { className: "bg-spark-border/30 border border-spark-border rounded-md p-3 my-2 overflow-x-auto text-xs font-mono", children: children })),
    table: ({ children }) => (_jsx("div", { className: "overflow-x-auto my-2", children: _jsx("table", { className: "w-full text-xs border-collapse", children: children }) })),
    thead: ({ children }) => (_jsx("thead", { className: "bg-spark-border/30", children: children })),
    th: ({ children }) => (_jsx("th", { className: "border border-spark-border px-2 py-1 text-left font-semibold", children: children })),
    td: ({ children }) => (_jsx("td", { className: "border border-spark-border px-2 py-1 align-top", children: children })),
    strong: ({ children }) => (_jsx("strong", { className: "font-semibold text-spark-text", children: children })),
    em: ({ children }) => _jsx("em", { className: "italic", children: children }),
    del: ({ children }) => (_jsx("del", { className: "line-through text-spark-muted", children: children })),
    input: ({ checked, type }) => {
        // GFM task list checkbox. Always disabled — the rendered chat is
        // read-only, clicking shouldn't mutate state.
        if (type !== "checkbox")
            return null;
        return (_jsx("input", { type: "checkbox", checked: !!checked, disabled: true, readOnly: true, className: "mr-1 align-middle accent-spark-accent" }));
    },
};
