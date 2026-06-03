# Implementation Plan

**Document:** 06 of 7 in `docs/`
**Read after:** `01-05`. This doc translates the architecture into a phased build with milestones.

---

## Plan overview

**Goal:** v1 production engine processing all 5 POC unions, with full provenance, auto-publish for high-confidence cases, and human-review queue for low-confidence cases.

**Timeline:** 8 weeks with one full-stack engineer + part-time PDF/OCR specialist.

**Prerequisites:**
- AWS account (NBS or LaborAid)
- Bedrock model access enabled (Claude Sonnet 4.x, Claude Haiku, Titan Embed)
- Customer working session completed (resolves 5 blocker questions from `discovery/11_Findings_for_Client.md`)
- Customer-supplied input pack (already in `From Customer/`)
- Sample expected outputs for the 5 POC unions (the customer's existing rate-sheet xlsx files)

---

## Phase 1 — Foundation (Weeks 1-2)

**Goal:** Local development environment up; canonical schemas locked; Profiles authored for 1-2 unions; basic deterministic resolver working without AI.

### Week 1
- **Day 1-2:** Set up dev environment
  - Repo structure
  - Python virtualenv with all dependencies (pdftotext, pdfplumber, openpyxl, boto3, pydantic)
  - LocalStack for offline AWS development
  - Pre-commit hooks (ruff, black, mypy)
- **Day 3-5:** Implement canonical JSON schemas
  - Pydantic models for all 5 schemas (`ClassificationResult`, `ExtractedDocument`, `RuleManifest`, `Profile`, `CanonicalRateSheet`)
  - JSON schema export
  - Schema validation tests
  - Sample fixtures for 537 (the smallest case)

**Milestones:**
- [ ] Schemas defined and committed
- [ ] All 5 fixture files for 537 produced manually (`samples/profile_537.yaml`, `extracted_537_*.json`, etc.)
- [ ] Schema validation passing on fixtures

### Week 2
- **Day 1-3:** Build the formula DSL evaluator
  - Lark or pyparsing-based parser
  - AST node types
  - Evaluator with reference resolver
  - Evaluation trace for provenance
  - Comprehensive unit tests with all DSL examples from `04_Schemas_and_DSL.md`
- **Day 4-5:** Build the rule resolver (Stage 4)
  - Pure Python, no AI
  - Consumes Profile + ExtractedDocument + RuleManifest
  - Produces CanonicalRateSheet
  - Reads from fixtures, writes to fixtures
  - Tests: 537 fixture round-trips correctly

**Milestones:**
- [ ] DSL evaluator handling all 5 unions' formula examples
- [ ] Stage 4 resolver passes all 537 test cases
- [ ] Provenance generation for `derived` and `convention` source types

---

## Phase 2 — Extraction (Weeks 3-4)

**Goal:** Stage 2 extractors working for the 3 paths (text PDF, OCR, Bedrock Claude).

### Week 3
- **Day 1-2:** Path A — Text-PDF extractor
  - pdftotext + pdfplumber integration
  - Labeled-money table parser (regex + heuristics)
  - Confidence scoring per field
  - Tests against 537, 821, 281 (text-extractable PDFs)
- **Day 3-5:** Path B — OCR extractor
  - Tesseract integration in Fargate Docker container
  - Image preprocessing pipeline
  - Tested on 704's scanned Notices
  - Textract fallback when Tesseract confidence <0.85

**Milestones:**
- [ ] All 5 unions' Rate Notices produce ExtractedDocument JSON
- [ ] Confidence scoring per field
- [ ] Multi-page bundle handling (704's annual notices)

### Week 4
- **Day 1-3:** Path C — Bedrock Claude extractor
  - Boto3 Bedrock client setup with retry/timeout
  - Multi-modal PDF invocation
  - Prompt engineering (detailed in `03_Bedrock_AI_Layer.md`)
  - Structured output validation
  - Tests against the same 5 unions' Rate Notices
- **Day 4-5:** Stage 2 orchestration
  - Choose path based on classification + confidence
  - Fall-through: Path A → B → C
  - Result merging for multi-file bundles (281's 4-file)
  - Stage 1 (Classify) implemented in parallel

**Milestones:**
- [ ] All 3 paths working
- [ ] Auto path-selection logic
- [ ] Bundle handling for 281
- [ ] End-to-end Stage 1 → Stage 2 → Stage 4 working in local environment

---

## Phase 3 — CBA Mining (Week 5)

**Goal:** Stage 3 (CBA Rule Mining) using Bedrock Agent + Knowledge Base.

- **Day 1-2:** Bedrock Knowledge Base setup
  - Create KB with S3 Vectors backend
  - CBA chunking pipeline (article-aware splitter)
  - Ingest 537, 704, 821, 483, 281 CBAs
  - Metadata schema (union_local, article, section, page)
  - Verify retrieval quality: queries like "Foreman premium dollar amount" should return relevant passages
- **Day 3-5:** Bedrock Agent
  - Define agent with 4 tools (`search_cba_kb`, `extract_rule_from_passage`, `validate_rule`, `cross_reference_existing_profile`)
  - Implement tool Lambdas
  - Tune agent prompt for rule extraction
  - Run on 5 POC CBAs
  - Compare auto-extracted RuleManifest vs hand-authored fixture

**Milestones:**
- [ ] KB ingestion working with proper chunking
- [ ] Agent extracts complete RuleManifest for all 5 CBAs
- [ ] RuleManifest matches hand-authored fixture (with reasonable tolerance)
- [ ] Citation metadata correctly preserved through pipeline

---

## Phase 4 — Validation + Render (Week 6)

**Goal:** Stage 5 (Validate) and Stage 6 (Render) complete; end-to-end pipeline producing rate sheet xlsx.

- **Day 1-2:** Validation framework
  - Total package checksum
  - Apprentice % cross-check
  - Range checks
  - Year-over-year delta sanity
  - Article-20 awareness
  - Confidence rollup
  - Branch logic: auto-publish vs review queue
- **Day 3:** Bedrock Claude sanity review for flagged cells
- **Day 4-5:** Render layer
  - openpyxl-based xlsx renderer
  - Three layouts: single sheet, multi-sheet workbook, file-per-period
  - Cell-comment provenance embedding
  - Articles sheet/file generation
  - CSV mirror

**Milestones:**
- [ ] All validation checks implemented
- [ ] Rate sheet xlsx renders for all 5 POC unions
- [ ] Output matches customer's existing rate sheets to within tolerance (or known discrepancies are flagged)
- [ ] Provenance visible as cell comments

---

## Phase 5 — AWS Deployment (Week 7)

**Goal:** Engine running in AWS, not just locally. Step Functions orchestration, S3 storage, Bedrock production calls.

- **Day 1-2:** CDK stacks
  - Storage stack (S3 buckets, DynamoDB tables, Aurora cluster)
  - Compute stack (Lambdas, Fargate task definition, Step Function)
  - API stack (API Gateway, Cognito user pool)
  - Observability stack (CloudWatch, X-Ray)
- **Day 3:** Step Functions state machine
  - All 6 stages wired up
  - Error handlers and retries
  - Branching for auto-publish vs review
  - X-Ray tracing
- **Day 4-5:** End-to-end tests in dev AWS environment
  - Upload sample Rate Notice → state machine kicks off → rate sheet published
  - Cold-path testing: new union with full pipeline (Stage 3 included)
  - Hot-path testing: existing union with cached RuleManifest

**Milestones:**
- [ ] CDK deploys cleanly to dev account
- [ ] All Step Function executions complete successfully on test inputs
- [ ] Cost monitoring confirmed (CloudWatch dashboards)
- [ ] Bedrock invocations working with proper IAM scoping

---

## Phase 6 — Admin UI + Hardening (Week 8)

**Goal:** Ops admin can use the system end-to-end. Production-ready.

- **Day 1-2:** Admin SPA
  - File upload (presigned URL)
  - Job status dashboard
  - Side-by-side review (PDF + extracted values)
  - Provenance side panel
  - Manual override UI
  - Year-over-year diff view
- **Day 3:** "Ask the CBA" Q&A feature
  - Chat input → Bedrock KB search → Claude answer with citations
- **Day 4:** Profile editor
  - Form-based for common fields
  - Raw YAML view for power users
  - Validation feedback
- **Day 5:** End-to-end production test
  - Process all 5 POC unions through the full pipeline
  - Compare against customer's rate sheets
  - Document any discrepancies (with provenance trace)
  - Publish to staging environment

**Milestones:**
- [ ] All 5 POC unions producing audited rate sheets through the full system
- [ ] Admin can review, override, and publish via UI
- [ ] All 30 open questions from `discovery/11_Findings_for_Client.md` either resolved or explicitly deferred
- [ ] Production deployment ready for customer demo

---

## Post-v1 roadmap (months 3-6)

### v1.1 — Operational Polish (month 3)
- Cadence reminders (alert when expected Rate Notice is late)
- Bulk backfill workflow (multi-period historical processing)
- Enhanced year-over-year reporting
- Slack notifications

### v1.2 — Profile Drafting (month 4)
- AI-assisted Profile draft from CBA (extracts initial structure for human polish)
- Profile diff view between contract terms

### v1.3 — Multi-tenant (month 5)
- Tenant isolation in data layer
- Role-based access (admin, viewer, contractor)

### v1.4 — Self-service uploads (month 6)
- Union representatives upload Rate Notices directly
- Auto-classification + suggested Profile updates
- Reduced ops review burden

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Bedrock Sonnet quality below expectations on hard PDFs | Medium | High | Tesseract + Textract first; Claude as fallback. Multiple model options (Opus if needed). |
| OCR can't read 281's older scanned wage sheets reliably | Medium | Medium | Manual entry workflow for first onboarding; subsequent periods auto. |
| Customer working session reveals new architecture requirement | Medium | Medium | Profile schema is extensible; most new requirements are config additions. |
| Bedrock KB cost surprises us | Low | Low | Cost alarms + monitoring. S3 Vectors keeps cost low. |
| Admin UI takes longer than 1 week | High | Low | Cut Profile editor to YAML-only for v1; defer to v1.1. |
| 537's "SMART" issue or 821's column swap reveals more deeply broken data | Low | Medium | Discrepancy report makes these visible; engine sides with PDF, customer decides. |
| Multi-file bundle joining (281) has edge cases | Medium | Medium | Conservative bundle detection (exact filename pattern + same-folder); fall back to manual association. |

---

## Definition of Done (v1)

A union is "done" when:
1. ✓ Profile YAML committed and validated
2. ✓ Engine processes all customer-supplied Rate Notices for that union
3. ✓ Output xlsx matches customer's hand-built xlsx within rounding tolerance, OR all discrepancies are flagged with provenance trace
4. ✓ Articles sheet/file populated with CBA citations
5. ✓ Per-cell provenance for every published cell
6. ✓ At least one period has been processed end-to-end through the AWS pipeline (not just locally)
7. ✓ Confidence scores within target (≥0.95 average for text-PDF Notices)

When all 5 POC unions reach this bar → **v1 production**.

---

## Team

**Required for 8-week build:**
- 1 × Senior full-stack engineer (Python, AWS CDK, React) — full-time
- 1 × PDF/OCR specialist — half-time (heavy in weeks 3-4, lighter after)
- 1 × Product/QA — quarter-time (sprint reviews, fixture validation, customer working session)

**Optional accelerators:**
- 1 × ML/Prompt engineer — focus on Bedrock prompt tuning if quality lags

---

## Costs (engineering)

- 8 weeks × 1.75 FTE engineering = 14 FTE-weeks
- At blended rate, total engineering cost is bounded
- AWS infrastructure during build: ~$200-500/month (lower than production due to small data volumes)

---

## What we deliver to LaborAid at end of v1

1. **Running engine** in production AWS account (NBS-managed initially, can transfer)
2. **All 5 POC union Profiles** committed and version-controlled
3. **Admin UI** with upload, review, override, publish, audit log, "Ask the CBA"
4. **Canonical JSON API** for LaborAid product to consume rate data
5. **Documentation:**
   - Architecture diagrams (this Design folder)
   - Operations runbook (how to onboard a new union, how to handle errors)
   - API specification
   - Profile authoring guide
6. **Fixture library** of expected outputs for regression testing
7. **Open issues list** with all flagged discrepancies and customer decisions made

---

## Beyond v1 — engagement options

NBS and LaborAid will choose at end of v1:
- **Build → handover:** NBS hands off code; LaborAid maintains
- **Build → managed service:** NBS continues to operate the engine; LaborAid pays per rate sheet published
- **Hybrid:** NBS handles new union onboardings + engine improvements; LaborAid runs day-to-day

This decision is informed by LaborAid's hiring plans (will they hire an ML/PDF engineer?) and the engagement's value to NBS (continued revenue vs project completion).

---

## Final word

The Discovery phase (docs 01-11) gave us deep understanding of the problem. The Design phase (this folder) translated it into an engine architecture. The Implementation Plan (this doc) translates it into a 8-week build with concrete milestones.

**With customer green-light on the 5 blocker questions, we can start Phase 1 in week 1.**
