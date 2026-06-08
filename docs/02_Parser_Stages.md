# Parser Stages — Detailed Design

**Document:** 02 of 7 in `docs/`
**Read after:** `01_Engine_Architecture.md`. This doc zooms into each stage of the pipeline.

> **Status update (2026-06-05) — see [`STATUS.md`](STATUS.md).** The deterministic
> kernel adds a **Stage 6 — completeness-coverage critic** (`kernel/pipeline/critic.py`,
> advisory): after evaluate, it scans the CBA/notice text for the vocabulary of a
> ratesheet (classifications, zones, fund names) and flags any missing from the
> output. Also: derived multiplier columns now use Decimal-multiply half-up
> (`canonical.model.rmul`); the evaluator's row key includes the indenture-date
> columns (281/821 cohorts); and `run.py --min-accuracy` gates on sourced accuracy.

---

## Stage map

| # | Stage | Compute | Latency target | What's deterministic | What uses AI |
|---|---|---|---|---|---|
| 1 | Ingest & Classify | Lambda | <2s | Filename pattern, folder structure, magic bytes | Claude Haiku fallback for ambiguous files |
| 2 | Extract | Lambda or Fargate | <90s text, <300s image | pdftotext, pdfplumber, Camelot, Textract | Claude Sonnet multi-modal for messy/image PDFs |
| 3 | CBA Rule Mining (lazy/cached) | Fargate | <600s (one-time per CBA) | Schema validation, regex checks | Bedrock Agent + KB + Claude Sonnet |
| 4 | Rule Resolution | Lambda | <10s | Profile-driven formula evaluation | None (deterministic by design) |
| 5 | Validation | Lambda | <5s | Checksums, range checks, YoY delta | Claude Sonnet for sanity review of suspicious cells |
| 6 | Render & Publish | Lambda | <15s | xlsx generation (openpyxl), CSV writer | None |

---

## Stage 1 — Ingest & Classify

### Purpose
Take a raw file from S3, identify what it is, and route it down the right pipeline path.

### Inputs
- S3 key of uploaded file
- Optional: tenant context, expected union (when admin manually associates)

### Outputs (`ClassificationResult` JSON)
```json
{
  "file_id": "uuid",
  "s3_key": "laboraid/Sprinkler/704/2026-07-01/2026.07.01.704 Rate Notice.pdf",
  "format": "pdf_text",  // pdf_text | pdf_image | pdf_mixed | docx | doc | xlsx | csv | image
  "document_type": "rate_notice",  // cba | rate_notice | wage_sheet | apprentice_wage_sheet | reference | unknown
  "tenant": "laboraid",
  "trade": "Sprinkler",
  "union_local": 704,
  "scope": null,  // for 537-style multi-scope unions: "NEMCA" | "NEMSCA"
  "effective_period": { "start": "2026-07-01", "end": null },
  "page_count": 1,
  "size_bytes": 287452,
  "content_hash": "sha256:abc...",
  "bundle_id": null,  // if part of a multi-file bundle
  "classification_confidence": 0.98,
  "classification_path": "deterministic"  // or "ai_assisted"
}
```

### Implementation
**Step 1.1 — Detect file format**

Use a sequence of cheap checks:
- File extension (`.pdf`, `.docx`, `.doc`, `.xlsx`, `.csv`, `.jpg`, `.png`)
- Magic bytes (verify the extension is honest)
- For PDFs: try `pdftotext -layout` → if extracted text length >100 chars, it's `pdf_text`; else `pdf_image`
- For PDFs with mixed: `pdf_mixed` (some pages text, some scanned — rare but observed)

**Step 1.2 — Detect document type**

