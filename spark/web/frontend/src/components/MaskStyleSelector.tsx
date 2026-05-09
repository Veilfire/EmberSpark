import { useMemo } from "react";

export type MaskStyleValue =
  | "placeholder_class"
  | "placeholder_plain"
  | "last_4"
  | "first_4"
  | "initial"
  | "hash_short"
  | "strip";

export interface MaskStyleOption {
  value: MaskStyleValue;
  label: string;
  /** Per-data-class preview string keyed by ``DataClass.value``. */
  samples: Record<string, string>;
}

interface Props {
  /** Available styles, with backend-rendered preview samples. */
  options: MaskStyleOption[];
  /** Current value; ``null`` means "use the per-class default". */
  value: MaskStyleValue | null;
  /** Data class this selector belongs to — drives which sample to show. */
  dataClass: string;
  onChange: (value: MaskStyleValue | null) => void;
  defaultStyle: MaskStyleValue;
}

/**
 * Dropdown for choosing how a redacted hit is rendered. Shows a live
 * preview of the chosen style applied to a sample matching the
 * category — operators see ``****-1234`` next to LAST_4 for a card,
 * ``J. D.`` next to INITIAL for a name. The empty value (``Default``)
 * means "fall back to the per-category default", and the preview
 * shows that style.
 */
export function MaskStyleSelector({
  options,
  value,
  dataClass,
  onChange,
  defaultStyle,
}: Props) {
  const effective: MaskStyleValue = value ?? defaultStyle;

  const previewSample = useMemo(() => {
    const opt = options.find((o) => o.value === effective);
    return opt?.samples[dataClass] ?? "";
  }, [options, effective, dataClass]);

  return (
    <div className="space-y-1">
      <select
        className="input w-full text-sm"
        value={value ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          onChange((v === "" ? null : (v as MaskStyleValue)));
        }}
      >
        <option value="">Default ({labelFor(defaultStyle)})</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {previewSample !== "" ? (
        <code className="block text-xs font-mono text-spark-muted bg-spark-surface px-2 py-1 rounded border border-spark-border truncate">
          {previewSample}
        </code>
      ) : (
        <code className="block text-xs font-mono text-spark-muted bg-spark-surface px-2 py-1 rounded border border-spark-border italic">
          (stripped — empty)
        </code>
      )}
    </div>
  );
}

function labelFor(v: MaskStyleValue): string {
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
