# Strands Agents on AgentCore — Agentic AI Design

**Document:** 07 of the `docs/` folder (added after 06)
**Date:** 2026-05-05
**Status:** First agentic design, replaces the generic "Bedrock Agents" references in docs 01-06

> **Read after:** docs 01-06. This doc replaces the prior abstract "Bedrock Agent" mentions with a concrete Strands-on-AgentCore implementation. Where docs 01-06 said "Bedrock Agent", this doc names which Strands agent does the work, what tools it exposes, what skills it draws on, and what steering policies guard it.

---

## TL;DR

After studying [strandsagents.com](https://strandsagents.com/) and AWS AgentCore docs, we're going from **abstract "an AI orchestration layer"** to a **concrete 9-agent system built with the Strands Agents SDK and deployed on AWS Bedrock AgentCore Runtime**:

- **6 production agents** — one per substantive reasoning task in the pipeline (extract, mine, validate, cite, concierge, review-assist)
- **1 orchestrator agent** — top-level traffic cop that routes work via Agent-as-Tool
- **2 specialty agents** — `ProfileDrafterAgent` (onboarding) and `BackfillAgent` (historical)
- The deterministic stages (resolver, renderer) stay as **pure Lambda code** — agents are reserved for tasks where reasoning adds value

We use **all 7 of AgentCore's relevant services**:
- **AgentCore Runtime** — secure serverless container hosts every agent
- **AgentCore Memory** — short-term per-job + long-term per-union learning (override patterns, prior overrides)
- **AgentCore Gateway** — turns our Lambda backends into MCP tools agents can call
- **AgentCore Identity** — Cognito-federated agent identity
- **AgentCore Observability** — OpenTelemetry traces for every agent decision
- **AgentCore Evaluations** — agent quality measurement on Strands traces
- **AgentCore Policy** — Cedar-based deterministic guardrails on tool calls
- **AgentCore Registry** — catalog of our agents, tools, and skills

And the Strands SDK gives us `@tool`, `@hook`, `SteeringHandler`, `ConversationManager`, `MCPClient`, `structured_output_model` — all the primitives we need to build agents that are observable, steerable, and deterministic where it matters.

---

## Why Strands Agents

After reviewing the Strands docs we picked it because:

| Strands feature | Why it fits our needs |
|---|---|
| **Open-source, AWS-native, framework-agnostic** | We avoid vendor lock-in but get first-class AgentCore deploy without code changes |
| **Tools via `@tool` decorator** | Each pipeline capability (Textract call, KB search, DSL eval) becomes a one-line tool |
| **Hooks (`BeforeToolCallEvent` / `AfterToolCallEvent`)** | We can intercept any tool call to log, validate, or veto — critical for our provenance + audit needs |
| **Steering (`SteeringHandler`)** | Agents self-correct via natural-language `Guide(reason="…")` returns. Their benchmark: **100% accuracy with steering vs 82.5% prompt-only** |
| **`ConversationManager`** | `SummarizingConversationManager` for long CBA sessions, `SlidingWindowConversationManager` for chat |
| **Multi-agent: Agent-as-Tool, Swarm** | Our 9-agent topology is exactly an Agent-as-Tool pattern under the orchestrator |
| **MCP first-class** | Connect to AgentCore Gateway tools without custom integration |
| **Structured output via Pydantic (`structured_output_model`)** | Each agent returns JSON matching our canonical schemas |
| **Hooks support `event.interrupt(...)` for human-in-the-loop** | Built-in pause-for-approval — we use it for low-confidence cell publishing |
| **OpenTelemetry built-in** | Native AgentCore Observability integration |
| **Same code runs locally + AgentCore** | Devs test on laptop with `pip install strands-agents`, deploy unchanged |

---

## Why AgentCore (vs deploying agents on Lambda or ECS directly)

After reading the AWS docs, AgentCore's value over rolling our own is:

| AgentCore service | What we'd otherwise have to build |
|---|---|
| **Runtime** | Lambda + Fargate scaffolding, ARM64 containers, session isolation, fast cold starts, support for long-running tasks (>15 min Lambda limit) |
| **Memory** | DynamoDB schemas + retrieval logic for short-term + long-term + namespacing + summarization strategies + integration with conversation context |
| **Gateway** | API Gateway + Lambda authorizer + custom MCP-server-from-Lambda translation layer + SigV4/OAuth flows |
| **Identity** | Cognito + custom token exchange for agent-to-tool auth |
| **Observability** | CloudWatch + X-Ray scaffolding for agent decision traces |
| **Evaluations** | Hand-built test fixtures + metrics dashboards |
| **Policy** | Custom guardrail logic in every Lambda |
| **Registry** | Internal catalog UI + governance workflow |

AgentCore gives us all of this **purpose-built for agentic workloads**, with native Strands SDK integration. We focus on agent logic, not platform plumbing.

---

## 1. The 9-agent topology (with rationale)

### Decision: many specialized agents, not one mega-agent

Strands supports both patterns. We chose **multi-agent specialization** because:

1. **Different reasoning tasks need different system prompts** — telling one agent "you are a CBA rule extractor AND a Rate Notice extractor AND a validator" muddles all three. Specialists are sharper.
2. **Independent scaling** — extraction is bursty (10 Notices/day); CBA mining is rare (10/year). Different scaling profiles.
3. **Independent versioning** — improving one agent's prompts shouldn't risk regressing others.
4. **Independent evaluation** — AgentCore Evaluations runs against specific (agent, scenario) pairs.
5. **Per-agent cost accounting** — we know which capability is expensive.
6. **Failure isolation** — a hung CBA-mining agent doesn't block extraction.
7. **Reusability** — `CitationAgent` is used by 3 other agents AND the admin UI.

### The 9 agents at a glance

```
                              ┌──────────────────────────────┐
                              │   OrchestratorAgent          │
                              │   (top-level dispatcher)     │
                              └──────────────┬───────────────┘
                                             │
                ┌────────────────────────────┼─────────────────────────────────┐
                │                            │                                  │
        ┌───────▼────────┐         ┌────────▼─────────┐              ┌────────▼─────────┐
        │ Classifier     │         │ Extractor        │              │ ConciergeAgent   │
        │ Agent          │         │ Agent            │              │ ("Ask the CBA")  │
        │                │         │                  │              │                  │
        │ Identifies     │         │ Reads PDF/scan/  │              │ Admin UX: Q&A    │
        │ file type,     │         │ doc → Extracted  │              │ over union CBA   │
        │ union, period  │         │ Document JSON    │              │ with citations   │
        └────────────────┘         └────────┬─────────┘              └──────────────────┘
                                            │
                                            │ (when CBA mining needed)
                                            │
                                   ┌────────▼─────────┐
                                   │ CBAMiner         │
                                   │ Agent            │
                                   │                  │
                                   │ Reads 30-50 page │
                                   │ CBA → Rule       │
                                   │ Manifest         │
                                   └──────────────────┘

        ┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
        │ Validator        │         │ Citation         │         │ ReviewAssist     │
        │ Agent            │         │ Agent            │         │ Agent            │
        │                  │         │                  │         │                  │
        │ Sanity-checks    │         │ Given a value,   │         │ Helps human admin│
        │ rate sheet, runs │         │ finds CBA passage│         │ resolve flagged  │
        │ checksums + LLM  │         │ that justifies   │         │ cells; learns    │
        │ explain outliers │         │ it, with quotes  │         │ from past overrides│
        └──────────────────┘         └──────────────────┘         └──────────────────┘

        ┌──────────────────┐         ┌──────────────────┐
        │ ProfileDrafter   │         │ Backfill         │
        │ Agent (onboard)  │         │ Agent (historical)│
        │                  │         │                  │
        │ Drafts Profile   │         │ Processes years  │
        │ YAML from CBA +  │         │ of historical    │
        │ existing rate    │         │ Notices for new  │
        │ sheets           │         │ unions           │
        └──────────────────┘         └──────────────────┘
```

---

## 2. Agent specifications

For each agent, this section defines: **role**, **tools** (`@tool` functions), **hooks** (audit/validation interceptors), **steering** (`SteeringHandler` policies that course-correct), **memory**, **conversation manager**, and **deployment** notes.

---

### 2.1 OrchestratorAgent

**Role:** Top-level traffic cop. Receives a file (or batch), classifies via the Classifier sub-agent, decides the pipeline path, invokes specialist agents in sequence, accumulates results into the canonical JSON, hands to the deterministic resolver/renderer Lambdas.

**Pattern:** Strands **Agent-as-Tool** — the specialist agents are exposed to the orchestrator as Python tools.

**System prompt:**
```
You are the LaborAid rate-sheet pipeline orchestrator. Given an input file or
file bundle, you decide what stages are needed and invoke the specialist
agents in order. You never extract or analyze content yourself — your job
is routing and state management.

Rules:
- Always classify first. If classification confidence < 0.85, escalate to human.
- For Rate Notices: extract → resolve → validate → render → publish.
- For CBAs: extract → mine_cba → store RuleManifest. (No rate sheet output.)
- For bundles (multiple files same period): collect all, then process as one logical input.
- Use AgentCore Memory to track in-flight jobs and dedupe re-uploads.
```

**Tools (Strands `@tool` functions):**

```python
from strands import Agent, tool
from strands.multiagent import AgentTool  # Agent-as-Tool pattern

@tool
def classify_file(s3_key: str) -> dict:
    """Invoke ClassifierAgent to identify document type, union, period."""
    return classifier_agent.invoke(f"Classify file at {s3_key}")

@tool
def extract_rate_notice(file_id: str, profile_id: str) -> dict:
    """Invoke ExtractorAgent to convert Rate Notice PDF → ExtractedDocument JSON."""
    return extractor_agent.invoke(f"Extract Rate Notice {file_id} using profile {profile_id}")

@tool
def mine_cba(file_id: str, profile_hint_id: str = None) -> dict:
    """Invoke CBAMinerAgent to extract structured rules from a CBA."""
    return cba_miner_agent.invoke(f"Mine CBA {file_id}, profile hint: {profile_hint_id}")

@tool
def resolve_rate_sheet(extracted_id: str, manifest_id: str, profile_id: str) -> dict:
    """Pure deterministic Lambda — no agent. Apply Profile + manifest to extracted values."""
    return lambda_invoke("rule-resolver", {...})

@tool
def validate_rate_sheet(canonical_id: str) -> dict:
    """Invoke ValidatorAgent to run quality checks + LLM sanity review."""
    return validator_agent.invoke(f"Validate canonical rate sheet {canonical_id}")

@tool
def render_outputs(canonical_id: str, profile_id: str) -> dict:
    """Pure deterministic Lambda — produce xlsx + CSV + Articles."""
    return lambda_invoke("renderer", {...})

@tool
def store_in_aurora(canonical_id: str, version: int) -> dict:
    """Persist published rate sheet to Aurora + emit publish event."""
    return lambda_invoke("publisher", {...})

orchestrator = Agent(
    name="OrchestratorAgent",
    system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
    tools=[classify_file, extract_rate_notice, mine_cba,
           resolve_rate_sheet, validate_rate_sheet,
           render_outputs, store_in_aurora],
    hooks=[log_every_step, enforce_idempotency],
    plugins=[OrchestratorSteering()],
    session_manager=AgentCoreMemorySessionManager(
        config=AgentCoreMemoryConfig(
            memory_id=ORCHESTRATOR_MEMORY_ID,
            session_id=job_id,
            actor_id=tenant_id,
        ),
    ),
    trace_attributes={
        "service": "laboraid-orchestrator",
        "env": ENV,
    },
)
```

**Hooks (intercepting tool calls for audit/validation):**

```python
from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent

def log_every_step(event: AfterToolCallEvent):
    """Every tool call gets logged to CloudWatch with structured fields."""
    log.info("orchestrator.tool_call",
             tool=event.tool_use["name"],
             status=event.result["status"],
             duration_ms=event.duration_ms,
             job_id=event.context.session_id)

def enforce_idempotency(event: BeforeToolCallEvent):
    """If a tool call's input hash matches a prior successful run, return the cached result."""
    cache_key = hash_inputs(event.tool_use)
    if cached := dynamodb.get(cache_key):
        event.cancel_tool = f"Cached result: {cached}"
```

**Steering (`SteeringHandler`):**

```python
from strands.vended_plugins.steering import SteeringHandler, Guide, Proceed

class OrchestratorSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        # Don't run cba_mine if no CBA file exists for this union
        if tool_use["name"] == "mine_cba":
            if not aurora.cba_exists(tool_use["input"]["file_id"]):
                return Guide(reason="No CBA file for this union; classify the file first or "
                                     "ask the user to upload the CBA.")
        # Don't run extract_rate_notice if no Profile exists yet
        if tool_use["name"] == "extract_rate_notice":
            profile_id = tool_use["input"]["profile_id"]
            if not aurora.profile_exists(profile_id):
                return Guide(reason="Profile missing for this union. "
                                     "Invoke ProfileDrafterAgent first to bootstrap.")
        # Don't render before validate passes
        if tool_use["name"] == "render_outputs":
            canonical_id = tool_use["input"]["canonical_id"]
            if not aurora.validation_passed(canonical_id):
                return Guide(reason="Validation has not passed. Run validate_rate_sheet first.")
        return Proceed(reason="Routing OK.")
```

**Memory:** `AgentCoreMemorySessionManager` with `summaryMemoryStrategy` — keeps short-term per-job state (which steps ran, what results) plus long-term per-tenant patterns.

**Deployment:** AgentCore Runtime, Python 3.12 ARM64 container, IAM execution role with least-privilege access to other agents + Lambda functions.

---

### 2.2 ClassifierAgent

**Role:** Identifies file format, document type, union, period, scope. Cheap and fast (most files classify deterministically; agent is the fallback for ambiguous filenames).

**System prompt:**
```
You classify uploaded documents in the LaborAid rate-sheet pipeline.

Document types:
- cba (Collective Bargaining Agreement, multi-year, 25-50 pages)
- rate_notice (single-period dollar values, 1-15 pages)
- apprentice_wage_sheet (per-class apprentice rates, often per indenture date)
- reference (Articles, Fund Addresses, summaries)
- unknown (escalate to human)

Workflow:
1. First inspect the filename — if it matches a known pattern, you're done.
2. If not, peek at the first page (use the page_text tool).
3. Output structured JSON via structured_output_model.
```

**Tools:**
```python
@tool
def parse_filename(filename: str) -> dict:
    """Apply known filename regex patterns to extract type/union/period."""
    # Deterministic regex; returns None if no pattern matches

@tool
def get_first_page_text(s3_key: str, max_chars: int = 2000) -> str:
    """Extract first-page text using pdftotext (fast deterministic check)."""

@tool
def render_first_page_image(s3_key: str) -> bytes:
    """Render first page as image (for image-only PDFs)."""

@tool
def lookup_known_unions(local_number: int) -> dict:
    """Query Aurora for known union profile by local number."""
```

**Steering:**
```python
class ClassifierSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        # Don't render image if filename already gave us full identification
        if tool_use["name"] == "render_first_page_image":
            if agent.has_complete_classification():
                return Guide(reason="Filename already provided full identification. "
                                     "Skip image rendering to save cost.")
        return Proceed(reason="OK.")
```

**Output:** Pydantic `ClassificationResult` model (see doc 04 schema).

**Model:** Claude Haiku (cheap, classification-grade).

**Memory:** None (stateless — every classification is independent).

---

### 2.3 ExtractorAgent ⭐ (the workhorse)

**Role:** Converts a Rate Notice (PDF text or scan, single page or multi-page bundle) into the structured `ExtractedDocument` JSON. Falls back through 3 paths:

1. **Path A:** pdftotext + pdfplumber for clean text PDFs (~70% of cases)
2. **Path B:** Tesseract → Textract for image PDFs
3. **Path C:** Multi-modal Claude reads the raw PDF and returns structured JSON

**System prompt:**
```
You are an expert reader of US construction-trade union Rate Notices.
Given a Rate Notice PDF, extract values into the ExtractedDocument schema.

Always:
- Try the cheapest extraction path first (pdftotext).
- Validate confidence; if any required field has confidence < 0.85, escalate
  to the next path.
- Cite the page and the exact label-text-as-printed for every value.
- Run total_package_checksum (sum of components vs printed total). If it
  fails, retry with Path C (multi-modal Claude).
- Never invent values. If a field is missing, omit it.

Reference the Profile's `fringe_schema.notice_label_aliases` to map noisy
PDF labels to canonical column names.
```

**Tools:**
```python
@tool
def extract_text_pdf(s3_key: str) -> dict:
    """Path A: pdftotext + pdfplumber + table parser. Returns labeled values + confidence."""

@tool
def extract_with_tesseract(s3_key: str) -> dict:
    """Path B: Render PDF → preprocess image → Tesseract OCR → parse."""

@tool
def extract_with_textract(s3_key: str) -> dict:
    """Path B fallback: AWS Textract for table-heavy scans."""

@tool
def extract_with_claude_multimodal(s3_key: str, profile_hint: dict) -> dict:
    """Path C: send PDF directly to Claude Sonnet 4.x. Universal fallback."""

@tool
def merge_bundle_files(file_ids: list[str]) -> dict:
    """Combine extracted data from a multi-file bundle (e.g., 281's 4 files)."""

@tool
def validate_total_package_checksum(extracted: dict) -> dict:
    """Verify sum of fringes + wage matches the printed Total Package."""

@tool
def lookup_profile_label_aliases(profile_id: str) -> dict:
    """Get the union's fringe label alias map (e.g., 'H & W' → 'Health & Welfare')."""
```

**Hooks:**
```python
def confidence_gate(event: AfterToolCallEvent):
    """If extraction returns low confidence, force escalation."""
    if event.tool_use["name"].startswith("extract_with_"):
        result = event.result
        if result.get("confidence_overall", 1.0) < 0.85:
            # Annotate result so steering can catch it
            result["_escalate"] = True

def cite_every_value(event: AfterToolCallEvent):
    """Every extracted value must have a page citation. Strict."""
    if event.tool_use["name"].startswith("extract_with_"):
        result = event.result
        for fringe, val in result.get("fringes", {}).items():
            assert "_page" in val, f"Missing page citation for {fringe}"
```

**Steering:**
```python
class ExtractorSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        # Force fallback if previous path returned low confidence
        if agent.last_result and agent.last_result.get("_escalate"):
            if tool_use["name"] == "extract_text_pdf":
                return Guide(reason="Last extraction was low-confidence. Use OCR instead.")

        # Don't claim done if checksum hasn't been validated
        if tool_use["name"] == "return_extraction_complete":
            if not agent.checksum_validated:
                return Guide(reason="Run validate_total_package_checksum before declaring done.")

        # Multi-page bundles: ensure all members extracted
        if tool_use["name"] == "merge_bundle_files":
            file_ids = tool_use["input"]["file_ids"]
            unextracted = [fid for fid in file_ids if not extracted.get(fid)]
            if unextracted:
                return Guide(reason=f"Missing extractions for bundle members: {unextracted}. "
                                     f"Extract them first.")
        return Proceed(reason="OK.")
```

**Output:** Pydantic `ExtractedDocument` model.

**Model:** Strands `Agent` configured with **Claude Sonnet 4.x** (multi-modal-capable).

**Memory:** Short-term per-extraction (which paths tried, intermediate results). Long-term per-union: store **successful label-alias mappings** so subsequent Notices for the same union extract faster (e.g., once we learn 821 calls H&W "H & W RESA", we cache it).

**Conversation manager:** `SummarizingConversationManager` — for multi-page bundle extractions where the agent reasons over many pages.

**Deployment:** AgentCore Runtime, Python 3.12 ARM64. May need >15 min for very large bundles (rare); AgentCore Runtime supports long-running unlike Lambda.

---

### 2.4 CBAMinerAgent ⭐⭐ (most agentic)

**Role:** Extract a complete `RuleManifest` from a CBA. Most reasoning-heavy agent — uses retrieval-augmented generation (Bedrock KB) and structured rule extraction. Caches manifest per CBA file hash.

**System prompt:**
```
You are a Collective Bargaining Agreement (CBA) analyzer. Given a CBA for a
construction trade union, extract structured rules into a RuleManifest JSON.

For each rule type, search the CBA Knowledge Base for relevant passages,
extract the rule via structured tool, validate, and assemble the manifest.

Required rule types (must extract every one):
- wage_anchor_definition (Article 5 or 6)
- foreman_premium (with date-keyed schedule)
- general_foreman premium
- apprentice_schedule (count, percentages, anchor)
- apprentice_pension_exclusion (cutoff_unit, cutoff_value, excluded_funds)
- ot_rules (1.5x and 2.0x)
- shift_differential
- funds (Health & Welfare, Pension, SIS, etc.) — one per fund
- uniformity_rule (Article 20-style)
- rate_change_cadence
- rounding rule
- vacation rules (if applicable)

Cite the Article and section for every rule. If text is ambiguous (e.g., "as
per Area Practice"), flag in `ambiguities_flagged` with a suggested
resolution. Never invent rules — if you can't find one, mark unresolved.
```

**Tools (most are MCP tools served by AgentCore Gateway):**

```python
@tool
def search_cba_kb(query: str, max_results: int = 5) -> list[dict]:
    """Bedrock Knowledge Base retrieval (filtered to this union's CBA)."""

@tool
def extract_rule_from_passage(passage: str, rule_type: str, schema: dict) -> dict:
    """Call sub-agent (Claude Sonnet) with rule-type-specific schema."""

@tool
def validate_rule(rule: dict, rule_type: str) -> dict:
    """Validate against rule schema; return errors or success."""

@tool
def cross_reference_existing_profile(union_local: int) -> dict:
    """Look up prior version of Profile (if exists) to detect contract changes."""

@tool
def detect_ambiguity_phrases(passage: str) -> list[str]:
    """Find phrases like 'as per area practice', 'subject to negotiation', etc."""

@tool
def write_rule_manifest(manifest: dict) -> dict:
    """Persist completed manifest to S3 + Aurora."""
```

**Hooks:**
```python
def require_citation_for_every_rule(event: AfterToolCallEvent):
    """Every rule extracted must have a `_cba_citation` with article + section."""
    if event.tool_use["name"] == "extract_rule_from_passage":
        rule = event.result
        if not rule.get("_cba_citation"):
            event.result["_validation_error"] = "Missing CBA citation"
```

**Steering:**
```python
class CBAMinerSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        if tool_use["name"] == "write_rule_manifest":
            manifest = tool_use["input"]["manifest"]
            required = {"wage_anchor_definition", "foreman_premium", "apprentice_schedule",
                        "apprentice_pension_exclusion", "ot_rules", "funds"}
            missing = required - manifest.keys()
            if missing:
                return Guide(reason=f"RuleManifest is incomplete. Missing: {missing}. "
                                     f"Search KB for these and extract before completing.")

        if tool_use["name"] == "extract_rule_from_passage":
            passage = tool_use["input"]["passage"]
            ambiguous_phrases = ["as per area practice", "subject to negotiation",
                                  "to be determined", "by mutual agreement"]
            if any(p in passage.lower() for p in ambiguous_phrases):
                return Guide(reason="Passage contains ambiguous phrasing. "
                                     "Add to ambiguities_flagged with suggested_resolution. "
                                     "Don't pretend the rule is unambiguous.")

        return Proceed(reason="OK.")
```

**Output:** Pydantic `RuleManifest` model.

**Model:** Claude Sonnet 4.x (reasoning over long context).

**Memory:** **Long-term per-union** — semantic memory of every rule extracted (so subsequent CBAs for the same union benefit from prior learnings). AgentCore Memory `semanticMemoryStrategy` with namespace `/cba_rules/{union_local}/`.

**Conversation manager:** `SummarizingConversationManager` (CBA mining is a long agentic loop with 30-60 tool calls).

**Deployment:** AgentCore Runtime, Python 3.12 ARM64. Long-running task (5-10 min typical). Cached per CBA hash — runs once per CBA unless the file changes.

---

### 2.5 ValidatorAgent

**Role:** Quality gate before publish. Runs deterministic checksums + invokes Claude for sanity review of suspicious cells.

**System prompt:**
```
You are the rate-sheet quality gate. Given a CanonicalRateSheet, run all
required validations and decide: AUTO_PUBLISH or HUMAN_REVIEW.

Required checks:
1. Total package checksum (sum of components vs printed total, ±$0.05)
2. Apprentice percentage cross-check (computed wage matches published)
3. Range checks per column (e.g., wage $5-200, fringes $0-30)
4. Year-over-year delta sanity (>20% wage change without Article-20 explanation)
5. Per-cell confidence rollup (any cell <0.95 → review)

For YoY anomalies, invoke explain_anomaly to get LLM reasoning. If the
anomaly is explained by Article-20 uniformity, accept; else flag.
```

**Tools:**
```python
@tool
def run_checksum(canonical_id: str) -> dict:
    """Pure deterministic Lambda — verify package totals."""

@tool
def cross_check_apprentice_percentages(canonical_id: str) -> dict:
    """Pure deterministic — verify apprentice computed % matches anchor × percentage."""

@tool
def get_prior_period(union_local: int, current_period: dict) -> dict:
    """Fetch previous published rate sheet for YoY comparison."""

@tool
def compute_yoy_delta(current: dict, prior: dict) -> dict:
    """Cell-by-cell percentage change."""

@tool
def explain_anomaly(cell_path: str, current_value: float, prior_value: float,
                    notice_text: str, cba_text: str) -> dict:
    """Invoke Claude Sonnet with prompt: 'is this change explained by inputs?'"""

@tool
def queue_for_review(cell_id: str, reason: str) -> dict:
    """Write to DynamoDB review queue + send SES notification."""
```

**Hooks:**
```python
def block_publish_on_failed_checksum(event: BeforeToolCallEvent):
    if event.tool_use["name"] == "approve_for_publish":
        if not agent.checksum_passed:
            event.cancel_tool = "Checksum failed; cannot approve."
```

**Steering:**
```python
class ValidatorSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        if tool_use["name"] == "approve_for_publish":
            unrun = []
            if not agent.ran_checksum: unrun.append("checksum")
            if not agent.ran_apprentice_check: unrun.append("apprentice")
            if not agent.ran_yoy: unrun.append("yoy")
            if unrun:
                return Guide(reason=f"Required validations not yet run: {unrun}. "
                                     f"Don't approve without them.")
        return Proceed(reason="OK.")
```

**Output:** `ValidationResult` with verdict + reasoning.

**Model:** Claude Haiku for routine checks; Sonnet for `explain_anomaly`.

**Memory:** Long-term per-union — track historical YoY patterns (so a 35% spike that's normal for this union doesn't get flagged each time).

---

### 2.6 CitationAgent

**Role:** Given a value or formula, find the CBA passage that supports it. Used by:
- ResolverAgent (when materializing `derived` provenance)
- CBAMinerAgent (when extracting rules)
- Admin UI (cell click → "where is this from?")
- ConciergeAgent (Q&A grounding)

**System prompt:**
```
Given a topic or value, find the CBA passage(s) that justify it. Always cite
Article + section + page. Quote the exact text excerpt (max 200 chars). If
nothing in the CBA matches, return null with explanation.
```

**Tools:**
```python
@tool
def search_cba_kb(query: str, union_local: int, scope: str = None,
                  max_results: int = 5) -> list[dict]:
    """Filtered KB search."""

@tool
def rank_passages_by_relevance(query: str, passages: list[dict]) -> list[dict]:
    """Re-rank using Claude Haiku based on semantic match."""

@tool
def extract_quote_excerpt(passage: str, max_chars: int = 200) -> str:
    """Pull the most relevant 200 chars."""
```

**Steering:** Strict citation format (must include article, section, page, excerpt).

**Memory:** None (stateless lookup).

**Reusable:** Other agents call this via `agent_as_tool(citation_agent)`.

---

### 2.7 ConciergeAgent ("Ask the CBA")

**Role:** Admin UX feature. Free-form Q&A grounded in a specific union's CBA. Multi-turn conversation.

**System prompt:**
```
You're the CBA concierge for the LaborAid ops team. You answer questions
about a specific union's CBA using only what's in the document. Always cite
the Article and section. If the CBA doesn't address the question, say
"I don't see this in the [union] CBA" — never speculate.
```

**Tools:**
- `search_cba_kb` (same as CitationAgent)
- `find_article_section` (when user asks about a specific article)
- `compare_to_prior_contract` (when comparing 2018-2024 vs 2024-2030)

**Hooks:**
```python
def require_citation_in_every_answer(event: AfterToolCallEvent):
    """Every concierge response must include at least one citation."""
    if event.tool_use["name"] == "respond_to_user":
        text = event.result["text"]
        if not re.search(r"Article \d+", text):
            event.result["_warning"] = "Answer lacks Article citation; ConciergeSteering will retry"
```

**Steering:**
```python
class ConciergeSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        if tool_use["name"] == "respond_to_user":
            text = tool_use["input"]["text"]
            if "I think" in text or "probably" in text or "likely" in text:
                return Guide(reason="Don't speculate. Either find a CBA citation or say "
                                     "'I don't see this in the CBA'.")
            if not re.search(r"Article \d+", text):
                return Guide(reason="Every answer must include a CBA Article citation. "
                                     "Search the KB for relevant passages first.")
        return Proceed(reason="OK.")
```

**Conversation manager:** `SlidingWindowConversationManager(window_size=20)` — keeps last 20 turns for follow-up questions.

**Memory:** Short-term per-conversation. Long-term per-tenant: track frequently-asked questions to surface FAQ.

---

### 2.8 ReviewAssistAgent

**Role:** When ValidatorAgent flags a cell for human review, this agent presents the human admin with: source PDF page, OCR alternatives, prior period values, similar past overrides, and a suggested correction.

**System prompt:**
```
A rate-sheet cell has been flagged for review. Your job is to gather all
relevant context for the human reviewer:
1. Show the source PDF page rendering
2. Show all OCR candidates with confidences
3. Show what the prior period had
4. Search prior overrides for similar cases (semantic memory)
5. Suggest a value if confidence > 0.85; else say "needs human judgment"
```

**Tools:**
```python
@tool
def render_source_pdf_page(file_id: str, page: int) -> bytes:
    """Render PDF page as image for the reviewer."""

@tool
def get_ocr_alternatives(extraction_id: str, cell_path: str) -> list[dict]:
    """Return all OCR candidate values with confidence scores."""

@tool
def get_prior_period_value(union_local: int, cell_path: str) -> dict:
    """Same cell from previous period."""

@tool
def search_prior_overrides(union_local: int, cell_path: str) -> list[dict]:
    """Semantic memory: 'have we seen similar overrides before?'"""

@tool
def suggest_correction(context: dict) -> dict:
    """Claude reasons over all gathered info to suggest a value."""

@tool
def submit_human_decision(cell_id: str, decision: dict) -> dict:
    """Record the human's choice + write provenance:manual."""
```

**Memory:** **Long-term semantic per-union** — every accepted override becomes a learned pattern. AgentCore Memory `semanticMemoryStrategy` with namespace `/overrides/{union_local}/`.

This is where the system **learns from corrections over time**. After the 5th similar OCR mistake, the suggestion becomes near-certain.

**Steering:**
```python
class ReviewAssistSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        if tool_use["name"] == "suggest_correction":
            if not agent.gathered_context_complete():
                return Guide(reason="Don't suggest before gathering: PDF page, OCR alts, "
                                     "prior period, prior overrides.")
        return Proceed(reason="OK.")
```

---

### 2.9 ProfileDrafterAgent (onboarding helper)

**Role:** When onboarding a new union, draft an initial Profile YAML by analyzing the CBA + any sample customer rate sheets. Human polishes the draft.

**System prompt:**
```
You're drafting an initial Union Rule Profile for a new union. Given the
CBA + any existing rate sheet (if customer provided one):
1. Identify zones, packages, apprentice ladder, foreman structure
2. Identify funds and their CBA citations
3. Detect format conventions (sheet naming, layout)
4. Output Profile YAML with as many fields populated as possible
5. Mark fields you couldn't determine as "TODO_HUMAN: <reason>"
```

**Tools:**
- `mine_cba` (calls CBAMinerAgent)
- `analyze_existing_rate_sheet` (parse customer's xlsx if provided)
- `compare_to_known_profiles` (similarity to existing 5 POC unions)
- `write_profile_draft` (output YAML)

**Memory:** Long-term — pattern library of all profiles authored.

---

### 2.10 BackfillAgent (historical processing)

**Role:** Process years of historical Rate Notices for a newly-onboarded union. Iterates through periods in chronological order, building a complete history.

**Tools:**
- `list_historical_notices` (S3 listing)
- `process_period` (invokes Orchestrator for each period)
- `verify_continuity` (no period gaps; effective dates contiguous)

**Long-running:** May take hours. AgentCore Runtime supports it (no Lambda 15-min limit).

---

## 3. AgentCore service mapping

How each agent uses each AgentCore service:

| AgentCore Service | Used By | For What |
|---|---|---|
| **Runtime** | All 9 agents | Serverless container hosting, ARM64 Python 3.12 |
| **Memory** | Orchestrator (job state), Extractor (label aliases), CBAMiner (rule patterns), Concierge (chat history), ReviewAssist (override patterns), ProfileDrafter (profile patterns), Backfill (progress) | Short-term + long-term semantic memory; per-actor namespacing |
| **Gateway** | All agents that call Lambdas (resolver, renderer, publisher, KB search) | Lambdas exposed as MCP tools via Gateway, with SigV4/OAuth |
| **Identity** | All agents | Cognito-federated identity; token exchange for tool auth |
| **Code Interpreter** | (potentially) ProfileDrafter | If we let it test-run draft profiles against sample data |
| **Browser** | None (rate sheets aren't web UIs) | Not applicable for v1 |
| **Observability** | All 9 agents | OpenTelemetry traces; per-step debugging in CloudWatch |
| **Evaluations** | All 9 agents | Quality measurement; test fixtures per agent per scenario |
| **Policy** | All agents | Cedar guardrails on tool calls (e.g., "only Validator can approve_for_publish"; "Concierge is read-only") |
| **Registry** | All agents + tools + skills | Centralized catalog; governs publishing, discovery |

---

## 4. Skills (in AgentCore Registry)

AgentCore Registry catalogs reusable agents, tools, and **skills**. We register the following skills:

| Skill | Backed by | Used by |
|---|---|---|
| `classify_document` | ClassifierAgent | Orchestrator, admin UI |
| `extract_rate_notice_values` | ExtractorAgent | Orchestrator, BackfillAgent |
| `mine_cba_rules` | CBAMinerAgent | Orchestrator, ProfileDrafter, BackfillAgent |
| `validate_rate_sheet` | ValidatorAgent | Orchestrator |
| `find_cba_citation` | CitationAgent | All agents that need citations + admin UI |
| `ask_cba` | ConciergeAgent | Admin UI |
| `assist_human_review` | ReviewAssistAgent | Admin UI |
| `draft_union_profile` | ProfileDrafterAgent | Admin UI (onboarding wizard) |
| `backfill_historical_periods` | BackfillAgent | Admin UI (onboarding wizard) |
| `compute_yoy_delta` | Lambda + Validator | Validator + admin UI |
| `render_rate_sheet_xlsx` | Lambda (deterministic) | Orchestrator, admin UI |
| `evaluate_dsl_formula` | Lambda (deterministic) | Resolver |

The Registry exposes these via MCP, so external systems (LaborAid product, partner integrations) can also call them with proper auth.

---

## 5. Steering — the secret sauce

Strands' benchmark claims **100% agent accuracy with steering vs 82.5% prompt-only** (and 80.8% for hard-coded workflows). Steering is the design pattern that makes our agents reliable.

### Pattern: SteeringHandler returns `Guide(reason=...)` or `Proceed(reason=...)`

When the agent is about to take an action, the steering handler can:
- **Proceed** — let the action happen
- **Guide** — block the action and provide natural-language feedback to the agent so it can self-correct

This is **better than hard-coded workflows** because:
- The agent sees the feedback as a message, retains context, and adapts
- Corrections are logged to the audit trail (we know what went wrong)
- The pattern composes — multiple steering handlers can run in sequence

### Cross-cutting steering policies (apply to ALL agents)

```python
class CrossCuttingSteering(SteeringHandler):
    """Applied to every agent in the system."""

    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        # 1. Provenance enforcement — any tool that produces a value MUST cite source
        if tool_use["name"] in VALUE_PRODUCING_TOOLS:
            if "_provenance" not in tool_use["input"].get("output_format", {}):
                return Guide(reason="Tool output must include _provenance field "
                                     "(rate_notice / cba / derived / convention / default / manual).")

        # 2. Confidence required on numeric outputs
        if tool_use["name"] in NUMERIC_OUTPUT_TOOLS:
            if "_confidence" not in tool_use["input"].get("output_format", {}):
                return Guide(reason="Numeric tool outputs must include _confidence (0-1).")

        # 3. Don't operate on stale data
        if tool_use["name"] == "publish":
            data_age = agent.get_data_age()
            if data_age > timedelta(hours=24):
                return Guide(reason="Data is >24h old; refresh before publish.")

        return Proceed(reason="Cross-cutting checks pass.")
```

### Per-agent steering (specialized)

Each agent has its own steering handler (specified in section 2). Examples:

| Agent | Key steering rule |
|---|---|
| Orchestrator | Don't run mining without CBA; don't render without validation |
| Extractor | Always validate checksum; escalate if confidence drops |
| CBAMiner | Every rule must have CBA citation; flag ambiguities |
| Validator | All required checks must run before approve |
| Citation | Citation must include article + section + quote |
| Concierge | Every answer needs citation; no speculation |
| ReviewAssist | Don't suggest before gathering full context |

---

## 6. Memory strategies per agent

AgentCore Memory supports 3 strategies. Mapping per agent:

| Agent | summaryMemoryStrategy | userPreferenceMemoryStrategy | semanticMemoryStrategy |
|---|---|---|---|
| Orchestrator | ✓ (job summaries) | — | — |
| Classifier | — | — | — (stateless) |
| Extractor | — | — | ✓ (label-alias patterns per union) |
| CBAMiner | ✓ (CBA reading sessions) | — | ✓ (rule extraction patterns) |
| Validator | — | — | ✓ (YoY anomaly patterns per union) |
| Citation | — | — | — (uses Bedrock KB instead) |
| Concierge | ✓ (conversation summaries) | ✓ (admin's preferences for level of detail) | — |
| ReviewAssist | — | — | ✓ (override patterns per union) |
| ProfileDrafter | — | — | ✓ (profile patterns library) |
| Backfill | ✓ (progress per backfill job) | — | — |

### Namespaces

```yaml
# AgentCore Memory namespaces
/sessions/{actor_id}/{job_id}/                      # Orchestrator job state
/extractor/label_aliases/{union_local}/             # ExtractorAgent learned aliases
/cba_rules/{union_local}/                           # CBAMinerAgent learned rules
/yoy_baselines/{union_local}/                       # ValidatorAgent baselines
/concierge/{actor_id}/conversations/{session_id}/   # ConciergeAgent chat
/concierge/{actor_id}/preferences/                  # admin's UX preferences
/overrides/{union_local}/{cell_pattern}/            # ReviewAssistAgent override patterns
/profiles/library/                                   # ProfileDrafterAgent patterns
/backfill/jobs/{backfill_job_id}/progress/           # BackfillAgent progress
```

---

## 7. Tool exposure via AgentCore Gateway

Our internal Lambdas (resolver, renderer, KB search wrappers) become **MCP tools** via Gateway. Once registered, any agent can call them with one line:

```python
from strands.tools.mcp import MCPClient
from mcp import StdioServerParameters

# Connect to AgentCore Gateway as an MCP client
gateway = MCPClient(
    transport_factory=lambda: agentcore_gateway_transport(
        endpoint="https://gateway.bedrock-agentcore.us-east-1.amazonaws.com/laboraid-prod",
        auth_token=identity.get_token(),
    )
)

# Add gateway tools to any agent
agent = Agent(
    tools=[gateway],  # all gateway-exposed tools available
    ...
)
```

### Tools served via Gateway

| Tool name | Backed by | Purpose |
|---|---|---|
| `search_cba_kb` | Bedrock Knowledge Base | Semantic search |
| `evaluate_dsl_formula` | Lambda (resolver) | DSL evaluator |
| `render_rate_sheet_xlsx` | Lambda (renderer) | xlsx generation |
| `compute_total_package_checksum` | Lambda (validator) | Deterministic checksum |
| `pdftotext_extract` | Lambda (extractor helper) | Text extraction |
| `tesseract_ocr` | Lambda + container | OCR |
| `textract_analyze` | Lambda + Textract | Table-aware OCR |
| `aurora_query` | Lambda (read-only DB) | Look up Profile, manifest, rate sheet |
| `s3_get_object` | Lambda (read S3) | File access |
| `dynamodb_get_item` | Lambda (state) | Job state lookup |
| `lookup_known_unions` | Lambda | Parent-international lookup, etc. |

---

## 8. AgentCore Policy — Cedar guardrails

Beyond Strands steering (which is agent-internal), AgentCore Policy enforces **org-wide rules** at tool-call time, regardless of which agent calls.

Examples in Cedar:

```cedar
// Only ValidatorAgent can call approve_for_publish
permit (
    principal == Agent::"ValidatorAgent",
    action == Action::"call_tool",
    resource == Tool::"approve_for_publish"
);

// ConciergeAgent is read-only — cannot write to any DB or S3
forbid (
    principal == Agent::"ConciergeAgent",
    action == Action::"call_tool",
    resource in [Tool::"aurora_write", Tool::"s3_put_object", Tool::"dynamodb_put_item"]
);

// Cross-tenant isolation
forbid (
    principal,
    action,
    resource
)
when {
    resource has tenant && principal has tenant && resource.tenant != principal.tenant
};

// CBAMinerAgent can only read CBAs (never write)
forbid (
    principal == Agent::"CBAMinerAgent",
    action == Action::"call_tool",
    resource == Tool::"s3_put_object"
);

// Backfill can run for max 24 hours
forbid (
    principal == Agent::"BackfillAgent",
    action == Action::"call_tool",
    resource
)
when {
    context.session_duration > duration("24h")
};
```

These are **deterministic** — they don't slow agents down; they reject bad calls instantly.

---

## 9. Observability (OpenTelemetry traces)

Every agent emits OTEL traces. AgentCore Observability ingests them and CloudWatch surfaces them.

A typical Orchestrator job trace:

```
trace_id: t-abc123
└─ orchestrator.invoke (5.2s)
   ├─ tool: classify_file (0.3s)
   │  └─ classifier_agent.invoke (0.3s)
   │     └─ tool: parse_filename (0.05s, deterministic)
   ├─ tool: extract_rate_notice (3.1s)
   │  └─ extractor_agent.invoke (3.1s)
   │     ├─ tool: extract_text_pdf (1.2s)
   │     ├─ tool: lookup_profile_label_aliases (0.1s)
   │     └─ tool: validate_total_package_checksum (0.05s, PASSED)
   ├─ tool: resolve_rate_sheet (0.4s, Lambda — no agent)
   ├─ tool: validate_rate_sheet (1.2s)
   │  └─ validator_agent.invoke (1.2s)
   │     ├─ tool: run_checksum (0.05s, PASSED)
   │     ├─ tool: cross_check_apprentice_percentages (0.05s, PASSED)
   │     ├─ tool: compute_yoy_delta (0.2s)
   │     └─ result: AUTO_PUBLISH (confidence 0.98)
   ├─ tool: render_outputs (0.6s, Lambda)
   └─ tool: store_in_aurora (0.2s, Lambda)
```

Trace attributes per agent: `service`, `agent_name`, `tenant`, `union_local`, `period_start`, `job_id`, `invocation_id`.

CloudWatch dashboards built on these:
- Mean time per stage
- Auto-publish rate
- Steering interventions per agent (signal of prompt quality issues)
- Memory hit rate (semantic memory effectiveness)
- Cost per rate sheet (Bedrock token spend per agent invocation)

---

## 10. AgentCore Evaluations

Agentic systems need agentic eval. AgentCore Evaluations is purpose-built for this — it works with Strands traces directly.

### Evaluation suites we build

**ExtractorAgent eval:**
- 50 fixture Rate Notices spanning the 5 POC unions
- For each: golden ExtractedDocument JSON
- Metrics: per-field accuracy, confidence calibration, latency, token spend

**CBAMinerAgent eval:**
- 5 fixture CBAs (1 per union)
- Golden RuleManifests
- Metrics: rule completeness, citation accuracy, ambiguity-flag recall

**ValidatorAgent eval:**
- Synthetic rate sheets with injected errors (wrong total, missing field, etc.)
- Metric: detection rate, false-positive rate

**ConciergeAgent eval:**
- Q&A test suite (real questions an admin would ask)
- Metric: citation accuracy (did the agent cite the correct CBA section?), answer quality

Run nightly. Regressions block deploys.

---

## 11. Deployment to AgentCore Runtime

Each agent deploys as its own AgentCore Runtime container.

### Deployment flow

```bash
# Per-agent deployment
agentcore configure --name extractor-agent
# Edit agent.py with Strands code
agentcore deploy
```

That's the entire deploy. AgentCore Runtime:
- Builds the ARM64 Docker image (with `strands-agents` and our deps)
- Provisions secure isolated runtime
- Wires up Memory, Gateway, Identity per config
- Returns an invocation endpoint (HTTPS)

### Per-agent CDK stack (programmatic deploy)

```typescript
new bedrockAgentCore.AgentRuntime(this, 'ExtractorAgent', {
  agentName: 'extractor-agent',
  runtimeImage: ContainerImage.fromAsset('./agents/extractor'),
  environment: {
    PROFILE_BUCKET: profilesBucket.bucketName,
    KB_ID: cbaKnowledgeBase.attrKnowledgeBaseId,
  },
  memory: {
    memoryId: extractorMemory.attrMemoryId,
    strategies: ['SEMANTIC'],
  },
  gateway: {
    gatewayId: laboraidGateway.attrGatewayId,
  },
  identity: {
    cognitoUserPoolId: agentCognitoPool.userPoolId,
  },
  policy: {
    policyArn: extractorPolicy.attrPolicyArn,
  },
  observability: {
    enabled: true,
    otelEndpoint: 'cloudwatch',
  },
  executionRole: extractorExecutionRole,
});
```

Repeat for each of the 9 agents. ~50 lines of CDK total.

### Cost (revised with AgentCore)

| Component | Monthly cost @ moderate scale (50 unions, ~500 invocations/month) |
|---|---|
| AgentCore Runtime (9 agents, mostly idle) | ~$30 |
| AgentCore Memory (semantic + summary) | ~$15 |
| AgentCore Gateway | ~$10 |
| AgentCore Identity (Cognito federated) | ~$5 |
| AgentCore Observability | included with CloudWatch |
| AgentCore Evaluations (nightly runs) | ~$10 |
| AgentCore Policy | included |
| AgentCore Registry | ~$5 |
| Bedrock model calls (Claude Sonnet/Haiku) | ~$15 (extraction + mining + concierge) |
| Bedrock KB queries | ~$5 |
| **Subtotal AI/Agentic** | **~$95/month** |
| Plus rest of AWS infra (Lambda, S3, Aurora, etc. from doc 10) | ~$80 |
| **Total** | **~$175/month** at moderate scale |

Slight uptick over the prior ~$150 estimate because AgentCore is paid-feature-rich. We get the Memory + Gateway + Policy + Evaluations + Registry "for free" in operational complexity.

---

## 12. Migration: from "abstract Bedrock Agent" (docs 01-06) to "Strands+AgentCore"

Where the prior design docs said "Bedrock Agent does X", here's the concrete mapping:

| Old reference (docs 01-06) | New (this doc) |
|---|---|
| "Stage 1 classifier with Haiku fallback" | **ClassifierAgent** (Strands) on AgentCore Runtime |
| "Stage 2 extractor with Claude Sonnet multi-modal fallback" | **ExtractorAgent** (Strands) with 3-path tool set |
| "Stage 3 Bedrock Agent for CBA mining" | **CBAMinerAgent** (Strands) on AgentCore Runtime + Bedrock KB |
| "Stage 5 Claude sanity review" | **ValidatorAgent** with `explain_anomaly` tool |
| "'Ask the CBA' admin chat" | **ConciergeAgent** with `SlidingWindowConversationManager` |
| "Manual override UI assistance" | **ReviewAssistAgent** with semantic memory |
| "Bedrock Agent's tool use" | Strands `@tool` + AgentCore Gateway-served MCP tools |
| "Step Functions orchestration" | **OrchestratorAgent** (Strands) replaces Step Functions for the agent layer; Step Functions still orchestrates the deterministic Lambdas (resolver, renderer) |
| "Custom guardrails per Lambda" | **AgentCore Policy** (Cedar rules) — central |
| "Custom audit logging" | **AgentCore Observability** (OTEL traces) — built-in |
| "Custom test fixtures" | **AgentCore Evaluations** — purpose-built |

### Step Functions vs Orchestrator Agent — when to use which

After this redesign:
- **OrchestratorAgent (Strands)** handles the **agent layer** routing — when reasoning matters (e.g., "should we re-extract, or is this confidence good enough?")
- **Step Functions** still wraps the **end-to-end pipeline** — for deterministic state transitions (S3 event → orchestrator invocation → cleanup)

So the high-level flow is now:

```
S3 event → Step Function (deterministic state machine)
            ↓
            Invokes OrchestratorAgent (AgentCore Runtime)
            ↓
            Orchestrator delegates to specialist agents (Agent-as-Tool)
            ↓
            Specialists return canonical JSON
            ↓
            Step Function calls deterministic resolver Lambda
            ↓
            Step Function calls deterministic renderer Lambda
            ↓
            Step Function calls publisher Lambda → done
```

Step Functions for state. Orchestrator for reasoning. Specialists for capabilities. Deterministic Lambdas for math.

---

## 13. What this changes about the implementation plan (doc 06)

| Phase | Was (doc 06) | Now (with Strands+AgentCore) |
|---|---|---|
| Week 1-2 | Schemas + DSL + Resolver | Same + add Strands setup + AgentCore CLI scaffold |
| Week 3-4 | Custom extraction (Lambda) | **ExtractorAgent** in Strands; deploy to AgentCore Runtime; iteratively tune |
| Week 5 | Custom CBA mining (Bedrock Agent) | **CBAMinerAgent** with KB tools |
| Week 6 | Custom validation + render | **ValidatorAgent** + deterministic renderer Lambda |
| Week 7 | CDK + Step Functions | CDK with **AgentCore Runtime + Memory + Gateway + Policy + Identity**; Step Functions wraps |
| Week 8 | Admin UI | Admin UI + **ConciergeAgent + ReviewAssistAgent** integration |

Net effect: **same 8-week timeline**, but the resulting system is much more powerful (memory-equipped, evaluable, governed via Policy). Nice-to-haves like "Ask the CBA" and "learn from past overrides" come for free with the Strands+AgentCore primitives.

---

## 14. Open architectural questions specific to agents

| Q# | Question | Why it matters |
|---|---|---|
| Q31 | Single AgentCore Memory or per-agent memories? | Sharing learned data across agents (e.g., Extractor learns label aliases that ConciergeAgent could use) |
| Q32 | Agent versioning policy — semver per agent? | When ExtractorAgent prompt changes, ValidatorAgent shouldn't break |
| Q33 | Cross-tenant isolation in Memory namespaces | At LaborAid scale (50+ unions, multiple tenants later) |
| Q34 | Steering benchmark target | Strands says 100% with steering; what's our minimum acceptable accuracy per agent before deploy? |
| Q35 | Agent-to-agent timeout handling | When CBAMinerAgent takes too long, should Orchestrator give up or wait? |
| Q36 | Cost cap per job | Limit Bedrock token spend per single rate sheet to prevent runaway costs |
| Q37 | Eval-driven deploy gates | Block agent prompt changes from deploying if eval suite regresses |
| Q38 | Manual agent invocation | Admin UI feature: "re-run ExtractorAgent on this file with these instructions" |

---

## 15. Bottom line

The pivot to **Strands Agents on AgentCore** transforms the engine from "AWS plumbing with Bedrock calls" into a **governed, observable, evaluable agentic system** — without rewriting our pipeline thinking.

What we get:
- **9 specialized agents** instead of one mega-agent → sharper prompts, independent eval, failure isolation
- **Steering** as a first-class pattern → 100% accuracy claim from Strands + audit-friendly course corrections
- **Memory** built-in → agents learn from prior runs (label aliases, override patterns, rule extractions)
- **Gateway** turning Lambdas into MCP tools → no custom integration code
- **Policy** for org-wide guardrails → Cedar rules that are deterministic and auditable
- **Observability + Evaluations** purpose-built for agentic workloads → quality measurement that actually works
- **Same 8-week build timeline** → no schedule cost
- **~$25/month additional spend** → trivial vs the platform we'd otherwise have to build

This is the right architecture for an enterprise agentic system. We should adopt it.
