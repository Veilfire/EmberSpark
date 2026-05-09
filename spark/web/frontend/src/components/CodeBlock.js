import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { Check, Copy } from "lucide-react";
/** Lightweight code block with copy button. No syntax highlighter dependency. */
export function CodeBlock({ children, language, className = "", showLineNumbers, }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        await navigator.clipboard.writeText(children);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };
    const lines = children.split("\n");
    return (_jsxs("div", { className: `relative group rounded-md border border-spark-border bg-spark-bg my-2 ${className}`, children: [language && (_jsx("div", { className: "absolute top-2 left-3 text-[10px] uppercase tracking-wide text-spark-muted font-mono", children: language })), _jsx("button", { className: "absolute top-2 right-2 text-spark-muted hover:text-spark-text p-1.5 rounded-md hover:bg-spark-border/50 transition opacity-0 group-hover:opacity-100 focus:opacity-100", onClick: copy, title: "Copy", children: copied ? (_jsx(Check, { className: "w-3.5 h-3.5 text-spark-good" })) : (_jsx(Copy, { className: "w-3.5 h-3.5" })) }), _jsx("pre", { className: `overflow-x-auto text-xs font-mono text-spark-text ${language ? "pt-7" : "pt-3"} px-3 pb-3 whitespace-pre`, children: showLineNumbers ? (_jsx("table", { className: "border-collapse", children: _jsx("tbody", { children: lines.map((line, i) => (_jsxs("tr", { children: [_jsx("td", { className: "pr-3 text-right select-none text-spark-muted align-top", children: i + 1 }), _jsx("td", { children: line || " " })] }, i))) }) })) : (_jsx("code", { children: children })) })] }));
}
