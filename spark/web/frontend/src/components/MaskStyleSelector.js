import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useMemo } from "react";
/**
 * Dropdown for choosing how a redacted hit is rendered. Shows a live
 * preview of the chosen style applied to a sample matching the
 * category — operators see ``****-1234`` next to LAST_4 for a card,
 * ``J. D.`` next to INITIAL for a name. The empty value (``Default``)
 * means "fall back to the per-category default", and the preview
 * shows that style.
 */
export function MaskStyleSelector({ options, value, dataClass, onChange, defaultStyle, }) {
    const effective = value ?? defaultStyle;
    const previewSample = useMemo(() => {
        const opt = options.find((o) => o.value === effective);
        return opt?.samples[dataClass] ?? "";
    }, [options, effective, dataClass]);
    return (_jsxs("div", { className: "space-y-1", children: [_jsxs("select", { className: "input w-full text-sm", value: value ?? "", onChange: (e) => {
                    const v = e.target.value;
                    onChange((v === "" ? null : v));
                }, children: [_jsxs("option", { value: "", children: ["Default (", labelFor(defaultStyle), ")"] }), options.map((o) => (_jsx("option", { value: o.value, children: o.label }, o.value)))] }), previewSample !== "" ? (_jsx("code", { className: "block text-xs font-mono text-spark-muted bg-spark-surface px-2 py-1 rounded border border-spark-border truncate", children: previewSample })) : (_jsx("code", { className: "block text-xs font-mono text-spark-muted bg-spark-surface px-2 py-1 rounded border border-spark-border italic", children: "(stripped \u2014 empty)" }))] }));
}
function labelFor(v) {
    switch (v) {
        case "placeholder_class":
            return "[REDACTED:class]";
        case "placeholder_plain":
            return "[REDACTED]";
        case "last_4":
            return "Reveal last 4";
        case "first_4":
            return "Reveal first 4";
        case "initial":
            return "Initials only";
        case "hash_short":
            return "Hash";
        case "strip":
            return "Strip";
    }
}
