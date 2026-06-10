# Ground Truth, PDF→JSON→LLM, and the Self-Verification Loop

**Document:** 08 of `docs/`
**Date:** 2026-05-05
**Audience:** Engineering team + LaborAid product
**Purpose:** Answer the four questions docs 01-07 didn't address head-on:

1. **How does the engine know what a rate sheet should contain?** (The ground-truth problem.)
2. **How does a 30-page PDF get to the LLM?** (The ingestion-and-tokenization problem.)
3. **How does the agent verify its own output is correct?** (The self-verification problem.)
4. **What happens when a brand-new union arrives that we've never seen?** (The bootstrap problem.)

---

## TL;DR

These are the right questions to ask before writing code. Short answers:

1. **Ground truth comes from 3 layers, in priority order:**
   - **Profile** (per-union schema): tells the engine what columns this union's rate sheet has
   - **CBA structural rules** (mined into RuleManifest): tells the engine the formulas behind values
   - **Rate Notice extracted values**: provides the actual dollars
   - For brand-new unions, layers 1-2 are bootstrapped by the **ProfileDrafterAgent** running on the customer's input pack (CBA + 1+ existing rate sheets), then human-polished.

2. **PDF→LLM is a 2-stage pipeline:**
   - **Stage A (cheap, deterministic):** PDF → tokens via pdftotext / Textract / OCR — produces an intermediate `RawDocumentJSON` artifact
   - **Stage B (LLM-aided when needed):** RawDocumentJSON + Profile + RuleManifest → `ExtractedDocument` via structured-output Claude calls
   - For multi-page CBAs (30-50 pages), we **don't** send the whole thing to the LLM in one shot. We chunk it into a Bedrock Knowledge Base, then retrieve only what each rule needs (~3-5 chunks per rule extraction).

