# Provenance and Citations

**Document:** 05 of 7 in `docs/`
**Read after:** `04_Schemas_and_DSL.md`. This doc details the per-cell provenance system — the most important differentiator of this engine.

---

## Why this matters

LaborAid markets *"auditability"* as a product feature. Trustees and unions must be able to verify any rate value against its CBA source. Today, with hand-built Excel rate sheets, this is impossible — a cell is just a number with no link to where it came from.

Our engine makes every cell answer the question **"why is this number $X?"** in one click, with a citation linking to:
- The Rate Notice line, OR
- The CBA article and section, OR
- The formula that derived it (with the formula's CBA citation), OR
- The convention rule applied, OR
- The manual override (with user + timestamp)

This is **not optional**. It's baked into every stage's output schema.

---

## The 6 provenance source types

Every output cell has a provenance tag with one of 6 source types:

| # | Source type | When used | Example |
|---|---|---|---|
| **1** | `rate_notice` | Direct $-value extracted from Rate Notice PDF | "Pension = $7.45 from page 1, line 21 of 2026.01.01.704 Rate Notice.pdf" |
| **2** | `cba` | Value comes from the CBA (rare; usually only when Notice doesn't include it) | "Foreman premium per Article 6 §14: $4.50" |
| **3** | `derived` | Value computed from a formula (with the formula's CBA citation) | "General Foreman = Foreman + $2.00 per CBA Article 6 §15" |
| **4** | `convention` | LaborAid normalization rule (e.g., vacation column ordering, parent-international lookup) | "Union Group = UA per parent-international lookup table" |
| **5** | `default` | Profile-configured fallback when PDF is silent or ambiguous | "Power & Gas markup base = Building Foreman per Profile fallback rule" |
| **6** | `manual` | Human override | "Cell value $X overridden to $Y by surender@nbs at 2026-05-04T17:23" |

---

## Provenance schema (per cell)

```json
{
  "source": "rate_notice" | "cba" | "derived" | "convention" | "default" | "manual",
  "value": 7.45,
  "confidence": 0.99,

  // Per-source-type fields:

  "rate_notice": {
    "file_id": "f-704-2026-07-01-001",
    "filename": "2026.01.01.704 Rate Notice.pdf",
    "page": 1,
    "line": 21,
    "label_in_pdf": "Pension Fund - (Increase $.05)",
    "extraction_method": "pdftotext"
  },

  "cba": {
    "manifest_id": "rm-704-2022-2027-v1",
    "filename": "2022.08.01-2027.07.31.704 CBA.pdf",
    "article": "Article 19",
    "section": "§79",
    "page": 13,
    "literal_text_excerpt": "...$7.10 per hour for each hour worked..."
  },

  "derived": {
    "formula": "package:Foreman + 2.00",
    "evaluated_inputs": { "package:Foreman": 58.42 },
    "computation": "58.42 + 2.00 = 60.42",
    "rule_citation": {
      "manifest_id": "rm-704-2022-2027-v1",
      "article": "Article 6",
      "section": "§15",
      "page": 4
    },
    "alternative_formulas_considered": [
      {
        "formula": "(package_wage - sum_deductions) * 1.5 + sum_deductions",
        "evaluated": "(60.42 - 5.18) * 1.5 + 5.18 = 87.04",
        "rejected_because": "rate_sheet_simple is profile policy choice"
      }
    ]
  },

  "convention": {
    "rule_name": "parent_international_lookup",
    "rule_description": "Local 704 → UA per LaborAid lookup table",
    "rule_source": "Profile.union.parent_international"
  },

  "default": {
    "fallback_name": "pg_markup_base_building_foreman",
    "rule_description": "Power & Gas markup applied to Building Foreman base when CBA is ambiguous",
    "ambiguity_in_cba": {
      "literal_text": "As per Area Practice. General Foreman — 25%, Area Foreman — 15%, Foreman — 10%.",
      "ambiguity": "base of percentage not specified in CBA"
    }
  },

  "manual": {
    "user_id": "surender@nbs",
    "timestamp": "2026-05-04T17:23:00Z",
    "previous_value": 12.50,
    "previous_source": "rate_notice",
    "reason": "OCR misread; corrected after reviewing scan",
    "reviewed_pdf_url": "s3://laboraid-inputs/.../page1.png"
  }
}
```

Mostly only one of the per-source-type sub-objects is populated per cell. The schema reserves space for all 6 for clarity.

---

## Provenance generation pipeline

How each source type's provenance gets created during the pipeline:

### Source type 1: `rate_notice`

Generated during **Stage 2 (Extract)**. The extractor (whether pdftotext, Tesseract, Textract, or Claude) records:
- Page number
- Line number (or coordinates for visual extraction)
- Exact label text as printed
- Extraction method
- Confidence

Resolver in **Stage 4** copies this provenance directly to the cell.

```python
# In Stage 4 resolver
def resolve_pension(row, extracted, profile):
    if "Pension" in extracted.fringes:
        notice_value = extracted.fringes["Pension"]
        row.fringes["Pension"] = notice_value.value
        row.provenance["fringes.Pension"] = {
            "source": "rate_notice",
            "value": notice_value.value,
            "confidence": notice_value._confidence,
            "rate_notice": {
                "file_id": extracted.source_file_id,
                "filename": extracted.source_filename,
                "page": notice_value._page,
                "line": notice_value._line,
                "label_in_pdf": notice_value._label_in_pdf,
                "extraction_method": extracted.extraction_method
            }
        }
```

### Source type 2: `cba`

Used when a value comes from the CBA without a Rate Notice analog. Rare but happens (e.g., Foreman premium that's not in the Notice).

```python
def resolve_general_foreman_premium(row, manifest, profile):
    rule = manifest.general_foreman
    row.provenance["wage"] = {
        "source": "cba",
        "cba": {
            "manifest_id": manifest.manifest_id,
            "filename": manifest.source.cba_filename,
            "article": rule._cba_citation.article,
            "section": rule._cba_citation.section,
            "page": rule._cba_citation.page
        }
    }
```

### Source type 3: `derived`

Generated when the resolver evaluates a formula. The DSL evaluator returns a trace alongside the value:

```python
# Formula evaluator returns (value, trace)
value, trace = evaluator.evaluate("package:Foreman + 2.00", context)
# trace = { "op": "+", "left": {"reference": "package:Foreman", "value": 58.42}, "right": {"literal": 2.00} }

# Resolver builds derived provenance
row.provenance["wage"] = {
    "source": "derived",
    "derived": {
        "formula": "package:Foreman + 2.00",
        "evaluated_inputs": trace.collect_references(),  # {"package:Foreman": 58.42}
        "computation": f"{trace.left.value} + {trace.right.value} = {value}",
        "rule_citation": manifest.general_foreman._cba_citation
    }
}
```

The `derived` provenance is **transitive** — it shows the formula plus the rule's CBA citation. If the user wants to dig deeper, they can click `package:Foreman` and see ITS provenance (which traces back to either Rate Notice or another derivation).

### Source type 4: `convention`

When the engine applies a LaborAid normalization rule that doesn't come from a specific PDF.

Examples:
- `Union Group = UA` from parent-international lookup
- `Vacation column 1 = $0` from canonical ordering convention
- `Apprentice Y1 zero-out includes Annuity even though CBA says "all other fringes paid"` (537-style customer convention)

```python
def apply_convention_parent_intl(row, profile):
    row.union_group = profile.union.parent_international  # "UA"
    row.provenance["union_group"] = {
        "source": "convention",
        "value": "UA",
        "convention": {
            "rule_name": "parent_international_from_profile",
            "rule_description": f"Local {profile.union.local} → {profile.union.parent_international}"
        }
    }
```

### Source type 5: `default`

When a CBA is ambiguous (e.g., 537's "As per Area Practice"), the Profile specifies a fallback rule. That fallback gets `default` provenance.

```python
def resolve_pg_general_foreman(row, profile, manifest):
    if manifest.has_ambiguity("pg_markup_base"):
        # Use Profile's fallback
        base_package = profile.fallbacks["pg_markup_base"]  # "Building Foreman"
        base_wage = get_wage_from_other_row(rs, "Building", base_package)
        wage = base_wage * 1.25  # General Foreman markup
        
        row.provenance["wage"] = {
            "source": "default",
            "default": {
                "fallback_name": "pg_markup_base_building_foreman",
                "rule_description": "Power & Gas markup applied to Building Foreman base when CBA is ambiguous",
                "ambiguity_in_cba": manifest.get_ambiguity("pg_markup_base"),
                "computed_as": f"{base_wage} × 1.25 = {wage}"
            }
        }
```

The user can see both the fallback rule applied AND the original CBA ambiguity that triggered it.

### Source type 6: `manual`

Generated when an admin uses the override UI. POST `/cells/{cell_id}/override` with the new value and reason.

```python
@app.route("/cells/<cell_id>/override", methods=["POST"])
def override_cell(cell_id):
    body = request.json
    cell = db.get_cell(cell_id)
    
    cell.previous_value = cell.value
    cell.previous_source = cell.provenance["source"]
    cell.value = body["new_value"]
    cell.provenance = {
        "source": "manual",
        "value": body["new_value"],
        "confidence": 1.0,
        "manual": {
            "user_id": request.user.id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "previous_value": cell.previous_value,
            "previous_source": cell.previous_source["source"],
            "reason": body["reason"],
            "reviewed_pdf_url": body.get("pdf_url")
        }
    }
    db.save(cell)
    
    # Log to immutable audit
    audit.log("cell.manual_override", cell_id=cell_id, ...)
```

---

## Citation generation (using Bedrock KB)

For `derived` and `cba` provenance, we need precise CBA citations. The CBA Knowledge Base (doc 03) makes this easy:

```python
def generate_citation(rule_name: str, union_local: int, scope: str = None) -> CbaCitation:
    """Find the CBA passage that defines a rule."""
    query = CITATION_QUERIES[rule_name]  # e.g., "foreman premium dollar amount"
    
    passages = bedrock_kb.search(
        query=query,
        union_local=union_local,
        scope=scope,
        max_results=3
    )
    
    if not passages:
        return None
    
    # Highest-scoring passage
    best = passages[0]
    return CbaCitation(
        article=best.metadata["article"],
        section=best.metadata["section"],
        page=best.metadata["page"],
        literal_text_excerpt=best.text[:200],
        confidence=best.score
    )
```

This is invoked during Stage 3 (CBA mining) for each rule, baked into the RuleManifest. Stage 4 doesn't need to re-search — it just copies the citation from the manifest.

For "ad-hoc" citation requests (e.g., admin asks "where is this rule from?"), the same KB search powers it.

---

## Provenance UX in the admin UI

### Cell click → side panel

When admin clicks any cell in the rate-sheet review UI, a side panel shows:

```
┌──────────────────────────────────────────────────────────┐
│ Building / Journeyman / Wage = $53.92                    │
├──────────────────────────────────────────────────────────┤
│ Source: Rate Notice                                      │
│ Confidence: 99%                                          │
│                                                          │
│ From: 2026.01.01.704 Rate Notice.pdf                     │
│ Page: 1                                                  │
│ Line: 11                                                 │
│ Label in PDF: "Journeyman's Wage - $52.32"               │
│                                                          │
│ [📄 View PDF page]  [✎ Override]                         │
│                                                          │
│ ─────────────────────────────────────────────            │
│ Related rules (from CBA):                                │
│ • Article 6 §11: "Effective August 1, 2022, the rate    │
│   of wage to be paid under this Agreement for           │
│   Journeyman Sprinkler Fitters shall be Forty-Eight     │
│   Dollars and Seventy-Three Cents ($48.73) per hour..." │
│   [📄 View CBA page 4]                                   │
│                                                          │
│ ─────────────────────────────────────────────            │
│ 🔍 Ask the CBA                                           │
│ [_____________________] [Ask]                            │
└──────────────────────────────────────────────────────────┘
```

For derived cells:

```
┌──────────────────────────────────────────────────────────┐
│ Building / General Foreman / Wage = $60.42               │
├──────────────────────────────────────────────────────────┤
│ Source: Derived                                          │
│ Formula: package:Foreman + 2.00                          │
│   = 58.42 + 2.00                                         │
│   = 60.42                                                │
│                                                          │
│ Rule: CBA Article 6 §15                                  │
│ Quote: "The wage rate of a General Foreman shall be      │
│ Two Dollars ($2.00) more than the Foreman rate."         │
│ [📄 View CBA page 4]                                      │
│                                                          │
│ Related cells:                                           │
│ • Foreman wage = $58.42 [click to drill in]              │
└──────────────────────────────────────────────────────────┘
```

Drilling into Foreman shows ITS provenance, which traces back to:

```
package:Journeyman ($53.92, from Rate Notice)
  + schedule:foreman_premium ($4.50, from CBA Article 6 §14)
  = $58.42
```

This is **end-to-end traceability** — from any output cell to the root inputs, in 1-3 clicks.

### Provenance in xlsx output

When rendering the rate sheet xlsx, every cell gets a comment (the little red triangle) with the provenance:

```python
cell.comment = openpyxl.comments.Comment(
    text=format_provenance_for_comment(row.provenance[col_def.field]),
    author="LaborAid Engine"
)
```

Comment format:
```
Wage 1.5x = $80.88
Source: Derived
Formula: Wage × 1.5
CBA: Article 8 §30
Confidence: 99%
```

This way, even if the xlsx is sent to a trustee outside LaborAid, the provenance is embedded in the file (Excel users see it as a hover tooltip).

### Articles output (the customer's existing pattern)

821 has an `Articles` sheet inside the rate-sheet xlsx. 281 has a separate `Articles.xlsx` file. **Our engine populates both formats** based on Profile config.

The Articles sheet/file is a 2-column table:

| Funds | Articles |
|---|---|
| Wage | Article 6 §11 |
| Foreman premium | Article 6 §14 |
| General Foreman | Article 6 §15 |
| Wage 1.5x | Article 8 §30 |
| Health & Welfare | Article 18 §76 |
| RESA | Article 18 §76 (subcomponent) |
| Pension | Article 19 §79 |
| SIS | Article 27 §103 |
| ... | ... |

Auto-populated from Profile's `cba_citation` fields and the RuleManifest.

---

## Audit log integration

Every provenance event also writes to the audit log:

```sql
-- audit_log table
INSERT INTO audit_log (
    timestamp, tenant, action, actor, details
) VALUES (
    NOW(), 'laboraid', 'cell.value_set', 'system:resolver',
    '{
        "rate_sheet_id": "rs-704-2026-07-01-v1",
        "cell_id": "rs-704-2026-07-01-v1.row5.fringes.Pension",
        "value": 7.45,
        "provenance": { ... full provenance JSON ... }
    }'
);
```

For SOC 2 audit requirements:
- 7-year retention
- Immutable (S3 Object Lock + Aurora point-in-time recovery)
- Tamper-evident (hash chain across consecutive log entries — optional v2 feature)

---

## Provenance for low-confidence cells

When a cell's confidence is below the auto-publish threshold (e.g., 0.85), provenance includes:

```json
{
  "source": "rate_notice",
  "value": 7.79,
  "confidence": 0.62,
  "rate_notice": {
    "file_id": "...",
    "page": 5,
    "line": 14,
    "label_in_pdf": "S.I.S.    $7.79",
    "extraction_method": "tesseract_ocr",
    "ocr_low_confidence_alternatives": [
      { "value": 7.79, "confidence": 0.62 },
      { "value": 7.22, "confidence": 0.42 },
      { "value": 1.79, "confidence": 0.21 }
    ]
  },
  "review_required": true,
  "review_reason": "OCR confidence below 0.85"
}
```

The admin review UI shows all alternatives with the original PDF rendering side-by-side. Admin selects the right value → engine writes a `manual` override.

---

## Why this design works

| Requirement | How provenance addresses it |
|---|---|
| Auditor: "where did this number come from?" | One click → side panel with file, page, label, formula, CBA citation |
| Trustee: "is this rate authorized by the CBA?" | Drill from rate value to CBA article quote |
| Engineer: "why did the engine produce this value?" | Provenance trace shows formula evaluation step-by-step |
| Compliance: "preserve all decisions for 7 years" | Audit log + immutable storage |
| LaborAid product: "show the contractor where this rate is from" | API endpoint serves provenance as JSON |
| New union onboarding: "how do we know our extraction is right?" | Citation-driven CBA mining produces RuleManifest with article references |

---

## Implementation notes

### Storage in Aurora

`rate_cells` table:
```sql
CREATE TABLE rate_cells (
    id UUID PRIMARY KEY,
    rate_sheet_id UUID REFERENCES rate_sheets(id),
    row_index INT,
    zone TEXT,
    package TEXT,
    column_name TEXT,
    value NUMERIC,
    value_type TEXT,  -- 'dollars' or 'percent'
    confidence FLOAT,
    provenance JSONB,  -- full provenance object
    INDEX (rate_sheet_id, zone, package, column_name),
    INDEX USING GIN (provenance jsonb_path_ops)
);
```

Provenance as JSONB enables fast queries like:
- "Show me all cells whose source is 'manual' (overridden by humans)"
- "Show me all cells citing CBA Article 5"
- "Show me all cells with confidence < 0.95"

### Storage in S3

Full canonical JSON (with provenance) at:
```
s3://laboraid-outputs/{tenant}/{trade}/{local}/{period}/canonical_v{N}.json
```

Versioned and immutable.

### API responses

`GET /cells/{cell_id}` returns the full cell + provenance. Used by the side panel UI.

```json
{
  "cell_id": "rs-704-2026-07-01-v1.row5.fringes.Pension",
  "value": 7.45,
  "provenance": { ... full object ... }
}
```

---

## Summary

Provenance is **the engine's killer feature** — what makes it more than a PDF parser. Every cell traces to a source (Notice, CBA, formula, convention, default, or manual override). The 6 source types cover all observed cases across 5 POC unions. The Bedrock KB powers citation lookup. The admin UI surfaces provenance in 1 click. The xlsx output embeds it as cell comments. The Aurora schema enables fast provenance queries.

**Without this, we're a PDF parser. With this, we're an auditable rate-data system that LaborAid can defend to any trustee or court.**

---

## Next docs in this folder
- `06_Implementation_Plan.md` — phased build with provenance as a Phase 1 deliverable
