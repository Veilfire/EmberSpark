import { useState } from "react";
import { Check, Copy } from "lucide-react";

interface CodeBlockProps {
  children: string;
  language?: string;
  className?: string;
  showLineNumbers?: boolean;
}

/** Lightweight code block with copy button. No syntax highlighter dependency. */
export function CodeBlock({
  children,
  language,
  className = "",
  showLineNumbers,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(children);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const lines = children.split("\n");

  return (
    <div
      className={`relative group rounded-md border border-spark-border bg-spark-bg my-2 ${className}`}
    >
      {language && (
        <div className="absolute top-2 left-3 text-[10px] uppercase tracking-wide text-spark-muted font-mono">
          {language}
        </div>
      )}
      <button
        className="absolute top-2 right-2 text-spark-muted hover:text-spark-text p-1.5 rounded-md hover:bg-spark-border/50 transition opacity-0 group-hover:opacity-100 focus:opacity-100"
        onClick={copy}
        title="Copy"
      >
        {copied ? (
          <Check className="w-3.5 h-3.5 text-spark-good" />
        ) : (
          <Copy className="w-3.5 h-3.5" />
        )}
      </button>
      <pre
        className={`overflow-x-auto text-xs font-mono text-spark-text ${
          language ? "pt-7" : "pt-3"
        } px-3 pb-3 whitespace-pre`}
      >
        {showLineNumbers ? (
          <table className="border-collapse">
            <tbody>
              {lines.map((line, i) => (
                <tr key={i}>
                  <td className="pr-3 text-right select-none text-spark-muted align-top">
                    {i + 1}
                  </td>
                  <td>{line || " "}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <code>{children}</code>
        )}
      </pre>
    </div>
  );
}