3. **Self-verification is a 4-layer loop:**
   - **Per-cell extraction confidence** (the model's own self-reported certainty)
   - **Deterministic checksums** (does sum-of-fringes equal the printed Total Package?)
   - **Cross-source agreement** (does the Notice value match the CBA-derived value?)
   - **Year-over-year sanity** (is this within plausible delta from last period?)
   - If any layer fails: **escalate, don't silently publish**.

4. **Brand-new union flow:** customer drops input pack → engine runs `ProfileDrafterAgent` → human polishes Profile → backfill historical Notices → from then on, normal pipeline.

The rest of this doc walks through each in detail with concrete examples.

---

## Section 1 — The Ground Truth Problem

### 1.1 The question

When a Rate Notice for Sprinkler Local 704 lands in S3, the engine produces a rate sheet with 13 rows × 24 columns. **How does the engine know there should be 13 rows × 24 columns?** What if Local 704 actually has 14 packages? What if there's a fund column we're missing?

### 1.2 The three sources of ground truth

The engine never invents the rate-sheet schema. It comes from three explicitly-defined sources:

#### Source 1 — The Union Rule Profile (canonical schema authority)

`Profile` is a per-union YAML/JSON config (see doc 04 for the schema). It defines:
- **`zones`**: how many zones (1 for 704, 4 for 821, etc.)
- **`packages`**: every row that should appear in the rate sheet (GF, F, JW, App10..App1, etc.)
- **`fringe_schema`**: every column with its type, label aliases, and apprentice scaling rule
- **`deduction_schema`**: every deduction column
- **`output_schema.columns`**: the final ordered column list for the xlsx

**For the 5 POC unions, Profiles are hand-authored** during Phase 1 of the build (week 1-2 of doc 06). They're the result of human review of the customer's existing rate sheets + the CBA.

**For new unions, the Profile is auto-drafted by `ProfileDrafterAgent`** (see doc 07 §2.9), then human-polished. The drafter analyzes the CBA + any sample customer rate sheet and produces ~80% of the Profile; the human fills in the rest.

> **The Profile is the ground truth for "what fields exist."** Every other layer (CBA mining, Rate Notice extraction, validation) flows from it.

#### Source 2 — The CBA RuleManifest (formula authority)

`RuleManifest` is a structured JSON extracted from the CBA (see doc 04 schema, doc 02 §3, doc 07 §2.4). It defines:
- **Wage anchor:** which package's wage is the anchor (e.g., "Building Journeyman is the Notice value")
- **Foreman premium:** the formula (e.g., "JW + $4.50")
- **Apprentice ladder:** percentages and anchors (e.g., "Y5 = 85% of Building JW")
- **Pension exclusion:** which apprentice levels skip which fringes
- **OT formulas:** how 1.5× and 2.0× are computed
- **Fund definitions:** every fund's CBA citation (Article + section)

The RuleManifest is **derived from the CBA by `CBAMinerAgent`** running once per CBA file (cached by file hash). The agent uses Bedrock KB retrieval over the CBA, then for each rule type expected by the Profile, it searches the KB and uses Claude with a structured-output schema to extract the rule.

> **The RuleManifest is the ground truth for "how values are computed."** Every cell that's not a direct Notice value flows from a rule in the manifest.

#### Source 3 — The Rate Notice ExtractedDocument (value authority)

`ExtractedDocument` is the structured result of reading a Rate Notice (see doc 04 schema, doc 02 §2). It contains:
- **Anchor wage(s)** per zone (e.g., "Industrial Journeyman = $38.18")
- **Fringe values** with PDF citations (page, line, label-as-printed)
- **Apprentice schedule values** (when the Notice publishes them)
- **Deductions** per class (when the Notice provides per-class detail)
- **Total package** (used as a checksum)

> **The ExtractedDocument is the ground truth for "the actual dollars."** Every numeric value in the rate sheet comes either directly from here or is derived via a RuleManifest formula applied to anchor values from here.

### 1.3 How the three sources combine

```
                  Profile (schema)
                       │
                       │ "for union 704, build a rate sheet with these
                       │  13 packages, 24 columns, in this order"
                       │
                       ▼
       ┌────────── Resolver ───────────┐
       │                                │
       ▼                                ▼
RuleManifest                  ExtractedDocument
("Foreman = JW + 4.50")       ("JW Notice value = $52.32")
       │                                │
       └────────────┬───────────────────┘
                    │
                    ▼
              For each (zone, package):
                ─ Fetch wage formula from Profile
                ─ Resolve formula via RuleManifest rules
                ─ Substitute extracted values from ExtractedDocument
                ─ Apply rounding (per Profile)
                ─ Tag provenance (rate_notice / cba / derived)
                    │
                    ▼
              CanonicalRateSheet
              (the rate sheet)
```

**The Profile says what.** The RuleManifest says how. The ExtractedDocument says how much.

### 1.4 What if there's a column the Profile doesn't know about?

**Two scenarios:**

**Scenario A: Notice has a fringe value for a label not in Profile.**

Example: 704 Notice introduces a new "Family Care Fund" line not in the Profile.

- ExtractorAgent reports the value with a `_label_in_pdf` field
- Resolver tries to map the label via Profile's `notice_label_aliases`
- No match → resolver flags `unknown_fringe` with confidence 0.0
- ValidatorAgent escalates to human review queue
- Human admin updates the Profile to include the new column → Profile gets a new version → engine re-runs

**Scenario B: Profile expects a fringe but Notice doesn't include it.**

Example: Profile says `S.U.B. 704` should always be present; new Notice omits it.

- Resolver looks up the value, doesn't find it
- Profile's `fringe_schema[i].optional` flag determines behavior:
  - If `optional: true` → resolver fills 0 with `provenance: default`
  - If `optional: false` → resolver flags missing required field; validator blocks publish

**The Profile is the contract.** Mismatches between Profile and Notice are surfaced, not silently absorbed.

### 1.5 Example: 704 ground-truth flow

For the period 2026-01-01 to 2026-07-31:

| Source | What it provides | Example |
|---|---|---|
| Profile (`profile_sprinkler_704.yaml`) | Schema: 1 zone (Building), 13 packages, 24 columns | "Apprentice Class 5 wage formula: `anchor:zone:Building * 0.60`" |
| RuleManifest (`rm-704-2022-2027-v1`) | Formulas: Foreman premium schedule, apprentice ladder percentages | "Foreman = JW + $4.50 (effective 2024-08-01)" with citation Article 6 §14 |
| ExtractedDocument (from `2026.01.01.704 Rate Notice.pdf`) | Values: JW = $52.32, Pension = $7.45, etc. | "Pension Fund: $7.45" extracted from page 1 line 21 |

These three combine to produce **all 13 rows × 24 columns** of the rate sheet, with every cell traceable to one of the three sources via provenance.

---

## Section 2 — PDF → JSON → LLM (the ingestion pipeline)

### 2.1 The misconception to clear up

Reading your question — *"all this has to be sent to LLM right, so we have to convert pdf to json and send"* — the answer is: **not always, and almost never the whole document.**

Three things happen in different cases:

**Case A: PDF text extraction succeeds (~70% of Rate Notices).**
- `pdftotext` / `pdfplumber` get clean text + table structure
- A pure-Python regex/pattern parser produces `ExtractedDocument` JSON
- **No LLM call needed.** Cheap, fast, deterministic.

**Case B: PDF is image-only or text extraction quality is too low.**
- OCR (Tesseract for cheap, AWS Textract for tables) → text + cell coordinates
- Same regex parser as Case A → `ExtractedDocument`
- **Still no LLM call** if OCR confidence is high.

**Case C: All deterministic paths fail confidence threshold.**
- Now we send to LLM (Bedrock Claude Sonnet, multi-modal)
- We send the **PDF bytes directly** (Claude reads PDFs natively, no need to convert to JSON first)
- LLM returns structured JSON matching `ExtractedDocument` schema
- **This is the only case where the full PDF goes to an LLM.**

### 2.2 The two-stage architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  STAGE A: PDF → RawDocumentJSON (cheap, deterministic)                  │
│                                                                         │
│   PDF bytes                                                             │
│      │                                                                  │
│      ├── Path A.1: pdftotext + pdfplumber                              │
│      │     → text strings + table cells with coordinates                │
│      │     → if extraction quality OK, output                          │
│      │                                                                  │
│      ├── Path A.2: Tesseract OCR (for image PDFs)                       │
│      │     → text + per-token confidence                                │
│      │                                                                  │
│      └── Path A.3: Textract (for table-heavy scans)                     │
│            → structured cells with confidence                            │
│                                                                         │
│   Output: RawDocumentJSON                                               │
│   {                                                                     │
│     "pages": [                                                          │
│       { "page": 1, "text": "...", "tables": [...], "confidence": ... }  │
│     ],                                                                  │
│     "extraction_method": "pdftotext"                                    │
│   }                                                                     │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STAGE B: RawDocumentJSON → ExtractedDocument                           │
│                                                                         │
│   ┌── Path B.1: Pattern parser (deterministic)                          │
│   │     - Apply regex from Profile.fringe_schema.notice_label_aliases   │
│   │     - Extract labeled-money table                                    │
│   │     - Output: ExtractedDocument with per-field confidence            │
│   │                                                                      │
│   └── Path B.2: LLM-aided extraction (when B.1 confidence low)          │
│         - Send RawDocumentJSON to Claude with structured-output schema  │
│         - OR send raw PDF bytes (multi-modal) if RawDocumentJSON garbled│
│         - Output: ExtractedDocument validated by Pydantic                │
│                                                                         │
│   Output: ExtractedDocument (canonical schema)                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 Sample data flow — 537's 1-page Rate Notice (Path A + Path B.1, no LLM)

**Step 1 — PDF bytes in S3**
```
s3://laboraid-inputs/.../2026.03.01.537 Rate Notice.pdf  (220 KB, 1 page, text PDF)
```

**Step 2 — pdftotext output (RawDocumentJSON)**
```json
{
  "pages": [{
    "page": 1,
    "text": "PIPEFITTERS' ASSOCIATION\nLocal Union 537\n...\n\nTo Whom It May Concern:\n\nBelow is a breakdown of the new wage and fringe package effective March 1, 2026 through August 31, 2026 in the Joint Agreement between the New England Mechanical Contractors Association, Air Conditioning Refrigeration Contractors of Boston and Pipefitters Association Local Union No. 537.\n\n3-1-2026 to 8-31-2026\n\nWages                  $    70.58\nLU 537 Pension         $    14.00\nHealth & Welfare       $    13.95\nAnnuity                $     9.55\n...",
    "tables": [],
    "char_confidence_avg": 0.99
  }],
  "extraction_method": "pdftotext"
}
```

**Step 3 — Pattern parser (Path B.1) produces ExtractedDocument**

The parser uses the Profile's expected fringe labels:
```python
# From profile_pipefitter_537.yaml
fringe_schema:
  - name: Pension Local
    notice_label_aliases: ["LU 537 Pension", "Pension"]
  - name: Health & Welfare
    notice_label_aliases: ["Health & Welfare", "H&W", "H & W"]
  ...
```

For each expected fringe, regex finds the value in the raw text:
```python
for fringe in profile.fringe_schema:
    for alias in fringe.notice_label_aliases:
        match = re.search(
            rf'{re.escape(alias)}\s+\$?\s*(\d+\.\d{{2}})',
            raw_doc.pages[0].text
        )
        if match:
            extracted.fringes[fringe.name] = {
                "value": float(match.group(1)),
                "_page": 1,
                "_label_in_pdf": alias,
                "_confidence": raw_doc.pages[0].char_confidence_avg
            }
```

Output:
```json
{
  "extraction_method": "pdftotext_with_pattern_parser",
  "extraction_confidence_overall": 0.99,
  "anchor_wages": { "Journeyman": { "value": 70.58, "_page": 1, "_confidence": 0.99 } },
  "fringes": {
    "Pension Local": { "value": 14.00, "_page": 1, "_label_in_pdf": "LU 537 Pension", "_confidence": 0.99 },
    "Health & Welfare": { "value": 13.95, "_page": 1, "_confidence": 0.99 },
    ...
  },
  "total_package_printed": 113.00
}
```

**No LLM was called. Total cost: ~$0.0001 (Lambda compute only).**

### 2.4 When the LLM IS called (Path B.2)

For 704's image-only Rate Notices, the deterministic path fails (OCR confidence might be 0.78 — below the 0.85 threshold). Then we invoke `ExtractorAgent` (Strands):

```python
# Inside ExtractorAgent — see doc 07 §2.3
@tool
def extract_with_claude_multimodal(s3_key: str, profile_hint: dict) -> dict:
    pdf_bytes = s3.get_object(s3_key)
    profile_aliases = profile_hint["fringe_schema"]

    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-sonnet-4-6-v1:0",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8000,
            "system": EXTRACT_RATE_NOTICE_SYSTEM_PROMPT,
            "messages": [{
                "role": "user",
                "content": [
                    # PDF sent directly — Claude reads it natively
                    {"type": "document",
                     "source": {"type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.b64encode(pdf_bytes).decode()}},
                    {"type": "text",
                     "text": build_extraction_prompt(profile_aliases)}
                ]
            }]
        })
    )
    return parse_extracted_document(response)
```

The PDF goes to Claude **as a document**, not as JSON. Claude reads the PDF (text + images + layout) and returns structured JSON. We don't manually convert the PDF first.

### 2.5 What about 30-page CBAs?

**CBAs are NOT sent to the LLM in one shot.** That would be:
- Expensive (~$0.30 per call for Claude Sonnet on a 35-page CBA)
- Wasteful (we only need ~10 specific rules out of the document)
- Less accurate (long-context tasks degrade)

Instead, **CBAs are chunked into a Bedrock Knowledge Base** (S3 Vectors-backed) — see doc 03 §3.

### 2.6 CBA ingestion pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CBA INGESTION (one-time per CBA)                                       │
│                                                                         │
│   CBA PDF                                                               │
│      │                                                                  │
│      ├── pdftotext → raw text                                           │
│      │                                                                  │
│      ├── Article structure detector                                     │
│      │     - Regex for "ARTICLE \d+", "Article \d+ -? \w+"              │
│      │     - Section detector "§\d+", "Section \d+", "\d+\."           │
│      │                                                                  │
│      ├── Per-section chunking (with metadata)                            │
│      │     {chunk_text, article: "Article 6", section: "§14",           │
│      │      page: 4, union_local: 704, scope: null}                     │
│      │                                                                  │
│      ├── Embedding (Titan Embed v2)                                     │
│      │                                                                  │
│      └── Store in Bedrock Knowledge Base (S3 Vectors backend)           │
│                                                                         │
│   Result: ~80-150 chunks per CBA, indexed by metadata                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.7 CBA rule extraction via retrieval

When `CBAMinerAgent` needs to extract a rule (say, the Foreman premium):

```python
# 1. Search KB for relevant chunks (filtered to this union's CBA)
passages = bedrock_kb.retrieve(
    knowledgeBaseId=KB_ID,
    query="Foreman wage premium dollar amount over Journeyman",
    filter={"andAll": [
        {"equals": {"key": "union_local", "value": "704"}},
        {"equals": {"key": "scope", "value": "NEMCA"}}  # if applicable
    ]},
    numberOfResults=5
)
# Returns 5 chunks (each a few hundred tokens), all relevant to Foreman premium

# 2. Send only those 5 chunks to Claude (not the whole CBA)
response = claude.invoke(
    prompt=f"""Extract the Foreman premium rule from these CBA passages.
              Return JSON matching schema {FOREMAN_PREMIUM_SCHEMA}.
              Passages: {passages}""",
    structured_output_model=ForemanPremiumRule
)
# Response: {type: "flat_dollars", schedule: [{effective: "2022-08-01", amount: 4.00}, ...],
#            cba_citation: {article: "Article 6", section: "§14", page: 4}}
```

For 11 rule types × 5 passages each = 55 small Claude calls, each ~1000 tokens input. Total cost per CBA: ~$0.10-0.50.

**Compare to sending the whole 35-page CBA to Claude per rule:** 11 × 35 pages × 4× more tokens = ~$3-10 per CBA.

**Cost saving by KB-driven retrieval: 10-30×.** Plus better accuracy because each query gets focused context.

### 2.8 Summary of when LLM is involved

| Stage | Stage A (deterministic) | Stage B (LLM) |
|---|---|---|
| Rate Notice (text PDF, ~70% of cases) | pdftotext + regex parser | none |
| Rate Notice (image PDF) | OCR (Tesseract/Textract) | only if OCR confidence <0.85 |
| Rate Notice (totally unparseable) | — | full multi-modal Claude (PDF in, structured JSON out) |
| CBA (one-time) | pdftotext + chunker + embedder | per-rule retrieval + Claude (small focused prompts) |
| Validation outliers | deterministic checksums | only when an anomaly needs explanation |
| Citation lookup | KB retrieval | Claude only to rank/synthesize the answer |

We **never** dump a 30-page document to Claude in a single shot. Always chunked, focused queries.

---

## Section 3 — How the Agent Knows It Did the Right Thing

### 3.1 The fundamental challenge

LLMs hallucinate. Tool use can fail silently. OCR can be wrong. **Without active self-verification, the engine could publish wrong rates that propagate to remittance dollars.**

We use **4 layers of verification**, each catching different failure modes. A cell only gets `auto_publish: true` if all 4 layers agree.

### 3.2 Layer 1 — Per-cell extraction confidence

Every extracted value has a confidence score. Sources:

| Extraction method | Where confidence comes from |
|---|---|
| pdftotext + regex | char_confidence from PDF rendering quality (usually 0.99 for born-digital PDFs) |
| Tesseract OCR | per-token confidence from Tesseract output |
| Textract | Textract returns per-cell confidence (often 0.85-0.99) |
| Claude multi-modal | Claude self-reports confidence per field in its structured output (we prompt it to do so) |

**Threshold:** Profile-configured `min_confidence_for_auto_publish` (default 0.95).

A cell with confidence 0.78 doesn't auto-publish, no matter what.

### 3.3 Layer 2 — Deterministic checksums

These are pure math, no LLM needed.

#### Total package checksum
Every Rate Notice prints a "Total Package" amount (e.g., `$113.00` for 537, `$87.52` for 704). The engine computes:

```python
computed_total = wage_jw + sum(fringes_jw) + industry_promotion
assert abs(computed_total - notice.total_package_printed) < 0.05
```

If this fails, **something is wrong** — either we missed a fringe, or we extracted the wrong value somewhere. Validation blocks publish and routes to human review.

For 537 example:
```
70.58 (Wage) + 14.00 (Pension Local) + 13.95 (H&W) + 9.55 (Annuity)
+ 0.25 (Industry) + 2.17 (Education) + 2.20 (Labor/Mgt) + 0.30 (UA Pension)
= 113.00 ✓ matches printed Total Package
```

#### Apprentice percentage cross-check
For each apprentice row, the rate sheet has both:
- A computed wage (from formula: `JW × percent`)
- The Notice's pre-computed apprentice value (when published)

```python
for apprentice_row in rate_sheet.apprentice_rows:
    computed = anchor_wage * apprentice_row.percent
    published = extracted.apprentice_schedule[apprentice_row.year].wage
    assert abs(computed - published) < 0.05  # rounding tolerance
```

Catches: Profile percentage wrong, apprentice anchor wrong, rounding rule wrong.

#### Range checks per column
```python
ranges = {
    "wage": (5.00, 200.00),
    "Pension": (0.00, 30.00),
    "Health & Welfare": (0.00, 30.00),
    "Apprentice ratio": (0.30, 1.00),
}
for col, (lo, hi) in ranges.items():
    for cell in column(col):
        assert lo <= cell.value <= hi, f"{col} value {cell.value} out of range"
```

Catches: catastrophic OCR errors (e.g., $7.45 read as $74.50).

### 3.4 Layer 3 — Cross-source agreement

Some values appear in **multiple sources**. The engine checks they agree:

| Value | Source 1 | Source 2 | Action if disagree |
|---|---|---|---|
| Apprentice Y3 wage | Notice "3rd year - 60%" line | Computed: JW × 0.60 from CBA Article 5 | Flag if diff > $0.05 |
| Foreman wage | Notice (when included) | Computed: JW + premium from CBA | Flag if disagree |
| Total Package | Notice's printed total | Sum of components | Block publish if mismatch (Layer 2) |
| OT 1.5× | Notice (when published, e.g., 704) | Computed via Profile formula | Flag the rate-sheet/notice discrepancy (704's $2.43 issue) |
| H&W package | Notice "H&W" line | Profile-derived: HW Notice − RESA | Verify subtraction yields valid value |

When Notice and CBA-derived values **agree**, confidence is bumped up. When they **disagree**, the engine surfaces both with provenance and routes to human review. We never silently choose one.

### 3.5 Layer 4 — Year-over-year sanity (with Article-20 awareness)

When publishing a new period, the engine compares to the last published period for the same union:

```python
for column in ["wage", "Pension", "Health & Welfare", ...]:
    delta_pct = (current[column] - prior[column]) / prior[column]

    if abs(delta_pct) > 0.20:  # >20% change
        # Check if explained by uniformity adjustment
        if is_uniformity_adjustment(current_period, prior_period):
            # Article 20: package total unchanged; reallocation expected → OK
            log("YoY delta {delta_pct} explained by uniformity adjustment")
        else:
            # Suspicious; ask Claude
            sanity = claude.explain_anomaly(
                current_value=current[column],
                prior_value=prior[column],
                notice_text=extracted.header_text,
                cba_text=manifest.relevant_section(column)
            )
            if not sanity.explained:
                flag_for_review(column, reason=sanity.reasoning)
```

**Example: 704's Jan 2026 wage decreased $1.25 from Aug 2025.**
- Layer 4 flags: wage change −2.4% (within threshold), no flag
- But suppose Pension changed +35% with a wage-decrease — Layer 4 would flag, then call Claude:
  - "Total package was $87.52 (Aug 2025) and $87.52 (Jan 2026). Wage dropped $1.25; H&W +$1.20; Pension +$0.05. This is an Article 20 §81 uniformity adjustment — net-zero. **Explained.**"
- Claude returns `explained: true` → publish proceeds, with note in publish notification

### 3.6 The agent's prompt asks itself "did I do this right?"

Beyond the 4 deterministic layers, the agents themselves are prompted with self-check responsibilities:

**ExtractorAgent system prompt (excerpt):**
```
After extraction, verify:
1. Sum of fringes + wage = printed Total Package (within $0.05)
2. Every required field per Profile is populated
3. No value is implausibly high or low
If any check fails, escalate by calling extract_with_claude_multimodal as fallback.
Do NOT return success unless all checks pass.
```

**CBAMinerAgent system prompt (excerpt):**
```
Before declaring the RuleManifest complete, verify:
1. Every required rule type per Profile is present
2. Every rule has a CBA citation (Article + section)
3. Any "as per Area Practice" or similar ambiguity is flagged in ambiguities_flagged
4. Cross-reference against existing Profile (if any) to detect contract changes
```

**ValidatorAgent system prompt (excerpt):**
```
Before approving publish, verify:
1. Total package checksum passes
2. All apprentice cross-checks pass
3. All range checks pass
4. YoY delta within tolerance OR explained by uniformity rule
5. Every cell has confidence ≥ Profile.min_confidence_for_auto_publish
If any fails, route to review queue with specific reason.
```

### 3.7 Steering reinforces self-checks

This is where Strands **steering** earns its keep. The `SteeringHandler` enforces these checks at the harness level — even if the agent's prompt fails to follow them, steering blocks the bad action and tells the agent to retry.

Example from doc 07 §2.3:

```python
class ExtractorSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        # Don't claim done if checksum hasn't been validated
        if tool_use["name"] == "return_extraction_complete":
            if not agent.checksum_validated:
                return Guide(reason="Run validate_total_package_checksum before declaring done.")
        return Proceed(reason="OK.")
```

If ExtractorAgent tries to return without checksum validation, steering returns a `Guide` message → agent sees: *"Guide: Run validate_total_package_checksum before declaring done."* → agent calls the checksum tool → if it passes, retries the return-complete call.

This is **enforcement at the loop level**, not "we hope the prompt was followed."

### 3.8 The combined verification flow

```
Per-cell value extracted
    │
    ├── Layer 1: confidence check (≥ 0.95?)
    │     ├── PASS: continue
    │     └── FAIL: route to review
    │
    ├── Layer 2: deterministic checksums
    │     ├── total_package: PASS
    │     ├── apprentice_pct: PASS
    │     ├── range_check: PASS
    │     └── one or more FAIL → route to review
    │
    ├── Layer 3: cross-source agreement
    │     ├── Notice vs CBA-derived: AGREE (or N/A)
    │     ├── If DISAGREE: flag, surface both, route to review
    │     └── (no agent autonomy to "pick one")
    │
    ├── Layer 4: YoY delta sanity
    │     ├── Within threshold: PASS
    │     ├── Outside threshold: invoke explain_anomaly (Claude)
    │     └── If unexplained: route to review
    │
    └── ALL 4 PASS → AUTO_PUBLISH (with provenance)
       ANY FAIL  → HUMAN REVIEW (with specific reason)
```

This is **defense in depth.** No single point of failure. An agent hallucination has to evade all 4 layers to silently publish wrong data — extremely unlikely.

---

## Section 4 — Onboarding a brand-new union (the bootstrap problem)

### 4.1 The scenario

LaborAid signs a new contract with Pipefitters Local 38 (a union we've never seen). Customer ships us:
- 1 CBA (40 pages, contract term 2024-2029)
- 4 Rate Notices (one per period from 2024-2026)
- Optionally: 1 sample rate sheet they've been hand-building

We've never seen Local 38 before. **No Profile exists. No Bedrock KB has its CBA. The engine has nothing.**

### 4.2 Why we can't just "run the pipeline"

The pipeline assumes a Profile exists. Without one:
- ExtractorAgent doesn't know what fringe labels to look for
- CBAMinerAgent doesn't know what rules to extract
- Resolver has no schema to materialize
- Renderer has no column list

So onboarding a new union is a **separate workflow** that runs once per union, before the production pipeline.

### 4.3 The onboarding workflow

```
1. Customer ships input pack to S3 onboarding bucket
       │
       ▼
2. ProfileDrafterAgent runs (Strands agent on AgentCore Runtime)
       │
       │  Inputs:
       │    - CBA PDF (or several if multi-scope union)
       │    - Sample rate sheet xlsx (if customer has one)
       │  Tools:
       │    - mine_cba (calls CBAMinerAgent in "discovery mode")
       │    - analyze_existing_rate_sheet
       │    - compare_to_known_profiles (similarity to 5 POC unions)
       │    - write_profile_draft
       │
       ▼
3. Outputs: profile_local_38_DRAFT.yaml + DRAFT_NOTES.md
       │
       │  Profile draft populated for ~80% of fields
       │  Marked "TODO_HUMAN: <reason>" for fields it couldn't determine
       │  Examples of TODO_HUMAN:
       │    - "Sheet naming convention — sample rate sheet not provided"
       │    - "Apprentice on Industrial job rule (CBA mentions but unclear if rate sheet models)"
       │
       ▼
4. Human onboarding specialist reviews + completes the Profile
       │  Time: 1-3 hours typically
       │  Tools: Profile editor UI (form-based + raw YAML view)
       │
       ▼
5. Run BackfillAgent on historical Notices
       │  - For each historical Rate Notice, run the normal pipeline
       │  - Compare output rate sheet against customer's existing rate sheet
       │  - Flag any cell-level discrepancies
       │
       ▼
6. Human reviews discrepancies
       │  - Each discrepancy gets resolved: either fix Profile, or accept as known difference
       │  - Build up "known issues" list (similar to the 25 open questions for POC)
       │
       ▼
7. Onboarding complete: Local 38 is "live"
       │  - Future Rate Notices auto-process via the production pipeline
       │  - Cadence reminder set per Profile (e.g., "expect next Notice around 2027-01-01")
```

**Target time:** 3 business days from input pack to first production rate sheet.

### 4.4 ProfileDrafterAgent in detail

This is the bootstrap agent (doc 07 §2.9). Its job: **infer a Profile from the input pack.**

#### Discovery mode — what the agent does

```
1. Identify the union basics:
   - Local number (from filenames + CBA header)
   - Trade (CBA title page)
   - Parent international (CBA title page)
   - Contract term (CBA Article 1)
   - Contractor associations (CBA preamble)

2. Run CBAMinerAgent in "discovery mode":
   - Don't expect a specific list of rules — extract everything that looks like a rule
   - Pattern-match for: "wage", "premium", "fringe", "fund", "apprentice", "overtime"
   - Use Claude to identify rule types semantically
   - Build a candidate RuleManifest

3. From the customer's sample rate sheet (if provided):
   - Read column headers (this gives us the fringe schema)
   - Read rows (this gives us the package list)
   - Read formulas (gives us hints at apprentice ladders, foreman premiums)
   - Cross-check against CBA-derived rules

4. Compare to known union profiles:
   - Sprinkler unions cluster (704, 821, 483, 281 → similar shape)
   - Pipefitter unions cluster (537, others)
   - Find the most similar known Profile, use as starting template
   - Override/add fields based on the new union's specifics

5. Generate Profile draft YAML
   - Populate fields the agent is confident about
   - Mark uncertain fields as TODO_HUMAN with reason

6. Write DRAFT_NOTES.md explaining the agent's reasoning:
   - "I inferred 5 packages because the CBA's apprentice section says '5-year apprenticeship'..."
   - "Sample rate sheet has 12 columns; Profile reflects all 12; Industry Promotion column purpose unclear"
   - "TODO_HUMAN: confirm whether Y1 zeros only Pension or also Annuity (CBA ambiguous)"
```

#### What the agent does NOT do

- It does NOT decide ambiguous cases on its own. It flags them.
- It does NOT publish anything. The Profile draft is for human review.
- It does NOT trust any single source — it cross-references CBA, sample sheet, and known-similar profiles.
- It does NOT handle exotic structures it can't recognize. If the new union has something truly unique, it surfaces a comprehensive TODO_HUMAN section.

### 4.5 Concrete example — onboarding Pipefitters Local 38

**Input pack:**
```
From Customer/CBAs/Pipefitter/38/2024-2029.38 CBA.pdf
From Customer/CBAs/Pipefitter/38/2024.07.01.38 Rate Notice.pdf
From Customer/CBAs/Pipefitter/38/2025.01.01.38 Rate Notice.pdf
... (8 more historical Notices)
From Customer/Rate Sheets/Pipefitter/38/sample_rate_sheet.xlsx (customer's hand-built)
```

**ProfileDrafterAgent runs:**
1. Identifies: Trade=Pipefitter, Local=38, Parent=UA, Contract=2024-2029, Contractor Assoc=NEMCA-equivalent
2. Mines CBA: extracts wage anchor ($72.00), foreman premium ($3.50), apprentice ladder (5 years 50/55/65/75/85%), 12 funds with citations
3. Reads sample rate sheet: 1 zone "Building", 8 packages, 23 columns (including familiar ones + 1 new "Mentorship Fund" not seen before)
4. Compares to 537 Profile (closest match — both are East Coast UA Pipefitter): 80% schema overlap; differences noted
5. Generates `profile_pipefitter_38_DRAFT.yaml` with TODO_HUMAN markers for:
   - "Mentorship Fund" — new column, customer to confirm canonical label
   - Sheet naming convention (sample uses end-date; confirm)
   - Apprentice rounding rule (sample uses $0.01; CBA silent — confirm)
   - Multi-Joint-Agreement scope (1 CBA found; confirm no others)

**Human onboarding specialist reviews:**
- Reviews the draft + DRAFT_NOTES.md (~30 minutes)
- Resolves the TODO_HUMAN markers via customer Q&A (~1 hour)
- Saves Profile v1.0
- Triggers BackfillAgent

**BackfillAgent runs the pipeline on all 10 historical Notices:**
- Produces 10 rate sheets
- Compares each to the customer's sample (if for the same period)
- Flags 3 cells that disagree → review queue

**Human resolves discrepancies:**
- 2 are customer rate-sheet errors (engine sides with PDF + CBA) — accept engine output
- 1 is a Profile gap (CBA mentions a fringe the Profile didn't capture) — fix Profile, re-run
- All published

**Local 38 is now LIVE.** Future Rate Notices auto-process.

**Total elapsed time: 2 business days.** (1 day for ProfileDrafter + human polish, 1 day for backfill + reconciliation.)

### 4.6 What if the new union is REALLY weird?

Some hypothetical exotic case: the union uses a 4-class apprentice system based on union seniority not duration. CBA is in Spanish. Rate Notices come as scanned faxes.

**ProfileDrafterAgent will:**
- Recognize Spanish (it's not in our supported languages list yet) → escalate
- Recognize the 4-class system as different from any of the 5 POC patterns → mark TODO_HUMAN
- Recognize the fax-quality scans → recommend OCR upgrade or manual entry first

**Human path:**
- Decide whether to extend the engine to support this union (requires 1-2 weeks of new code)
- OR mark this union as "manual entry only" (customer keeps using their existing Excel)

**The engine never silently misrepresents an exotic case as a standard one.** It surfaces the gap for explicit decision.

### 4.7 Onboarding metrics we'll track

For each new union onboarded, we record:
- Time from input pack received to first production rate sheet
- Number of TODO_HUMAN markers in draft Profile (signal of how exotic the union is)
- Number of cell-level discrepancies during backfill reconciliation
- Number of Profile revisions during onboarding (signal of how much human iteration was needed)

Over time, as ProfileDrafterAgent's semantic memory grows (with patterns from prior onboardings), TODO_HUMAN counts and revision counts should drop. Onboarding gets faster as the system learns what construction-trade unions look like.

---

## Section 5 — How does this all map to existing docs?

To make sure nothing's lost — here's where each topic lives now:

| Topic | Doc | Section |
|---|---|---|
| **Ground truth — Profile** | doc 04 | §4 (Profile YAML schema) |
| **Ground truth — RuleManifest** | doc 04 | §3 |
| **Ground truth — ExtractedDocument** | doc 04 | §2 |
| **PDF→JSON deterministic path** | doc 02 | §2 (Extract stage paths A and B) |
| **PDF→LLM (Claude multi-modal)** | doc 02 | §2 (Path C); doc 03 | §1 |
| **CBA chunking + KB** | doc 03 | §3 (Knowledge Base); this doc §2.6 |
| **CBA rule mining via retrieval** | doc 02 | §3; doc 07 | §2.4 (CBAMinerAgent); this doc §2.7 |
| **Confidence scoring** | doc 02 | §2.4; doc 07 | §2.3 hooks |
| **Total package checksum** | doc 02 | §5; this doc §3.3 |
| **Apprentice cross-check** | doc 02 | §5.2 |
| **Range checks** | doc 02 | §5.3 |
| **YoY delta sanity** | doc 02 | §5.4 |
| **Article-20 awareness** | doc 02 | §5.5; this doc §3.5 |
| **LLM sanity review** | doc 02 | §5; doc 07 | §2.5 (ValidatorAgent) |
| **Strands steering for self-checks** | doc 07 | §5; this doc §3.7 |
| **Onboarding flow** | doc 06 | Phase 1; doc 07 | §2.9 (ProfileDrafterAgent); this doc §4 |
| **Backfill** | doc 07 | §2.10 (BackfillAgent) |

### Why this doc exists separately

The above topics ARE in the prior docs, but they're scattered. The questions you asked — *"how does the agent know it did the right thing?"*, *"how does the engine handle a 30-page PDF?"*, *"how do we know what the rate sheet should contain?"* — cut across multiple stages and need a **single doc that answers them end-to-end**.

That's this doc.

---

## Section 6 — TL;DR for client conversation

If LaborAid asks any of your four questions in a meeting, the soundbite answers:

**Q: How does the engine know what to extract?**
> The Profile (per-union YAML) is the contract for "what columns the rate sheet has." The CBA RuleManifest defines "how each value is computed." The Rate Notice provides "the actual dollars." All three combine deterministically. For new unions, the Profile is bootstrapped by an AI agent (`ProfileDrafterAgent`) and human-polished — typically 1-3 hours of human time per new union.

**Q: How does the LLM see a 30-page PDF?**
> It usually doesn't see the whole thing. CBAs are chunked once into a Bedrock Knowledge Base (one-time per CBA). For each rule we need to extract, we retrieve only the 3-5 relevant chunks and send those to Claude — focused, cheap (~$0.01 per rule), accurate. The whole 30-page CBA is never sent in one shot. Rate Notices are usually 1-15 pages and processed by deterministic parsers; only when those fail confidence checks does the full Notice go to multi-modal Claude.

**Q: How does the agent verify it did the right thing?**
> Four-layer defense:
> 1. **Per-cell confidence** (extractor's self-reported certainty)
> 2. **Deterministic checksums** (sum of fringes = printed Total Package; apprentice % cross-check; range checks)
> 3. **Cross-source agreement** (Notice value vs CBA-derived value — must agree or be flagged)
> 4. **Year-over-year sanity** (with awareness of Article-20 zero-sum reallocations)
>
> Any failed layer → routed to human review queue, never silently published. Plus Strands' steering pattern enforces self-checks at the agent loop level — the agent literally cannot return "done" without running checksums.

**Q: What about brand-new unions we've never seen?**
> A separate "onboarding workflow" runs once. Customer drops input pack (CBA + Rate Notices + sample rate sheet if any). The `ProfileDrafterAgent` analyzes everything, drafts a Profile (~80% complete), human polishes (1-3 hours), then `BackfillAgent` processes historical periods to validate. Once the union is "live", future Rate Notices auto-process via the normal pipeline. Target: 3 business days from input pack to first production rate sheet.

---

## Bottom line

The four questions you raised are exactly the questions that determine whether this engine is trustworthy in production. The answers — Profile-as-ground-truth, chunked KB retrieval (not full-doc dumps), 4-layer self-verification, and explicit onboarding workflow for unknowns — are foundational design decisions, not afterthoughts.

The cost of getting these wrong is **silently publishing wrong rates that propagate to dollars paid to trustees and workers.** The cost of getting them right is the engineering investment in the Profile schema, the KB ingestion pipeline, the validation suite, and the onboarding agent — all of which are scoped in doc 06's 8-week build plan.

This is the difference between *"an LLM that reads PDFs"* and *"a production rate-data pipeline LaborAid can defend in court."*
