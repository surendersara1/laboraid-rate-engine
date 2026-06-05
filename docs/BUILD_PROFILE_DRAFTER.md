# Build Instructions — ProfileDrafterAgent (overnight run)

**Audience:** the overnight Claude CLI / code-generation runner.
**Working branch:** `feat/path-c-and-drafter` (already has Path C committed).
**Mode:** unattended. Do NOT ask questions; make reasonable calls and keep going.
**Authority:** Spec/09 §15.1 (deferred agents), `Learning_Lessons.md` Lessons 1–4, `docs/AUDIT_REPORT.md` (audit discipline).

This run completes the second half of the Path-C + ProfileDrafterAgent feature branch. Path C (generic Claude-only runtime extractor) is already committed. Your job is to add ProfileDrafterAgent — the build-time agent that **auto-generates a profile YAML + a Python extractor function for each new union**, so that next runs use Path A (deterministic) instead of Path C (LLM).

---

## 0. Pre-flight

### 0.1 Where you are

- Repo root: `E:\NBS_LaborAid\laboraid-rate-engine\` (Windows, PowerShell, but bash works via git-bash)
- You are on branch `feat/path-c-and-drafter`.
- Previously committed on this branch:
  - `agents/extractor/extract_generic.py` — Path C extractor (LLM-driven, no profile needed)
  - `agents/extractor/agent.py` — registered `extract_via_claude_only` as 7th @tool
  - `agents/extractor/system-prompt.md` — updated SOP with Path A/B/C decision logic
  - `agents/extractor/tests/test_extract_generic.py` — 13 passing tests
  - `process_customer_samples.py` (workspace root, outside repo) — runner supports both paths

### 0.2 Read these first (in order, ~30 min)

1. `BUILD_INSTRUCTIONS.md` — repo build conventions
2. `docs/09_Technical_Implementation_Spec.md` §5 (agent layer) + §15.1 (deferred agents incl. ProfileDrafterAgent)
3. `docs/07_Strands_AgentCore_Agentic_Design.md` — original 9-agent design with ProfileDrafterAgent role spec
4. `agents/extractor/agent.py` — ExtractorAgent — pattern to follow
5. `kernel/profiles/sprinkler_fitters_704.yaml` + `kernel/profiles/sprinkler_fitters_483.yaml` + `kernel/profiles/pipe_fitters_537.yaml` — these are the templates the drafter must learn to generate
6. `kernel/pipeline/extract.py` — the `extract_704`, `extract_483`, `extract_537` functions — these are the patterns the drafter must learn to generate Python like
7. `kernel/canonical/fields.yaml` — the canonical vocabulary every profile maps into

### 0.3 Hard rules (NEVER violate)

1. **DO NOT modify `kernel/`** — it's a `git subtree` from Bitbucket. Generate code OUTSIDE kernel/ (the drafter's generated files DO go into `kernel/profiles/` and `kernel/pipeline/extract.py` — but ONLY via the drafter at runtime, and ONLY after human review per the workflow below).
2. **DO NOT use static AWS credentials** — IAM roles + Cognito federation only.
3. **DO NOT fabricate extracted values** — same prime directive as ExtractorAgent. Generated extractors must encode the never-fabricate rule.
4. **Generated code MUST pass `mypy --strict`** before commit. The drafter generates real Python that joins the audit-verified codebase — no slop.
5. **Generated profile YAMLs MUST validate against the schema of existing profiles** (704/483/537). Drafter output that fails schema check is treated as a failure, not a partial success.
6. **Test before commit** — generated extractor must pass the kernel's evaluator with ≥ accuracy threshold (configurable per union; default 80% on documented cells).

### 0.4 Resumability

Commit each numbered item below as `[DRAFT-NN] <title>`. If anything fails, write a note to `docs/BUILD_LOG.md` and stop. Next run reads the log to resume.

---

## 1. What ProfileDrafterAgent does (one-line architecture)

> Given (a) a new union's CBA + Rate Notice PDFs and (b) the customer's existing rate-sheet CSV/xlsx for that union (groundtruth header + sample rows), the drafter produces:
> 1. A new `kernel/profiles/<union_key>.yaml` matching the schema of existing profiles
> 2. A new `extract_<local>(union_dir)` function added to `kernel/pipeline/extract.py` and registered in `EXTRACTORS`
> 3. An auto-iteration loop that runs the kernel evaluator until accuracy ≥ threshold, regenerating on failure

Output is reviewed by a human via PR before landing on `main`. The drafter operates on a branch, commits its drafts, and opens a PR.

---

## 2. Sequenced build queue

Each row is one commit, in order. Total target: ~5 days of overnight work.

### Group D — ProfileDrafter foundation

| # | Item | Output paths | Acceptance |
|---|---|---|---|
| D.1 | ProfileDrafter container scaffold | `agents/profile_drafter/Dockerfile`, `agents/profile_drafter/pyproject.toml`, `agents/profile_drafter/uv.lock`, `agents/profile_drafter/system-prompt.md` | Mirrors `agents/extractor/` shape. Uses `public.ecr.aws/lambda/python:3.12-arm64` base. |
| D.2 | Drafter agent.py with @tool stubs | `agents/profile_drafter/agent.py`, `agents/profile_drafter/steering.py` | 5 `@tool` functions declared (see §3); SteeringHandler blocks completion until generated code passes mypy + accuracy threshold; `app.run()` unconditional at module top per audit B7 lesson |
| D.3 | Schema-validator helper | `agents/profile_drafter/schema_check.py` + tests | Validates a candidate profile YAML against existing-profile schema. mypy --strict clean. |
| D.4 | Codegen-validator helper | `agents/profile_drafter/codegen_check.py` + tests | Loads candidate extractor Python via importlib, runs through `ast` + `mypy --strict`, captures any failures with line numbers. mypy --strict clean. |

### Group E — Drafting tools

Each tool is one `@tool` decorated function in `agents/profile_drafter/agent.py`. The Strands agent picks the order; the SteeringHandler enforces the contract.

| # | Tool | What it does | Bedrock model |
|---|---|---|---|
| E.1 | `analyze_groundtruth(ratesheet_path) -> dict` | Open the customer's existing CSV/xlsx, extract column header, classify each column type ($/%/raw), identify zone+classification dimensions, sample 2-3 rows. Pure function. | none |
| E.2 | `draft_profile_yaml(union, groundtruth_analysis, cba_summary) -> str` | Send analysis + CBA structural summary to Claude Sonnet 4.6, ask for a profile YAML matching the schema of `kernel/profiles/sprinkler_fitters_704.yaml`. Claude must use canonical field names from `kernel/canonical/fields.yaml` or propose additions in a separate block. | Sonnet 4.6 |
| E.3 | `draft_extractor_python(union, profile, sample_rate_notice_path) -> str` | Send profile + Rate Notice PDF + 1-2 examples (`extract_704` + `extract_483`) to Claude Sonnet 4.6, ask for a `def extract_<local>(union_dir)` function. Output is plain Python source. | Sonnet 4.6 |
| E.4 | `validate_generated(profile_path, extractor_path, union_dir, groundtruth_path) -> dict` | Run candidate through schema_check + codegen_check + kernel evaluator (subprocess on a temp branch). Return `{schema_pass, codegen_pass, accuracy, mismatches}`. | none |
| E.5 | `iterate_or_finalize(union, drafts_so_far, validation_result) -> action` | Loop control. Decide: regenerate with prompt-tuning (if accuracy < threshold but improving), accept (if accuracy ≥ threshold), or escalate to human (if 3 iterations without improvement). | Haiku 4.5 |

### Group F — Orchestrator

| # | Item | Output paths | Acceptance |
|---|---|---|---|
| F.1 | Drafter orchestrator (the entry function the AgentCore Runtime calls) | `agents/profile_drafter/orchestrate.py` | Takes `{union, cba_paths, ratesheet_path, accuracy_threshold}`, runs the tool chain end-to-end, returns `{profile_yaml, extractor_py, validation, iterations}` |
| F.2 | Commit-helper (creates a draft PR with the generated files) | `agents/profile_drafter/commit_helper.py` | Uses `gh` CLI to open a draft PR titled `[DRAFTED] <union_key> profile + extractor`. Branch name `auto/drafted-<union_key>-<timestamp>`. PR body links to validation output. |
| F.3 | CDK integration | `cdk/laboraid_cdk/stacks/processing_stack.py` (extend) | Add a `ProfileDrafterRuntime` AgentCore runtime (mirroring `ExtractorAgent`) + an IAM role with `iam:PassRole` scoped tight |

### Group G — Tests

| # | Item | Output paths | Acceptance |
|---|---|---|---|
| G.1 | Static source-contract tests | `agents/profile_drafter/tests/test_system_prompt.py`, `test_agent.py` | Same shape as `agents/extractor/tests/test_system_prompt.py` |
| G.2 | Schema-validator tests | `agents/profile_drafter/tests/test_schema_check.py` | Test valid + invalid profile YAMLs |
| G.3 | Codegen-validator tests | `agents/profile_drafter/tests/test_codegen_check.py` | Test valid + broken Python — broken should fail with clear error messages |
| G.4 | End-to-end smoke (mocked Bedrock) | `agents/profile_drafter/tests/test_orchestrate_smoke.py` | Mock `_call_bedrock` returns; assert orchestrator runs the full tool chain and produces the expected output shape |

### Group H — Real-world validation

| # | Item | What | Acceptance |
|---|---|---|---|
| H.1 | Run drafter on `sprinkler_fitters_120` | Pick smallest customer dataset first | Drafter produces profile + extractor; ≥ 70% on documented cells; PR opened |
| H.2 | Run drafter on `sprinkler_fitters_709` | Largest dataset (16 historical PDFs) | Same; drafter handles multi-period data |
| H.3 | Run drafter on remaining 12 unmapped sprinkler unions | The full sweep | Open one PR per union; report aggregate accuracy + iteration counts |
| H.4 | Update `process_customer_samples.py` to also use drafted extractors | When a drafter-generated extractor lands on main, the next run uses Path A | Path-A coverage grows from 2 to up to 16 |

---

## 3. Per-tool detail (the Strands agent's interface)

### E.1 `analyze_groundtruth`

Pure Python, no LLM. Open CSV (use stdlib `csv`) or xlsx (use `openpyxl` read-only). Read header. For each column:
- Detect type by sampling a few rows: `$` (numeric, no %), `%` (string ending in %), `raw` (anything else)
- Match column name against `kernel/canonical/fields.yaml` aliases — record `canonical_field` if found, else `unknown` (drafter will propose addition)
- Detect "key" columns by typical names (`Union Group`, `Trade`, `Union Local`, `Zone`, `Package`, `Start Date`, `End Date`)

Return shape:
```json
{
  "columns": [{"name": "Wage", "kind": "$", "canonical_field": "wage"}, ...],
  "key_columns": ["Zone", "Package"],
  "sample_rows": [{"Zone": "Building", "Package": "Journeyman", "Wage": 54.70}, ...],
  "unknown_fields": ["Some New Fund 120"]
}
```

### E.2 `draft_profile_yaml`

Bedrock call. System prompt: "You are drafting a per-union profile YAML for the LaborAid Rate Engine. Output ONLY valid YAML, no prose. Match the schema of the reference profiles." User prompt includes the analysis + a CBA structural summary + the reference profile content.

Hard constraint in prompt: "Use canonical field names from the provided fields.yaml. For unknowns, propose them as a separate `# UNKNOWN_FIELDS:` block at the end."

