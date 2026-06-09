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
    CHECK (approval_state IN ('pending_review','approved','rejected','published'))
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
