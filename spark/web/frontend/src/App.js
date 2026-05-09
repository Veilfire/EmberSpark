import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { CommandPalette } from "./components/CommandPalette";
import { IncidentBanner } from "./components/IncidentBanner";
import { NotificationToaster } from "./components/NotificationToaster";
import Overview from "./pages/Overview";
import Scheduler from "./pages/Scheduler";
import RunHistory from "./pages/RunHistory";
import Chat from "./pages/Chat";
import CostDashboard from "./pages/CostDashboard";
import SecurityCenter from "./pages/SecurityCenter";
import MemoryBrowser from "./pages/MemoryBrowser";
import SkillCatalog from "./pages/SkillCatalog";
import AuditLog from "./pages/AuditLog";
import Ops from "./pages/Ops";
import PluginConfigPage from "./pages/PluginConfig";
import Persona from "./pages/Persona";
import Stats from "./pages/Stats";
import Guardrails from "./pages/Guardrails";
import Filtering from "./pages/Filtering";
import Replay from "./pages/Replay";
import Downloads from "./pages/Downloads";
import Settings from "./pages/Settings";
import Templates from "./pages/Templates";
import ProviderSetup from "./pages/ProviderSetup";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import { ShortcutHelp } from "./components/ShortcutHelp";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import ForensicReview from "./pages/ForensicReview";
import Secrets from "./pages/Secrets";
import Login from "./pages/Login";
import { useAuth } from "./hooks/useAuth";
import { ConfirmHost } from "./lib/confirm";
export default function App() {
    const { authed, loading } = useAuth();
    const { helpOpen, setHelpOpen } = useKeyboardShortcuts();
    if (loading)
        return _jsx("div", { className: "p-6 text-spark-muted", children: "Loading\u2026" });
    if (!authed) {
        return (_jsxs(Routes, { children: [_jsx(Route, { path: "/login", element: _jsx(Login, {}) }), _jsx(Route, { path: "*", element: _jsx(Navigate, { to: "/login", replace: true }) })] }));
    }
    return (_jsxs(_Fragment, { children: [_jsx(CommandPalette, {}), _jsx(NotificationToaster, {}), _jsx(ConfirmHost, {}), _jsx(ShortcutHelp, { open: helpOpen, onClose: () => setHelpOpen(false) }), _jsxs(Shell, { children: [_jsx(IncidentBanner, {}), _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Overview, {}) }), _jsx(Route, { path: "/provider", element: _jsx(ProviderSetup, {}) }), _jsx(Route, { path: "/agents", element: _jsx(Agents, {}) }), _jsx(Route, { path: "/agents/:agent_name", element: _jsx(AgentDetail, {}) }), _jsx(Route, { path: "/scheduler", element: _jsx(Scheduler, {}) }), _jsx(Route, { path: "/runs", element: _jsx(RunHistory, {}) }), _jsx(Route, { path: "/runs/:run_id/replay", element: _jsx(Replay, {}) }), _jsx(Route, { path: "/chat", element: _jsx(Chat, {}) }), _jsx(Route, { path: "/cost", element: _jsx(CostDashboard, {}) }), _jsx(Route, { path: "/security", element: _jsx(SecurityCenter, {}) }), _jsx(Route, { path: "/memory", element: _jsx(MemoryBrowser, {}) }), _jsx(Route, { path: "/skills", element: _jsx(SkillCatalog, {}) }), _jsx(Route, { path: "/audit", element: _jsx(AuditLog, {}) }), _jsx(Route, { path: "/ops", element: _jsx(Ops, {}) }), _jsx(Route, { path: "/plugins", element: _jsx(PluginConfigPage, {}) }), _jsx(Route, { path: "/persona", element: _jsx(Persona, {}) }), _jsx(Route, { path: "/stats", element: _jsx(Stats, {}) }), _jsx(Route, { path: "/guardrails", element: _jsx(Guardrails, {}) }), _jsx(Route, { path: "/filtering", element: _jsx(Filtering, {}) }), _jsx(Route, { path: "/downloads", element: _jsx(Downloads, {}) }), _jsx(Route, { path: "/settings", element: _jsx(Settings, {}) }), _jsx(Route, { path: "/templates", element: _jsx(Templates, {}) }), _jsx(Route, { path: "/forensic", element: _jsx(ForensicReview, {}) }), _jsx(Route, { path: "/forensic/:run_id", element: _jsx(ForensicReview, {}) }), _jsx(Route, { path: "/secrets", element: _jsx(Secrets, {}) }), _jsx(Route, { path: "*", element: _jsx(Navigate, { to: "/", replace: true }) })] })] })] }));
}
