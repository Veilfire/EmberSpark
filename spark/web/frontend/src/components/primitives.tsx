import { ReactNode } from "react";
import { Link } from "react-router-dom";

// ---------------------------------------------------------------------------
// StatCard — label + value + optional sub + optional sparkline
// ---------------------------------------------------------------------------

export interface StatCardProps {
  label: string;
  value: string | number | ReactNode;
  sub?: string | ReactNode;
  trend?: number[]; // optional sparkline data
  tone?: "default" | "good" | "warn" | "danger";
  className?: string;
}

export function StatCard({
  label,
  value,
  sub,
  trend,
  tone = "default",
  className = "",
}: StatCardProps) {
  const toneClass =
    tone === "good"
      ? "text-spark-good"
      : tone === "warn"
        ? "text-spark-accent"
        : tone === "danger"
          ? "text-spark-danger"
          : "text-spark-text";
  return (
    <div
      className={`panel p-4 shadow-sm hover:shadow-md transition-shadow ${className}`}
    >
      <div className="text-xs uppercase tracking-wide text-spark-muted">
        {label}
      </div>
      <div className={`text-2xl font-bold mt-1 tabular-nums ${toneClass}`}>
        {value}
      </div>
      {sub && (
        <div className="text-xs text-spark-muted mt-1 truncate">{sub}</div>
      )}
      {trend && trend.length > 1 && (
        <div className="mt-2">
          <Sparkline data={trend} tone={tone} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sparkline — inline SVG
// ---------------------------------------------------------------------------

export function Sparkline({
  data,
  tone = "default",
  height = 24,
  width = 80,
}: {
  data: number[];
  tone?: StatCardProps["tone"];
  height?: number;
  width?: number;
}) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const color =
    tone === "good"
      ? "#3fb950"
      : tone === "warn"
        ? "#f59e0b"
        : tone === "danger"
          ? "#f85149"
          : "#f59e0b";
  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={width} height={height} className="overflow-visible block">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// EmptyState
// ---------------------------------------------------------------------------

export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: { label: string; to?: string; onClick?: () => void };
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: EmptyStateProps) {
  return (
    <div className="panel p-8 text-center flex flex-col items-center gap-2">
      {icon && (
        <div className="text-spark-muted w-10 h-10 flex items-center justify-center">
          {icon}
        </div>
      )}
      <div className="font-semibold text-spark-text">{title}</div>
      {description && (
        <p className="text-sm text-spark-muted max-w-md">{description}</p>
      )}
      {action && (
        <div className="mt-2">
          {action.to ? (
            <Link to={action.to} className="btn btn-primary">
              {action.label}
            </Link>
          ) : (
            <button className="btn btn-primary" onClick={action.onClick}>
              {action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton — shimmering placeholder
// ---------------------------------------------------------------------------

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse bg-spark-border/40 rounded-md ${className}`}
    />
  );
}

export function SkeletonRow({ cols = 4 }: { cols?: number }) {
  return (
    <tr className="border-t border-spark-border">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="py-2 pr-4">
          <Skeleton className="h-4 w-full max-w-[12rem]" />
        </td>
      ))}
    </tr>
  );
}

export function SkeletonCard() {
  return (
    <div className="panel p-4 space-y-3">
      <Skeleton className="h-5 w-1/3" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-4/5" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// HealthDot — colored status indicator
// ---------------------------------------------------------------------------

export function HealthDot({
  ok,
  size = "sm",
  pulse,
}: {
  ok: boolean | null;
  size?: "sm" | "md";
  pulse?: boolean;
}) {
  const dim = size === "md" ? "w-3 h-3" : "w-2 h-2";
  const color =
    ok === null
      ? "bg-spark-muted"
      : ok
        ? "bg-spark-good"
        : "bg-spark-danger";
  return (
    <span className="relative inline-flex items-center justify-center">
      {pulse && ok && (
        <span
          className={`absolute inline-flex rounded-full ${color} opacity-60 animate-ping ${dim}`}
        />
      )}
      <span className={`relative inline-flex rounded-full ${color} ${dim}`} />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Divider — soft gradient
// ---------------------------------------------------------------------------

export function Divider({ label }: { label?: string }) {
  if (!label) {
    return (
      <div className="h-px bg-gradient-to-r from-transparent via-spark-border to-transparent my-6" />
    );
  }
  return (
    <div className="flex items-center gap-3 my-6">
      <div className="flex-1 h-px bg-gradient-to-r from-transparent to-spark-border" />
      <span className="text-xs uppercase tracking-wide text-spark-muted">
        {label}
      </span>
      <div className="flex-1 h-px bg-gradient-to-l from-transparent to-spark-border" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section — a titled panel with optional collapse
// ---------------------------------------------------------------------------

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export function Section({
  title,
  icon,
  children,
  actions,
  collapsible,
  defaultOpen = true,
}: {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="panel p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3
          className={`font-semibold flex items-center gap-2 ${
            collapsible ? "cursor-pointer select-none" : ""
          }`}
          onClick={collapsible ? () => setOpen(!open) : undefined}
        >
          {collapsible &&
            (open ? (
              <ChevronDown className="w-4 h-4" />
            ) : (
              <ChevronRight className="w-4 h-4" />
            ))}
          {icon && <span className="text-spark-accent">{icon}</span>}
          {title}
        </h3>
        {actions && <div className="flex gap-2">{actions}</div>}
      </div>
      {(!collapsible || open) && <div>{children}</div>}
    </section>
  );
}
