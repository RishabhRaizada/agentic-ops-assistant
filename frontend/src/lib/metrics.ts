export interface Metrics {
  generated_at: string;
  totals: Record<string, number>;
  by_status: Record<string, number>;
  by_category: Record<string, number>;
  by_region: Record<string, number>;
  by_risk: Record<string, number>;
  volume_14d: { date: string; count: number }[];
  cycle_times_h: Record<string, number | null>;
  sla: { decided: number; decided_on_time: number; on_time_rate: number | null; escalations: number };
  ai_quality: {
    agent_runs: number; success_rate: number | null; fallback_rate: number | null; degraded_runs: number; validation_retry_rate: number | null;
    extracted_fields: number; human_override_rate: number | null; ungrounded_rate: number | null; ai_recommendation_acceptance: number | null;
    injection_flags: number; tokens_in: number; tokens_out: number; est_cost_inr: number;
    per_agent: Record<string, { runs: number; avg_latency_ms: number | null; failures: number; fallbacks: number }>;
  };
}