### E.3 `draft_extractor_python`

Bedrock call. System prompt: "You are writing a deterministic Python extractor function for a union. Follow the pattern of the example. Output ONLY valid Python source, no markdown fences. Never fabricate values — set to `None` (becomes a gap) if not in the PDF."

Provide:
- Example: full source of `extract_704` from `kernel/pipeline/extract.py`
- The profile YAML the drafter just produced (E.2)
- The Rate Notice PDF as a Bedrock document attachment
- `kernel/canonical/model.py` source (RateCell + ClassificationRow shape)

Output is committed to a temp file, then validated via E.4.

### E.4 `validate_generated`

Pure Python:
1. Write profile YAML to `kernel/profiles/<union_key>.yaml.candidate`
2. Write extractor source to `kernel/pipeline/extract_<local>.py.candidate`
3. Run `python -m py_compile` on the extractor
4. Run `mypy --strict` on the extractor
5. Run schema_check on the profile
6. If all pass: write to real paths, register in `EXTRACTORS`, run `kernel/pipeline/run.py --union <union_key>`, capture evaluator output
7. Return `{schema_pass, mypy_pass, syntax_pass, accuracy, mismatches, evaluator_output}`

### E.5 `iterate_or_finalize`

Bedrock Haiku 4.5 call. Cheap. Returns one of:
- `"regenerate_profile"` (with tuning notes)
- `"regenerate_extractor"` (with tuning notes)
- `"finalize"`
- `"escalate"` (3 iterations without improvement → human review)

