# Build Log

Append-only log of the overnight build runner. One line per completed item;
detailed notes for anything that failed or deviated. A resume run reads this to
continue from the next unfinished item.

## Group A — CDK foundation

- [BUILD-A.1] CDK app bootstrap — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.2] Mandatory tags Aspect — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.3] Config (env-specific) — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.4] Naming helper — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.5] Tagged construct wrappers — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.6] Strands agent custom construct — DONE at 2026-06-02T20:55:57Z

### Notes

- **`cdk synth` exit code at end of Group A:** the CDK CLI prints
  "This app contains no stacks" and exits 1 because Group A defines only the
  app, aspect, config, naming, and construct wrappers — no stacks yet (those
  land in Group B onward). The Python app itself synthesizes a valid cloud
  assembly (`manifest.json` + `tree.json`, exit 0 via `uv run python app.py`).
  The `uv run cdk synth` gate goes green once Group B adds the first stack.
- Quality gates passing for `cdk/`: `ruff check` ✅, `black --check` ✅,
  `mypy --strict laboraid_cdk` ✅ (14 files), `pytest` ✅ (9 passed).
- `uv run cdk synth` literally requires the `cdk` CLI on PATH; it is the Node
  AWS CDK CLI (v2.1119.0 available via `npx`), not a Python package. Driven the
  synth via `npx cdk` / `uv run python app.py`.

## Group B — Storage & security stacks

- [BUILD-B.1] Security stack — DONE at 2026-06-02T21:08:38Z
- [BUILD-B.2] Storage stack — DONE at 2026-06-02T21:08:38Z

### Notes

- **`cdk synth` now exits 0** with `Laboraid-{env}-Security` + `Laboraid-{env}-Storage`.
  Gates green for `cdk/`: synth ✅, ruff ✅, black ✅, mypy --strict (16 files) ✅,
  pytest ✅ (11 passed).
- Stacks are **environment-agnostic** (no `env=` binding) so synth runs without
  AWS credentials — the dev/prod split is carried by `config.env`. Deploy binds
  to a concrete account/region via `CDK_DEFAULT_*`.
- **7 DynamoDB tables**, not 6: the BUILD §1 B.2 row says "6 DynamoDB tables",
  but Spec/09 §3.2 defines 7 (incl. `agent-config`, required by §4.4 SOW match
  for the Admin agent-toggle). Built all 7; flagging the BUILD-vs-Spec mismatch.
- **Aurora schema-init** uses the RDS **Data API** (`enable_data_api=True`) so the
  custom-resource Lambda needs no VPC attachment or `psycopg` bundling. DDL in
  `cdk/assets/schema_init/schema.sql` (idempotent `IF NOT EXISTS`), applied on
  Create/Update. Aurora sits in a minimal no-NAT VPC (isolated subnets).
- Audit bucket is the server-access-log target for the other 5 buckets.
