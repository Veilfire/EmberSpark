import ReactMarkdown, { type Components } from "react-markdown";
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
export function MarkdownView({
  content,
  className = "",
}: {
  content: string;
  className?: string;
}) {
  return (
    <div className={`emberspark-md ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

/** Only these URL schemes are rendered as live links. */
function isSafeHref(url: string | undefined): boolean {
  if (!url) return false;
  const trimmed = url.trim();
  if (!trimmed) return false;
  // Relative paths and fragment links are fine.
  if (
    trimmed.startsWith("/") ||
    trimmed.startsWith("#") ||
    trimmed.startsWith("./") ||
    trimmed.startsWith("../")
  ) {
    // Reject `//evil.com` protocol-relative URLs.
    return !trimmed.startsWith("//");
  }
  // Everything else must parse as a URL with a safe scheme.
  try {
    const parsed = new URL(trimmed);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

const MD_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="text-xl font-bold text-spark-text mt-4 mb-2 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-bold text-spark-text mt-4 mb-2 first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-bold text-spark-text mt-3 mb-1 first:mt-0">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-sm font-bold text-spark-text mt-3 mb-1 first:mt-0">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="text-sm leading-relaxed my-2 first:mt-0 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc list-outside ml-5 my-2 space-y-1 text-sm">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-outside ml-5 my-2 space-y-1 text-sm">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => {
    if (!isSafeHref(href)) {
      return <span className="underline decoration-dotted">{children}</span>;
    }
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-spark-accent underline hover:no-underline"
      >
        {children}
      </a>
    );
  },
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-spark-border pl-3 my-2 text-spark-muted italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-spark-border my-4" />,
  code: ({ className, children, ...rest }) => {
    // react-markdown's default: `inline` prop was removed in v9 — we
    // detect inline code by the absence of a language class (block code
    // always has `language-*`).
    const isBlock = className?.startsWith("language-");
    if (isBlock) {
      return (
        <code
          className={`${className} block whitespace-pre`}
          {...rest}
        >
          {children}
        </code>
      );
    }
    return (
      <code
        className="bg-spark-border/40 text-spark-accent px-1 py-0.5 rounded text-[0.85em] font-mono"
        {...rest}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="bg-spark-border/30 border border-spark-border rounded-md p-3 my-2 overflow-x-auto text-xs font-mono">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-2">
      <table className="w-full text-xs border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-spark-border/30">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="border border-spark-border px-2 py-1 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-spark-border px-2 py-1 align-top">
      {children}
    </td>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-spark-text">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => (
    <del className="line-through text-spark-muted">{children}</del>
  ),
  input: ({ checked, type }) => {
    // GFM task list checkbox. Always disabled — the rendered chat is
    // read-only, clicking shouldn't mutate state.
    if (type !== "checkbox") return null;
    return (
      <input
        type="checkbox"
        checked={!!checked}
        disabled={true}
        readOnly
        className="mr-1 align-middle accent-spark-accent"
      />
    );
  },
};
