// Hand-written API types (Spec/09 §4 L2). Mirrors the Lambda response shapes.

export type ApprovalState =
  | "pending_review"
  | "approved"
  | "rejected"
  | "published";

export interface Job {
  job_id: string;
  execution_arn?: string;
  status: string;
  union?: string;
  period?: string;
  started_at?: string;
  stopped_at?: string;
  duration_ms?: number | null;
  source_s3_key?: string;
}

export interface JobTimelineStep {
  name: string;
  entered_at?: string;
  exited_at?: string;
  duration_ms?: number | null;
  status: "ok" | "failed" | "running";
  error?: string;
  cause?: string;
  input?: string;
  output?: string;
  resource?: string;
  log_group?: string;
}

export interface JobArtifact {
  name: string;
  kind: "input" | "output";
  bucket: string;
  key: string;
  size?: number | null;
  url?: string | null;
}

export interface JobDetail {
  job_id: string;
  execution_arn: string;
  status: string;
  started_at?: string;
  stopped_at?: string;
  duration_ms?: number | null;
  union?: string;
  period?: string;
  source_s3_key?: string;
  output_csv_key?: string;
  timeline: JobTimelineStep[];
  artifacts: JobArtifact[];
  agent_log_group?: string;
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
