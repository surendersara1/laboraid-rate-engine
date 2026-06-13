-- LaborAid Aurora schema (Spec/09 §3.3).
-- Applied at stack create/update by the schema-init custom resource via the
-- RDS Data API. Statements are split on the ';' terminator by the handler, so
-- keep each statement self-contained and avoid ';' inside literals/bodies.

CREATE TABLE IF NOT EXISTS unions (
  id UUID PRIMARY KEY,
  local INT NOT NULL,
  trade TEXT NOT NULL,
  parent_intl TEXT,
  profile_yaml JSONB,
  profile_version TEXT
);

CREATE TABLE IF NOT EXISTS rate_periods (
  id UUID PRIMARY KEY,
  union_id UUID REFERENCES unions(id),
  start_date DATE,
  end_date DATE,
  status TEXT,
  approval_state TEXT NOT NULL DEFAULT 'pending_review',
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  rejected_by TEXT,
  rejected_at TIMESTAMPTZ,
  rejection_reason TEXT,
  rejection_tags TEXT[],
  published_by TEXT,
  published_at TIMESTAMPTZ,
  canonical_json JSONB,
  source_files JSONB,
  version INT NOT NULL DEFAULT 1,
  parent_version INT,
  rework_context JSONB,
  CONSTRAINT publish_requires_approval
    CHECK (approval_state IN ('pending_review','pending_approval','approved','rejected','published')),
  CONSTRAINT dual_control_required
    CHECK (approval_state <> 'approved' OR (reviewed_by IS NOT NULL AND approved_by IS NOT NULL AND reviewed_by <> approved_by))
);

CREATE INDEX IF NOT EXISTS idx_periods_inbox ON rate_periods (approval_state, start_date DESC);

CREATE INDEX IF NOT EXISTS idx_periods_versions ON rate_periods (union_id, start_date, version DESC);

CREATE TABLE IF NOT EXISTS rate_cells (
  id UUID PRIMARY KEY,
  period_id UUID REFERENCES rate_periods(id),
  zone TEXT,
  package TEXT,
  dimensions JSONB,
  column_name TEXT,
  value NUMERIC,
  value_type TEXT,
  provenance JSONB,
  confidence NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_cells_lookup ON rate_cells(period_id, zone, package, column_name);

CREATE INDEX IF NOT EXISTS idx_cells_prov_gin ON rate_cells USING GIN (provenance jsonb_path_ops);

CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ DEFAULT NOW(),
  tenant TEXT,
  actor TEXT,
  action TEXT,
  details JSONB
);

-- Human review corrections (comments + overrides) — the legal/financial record.
-- One row per reviewer action, FK'd to the cell + period, append-only + versioned,
-- carrying before/after + who/when/why. union_local + period are denormalized so
-- readers can key by {local, period} (matching the prior DDB access pattern) and so
-- a correction stays legible if a cell is recreated in a new version. Replaces the
-- DynamoDB overrides table as the source of truth (Phase 2, decision 1).
CREATE TABLE IF NOT EXISTS cell_corrections (
  id UUID PRIMARY KEY,
  period_id UUID REFERENCES rate_periods(id),
  version INT,
  cell_id UUID REFERENCES rate_cells(id),
  union_local TEXT,
  period TEXT,
  zone TEXT,
  package TEXT,
  column_name TEXT,
  kind TEXT NOT NULL,
  prior_value TEXT,
  new_value TEXT,
  reason TEXT,
  actor TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  status TEXT NOT NULL DEFAULT 'open',
  CONSTRAINT cell_corrections_kind CHECK (kind IN ('comment','override'))
);

CREATE INDEX IF NOT EXISTS idx_corrections_lookup ON cell_corrections (union_local, period, kind, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_corrections_cell ON cell_corrections (cell_id);

-- Phase-2 improvement loop: one row per "Improve" click (the agent's run), for audit.
-- Records what drove the change (from->to version, who, which model) and its lifecycle.
CREATE TABLE IF NOT EXISTS improvement_runs (
  id UUID PRIMARY KEY,
  period_id UUID REFERENCES rate_periods(id),
  union_local TEXT,
  period TEXT,
  from_version INT,
  to_version INT,
  triggered_by TEXT,
  model TEXT,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',
  summary TEXT,
  error TEXT,
  CONSTRAINT improvement_runs_status CHECK (status IN ('running','succeeded','failed'))
);

CREATE INDEX IF NOT EXISTS idx_improvement_runs_period ON improvement_runs (period_id, started_at DESC);

-- Per-cell record of what the agent did in a run (before/after + why + provenance) —
-- the explainable, replayable audit an auditor/trustee can inspect.
CREATE TABLE IF NOT EXISTS improvement_changes (
  id UUID PRIMARY KEY,
  run_id UUID REFERENCES improvement_runs(id),
  cell_id UUID,
  package TEXT,
  column_name TEXT,
  prior_value TEXT,
  new_value TEXT,
  source TEXT,
  provenance TEXT,
  confidence NUMERIC,
  CONSTRAINT improvement_changes_source CHECK (source IN ('override','resynth','recompute','profile-fix'))
);

CREATE INDEX IF NOT EXISTS idx_improvement_changes_run ON improvement_changes (run_id);