First try **deterministic** patterns:
```
filename matches:
  ^(\d{4}\.\d{2}\.\d{2})\.(\d+)\s+(Rate Notice|Wage Notice|Wage Rate Notice|Wage Sheet|Wage Rate Sheet)\.(pdf|doc|docx)$
  → document_type = rate_notice
  → effective_start parsed from prefix
  → union_local parsed from middle group

filename matches:
  ^(\d{4}\.\d{2}\.\d{2}-?\d{4}\.\d{2}\.\d{2})\.(\d+)\s+CBA\.(pdf|doc|docx)$
  → document_type = cba
  → contract_term parsed from prefix

filename matches:
  ^.*Apprentice Wage Sheet.*Indentured\s+(After|Prior).*\.pdf$
  → document_type = apprentice_wage_sheet (281-style bundle member)
  → indenture_bucket parsed from "Indentured After/Prior X"

filename matches:
  ^.*(Articles|Fund Addresses).*$
  → document_type = reference
```

If no pattern matches, **AI fallback**:
- Send first page (rendered as image if PDF, or extracted text) to Claude Haiku
- Prompt: *"Identify this document as one of: cba, rate_notice, apprentice_wage_sheet, reference, unknown. Also identify union local number and effective date if present. Return JSON."*
- Cheap (~$0.001 per file) and accurate for the 5% of files that don't match patterns

**Step 1.3 — Detect union local + period**

Three signals must agree (or escalate to human):
1. **Filename:** parse local number and date from filename
2. **Folder structure:** `From Customer/CBAs/Sprinkler/704/...` → trade=Sprinkler, local=704
3. **Content:** content of page 1 should mention "Local 704" and effective date

If all three agree → high confidence. If 2/3 → medium confidence + flag. If 1/3 or none → AI-assisted classification or human review.

**Step 1.4 — Detect bundle membership**

Some Rate Notices arrive as multiple files for the same effective date (281: 4 files; 704: sometimes uploaded as separate per-class PDFs).

Bundle detection logic:
- Group files in S3 prefix `{tenant}/{trade}/{local}/{effective_date}/`
- If 2+ files with same `(union, date)` and types in `{rate_notice, wage_sheet, apprentice_wage_sheet}` → bundle
- Generate `bundle_id` (UUID), tag all members

Step Function later joins bundle members into one logical extraction.

**Step 1.5 — Persist**

Write `ClassificationResult` JSON to:
- DynamoDB `laboraid-files` (for query)
- S3 manifests bucket (for archival)

### Failure modes
| Failure | Handling |
|---|---|
| Unknown file format | Manual review queue; admin classifies |
| Filename doesn't match any pattern | AI fallback (Haiku) |
| Content contradicts filename (e.g., filename says 704 but content mentions 821) | High-priority flag; human review |
| Bundle members have inconsistent dates | Flag and notify admin |

---

## Stage 2 — Extract

### Purpose
Convert PDF (or other format) into a structured `ExtractedDocument` JSON regardless of format.

### Inputs
- `ClassificationResult` from Stage 1
- The file itself in S3

### Outputs (`ExtractedDocument` JSON)

For a Rate Notice:
```json
{
  "extraction_id": "uuid",
  "source_file_id": "uuid",
  "extraction_method": "pdftotext",  // pdftotext | textract | tesseract | claude_multimodal
  "extraction_confidence": 0.97,
  "document_type": "rate_notice",
  "union_local": 704,
  "effective_period": { "start": "2026-07-01", "end": null },
  "header_text": "This is to notify you of the money change in the Contract, effective July 1, 2026.",
  "raw_text": "...full text dump...",
  "anchor_wages": {
    "Journeyman": { "value": 53.92, "confidence": 0.99, "page": 1, "line": 11 },
    "Foreman": { "value": null, "implied_by_rule": "+$4.50 over Journeyman" }
  },
  "apprentice_schedule": [
    { "class": 1, "wage": 21.57, "page": 3, "confidence": 0.97 },
    { "class": 2, "wage": 24.26, "page": 4, "confidence": 0.97 },
    ...
  ],
  "fringes": {
    "Health & Welfare": { "value": 13.95, "confidence": 0.99, "page": 1, "line": 17, "label_in_notice": "H & W" },
    "RESA": { "value": 1.35, "confidence": 0.99, "page": 1, "line": 18, "label_in_notice": "RESA" },
    "Pension": { "value": 7.45, "page": 1, "line": 21, "confidence": 0.99 },
    "SIS": { "value": 11.50, "page": 1, "line": 23, "confidence": 0.99, "label_in_notice": "Sprinkler Fitters & Apprentices Local 704 Defined Contribution Pension Fund" },
    ...
  },
  "deductions": { ... },
  "vacation_options": [0, 1, 2, 3, 4, 5],  // for unions with vacation options
  "ot_rates_published": { "1.5x": 78.40, "2.0x": 102.85 },  // when Notice publishes them (704 yes, 537/821 no)
  "total_package_printed": 87.52,  // for checksum validation
  "rule_text_fragments": [
    "Foreman - $4.50 over -",  // Notice may include rule snippets
    "Apprentice hourly wage rates based on ($53.92 JY wage)..."
  ]
}
```

