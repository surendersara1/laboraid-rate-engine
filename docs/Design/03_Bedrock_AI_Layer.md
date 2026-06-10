# Bedrock AI Layer

**Document:** 03 of 7 in `docs/`
**Read after:** `01_Engine_Architecture.md` and `02_Parser_Stages.md`. This doc focuses on **how, where, and why** Amazon Bedrock is used in the engine.

---

## TL;DR

Bedrock is used in **4 distinct ways**, each chosen because deterministic code can't do the job (or can't do it well enough):

| # | Bedrock capability | What it solves | Where in pipeline |
|---|---|---|---|
| **1** | Claude Sonnet (multi-modal) | PDF extraction when traditional parsers fail (image PDFs, complex tables, weird layouts) | Stage 2 fallback path |
| **2** | Bedrock Agent + tools | CBA rule mining (extract structured rules from 30-50 page legal text) | Stage 3 |
| **3** | Bedrock Knowledge Base + S3 Vectors | Semantic search across CBA corpus for citation lookup and "ask the CBA" UX | Stage 3 + Admin UI |
| **4** | Claude Sonnet (text) | Confidence sanity review of suspicious cells, manual-review assistance | Stage 5 + Admin UI |
| **5** | Claude Haiku (cheap) | File classification fallback when filename patterns don't match | Stage 1 |

The **deterministic core** of the pipeline (Stages 4 and 6) does NOT use Bedrock. AI is for the messy parts; the formulaic parts run on plain Python.

---

## 1. Claude Sonnet — Multi-modal PDF Extraction

### Use case
Stage 2 path C: when text-extractable PDF parsing yields garbage and OCR confidence is too low, send the entire PDF to Claude as a multi-modal input.

