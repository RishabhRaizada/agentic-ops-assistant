export type Role = "analyst" | "ops_manager" | "regional_head" | "director" | "auditor" | "admin";

export interface User {
  id: number; email: string; name: string; title: string; role: Role; region: string;
  approval_limit_inr: number; unread_notifications?: number;
}

export interface CaseSummary {
  id: number; ref: string; title: string; summary: string; category: string; status: string;
  priority: string | null; risk_level: string | null; region: string | null; store_code: string | null; sku_code: string | null;
  overall_confidence: number | null; flags: Record<string, boolean>; processing: boolean;
  decision_deadline_at: string | null; created_at: string; updated_at: string; channel: string | null; from: string | null;
  message_count: number; open_issue_count: number; blocking_issue_count: number; duplicate_of_id: number | null;
  parent_case_id: number | null; assignee: string | null;
}

export interface Fact {
  ref: string; key: string; label: string; source: string; ai_value: unknown; human_value: unknown; value: unknown;
  raw_text: string | null; status: string; confidence: number; evidence_quote: string | null; grounded: boolean | null;
  version: number; edited_by: string | null; edit_reason: string | null;
}

export interface Issue {
  id: number; type: string; severity: string; title: string; status: string; blocking: boolean; resolution: string | null;
  created_at: string; resolved_by: string | null;
  detail: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface Option {
  key: string; title: string; steps: string[]; est_cost_inr: number; cost_breakdown: string; covers_qty: number; eta_days: number;
  meets_deadline: boolean; feasibility: string; risks: string[]; rationale: string; fact_refs: string[]; tool_refs: string[];
  source: string; tool_evidence?: Record<string, { tool: string; args: unknown; data?: unknown; error?: string }>; edited_by_human?: boolean;
}

export interface Recommendation {
  id: number; option_key: string; option: Option; is_recommended: boolean; source: string; status: string; ledger_version: number;
  review: { verdict: string | null; issues: { option_key: string; problem: string; severity: string }[] };
  policy: { required: boolean; reasons: string[]; role: string } | null; created_at: string;
}

export interface Approval {
  id: number; case_id: number; recommendation_id: number; amount_inr: number; required_role: string; region: string; status: string;
  escalation_level: number; reasons: string[]; due_at: string; reminded_at: string | null; created_at: string; decided_at: string | null;
  decision_reason: string | null; budget_cap_inr: number | null; assigned_to: User | null; requested_by: User | null; decided_by: User | null;
  overdue: boolean; case?: CaseSummary; option?: Option; review?: Recommendation["review"]; is_recommended?: boolean;
}

export interface Comm {
  id: number; case_id: number | null; case_ref?: string | null; audience: string; channel: string; to: string[]; subject: string; body: string;
  fact_refs: string[]; generated_by: string; status: string; attempts: number; next_attempt_at: string | null; last_error: string | null;
  sent_at: string | null; created_at: string;
}

export interface AuditEvent {
  id: number; ts: string; case_id: number | null; case_ref?: string | null; actor_type: "ai" | "human" | "system"; actor_id: string;
  action: string; entity: string; entity_id: string | null; summary: string; before: unknown; after: unknown;
  meta: Record<string, any>; pinned: boolean; hash: string; prev_hash: string; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface AgentRun {
  id: number; agent: string; step: string; prompt_version: string; model: string; fallback_used: boolean; degraded: boolean;
  ledger_version: number; input_hash: string; input: unknown; output: any; status: string; error: string | null; // eslint-disable-line @typescript-eslint/no-explicit-any
  validation_retries: number; tool_calls: { tool: string; args: unknown; result: unknown }[]; confidence: number | null;
  latency_ms: number; tokens_in: number; tokens_out: number; started_at: string;
}

export interface Message {
  id: number; channel: string; from_addr: string; from_name: string; subject: string; body: string; received_at: string;
  relation: string; guard_label: string | null; guard_signals: { signal: string; severity: string; span: string; source: string }[];
}

export interface CaseDetail extends CaseSummary {
  intent_clarity: string;
  confidence_breakdown: { overall?: number; band?: string; components?: Record<string, number>; notes?: string[] };
  analysis: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  ledger_version: number; resolution_reason: string | null; resolution_note: string;
  timestamps: Record<string, string | null>; sla_paused_at: string | null;
  messages: Message[]; facts: Fact[]; issues: Issue[]; recommendations: Recommendation[]; approvals: Approval[];
  actions: { id: number; title: string; owner: string; status: string; due_at: string | null; completed_at: string | null }[];
  communications: Comm[]; agent_runs: AgentRun[]; timeline: AuditEvent[]; children: CaseSummary[]; merged: CaseSummary[];
  duplicate_of_ref: string | null; parent_ref: string | null; can_write: boolean;
}

export interface Scenario { key: string; tag: string; edge_cases: string[]; after?: string; from_name: string; from_addr: string; subject: string; body: string }
