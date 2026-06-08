// Hand-written API types (Spec/09 §4 L2). Mirrors the Lambda response shapes.

export type ApprovalState =
  | "pending_review"
  | "approved"
  | "rejected"
  | "published";

export interface Job {
  job_id: string;
  status: string;
  union?: string;
  period?: string;
}

export interface AgentConfig {
  agent_name: string;
  enabled: boolean;
  version?: string;
  image_tag?: string;
}

export interface RateSheetSummary {
  union: string;
  period: string;
  approval_state: ApprovalState;
  gap_count?: number;
  confidence?: number;
  trade?: string;
  local?: number;
  id?: string;
}

export interface RateCell {
  cell_id: string;
  zone: string;
  package: string;
  column_name: string;
  value: number | null;
  confidence: number;
  provenance?: Record<string, unknown>;
}

export interface ReviewItem {
  cell_id: string;
  field: string;
  confidence: number;
}

export interface AuditEntry {
  ts: string;
  actor: string;
  action: string;
}