### Why Claude (vs other models)
- **Native PDF support:** Claude reads PDFs directly without preprocessing. No need to render pages to images first.
- **Long context:** Claude's 200K+ token window comfortably handles 50-page CBAs and 12-page annual Rate Notice bundles.
- **Strong table understanding:** Claude reliably parses multi-column rate-notice tables (e.g., 483's complex Class × Fund matrix).
- **Tool use / structured output:** Claude can return strict JSON matching our schema, with confidence scores per field.
- **Citation generation:** Claude can return page numbers / line numbers for each extracted value (used for provenance).

### Bedrock invocation pattern

```python
import boto3
from botocore.config import Config

bedrock = boto3.client("bedrock-runtime", config=Config(read_timeout=300))

def extract_rate_notice_with_claude(pdf_bytes: bytes, profile: Profile) -> ExtractedDocument:
    expected_fringes = [f.name for f in profile.fringe_schema]
    expected_aliases = {f.name: f.notice_label_aliases for f in profile.fringe_schema}

    prompt = build_extraction_prompt(profile.union, expected_fringes, expected_aliases)

    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-sonnet-4-6-v1:0",  # or current latest
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8000,
            "system": SYSTEM_PROMPT_RATE_NOTICE,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(pdf_bytes).decode()}},
                    {"type": "text", "text": prompt}
                ]
            }],
            # Force JSON output via prefill
            "messages": [{"role": "assistant", "content": "{"}]
        })
    )

    raw = json.loads(response['body'].read())
    extracted_json = json.loads("{" + raw['content'][0]['text'])
    return ExtractedDocument.parse_obj(extracted_json)
```

### System prompt (Rate Notice extraction)

```
You are an expert reader of union benefit-fund Rate Notices issued by US construction
trade unions (Pipefitters, Sprinkler Fitters, etc.). Your job is to extract structured
data from a Rate Notice PDF.

A Rate Notice typically contains:
- A header naming the union local (e.g., "Sprinkler Fitters Local 704")
- An effective date (e.g., "effective January 1, 2026")
- An anchor wage for Journeyman (or "Fitter")
- A list of fringe benefits with dollar amounts (Health & Welfare, Pension, etc.)
- A list of deductions (Union Dues, Industry Promotion, etc.)
- Possibly: apprentice wage schedule, OT rates, foreman premium, vacation options

Extract values into the requested JSON schema. For every value, include:
- The numeric value
- A confidence score (0.0 to 1.0)
- The page number where you found it
- The exact label as printed (so we can map to canonical column names)

If a value is ambiguous or you can't find it, set confidence to 0.0 and explain in
"_notes" why.

Never invent values. If something isn't in the PDF, omit it.
```

### User prompt (parameterized)

```
Extract the Rate Notice for {union_local} into this JSON schema:

{schema}

Expected fringe column aliases (the Notice may use any of these labels):
{aliases}

Return ONLY valid JSON.
```

### Why pass the schema in the prompt
- Forces Claude to fill the right fields
- Allows per-union customization (different unions have different fringe sets)
- Easier to maintain than baking schema into the model

### Confidence handling
- Each extracted field has Claude-generated confidence
- Engine treats confidence <0.85 as a flag → human review
- Aggregate document confidence = min(field confidences)

### Cost
- Claude Sonnet 4.x (Bedrock): ~$3 per 1M input tokens, ~$15 per 1M output tokens
- A 1-page Rate Notice PDF ≈ 2K tokens input, 1K tokens output
- ~$0.02 per Notice (cheap given the value)
- 12-page Notice ≈ $0.10

### Failure modes
| Failure | Handling |
|---|---|
| Claude hallucinates a field not in PDF | Validation catches via checksum; route to manual review |
| Claude misses a field that IS in PDF | Validation catches missing required field; manual review |
| JSON parse error in Claude's response | Retry with stricter prompt; second retry uses tool-use (forces structured) |
| Bedrock throttling | Exponential backoff in Lambda |

---

## 2. Bedrock Agent — CBA Rule Mining

### Use case
Stage 3: extract a complete `RuleManifest` from a 35-50 page CBA. The CBA contains structural rules scattered across many articles ("Article 5 Wages," "Article 17 Health and Welfare," "Article 24 Industry Promotion", etc.). A single Claude call can read the whole CBA, but we get better quality and traceability by using an **Agent that breaks the task into focused sub-extractions**.

### Why Bedrock Agent (vs single Claude call)
- **Tool use** lets us combine search + extract + validate in one workflow
- **Multi-step reasoning** improves quality on a long, complex doc
- **Structured intermediate outputs** make debugging easier
- **Auditable trace** of every search query and extraction decision
- **Knowledge Base integration** for retrieval-augmented extraction

### Agent definition

```yaml
agent:
  name: laboraid-cba-rule-miner
  model: anthropic.claude-sonnet-4-6-v1:0
  instructions: |
    You are a Collective Bargaining Agreement (CBA) analyzer. Given a CBA for a
    construction trade union, your job is to extract the structured rules into
    a RuleManifest JSON.

    For each rule type listed below, you will:
    1. Search the CBA Knowledge Base for relevant passages
    2. Extract the structured rule using the extraction tool
    3. Validate the rule against the expected schema
    4. If a rule is ambiguous, flag it with a suggested resolution

    Rule types to extract (in order):
    - wage_anchor_definition (Article 5 or 6)
    - foreman_premium with date-keyed schedule
    - general_foreman premium
    - apprentice_schedule (with anchors per year/class)
    - apprentice_pension_exclusion
    - ot_rules (1.5x and 2.0x)
    - shift_differential
    - funds (one entry per benefit fund: Health & Welfare, Pension, etc.)
    - uniformity_rule (Article 20-style)
    - rate_change_cadence
    - vacation rules (if applicable)
    - rounding rule

    For each rule, cite the CBA Article and section.
    Be thorough. Don't skip rules even if they seem obvious.

  tools:
    - search_cba_kb
    - extract_rule_from_passage
    - validate_rule
    - cross_reference_existing_profile
```

### Tool 1: `search_cba_kb`

Wraps a Bedrock Knowledge Base retrieval call. Inputs: query text. Outputs: top-K passages with citations.

```python
@tool
def search_cba_kb(query: str, max_results: int = 5) -> list[Passage]:
    """Search the CBA's Knowledge Base for passages relevant to the query."""
    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": max_results,
                "filter": {"equals": {"key": "union_local", "value": str(current_union_local)}}
            }
        }
    )
    return [
        Passage(
            text=r["content"]["text"],
            page=r["metadata"].get("page"),
            article=r["metadata"].get("article"),
            section=r["metadata"].get("section"),
            score=r["score"]
        )
        for r in response["retrievalResults"]
    ]
```

### Tool 2: `extract_rule_from_passage`

Takes a passage + rule schema and returns a structured rule. Backed by another Claude call (this time small and focused).

```python
@tool
def extract_rule_from_passage(passage: str, rule_type: str) -> dict:
    """Extract a structured rule of the specified type from a CBA passage."""
    schema = RULE_SCHEMAS[rule_type]  # JSON schema per rule type
    prompt = f"""
    Extract a {rule_type} rule from this CBA passage.
    Return JSON matching this schema: {schema}
    Passage: {passage}
    """
    response = invoke_claude_sonnet(prompt)
    return json.loads(response)
```

### Tool 3: `validate_rule`

Validates extracted rule JSON against schema. Returns errors for missing/invalid fields. Also runs sanity checks (e.g., Foreman premium between $1 and $20).

### Tool 4: `cross_reference_existing_profile`

If a Profile exists for this union (we're processing a CBA renewal/amendment), looks up what was previously known. Helps catch CBA changes across contract terms (e.g., 537 NEMCA Yellow Book 2025-2030 vs 2021-2025 — what's different?).

### Sample agent trace

```
Agent: I'll start by extracting the wage_anchor_definition.
   → search_cba_kb("Journeyman wage rate")
   ← 5 passages from Article 6, page 3-4

Agent: Found Article 6 §11 with wage definition.
   → extract_rule_from_passage(<Article 6 §11 text>, "wage_anchor_definition")
   ← {anchor_zone: "Building", initial_value: 48.73, increase_schedule: [...]}

Agent: Validating...
   → validate_rule(<extracted>)
   ← VALID

Agent: Now extracting foreman_premium.
   → search_cba_kb("Foreman wage premium")
   ← Article 6 §14: "$4.00 (Aug 2022) → $4.25 → $4.50"

Agent: Multi-year schedule detected.
   → extract_rule_from_passage(<Article 6 §14>, "foreman_premium")
   ← {type: "flat_dollars", schedule: [{effective: "2022-08-01", amount: 4.00}, ...]}

Agent: Validating...
   → validate_rule(<extracted>)
   ← VALID

Agent: Continuing with general_foreman, apprentice_schedule, ...
[20+ more iterations]

Agent: All rules extracted. Returning RuleManifest.
```

Total: ~60 tool calls, ~5 minutes elapsed, ~$0.50 per CBA.

### Caching
RuleManifest cached in S3 + Aurora. Subsequent Rate Notices for the same union+period skip Stage 3 entirely. Re-mining only when CBA file changes.

---

## 3. Bedrock Knowledge Base + S3 Vectors — CBA Corpus

### Purpose
1. Power the CBA rule miner (Tool 1 above)
2. Power the "Ask the CBA" admin UX feature
3. Power citation lookup ("which CBA passage justifies this Foreman premium of $4.50?")

### Why Knowledge Base + S3 Vectors

**Bedrock Knowledge Bases** is AWS's managed RAG service. It handles:
- Chunking (configurable chunk size, overlap)
- Embedding generation
- Vector storage
- Retrieval API
- Metadata filtering

**S3 Vectors** is the vector storage backend (vs OpenSearch Serverless).

**Why S3 Vectors over OpenSearch:**
- Cost: S3 Vectors is dramatically cheaper for sparse-query workloads (~$0.10/GB/month + per-query cost). OpenSearch Serverless has a minimum spend (~$700/month). We don't need that scale.
- Setup: simpler (no index sizing, no shards)
- Native AWS-managed
- Suits our query pattern: hundreds of queries per day, not thousands per second

**Trade-offs:**
- Higher per-query latency than warm OpenSearch (acceptable: we're not user-facing)
- Less query expressiveness (basic similarity + metadata filter only)

### Knowledge Base structure

**Single Knowledge Base** with **per-union metadata filter**:
```
Knowledge Base: laboraid-cba-corpus
  Data source: s3://laboraid-cba-corpus/
  Folder structure:
    /sprinkler/704/2022-2027.704 CBA.pdf  (chunked)
    /sprinkler/821/2021-2026.821 CBA.pdf  (chunked)
    /pipefitter/537/yellow-book/...
    /pipefitter/537/green-book/...
    ...
  Metadata schema:
    - tenant: laboraid
    - trade: Sprinkler / Pipefitter / etc.
    - union_local: 704 / 821 / ...
    - cba_term_start: 2022-08-01
    - cba_term_end: 2027-07-31
    - scope: NEMCA / NEMSCA / null
    - article: parsed from chunk content
    - section: parsed from chunk content
    - page: page number
```

### Chunking strategy

CBAs have natural article structure. We use **structured chunking**:

1. **First pass:** detect article boundaries (look for `^ARTICLE \d+ -? \w+`, `^Article \d+`, etc.)
2. **Per-article:** further split into sections (`^Section \d+`, `^\d+\.`)
3. **Per-section:** if chunk >800 tokens, split with 100-token overlap
4. **Tag every chunk** with `(article, section, page_range)` metadata

This produces semantically meaningful chunks (one per article-section), enabling precise retrieval like:
```
search_cba_kb(query="Foreman premium dollar amount") 
  → returns Article 6 §14 chunk with the answer + citation
```

### Embedding model
- **Titan Embed Text v2** (Amazon's native, cost-effective, 1024-dim or 256-dim)
- Use 1024-dim for accuracy on technical English text
- Alternative: Cohere Embed English v3 (sometimes better for legal text; costs more)

### Ingestion pipeline

```
New CBA PDF arrives → S3 inputs bucket
  ↓
Lambda kicks off ingest job:
  1. Extract text via pdftotext / OCR / Claude (Stage 2 path)
  2. Detect article structure (regex)
  3. Chunk per-article + section
  4. Write chunks to KB-managed S3 bucket with metadata
  5. Bedrock auto-syncs (S3 sync trigger)
  6. KB embeds + stores vectors in S3 Vectors
  ↓
Ready for queries within ~5 minutes
```

### Querying with metadata filter

```python
def search_cba(query: str, union_local: int, scope: str = None) -> list[Passage]:
    filter_clause = {
        "andAll": [
            {"equals": {"key": "union_local", "value": str(union_local)}},
            {"equals": {"key": "tenant", "value": "laboraid"}},
        ]
    }
    if scope:
        filter_clause["andAll"].append(
            {"equals": {"key": "scope", "value": scope}}
        )

    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": 5,
                "filter": filter_clause
            }
        }
    )
    return parse_passages(response)
```

### "Ask the CBA" admin UX

Admin clicks any cell in the rate-sheet review UI → side panel pops up with:
- The PDF source (rendered preview)
- The provenance tag
- A chat input: "Ask anything about this CBA"

Behind the scenes:
- Chat input → query the KB for the union's CBA
- Retrieve top 3 passages
- Send to Claude with prompt: *"Answer this question using only these CBA passages. Cite the article."*
- Display answer with citations linked back to the PDF

This is **invaluable** for the ops admin doing review — they can verify any cell's logic in seconds without reading the whole CBA.

### Cost
- Bedrock Knowledge Base: $0.0001 per query
- S3 Vectors: $0.10/GB/month + small per-query cost
- For 50 unions × 50-page CBAs ≈ 100 MB total = $0.01/month storage
- 1000 queries/day × 30 days = 30K queries/month ≈ $3
- Total KB cost: trivial (<$10/month at our scale)

---

## 4. Claude Sonnet — Confidence Sanity Review

### Use case
Stage 5 + Admin UI: when a cell is flagged as suspicious (large YoY delta, missing required field, OCR confidence below threshold), invoke Claude to sanity-check.

### Pattern: "Is this anomaly explained?"

```
Prompt:
  Cell: SIS for Sprinkler 704 Journeyman, period 2026-07-01.
  Computed value: $15.50
  Prior period (2026-01-01): $11.50
  Change: +$4.00 (+34.8%)

  Inputs that produced this value:
  - Rate Notice 2026-07-01: Total package $87.52, with SIS shown as $15.50
  - CBA Article 27 §103: SIS = $10.00 initial, increases via economic package

  Question: Is this $4.00 increase explained by the inputs, or does it look anomalous?
  Reply with: { "explained": true|false, "reasoning": "...", "confidence": 0-1 }
```

Claude's response:
```json
{
  "explained": false,
  "reasoning": "Total package only changed by $0 (still $87.52); a $4 SIS increase should be offset by a wage decrease, but the wage was stable. This change is unexplained by Article 20 uniformity rule.",
  "confidence": 0.92
}
```

→ Engine treats this as `flag_for_human` instead of auto-publish.

### Pattern: "Suggest the most likely correct value"

For OCR-low-confidence cells where Tesseract gave 3 candidates:

```
Prompt:
  Field: SIS amount for Class 5 Apprentice
  Tesseract candidates with confidence:
    "$7.79" (0.55)
    "$7.22" (0.42)
    "$1.79" (0.21)
  Rate sheet's prior period had Class 5 SIS = $7.22
  CBA rule: SIS scales by class

  Which is most likely correct? Justify.
```

Claude reasons over the rule + history → suggests value → admin confirms.

### Cost
~$0.001 per review (small prompts, small responses). Hundreds per month at most → negligible.

---

## 5. Claude Haiku — File Classification Fallback

### Use case
Stage 1: when filename pattern doesn't match any known regex, send to Haiku for classification.

### Why Haiku (not Sonnet)
- Classification is a simple categorization task; Haiku is fast and 10x cheaper than Sonnet
- ~$0.0001 per classification

### Prompt

```
Look at this file and classify it as one of:
  - cba (Collective Bargaining Agreement, multi-year)
  - rate_notice (single-period dollar values)
  - apprentice_wage_sheet (per-class apprentice rates, possibly per indenture date)
  - reference (Articles, Fund Addresses, summary docs)
  - unknown

Also identify:
  - union_local (integer, if mentioned)
  - effective_date (YYYY-MM-DD if found in filename or first page)
  - trade (Pipefitter, Sprinkler, Sheet Metal, etc.)

Return JSON: { document_type, union_local, effective_date, trade, confidence }

File name: {filename}
First page text: {first_page_text}
```

Cheap, fast, accurate enough for the ~5% of files where filename heuristics fail.

---

## Putting it all together — when AI is invoked

```
┌─────────────────────────────────────────────────────────────────────┐
│                       PIPELINE STAGES                                 │
└─────────────────────────────────────────────────────────────────────┘

Stage 1 (Classify)
  └─> Filename pattern match? ─YES─> Done (deterministic)
                              └─NO─> Claude Haiku  ◄── #5

Stage 2 (Extract)
  ├─> Text PDF, table parses cleanly? ─YES─> Done (pdftotext/pdfplumber)
  │                                  └─NO─> Try OCR (Tesseract)
  │                                            └─> Confidence OK? ─YES─> Done
  │                                                                ├─NO─> Try Textract
  │                                                                │      └─> OK? ─YES─> Done
  │                                                                │              └─NO─> Claude Sonnet  ◄── #1
  └─> Image PDF / unusual format ────────────────────────────────────────> Claude Sonnet  ◄── #1

Stage 3 (CBA Rule Mining, lazy/cached)
  └─> Cached? ─YES─> Skip
              └─NO─> Bedrock Agent  ◄── #2
                       └─> Uses Tool: KB search  ◄── #3
                       └─> Uses Tool: extract_rule (Claude Sonnet) ◄── #1

Stage 4 (Resolve)
  └─> Pure deterministic Python evaluator (NO Bedrock)

Stage 5 (Validate)
  └─> All checks pass? ─YES─> Auto-publish
                       └─NO─> Claude Sonnet sanity review  ◄── #4
                                └─> Explained? ─YES─> Auto-publish
                                              └─NO─> Human review

Stage 6 (Render)
  └─> Pure deterministic Python (NO Bedrock)

Admin UI
  └─> "Ask the CBA" feature → KB search + Claude  ◄── #3 + #4
  └─> Cell review → "Why is this value?" → Claude  ◄── #4
```

The deterministic core (Stages 4 + 6) handles 100% of the formula evaluation and rendering. AI only enters at extraction (Stage 2 fallback), CBA mining (Stage 3), validation review (Stage 5 escalation), classification (Stage 1 fallback), and admin UX.

---

## Cost projections

For 50 unions × 2 Rate Notices/year × ~10 ingestion attempts/month:

| AI service | Use | Monthly cost @ 50 unions |
|---|---|---|
| Claude Sonnet 4.x (extraction) | ~30% of notices need it; ~$0.05/notice | $1-2 |
| Bedrock Agent (CBA mining) | 1-2 CBAs/month × $0.50 | $1 |
| Bedrock KB queries | ~3000 queries/month × $0.0001 | <$1 |
| S3 Vectors storage | 100 MB CBAs × $0.10/GB | <$1 |
| Claude Sonnet (validation) | ~50 reviews/month × $0.001 | <$1 |
| Claude Haiku (classification) | ~50/month × $0.0001 | <$1 |
| Embedding (Titan) | initial CBA ingest, ~$0.01 per CBA | <$1 |
| **Total Bedrock cost** | | **~$10/month** |

Plus AWS infrastructure (Lambda, S3, etc.) per the architecture cost estimate (~$120/month total). AI layer adds <10% to the bill.

---

## Model selection decisions

| Decision | Reasoning |
|---|---|
| Claude Sonnet (not Opus) for extraction | Sonnet is the best $-per-quality balance for structured extraction. Opus is overkill. |
| Claude Haiku for classification | Simple task; Haiku is 10x cheaper than Sonnet. |
| Titan Embed (not Cohere) | Native AWS, cheaper, suitable for English. |
| Single global KB (not per-union) | Easier to manage; metadata filter provides isolation. |
| Bedrock Agent (not custom orchestration) | Built-in tool-use loop, traces, retries — saves engineering time. |
| 200K-token context not needed for extraction | Even 50-page CBAs are <100K tokens. Plenty of headroom. |

---

## Production hardening

- **Streaming responses** for long Claude calls (better Lambda timeout management)
- **Retry with exponential backoff** for Bedrock throttling (max 3 retries)
- **Provisioned throughput** considered if usage spikes (probably not needed for our scale)
- **Cross-region failover** for high-availability (us-east-1 primary, us-west-2 secondary)
- **Cost alarms** on Bedrock spend (email + Slack alert if monthly spend exceeds $50)
- **Audit log** of every Bedrock invocation (who, when, what model, what input, output hash)

---

## Next docs in this folder
- `04_Schemas_and_DSL.md` — JSON schemas referenced by the prompts above
- `05_Provenance_and_Citations.md` — how citations from KB get baked into rate-sheet cells
- `06_Implementation_Plan.md` — week-by-week build with Bedrock integration milestones