---

## 4. Acceptance criteria (final gate)

After Group H completes:

- [ ] 16 sprinkler unions have either a deterministic extractor (Path A) or a drafted-and-validated one
- [ ] `cd cdk && uv run cdk synth` exits 0
- [ ] `uv run pytest agents/profile_drafter` all green
- [ ] `uv run mypy --strict agents/profile_drafter` exits 0
- [ ] At least 10 of 14 newly-drafted extractors pass ≥ 70% accuracy on documented cells
- [ ] Each drafted extractor's commit has the literal text `[DRAFTED-by-ProfileDrafterAgent]` in the message
- [ ] PR opened against `main` titled `feat: ProfileDrafterAgent + 14 auto-drafted union extractors`
- [ ] `docs/BUILD_LOG.md` shows iteration count + accuracy per union

---

## 5. Cost guardrails

- Each drafter run on one union: estimate ~50K input tokens + ~5K output × 3 iterations avg = ~165K tokens
- 14 unions × 3 iterations avg = ~2.3M tokens total
- At Sonnet 4.6 pricing (~$3 input / $15 output per 1M tokens) = **~$50 total Bedrock cost**
- Negligible against the $25K AWS funding

If costs exceed $100 (3× the estimate), stop and log; something is iterating excessively.

---

## 6. Hand-off

When done:
1. Final commit: `[DRAFT-FINAL] ProfileDrafterAgent complete + 14 unions drafted — see docs/BUILD_LOG.md`
2. Push branch
3. Update PR description (the one opened by the runner on day 1 for Path C) to also list the drafted unions + accuracy table
4. Print a one-paragraph summary of: drafter iterations per union, final accuracy, any unions that hit the escalate ceiling

Start now.
