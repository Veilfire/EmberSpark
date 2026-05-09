import { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { ChevronRight } from "lucide-react";

interface PageHeaderProps {
  icon?: ReactNode;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  breadcrumbs?: { label: string; to?: string }[];
}

export function PageHeader({
  icon,
  title,
  subtitle,
  actions,
  breadcrumbs,
}: PageHeaderProps) {
  return (
    <header className="flex flex-col gap-1 mb-6">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav className="flex items-center gap-1 text-xs text-spark-muted mb-1">
          {breadcrumbs.map((crumb, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="w-3 h-3" />}
              {crumb.to ? (
                <Link to={crumb.to} className="hover:text-spark-text transition">
                  {crumb.label}
                </Link>
              ) : (
                <span>{crumb.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            {icon && <span className="text-spark-accent">{icon}</span>}
            {title}
          </h1>
          {subtitle && (
            <p className="text-spark-muted text-sm mt-1">{subtitle}</p>
          )}
        </div>
        {actions && <div className="flex gap-2 shrink-0">{actions}</div>}
      </div>
    </header>
  );
}

/** Auto-generates a breadcrumb trail from the current URL path. */
export function useAutoBreadcrumbs(
  override?: { label: string; to?: string }[],
): { label: string; to?: string }[] {
  const location = useLocation();
  if (override) return override;
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts.length === 0) return [];
  const crumbs: { label: string; to?: string }[] = [];
  let path = "";
  parts.forEach((p, i) => {
    path += "/" + p;
    crumbs.push({
      label: p.charAt(0).toUpperCase() + p.slice(1).replace(/-/g, " "),
      to: i === parts.length - 1 ? undefined : path,
    });
  });
  return crumbs;
}
