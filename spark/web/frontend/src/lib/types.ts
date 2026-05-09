// Shared TypeScript types that mirror the FastAPI response models.

export interface AgentSummary {
  name: string;
  description: string;
  updated_at: string;
}

export interface TaskSummary {
  name: string;
  agent_name: string;
  mode: string;
  state: string;
  created_at: string;
  updated_at: string;
}

export interface TaskRunSummary {
  run_id: string;
  task_name: string;
  agent_name: string;
  state: string;
  started_at: string;
  finished_at: string | null;
  iterations: number;
  model_calls: number;
  tool_calls: number;
  summary: string | null;
  error: string | null;
}

export interface CostWindow {
  period: string;
  total_usd: number;
  by_provider: Record<string, number>;
  by_agent: Record<string, number>;
  by_model: Record<string, number>;
}

export interface Budget {
  budget_id: string;
  scope: "global" | "agent" | "provider";
  scope_key: string;
  period: "daily" | "weekly" | "monthly";
  limit_usd: number;
  soft_alert_usd: number;
  hard_stop: boolean;
  enabled: boolean;
}

export interface PendingSkill {
  review_id: string;
  agent_name: string;
  namespace: string;
  proposed_name: string;
  proposed_description: string;
  // "api" (discovery-flow), "behavior" (agent-proposed heuristic),
  // or "knowledge" (agent-proposed domain rule). Older review rows
  // that predate the agent-proposed flow default to "api".
  kind?: "api" | "behavior" | "knowledge";
  rationale?: string;
  examples?: string[];
  success_criteria?: string;
  service_name: string;
  base_url: string;
  auth_method: string;
  required_hosts: string[];
  required_secrets: string[];
  confidence: number;
  source_url: string;
  discovered_at: string | null;
  state: string;
}

export interface ApprovedSkill {
  skill_id: string;
  name: string;
  description: string;
  service_name: string;
  base_url: string;
  auth_method: string;
  required_hosts: string[];
  required_secrets: string[];
  confidence: number;
  uses: number;
  status: string;
  approved_by: string;
  approved_at: string;
}

export interface Playbook {
  playbook_id: string;
  name: string;
  description: string;
  uses: number;
  alpha: number;
  beta: number;
  success_rate: number;
  avg_duration_seconds: number;
  avg_tool_calls: number;
  last_success_at: string | null;
  tool_sequence: string[];
}

export interface AuditEntry {
  ts: string;
  actor: string;
  kind: string;
  target: string;
  diff: string;
  reason: string;
  severity: string;
}

export interface GlobalPosture {
  frozen: boolean;
  freeze_reason: string;
  compliance_mode: "standard" | "audit";
  allow_internal_ips: boolean;
  allow_raw_logging: boolean;
  default_privacy_mode: string;
  updated_at: string;
  updated_by: string | null;
}

export interface LongTermMemory {
  memory_id: string;
  agent_name: string;
  namespace: string;
  is_global?: boolean;
  memory_type: string;
  sensitivity: string;
  retention_class: string;
  confidence: number;
  content_summary: string;
  updated_at: string;
  // M1 additions (all optional — older rows may lack them)
  usage_count?: number;
  successful_citation_count?: number;
  is_anti_pattern?: boolean;
  contradicts_with?: string | null;
  superseded_by?: string | null;
  status?: string;
  circle_id?: string | null;
}

export interface InternalGrant {
  id: number;
  cidr: string;
  reason: string;
  granted_by: string;
  granted_at: string;
  expires_at: string;
}
