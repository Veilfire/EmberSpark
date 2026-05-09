import { ReactNode, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Blocks,
  Bot,
  Brain,
  Calendar,
  ChartBar,
  ChevronsLeft,
  ChevronsRight,
  Coins,
  Download,
  Eye,
  FileClock,
  Filter,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  Package,
  Search,
  Settings as SettingsIcon,
  Shield,
  Sparkles,
  User2,
  Wrench,
  Zap,
} from "lucide-react";
import { cn } from "../lib/utils";
import { useAuth } from "../hooks/useAuth";
import { NotificationBell } from "./NotificationBell";

type NavItem = { to: string; label: string; Icon: typeof LayoutDashboard };

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Run",
    items: [
      { to: "/", label: "Overview", Icon: LayoutDashboard },
      { to: "/agents", label: "Agents", Icon: Bot },
      { to: "/chat", label: "Chat", Icon: MessageSquare },
      { to: "/runs", label: "Runs", Icon: Activity },
      { to: "/scheduler", label: "Scheduler", Icon: Calendar },
      { to: "/templates", label: "Templates", Icon: Package },
    ],
  },
  {
    label: "Observe",
    items: [
      { to: "/cost", label: "Cost", Icon: Coins },
      { to: "/memory", label: "Memory", Icon: Brain },
      { to: "/skills", label: "Skills", Icon: Sparkles },
      { to: "/stats", label: "Stats", Icon: ChartBar },
      { to: "/downloads", label: "Downloads", Icon: Download },
    ],
  },
  {
    label: "Secure",
    items: [
      { to: "/security", label: "Security", Icon: Shield },
      { to: "/secrets", label: "Secrets", Icon: KeyRound },
      { to: "/guardrails", label: "Guardrails", Icon: AlertTriangle },
      { to: "/filtering", label: "Filtering", Icon: Filter },
      { to: "/forensic", label: "Forensic", Icon: Eye },
      { to: "/audit", label: "Audit", Icon: FileClock },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/provider", label: "Provider", Icon: Zap },
      { to: "/persona", label: "Persona", Icon: User2 },
      { to: "/plugins", label: "Plugins", Icon: Blocks },
      { to: "/ops", label: "Ops", Icon: Wrench },
      { to: "/settings", label: "Settings", Icon: SettingsIcon },
    ],
  },
];

export function Shell({ children }: { children: ReactNode }) {
  const { subject, role, logout } = useAuth();

  // Collapsed state persisted to localStorage.
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("spark.sidebar.collapsed") === "1";
  });

  useEffect(() => {
    window.localStorage.setItem(
      "spark.sidebar.collapsed",
      collapsed ? "1" : "0",
    );
  }, [collapsed]);

  // Trigger command palette from the nav's search affordance.
  const openCommandPalette = () => {
    const e = new KeyboardEvent("keydown", {
      key: "k",
      metaKey: true,
      bubbles: true,
    });
    window.dispatchEvent(e);
  };

  const asideWidth = collapsed ? "md:w-14" : "md:w-56";

  return (
    <div className="flex min-h-screen flex-col md:flex-row bg-spark-bg">
      <aside
        className={cn(
          "transition-all duration-200 bg-spark-panel border-r border-spark-border flex md:flex-col gap-1 overflow-x-auto md:overflow-visible",
          asideWidth,
        )}
      >
        {/* Branding */}
        <div className="flex items-center justify-between p-4 shrink-0 md:border-b md:border-spark-border">
          <div className="flex items-center gap-2 min-w-0">
            <img
              src="/spark-icon.png"
              alt=""
              className="w-6 h-6 shrink-0 rounded"
              aria-hidden="true"
            />
            {!collapsed && (
              <h1 className="font-bold text-lg tracking-tight">Spark</h1>
            )}
          </div>
          {!collapsed && <NotificationBell />}
        </div>

        {/* Command palette affordance */}
        <button
          className={cn(
            "mx-2 mb-2 flex items-center gap-2 rounded-md border border-spark-border bg-spark-bg text-xs text-spark-muted hover:border-spark-accent/50 hover:text-spark-text transition px-2 py-1.5 shrink-0",
            collapsed && "justify-center px-1",
          )}
          onClick={openCommandPalette}
          aria-label="Search"
        >
          <Search className="w-3.5 h-3.5 shrink-0" />
          {!collapsed && (
            <>
              <span className="flex-1 text-left">Search…</span>
              <span className="kbd">⌘K</span>
            </>
          )}
        </button>

        {/* Grouped nav */}
        <nav className="flex-1 flex md:flex-col gap-0.5 overflow-y-auto px-2">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="mb-3">
              {!collapsed && (
                <div className="text-[10px] uppercase tracking-wider text-spark-muted px-2 py-1.5">
                  {group.label}
                </div>
              )}
              {group.items.map(({ to, label, Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === "/"}
                  title={collapsed ? label : undefined}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-2 px-2 py-1.5 rounded-md text-sm shrink-0 transition-colors relative",
                      collapsed && "justify-center",
                      isActive
                        ? "bg-spark-accent/10 text-spark-accent"
                        : "text-spark-muted hover:bg-spark-border/50 hover:text-spark-text",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive && (
                        <span className="absolute left-0 top-1 bottom-1 w-0.5 bg-spark-accent rounded-full" />
                      )}
                      <Icon className="w-4 h-4 shrink-0" />
                      {!collapsed && <span>{label}</span>}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* Sticky footer: user + logout + collapse toggle */}
        <div className="hidden md:flex flex-col border-t border-spark-border shrink-0 mt-2">
          {!collapsed && subject && (
            <div className="px-3 py-2 flex items-center gap-2 min-w-0">
              <div className="w-7 h-7 rounded-full bg-spark-accent/20 flex items-center justify-center text-spark-accent font-bold text-[10px] shrink-0">
                {subject.slice(0, 2).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <div className="truncate text-xs">{subject}</div>
                <div className="text-spark-muted text-[10px] leading-tight">
                  {role}
                </div>
              </div>
            </div>
          )}
          {collapsed ? (
            <div className="flex flex-col gap-1 p-2">
              <button
                className="btn-icon w-full flex items-center justify-center"
                onClick={logout}
                title="Sign out"
                aria-label="Sign out"
              >
                <LogOut className="w-4 h-4" />
              </button>
              <button
                className="btn-icon w-full flex items-center justify-center"
                onClick={() => setCollapsed(false)}
                title="Expand sidebar"
                aria-label="Expand sidebar"
              >
                <ChevronsRight className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1 px-2 pb-2 pt-1">
              <button
                className="btn flex-1 flex items-center justify-center gap-1.5 text-xs py-1"
                onClick={logout}
                aria-label="Sign out"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span>Sign out</span>
              </button>
              <button
                className="btn-icon shrink-0"
                onClick={() => setCollapsed(true)}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
              >
                <ChevronsLeft className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      </aside>

      <main className="flex-1 p-4 md:p-6 overflow-auto animate-enter">
        {children}
      </main>
    </div>
  );
}
