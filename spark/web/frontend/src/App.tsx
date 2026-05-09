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

  if (loading) return <div className="p-6 text-spark-muted">Loading…</div>;
  if (!authed) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <>
      <CommandPalette />
      <NotificationToaster />
      <ConfirmHost />
      <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
      <Shell>
        <IncidentBanner />
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/provider" element={<ProviderSetup />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:agent_name" element={<AgentDetail />} />
          <Route path="/scheduler" element={<Scheduler />} />
          <Route path="/runs" element={<RunHistory />} />
          <Route path="/runs/:run_id/replay" element={<Replay />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/cost" element={<CostDashboard />} />
          <Route path="/security" element={<SecurityCenter />} />
          <Route path="/memory" element={<MemoryBrowser />} />
          <Route path="/skills" element={<SkillCatalog />} />
          <Route path="/audit" element={<AuditLog />} />
          <Route path="/ops" element={<Ops />} />
          <Route path="/plugins" element={<PluginConfigPage />} />
          <Route path="/persona" element={<Persona />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/guardrails" element={<Guardrails />} />
          <Route path="/filtering" element={<Filtering />} />
          <Route path="/downloads" element={<Downloads />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/forensic" element={<ForensicReview />} />
          <Route path="/forensic/:run_id" element={<ForensicReview />} />
          <Route path="/secrets" element={<Secrets />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </>
  );
}