For a CBA, the structure is different — see `RuleManifest` in Stage 3.

### Three extraction paths

**Path A — Text-PDF (cheap, fast, deterministic)**

Used for ~70% of Rate Notices observed.

1. Run `pdftotext -layout` to get column-preserved text
2. For tabular Rate Notices (e.g., 483's multi-row table), run `pdfplumber` or `Camelot` to extract structured tables
3. Apply regex/grammar parsers to identify labeled-money pairs:
   ```python
   # Examples of patterns:
   r'(?P<label>[A-Za-z &\.\-]+)\s*(?:-\s*\(?[^)]*\)?)?\s+\$?\s*(?P<value>\d+\.\d{2})'
   r'(?P<label>H\s*&\s*W|RESA|Pension|SIS|...)\s+\$?(?P<value>\d+\.\d{2})'
   ```
4. Resolve label aliases via Profile (`H & W` → canonical `Health & Welfare`)
5. Extract effective date from header text using regex on common patterns:
   ```python
   r'effective\s+(\w+\s+\d+,?\s+\d{4})'
   ```
6. Compute extraction confidence as min(field-level confidences)

**Path B — Image PDF / OCR (Tesseract or Textract)**

Used when text-PDF path yields <100 chars.

**Tesseract (free, in Fargate container):**
1. Render PDF pages to images at 300 DPI (PIL or pdf2image)
2. Preprocess: deskew, contrast normalization, denoise
3. Run Tesseract with custom config tuned for tabular content
4. Parse OCR'd text similar to Path A
5. Confidence is per-token from Tesseract, propagated up

**Amazon Textract (table-aware, paid):**
1. Send PDF directly to Textract via API
2. Use `AnalyzeDocument` with `TABLES` and `FORMS` flags
3. Receive structured response with cell-by-cell content + confidence
4. Map Textract's cells to our schema via Profile field mappings

**Strategy:** Try Tesseract first (free). If overall confidence <0.85, fallback to Textract. If still <0.85, fallback to Path C.

**Path C — Bedrock Claude multi-modal (universal fallback)**

Used when:
- File format is unusual (Word .doc, JPG, mixed)
- OCR confidence too low
- Tabular layout too complex for traditional parsers
- Profile explicitly requests AI extraction

Implementation:
1. Lambda receives the file
2. Constructs Bedrock Claude invocation:
   ```python
   response = bedrock.invoke_model(
     modelId="anthropic.claude-sonnet-4-6-v1:0",  # or whatever's latest
     body={
       "anthropic_version": "bedrock-2023-05-31",
       "max_tokens": 4096,
       "messages": [{
         "role": "user",
         "content": [
           {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "<base64 PDF>"}},
           {"type": "text", "text": "<extraction prompt — see doc 03>"}
         ]
       }],
       "system": "You are an expert reader of union benefit-fund Rate Notices. Extract values into the requested JSON schema. Mark confidence per field. Cite page numbers."
     }
   )
   ```
3. Parse Claude's structured JSON response
4. Map to ExtractedDocument schema
5. Confidence is what Claude reports per field

The prompt for Path C is detailed in `03_Bedrock_AI_Layer.md` — it's tuned per document type.

### Multi-page bundle handling

For 704's annual Notices (12-13 pages, one per apprentice class):
1. Treat the bundle as one logical document
2. Extract page-by-page, then merge:
   - Page 1 has JW summary → goes to `anchor_wages` and main `fringes`
   - Page 2 has apprentice rate schedule → goes to `apprentice_schedule[]`
   - Pages 3-12 are per-class detail → enrich `apprentice_schedule[]` with per-class fringe overrides

For 281's 4-file bundle:
1. Each file goes through extraction independently
2. After all bundle members extracted, merge into one ExtractedDocument with multiple indenture-bucket variants

### Failure modes
| Failure | Handling |
|---|---|
| Path A confidence too low | Auto-fallback to Path B |
| Path B confidence too low | Auto-fallback to Path C |
| Path C confidence too low (<0.7) | Route to manual review with all candidates |
| Total package checksum doesn't match | Re-extract with Path C; if still fails, manual review |
| Field totally missing (e.g., Pension fund value) | Profile must specify if optional or required; if required, fail |

---

## Stage 3 — CBA Rule Mining (lazy, cached)

### Purpose
Extract the **structural rules** from a CBA so they can be applied to Rate Notice values during Stage 4. This stage runs once per CBA (or when a CBA changes); subsequent Rate Notices reuse the cached `RuleManifest`.

### Inputs
- CBA file in S3
- Existing Profile (if any) for the union — used as a hint for what to extract

### Outputs (`RuleManifest` JSON)
```json
{
  "manifest_id": "uuid",
  "source_cba_file": "s3://...704 CBA.pdf",
  "source_cba_hash": "sha256:abc...",
  "manifest_version": "1.0",
  "manifest_authored_at": "2026-05-04T18:30:00Z",
  "union_local": 704,
  "contract_term": { "start": "2022-08-01", "end": "2027-07-31" },

  "wage_anchor_definition": {
    "type": "single_zone",
    "zone": "Building",
    "package": "Journeyman",
    "cba_citation": "Article 6 §11",
    "initial_value_text": "$48.73",
    "initial_value": 48.73,
    "increase_schedule": [
      { "effective": "2023-08-01", "amount": 2.60, "type": "economic_package", "cba_citation": "Article 6 §12" },
      { "effective": "2024-08-01", "amount": 2.60 },
      ...
    ]
  },

  "foreman_premium": {
    "type": "flat_dollars",
    "schedule": [
      { "effective": "2022-08-01", "amount": 4.00, "cba_citation": "Article 6 §14" },
      { "effective": "2023-08-01", "amount": 4.25 },
      { "effective": "2024-08-01", "amount": 4.50 }
    ]
  },

  "general_foreman": {
    "type": "flat_dollars_over",
    "base": "Foreman",
    "amount": 2.00,
    "effective": "2023-01-01",
    "applicability": "jobs with 18+ sprinkler fitters",
    "cba_citation": "Article 6 §15"
  },

  "apprentice_schedule": {
    "type": "class_based",
    "count": 10,
    "rates": [
      { "class": 1, "percent": 40, "anchor": "package:Journeyman" },
      { "class": 2, "percent": 45 },
      ...,
      { "class": 10, "percent": 85 }
    ],
    "rounding": 0.01,
    "cba_citation": "Article 5 §10"
  },

  "apprentice_pension_exclusion": {
    "cutoff_unit": "class",
    "cutoff_value": 1,
    "excluded_funds": ["Pension", "SIS"],
    "cba_citation": "Article 5 §10",
    "literal_text": "There shall be no other fringe contributions paid on Apprentices during the first six (6) months."
  },

  "ot_rules": [
    { "type": "time_and_a_half", "applies_to": "9th-10th hours M-F, first 8 hours Saturday", "cba_citation": "Article 8 §30" },
    { "type": "double_time", "applies_to": ">10 hours M-F, >8 hours Saturday, Sundays, Holidays" }
  ],

  "shift_differential": {
    "type": "off_hours_premium",
    "amount_percent": 15,
    "cba_citation": "Article 8 §27",
    "applies_to": "shifts starting 9 AM or later"
  },

  "funds": [
    {
      "canonical_name": "Health & Welfare",
      "amount_initial": 11.44,
      "subcomponents": [
        { "name": "RESA", "amount": 1.45 },
        { "name": "Welfare (NASI)", "amount": 9.99 }
      ],
      "cba_citation": "Article 18 §76"
    },
    {
      "canonical_name": "Pension",
      "amount_initial": 7.10,
      "cba_citation": "Article 19 §79"
    },
    ...
  ],

  "uniformity_rule": {
    "applies": true,
    "scheduled_dates": ["Jan 1 each year"],
    "applies_to": ["NASI Welfare", "Pension"],
    "offset_source": "wages",
    "cba_citation": "Article 20 §81"
  },

  "rate_change_cadence": [
    { "month": 8, "day": 1, "type": "annual_increase" },
    { "month": 1, "day": 1, "type": "uniformity_adjustment" }
  ],

  "ambiguities_flagged": [
    {
      "topic": "Power & Gas markup base",
      "literal_text": "As per Area Practice. General Foreman — 25%, Area Foreman — 15%, Foreman — 10%",
      "ambiguity": "base of percentage not specified",
      "suggested_resolution": "Building Foreman as base (consistent with Boston-area practice)"
    }
  ],

  "extraction_confidence_overall": 0.93,
  "extraction_method": "bedrock_agent_with_kb",
  "agent_invocation_id": "..."
}
```

### Implementation: Bedrock Agent

Stage 3 is the most agentic part of the engine. We use a **Bedrock Agent** with the following tools:

**Tool 1: `search_cba_kb`**
- Wraps Bedrock Knowledge Base retrieval
- Input: query string (e.g., "Foreman premium dollar amount")
- Output: top-k passages from the CBA with section/page citations

**Tool 2: `extract_rule_from_passage`**
- Wraps a Lambda that runs Claude with a structured-output prompt
- Input: passage text + rule schema (`{type: "foreman_premium", required_fields: ["amount", "base"]}`)
- Output: structured rule JSON

**Tool 3: `validate_rule`**
- Validates an extracted rule against the schema
- Returns errors if missing fields or out-of-range values

**Tool 4: `cross_reference_profile`**
- If a Profile already exists for this union, looks up what was previously known about the rule
- Helps catch CBA changes between contract terms

### Agent prompt (high-level)

```
You are a CBA rule extractor. The user has provided a CBA for Sprinkler Local 704.

Your task: produce a complete RuleManifest by extracting these rule types:
  - wage_anchor_definition
  - foreman_premium (with schedule)
  - general_foreman
  - apprentice_schedule
  - apprentice_pension_exclusion
  - ot_rules
  - shift_differential
  - funds (each fund's canonical name, amount, CBA article)
  - uniformity_rule
  - rate_change_cadence
  - any ambiguities you encounter

For each rule:
  1. Use search_cba_kb to find relevant CBA passages
  2. Use extract_rule_from_passage to get structured JSON
  3. Use validate_rule to confirm correctness
  4. If ambiguous, flag in ambiguities_flagged with suggested resolution

When complete, return the full RuleManifest.
```

The Agent loops, calling tools until done. Typical run: 30-60 tool invocations, ~5 minutes elapsed.

### Caching

- RuleManifest stored in S3 + Aurora keyed by CBA file hash
- Subsequent Rate Notices in the same period skip Stage 3 (just reuse cached manifest)
- If CBA file hash changes (new contract or CBA amendment), invalidate cache and re-mine

### Failure modes
| Failure | Handling |
|---|---|
| Agent fails to extract a required rule | Mark rule as `unresolved`; flag for human review |
| KB returns no relevant passages | Fall back to full-document Claude reading (one big invocation) |
| Extracted rule fails validation | Agent retries with refined query |
| Multi-language CBA (Spanish, French) | v1 doesn't support; flag and route to manual |

---

## Stage 4 — Rule Resolution

### Purpose
Apply Profile rules to extracted Rate Notice values, producing the canonical rate sheet.

### Inputs
- ExtractedDocument (from Stage 2)
- RuleManifest (from Stage 3)
- Profile YAML (from S3)

### Outputs (`CanonicalRateSheet` JSON)
See `04_Schemas_and_DSL.md` for the full schema.

### Implementation: Pure Python (no AI)

This stage is **fully deterministic by design.** AI-assisted extraction (Stages 2-3) feeds into a deterministic resolver here. Why:
- Reproducibility (same inputs → same outputs always)
- Auditability (can prove every output value via formula trace)
- Speed (pure computation, no model latency)
- Debugging (a wrong cell can be traced back to a specific Profile rule + input value)

### Resolution algorithm

```python
def resolve(profile: Profile, manifest: RuleManifest, extracted: ExtractedDocument) -> CanonicalRateSheet:
    rs = CanonicalRateSheet(
        union_local=profile.union.local,
        period=extracted.effective_period,
        rows=[]
    )

    # 1. Build the row matrix from Profile
    for zone in profile.zones:
        for package in profile.packages_for_zone(zone):
            for dim_combo in profile.dimensions_for_package(package):  # e.g., indenture buckets
                row = Row(zone=zone, package=package, dimensions=dim_combo)
                rs.rows.append(row)

    # 2. Resolve wages
    for row in rs.rows:
        formula = profile.wage_formula(row.zone, row.package)
        row.wage = evaluate_dsl(formula, context={"extracted": extracted, "rs": rs})
        row.wage = round_per_profile(row.wage, profile.apprentice_rounding if is_apprentice(row) else 0.01)
        row.provenance["wage"] = build_provenance(formula, extracted, manifest)

    # 3. Compute derived columns
    for row in rs.rows:
        row.wage_differential = row.wage * 1.15  # or per-zone rule
        row.wage_1_5x = row.wage * 1.5  # or notice-correct formula per Profile
        row.wage_2_0x = row.wage * 2.0
        # Temporary Heat (537), etc.

    # 4. Apply per-class fringe scaling
    for row in rs.rows:
        if is_apprentice(row):
            for fund in profile.fringe_schema:
                if fund.apprentice_scaling:
                    row.fringes[fund.name] = apply_scaling(fund, row, extracted)
                else:
                    row.fringes[fund.name] = extracted.fringes[fund.notice_label]

    # 5. Apply alt-fund routing
    for row in rs.rows:
        if profile.alt_fund_routing.applies(row):
            for from_col, to_col in profile.alt_fund_routing.routes:
                row.fringes[to_col] = row.fringes[from_col]
                row.fringes[from_col] = 0

    # 6. Apply exclusion zero-outs
    for row in rs.rows:
        if profile.apprentice_exclusion.applies(row):
            for excluded in profile.apprentice_exclusion.excluded_funds:
                row.fringes[excluded] = 0
                row.provenance[f"fringes.{excluded}"] = build_exclusion_provenance(profile)

    # 7. Stamp dates
    for row in rs.rows:
        row.start_date = extracted.effective_period.start
        row.end_date = compute_end_date(extracted.effective_period.start, profile.cadence)

    return rs
```

### Provenance tagging during resolution

Every cell gets a provenance tag:
- Direct from Notice → `rate_notice:file:line:page`
- Computed via formula → `derived:formula:cba_citation`
- Zeroed out by exclusion → `convention:apprentice_pension_exclusion`
- Conventional default → `default:vacation_canonical_ordering`

See `05_Provenance_and_Citations.md` for full design.

### Failure modes
| Failure | Handling |
|---|---|
| Formula references a missing extracted value | Throw error; route to manual review |
| Formula evaluates to NaN/inf | Throw error; flag as data corruption |
| Profile is invalid YAML | Reject; UI shows error |
| Profile references a fringe not in extracted | Warn; treat as 0; flag |

---

## Stage 5 — Validation

### Purpose
Quality gate before publishing.

### Checks performed

**5.1 — Total package checksum**
```
For each Journeyman row:
  sum(wage + fringes + apprentice_training + industry_promotion) == extracted.total_package_printed
  Tolerance: ±$0.05 (rounding noise)
```

**5.2 — Apprentice % cross-check**
```
For each Apprentice row:
  computed_wage = anchor * percent
  rate_sheet_wage matches computed_wage within rounding tolerance
```

**5.3 — Range checks per column**
```
Wage: $5 ≤ value ≤ $200
Health & Welfare: $0 ≤ value ≤ $30
Pension: $0 ≤ value ≤ $30
Apprentice ratios: 0.30 ≤ apprentice/JW ≤ 1.0
```

**5.4 — Year-over-year delta sanity**
```
For each column, compare to prior period:
  - Wage change: warn if |Δ| > 20%
  - Fund change: warn if |Δ| > 50% AND not explained by Article-20 uniformity
```

**5.5 — Article-20 awareness**
If wage decreases AND total package unchanged:
- Don't flag as error
- Tag the period as `uniformity_adjustment`
- In year-over-year report, show "this period reallocates X from wages to fringes per Article 20"

**5.6 — Schema completeness**
Every column in Profile.output_schema must be populated for every row.

**5.7 — Confidence rollup per cell**
Cell confidence = min(extraction confidence, formula evaluation confidence). If any cell <0.95 → route to review.

### AI-assisted sanity review (NEW capability)

For cells flagged by validation (especially YoY anomalies), invoke Bedrock Claude:

```
Prompt: "Here is a rate sheet cell that changed by 35% between periods.
         Period A (2026-01-01): SIS = $11.50
         Period B (2026-07-01): SIS = $15.50
         Notice for Period B: <text>
         CBA section 17: <text>
         Is this change explained by the inputs? If yes, what's the explanation?
         If no, flag as suspicious."
```

Claude returns structured response. If explained → continue. If suspicious → human review.

### Branch
- **All checks pass + all cells confidence ≥ 0.95** → AUTO_PUBLISH
- **Otherwise** → REVIEW_QUEUE

### Failure modes
| Failure | Handling |
|---|---|
| Total package checksum fails | Re-extract with Path C; if still fails, manual review |
| Apprentice % off by >$0.05 | Flag specific row; review |
| Range violation | Block publish; review |
| Required column missing | Block publish; treat as data extraction error |

---

## Stage 6 — Render & Publish

### Purpose
Convert canonical JSON to the customer-facing rate sheet artifacts.

### Outputs
- xlsx file (one per period sheet, layout per Profile)
- CSV mirror (if Profile requests)
- Articles file/sheet (auto-populated provenance summary)
- Updated canonical JSON in S3 + Aurora

### Implementation

**xlsx rendering (openpyxl):**

```python
def render_xlsx(canonical: CanonicalRateSheet, profile: Profile, output_path: str):
    # If layout=multi_sheet_workbook, open existing or create new
    if profile.output.layout == "multi_sheet_workbook":
        wb = load_or_create_workbook(profile.output_filename, output_path)
    else:
        wb = openpyxl.Workbook()

    # Add or replace sheet for this period
    sheet_name = profile.output.sheet_name(canonical.period)  # start_date or end_date convention
    if sheet_name in wb.sheetnames:
        wb.remove(wb[sheet_name])
    ws = wb.create_sheet(sheet_name)

    # Write header row
    for col_idx, col_def in enumerate(profile.output.columns, 1):
        ws.cell(row=1, column=col_idx, value=col_def.header)

    # Write data rows
    for row_idx, row in enumerate(canonical.rows, 2):
        for col_idx, col_def in enumerate(profile.output.columns, 1):
            value = extract_value_from_row(row, col_def)
            cell = ws.cell(row=row_idx, column=col_idx, value=value)

            # Add provenance comment (cell tooltip)
            if profile.provenance.enable_per_cell:
                comment = build_cell_comment(row.provenance.get(col_def.field))
                cell.comment = openpyxl.comments.Comment(comment, "LaborAid Engine")

    # Add Articles sheet if Profile requests inline
    if profile.output.articles_output == "inline_sheet":
        articles_sheet = wb.create_sheet("Articles")
        write_articles_summary(articles_sheet, canonical, profile)

    wb.save(output_path)
```

**CSV rendering:** straightforward — flatten canonical JSON's rows to CSV with header.

**Articles output:**
- Inline sheet: a 2-column table `(Funds | Articles)` with auto-populated CBA citations from Profile + RuleManifest
- Separate file: `<term>.<local> Articles.xlsx` with the same content

**Persistence:**
- xlsx + CSV + canonical JSON all written to `s3://laboraid-outputs/{tenant}/{trade}/{local}/{period}/`
- Aurora `rate_periods` table: insert/update row keyed by (union, period, version)
- Aurora `rate_cells` table: 1 row per cell with provenance JSONB column (for fast queries)

### Versioning
- Each publish gets a version number
- Re-publishing (e.g., after manual override) bumps version
- Old versions kept (immutable in S3 with Object Lock)
- Aurora tracks current version + version history

### Notification
- EventBridge custom event: `laboraid.rate-sheet.published`
- Subscribers:
  - LaborAid product service (consumes canonical JSON via API)
  - SES email to ops admin
  - Slack notification (via Lambda)
  - Audit log writer

### Failure modes
| Failure | Handling |
|---|---|
| openpyxl error rendering xlsx | Retry with different write strategy; if persistent, route to manual review with canonical JSON |
| S3 write failure | Retry with backoff |
| Aurora insert failure | Retry; rollback to ensure no partial state |

---

## Stage routing matrix

What stages run for what type of input?

| Input | Stage 1 | Stage 2 | Stage 3 | Stage 4 | Stage 5 | Stage 6 |
|---|---|---|---|---|---|---|
| New Rate Notice (existing union) | ✓ | ✓ | (cached, skip) | ✓ | ✓ | ✓ |
| New Rate Notice (no Profile yet) | ✓ | ✓ | (run if CBA exists) | ❌ Profile missing → human authoring | — | — |
| New CBA (no rate notices yet) | ✓ | ✓ (extract for indexing) | ✓ | — | — | — |
| New CBA + Rate Notices bundle | ✓ all files | ✓ all files | ✓ for CBA | ✓ for each notice | ✓ each | ✓ each |
| Reference doc (Articles, Fund Addresses) | ✓ | (basic) | — | — | — | — (just stored as reference) |
| Backfill (many historical Notices for one union) | ✓ each | ✓ each | (cached after first) | ✓ each | ✓ each | ✓ each |
| Manual override on a published cell | — | — | — | — | (validate override) | ✓ (re-render) |

---

## Test fixtures

For each of the 5 POC unions, we'll have:
- Sample input PDFs (already in `From Customer/`)
- Hand-authored Profile YAML (`docs/samples/profile_*.yaml`)
- Expected ExtractedDocument JSON for one period (`samples/expected_extracted_*.json`)
- Expected RuleManifest JSON (`samples/expected_manifest_*.json`)
- Expected CanonicalRateSheet JSON (`samples/expected_canonical_*.json`)

These become the regression tests. New code shouldn't change any of these without explicit acknowledgment.

---

## Next docs in this folder
- `03_Bedrock_AI_Layer.md` — full prompts, Agent design, KB ingestion
- `04_Schemas_and_DSL.md` — concrete JSON schemas for every artifact
- `05_Provenance_and_Citations.md` — provenance tag spec + citation pipeline
- `06_Implementation_Plan.md` — week-by-week build
