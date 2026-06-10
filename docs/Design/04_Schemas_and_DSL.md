# Schemas and Formula DSL

**Document:** 04 of 7 in `docs/`
**Read after:** `02_Parser_Stages.md` and `03_Bedrock_AI_Layer.md`. This doc gives the concrete JSON schemas and the formula DSL for the engine.

---

## Schemas overview

Five schemas govern the data flow:

| Schema | When produced | When consumed | Storage |
|---|---|---|---|
| `ClassificationResult` | Stage 1 (Classify) | Stage 2 (Extract) | DynamoDB + S3 |
| `ExtractedDocument` | Stage 2 (Extract) | Stage 4 (Resolve) | S3 manifests |
| `RuleManifest` | Stage 3 (CBA Mining) | Stage 4 (Resolve) | S3 + Aurora |
| `Profile` (YAML) | Authored by humans + AI | Stage 4 + 6 | S3 (versioned) |
| `CanonicalRateSheet` | Stage 4 (Resolve) | Stage 5 + 6 + LaborAid product | S3 + Aurora |

---

## 1. ClassificationResult

```json
{
  "$schema": "https://laboraid.com/schemas/classification-result-1.0.json",
  "file_id": "f-704-2026-07-01-001",
  "ingest_timestamp": "2026-05-04T17:30:00Z",
  "s3_key": "laboraid/Sprinkler/704/2026-07-01/2026.07.01.704 Rate Notice.pdf",
  "content_hash": "sha256:a3f2...",
  "size_bytes": 287452,

  "format": "pdf_text",
  "format_confidence": 0.99,
  "page_count": 1,

  "document_type": "rate_notice",
  "document_type_confidence": 0.97,
  "classification_path": "deterministic_filename",

  "tenant": "laboraid",
  "trade": "Sprinkler",
  "union_local": 704,
  "scope": null,

  "effective_period": {
    "start": "2026-07-01",
    "end": null,
    "source": "filename_prefix"
  },

  "bundle": {
    "is_bundle_member": false,
    "bundle_id": null,
    "expected_members": null
  },

  "next_stage_routing": {
    "stage": "extract",
    "preferred_path": "text_pdf"
  }
}
```

---

## 2. ExtractedDocument (for a Rate Notice)

```json
{
  "$schema": "https://laboraid.com/schemas/extracted-document-1.0.json",
  "extraction_id": "x-704-2026-07-01-001",
  "source_file_id": "f-704-2026-07-01-001",
  "extracted_at": "2026-05-04T17:30:30Z",
  "extraction_method": "pdftotext_with_pdfplumber",
  "extraction_confidence_overall": 0.97,

  "document_type": "rate_notice",
  "union_local": 704,
  "scope": null,
  "effective_period": { "start": "2026-07-01", "end": null },
  "header_text": "This is to notify you of the money change in the Contract, effective July 1, 2026.",

  "anchor_wages": {
    "Journeyman": {
      "value": 53.92,
      "base_wage": 49.06,
      "_label_in_pdf": "Journeyman's Wage",
      "_page": 1,
      "_line": 11,
      "_confidence": 0.99
    }
  },

  "apprentice_schedule": [
    {
      "level": 1,
      "level_label": "1st Period Apprentice",
      "wage": 21.57,
      "base_wage": 19.62,
      "_page": 3,
      "_confidence": 0.99,
      "fringe_overrides": {
        "Pension": 0.00,
        "SIS": 0.00
      },
      "deduction_overrides": {
        "S & E Fund": 0.08,
        "Craft Fund": 0.03,
        "Union Assessment": 1.97,
        "Retiree Holiday Fund": 0.03
      }
    },
    {
      "level": 2,
      "level_label": "2nd Period Apprentice",
      "wage": 24.26,
      "_page": 4,
      "_confidence": 0.99,
      "fringe_overrides": {},
      "deduction_overrides": {
        "S & E Fund": 0.09,
        "Craft Fund": 0.03,
        "Union Assessment": 2.21,
        "Retiree Holiday Fund": 0.03
      }
    }
  ],

  "fringes": {
    "Health & Welfare": {
      "value": 13.95,
      "subcomponents": { "RESA": 1.35 },
      "_label_in_pdf": "H & W",
      "_page": 1,
      "_confidence": 0.99
    },
    "Pension": {
      "value": 7.45,
      "_label_in_pdf": "Pension Fund",
      "_page": 1,
      "_confidence": 0.99
    },
    "SIS": {
      "value": 11.50,
      "_label_in_pdf": "Sprinkler Fitters & Apprentices Local 704 Defined Contribution Pension Fund",
      "_page": 1,
      "_confidence": 0.99
    },
    "Apprenticeship Training": {
      "value": 1.00,
      "_label_in_pdf": "Apprentice Education Fund",
      "_page": 1,
      "_confidence": 0.99
    },
    "S.U.B. 704": {
      "value": 1.20,
      "_label_in_pdf": "S.U.B. Fund",
      "_page": 1,
      "_confidence": 0.99
    },
    "UA International Training": {
      "value": 0.10,
      "_label_in_pdf": "I.T.F. International Training Fund",
      "_page": 1,
      "_confidence": 0.99
    },
    "Industry Promotion": {
      "value": 0.30,
      "_label_in_pdf": "Industry Promotion Fund",
      "_subcomponents_implied_from_cba": { "national": 0.20, "local": 0.10 },
      "_page": 1,
      "_confidence": 0.99
    }
  },

  "deductions": {
    "S & E Fund": { "value": 0.20, "_page": 1, "_confidence": 0.99 },
    "Craft Fund": { "value": 0.06, "_page": 1, "_confidence": 0.99 },
    "Union Assessment": { "value": 4.86, "_page": 1, "_confidence": 0.99 },
    "Retiree Holiday Fund": { "value": 0.06, "_page": 1, "_confidence": 0.99 }
  },

  "ot_rates_published": {
    "1.5x": 78.40,
    "2.0x": 102.85,
    "_page": 1,
    "_confidence": 0.99
  },

  "foreman_premium_text": "Foreman - $4.50 over -",
  "total_package_printed": 87.52,

  "extraction_notes": [
    "Unable to find general_foreman premium in this Notice; using CBA-derived value (Foreman + $2.00)"
  ]
}
```

### Key conventions
- Underscore-prefixed fields (`_page`, `_confidence`, `_label_in_pdf`) are extraction metadata
- Every value carries its provenance fragments (page, label, confidence) so downstream stages can build full provenance
- Fields the Notice doesn't include but the CBA implies (e.g., `general_foreman` premium for 704) are absent here; the resolver looks them up from RuleManifest

---

## 3. RuleManifest (CBA-extracted rules)

```json
{
  "$schema": "https://laboraid.com/schemas/rule-manifest-1.0.json",
  "manifest_id": "rm-704-2022-2027-v1",
  "manifest_version": "1.0",
  "manifest_authored_at": "2026-05-04T17:00:00Z",
  "extraction_method": "bedrock_agent",
  "agent_invocation_id": "ag-...",

  "source": {
    "cba_file_id": "f-704-cba-2022-2027",
    "cba_s3_key": "laboraid/Sprinkler/704/2022-2027/2022.08.01-2027.07.31.704 CBA.pdf",
    "cba_hash": "sha256:b4c3..."
  },

  "union_local": 704,
  "trade": "Sprinkler",
  "parent_international": "UA",
  "contract_term": { "start": "2022-08-01", "end": "2027-07-31" },
  "contractor_associations": ["NFSA"],

  "wage_anchor_definition": {
    "type": "single_zone_single_anchor",
    "zone_name": "Building",
    "package_name": "Journeyman",
    "initial_value": 48.73,
    "increase_schedule": [
      { "effective": "2023-08-01", "amount": 2.60, "type": "economic_package" },
      { "effective": "2024-08-01", "amount": 2.60, "type": "economic_package" },
      { "effective": "2025-08-01", "amount": 2.60, "type": "economic_package" },
      { "effective": "2026-08-01", "amount": 2.60, "type": "economic_package" }
    ],
    "_cba_citation": { "article": "Article 6", "section": "§11-12", "page": 4, "confidence": 0.98 }
  },

  "foreman_premium": {
    "type": "flat_dollars_over_anchor",
    "anchor": "package:Journeyman",
    "schedule": [
      { "effective": "2022-08-01", "amount": 4.00 },
      { "effective": "2023-08-01", "amount": 4.25 },
      { "effective": "2024-08-01", "amount": 4.50 }
    ],
    "_cba_citation": { "article": "Article 6", "section": "§14", "page": 4, "confidence": 0.99 }
  },

  "general_foreman": {
    "type": "flat_dollars_over_other_package",
    "base_package": "Foreman",
    "amount": 2.00,
    "effective": "2023-01-01",
    "applicability_text": "Any job that has eighteen (18) or more sprinkler fitters",
    "_cba_citation": { "article": "Article 6", "section": "§15", "page": 4, "confidence": 0.99 }
  },

  "apprentice_schedule": {
    "type": "class_based_percentage",
    "count": 10,
    "rates": [
      { "level": 1,  "percent": 40, "anchor": "package:Journeyman" },
      { "level": 2,  "percent": 45, "anchor": "package:Journeyman" },
      { "level": 3,  "percent": 50, "anchor": "package:Journeyman" },
      { "level": 4,  "percent": 55, "anchor": "package:Journeyman" },
      { "level": 5,  "percent": 60, "anchor": "package:Journeyman" },
      { "level": 6,  "percent": 65, "anchor": "package:Journeyman" },
      { "level": 7,  "percent": 70, "anchor": "package:Journeyman" },
      { "level": 8,  "percent": 75, "anchor": "package:Journeyman" },
      { "level": 9,  "percent": 80, "anchor": "package:Journeyman" },
      { "level": 10, "percent": 85, "anchor": "package:Journeyman" }
    ],
    "rounding": 0.01,
    "_cba_citation": { "article": "Article 5", "section": "§10", "page": 2-3, "confidence": 0.99 }
  },

  "apprentice_pension_exclusion": {
    "cutoff_unit": "class",
    "cutoff_value": 1,
    "excluded_funds": ["Pension", "SIS"],
    "literal_text": "There shall be no other fringe contributions paid on Apprentices during the first six (6) months",
    "interpretation": "rate_sheet zeros only Pension and SIS for Class 1; other fringes still apply",
    "interpretation_confidence": 0.85,
    "_cba_citation": { "article": "Article 5", "section": "§10", "page": 3 }
  },

  "ot_rules": [
    {
      "column": "Wage 1.5x",
      "formula_per_cba": "(Wage - sum(Deductions)) * 1.5 + sum(Deductions)",
      "formula_per_rate_sheet_practice": "Wage * 1.5",
      "discrepancy_flagged": true,
      "_cba_citation": { "article": "Article 8", "section": "§30", "page": 6 }
    },
    {
      "column": "Wage 2.0x",
      "formula_per_cba": "(Wage - sum(Deductions)) * 2.0 + sum(Deductions)",
      "formula_per_rate_sheet_practice": "Wage * 2.0",
      "discrepancy_flagged": true,
      "_cba_citation": { "article": "Article 8", "section": "§30", "page": 6 }
    }
  ],

  "shift_differential": {
    "type": "off_hours_premium_percentage",
    "amount_percent": 15,
    "applies_to_text": "shifts with start time of 9:00 A.M. or later",
    "_cba_citation": { "article": "Article 8", "section": "§27", "page": 5 }
  },

  "funds": [
    {
      "canonical_name": "Health & Welfare",
      "amount_initial": 11.44,
      "subcomponents": [
        { "name": "RESA", "amount": 1.45 },
        { "name": "NASI Welfare", "amount": 9.99 }
      ],
      "_cba_citation": { "article": "Article 18", "section": "§76", "page": 13 }
    },
    {
      "canonical_name": "Pension",
      "amount_initial": 7.10,
      "_cba_citation": { "article": "Article 19", "section": "§79", "page": 13 }
    },
    {
      "canonical_name": "SIS",
      "amount_initial": 10.00,
      "_aliases_in_cba": ["Sprinkler Fitters and Apprentices Local 704 Defined Contribution Pension Fund"],
      "_cba_citation": { "article": "Article 27", "section": "§103-107", "page": 17-18 }
    },
    {
      "canonical_name": "Apprenticeship Training",
      "amount_initial": 0.95,
      "_aliases_in_cba": ["NASI Apprentice and Training Fund"],
      "_cba_citation": { "article": "Article 22", "section": "§85", "page": 14 }
    },
    {
      "canonical_name": "S.U.B. 704",
      "amount_initial": 1.20,
      "_cba_citation": { "article": "Article 23", "section": "§87", "page": 15 }
    },
    {
      "canonical_name": "Industry Promotion",
      "amount_initial": 0.30,
      "subcomponents": [
        { "name": "Contract Administration", "amount": 0.06 },
        { "name": "National Programs", "amount": 0.14 },
        { "name": "Local Programs", "amount": 0.10 }
      ],
      "_cba_citation": { "article": "Article 24", "section": "§92", "page": 15 }
    }
  ],

  "deductions": [
    { "name": "Craft Protection Fund", "initial": 0.06, "_cba": "Article 26 §98" },
    { "name": "Union Assessment", "initial": 5.84, "_cba": "Article 26 §98" },
    { "name": "S & E Fund", "initial": 0.24, "_cba": "Article 26 §98" },
    { "name": "Retiree Holiday Fund", "initial": 0.02, "_cba": "Article 26 §98" }
  ],

  "uniformity_rule": {
    "applies": true,
    "scheduled_dates_text": "the 1st of January of each year",
    "applies_to": ["NASI Welfare", "Pension"],
    "offset_source": "wages",
    "_cba_citation": { "article": "Article 20", "section": "§81", "page": 14 }
  },

  "rate_change_cadence": [
    { "month": 8, "day": 1, "type": "annual_increase" },
    { "month": 1, "day": 1, "type": "uniformity_adjustment" }
  ],

  "ambiguities_flagged": [],

  "extraction_confidence_overall": 0.96,
  "rules_extracted": 11,
  "rules_pending_review": 0
}
```

---

## 4. Profile YAML

The Profile is the per-union configuration. Authored manually (with AI assistance) and stored versioned in S3. Below is the full schema with 704 as the worked example.

```yaml
# unions/sprinkler-704.yaml
profile_version: "1.0"
profile_authored_at: "2026-05-04T17:00:00Z"
profile_authored_by: "system+human:onboarding-1"

union:
  local: 704
  trade: Sprinkler
  parent_international: UA
  jurisdiction:
    state: Michigan
    counties: [Wayne, Oakland, Macomb, Washtenaw]
  contract_term:
    start: 2022-08-01
    end: 2027-07-31
  contractor_associations: [NFSA]

zones:
  - name: Building
    anchor_label_in_notice: Journeyman   # how it appears in Rate Notice
    is_default: true

packages:
  # Identifiers; per-package detail in apprentice_schedule and foreman_premiums sections
  - name: General Foreman
    zones: [Building]
    wage_formula: "package:Foreman + 2.00"
    cba_citation: "Article 6 §15"
    applies_when: "job has 18+ sprinkler fitters"

  - name: Foreman
    zones: [Building]
    wage_formula: "schedule:foreman_premium"  # multi-year schedule, evaluator picks based on effective_date
    cba_citation: "Article 6 §14"

  - name: Journeyman
    zones: [Building]
    wage_formula: "anchor:zone:Building"  # Notice value
    cba_citation: "Article 6 §11"

  - name: Apprentice Class 10
    zones: [Building]
    wage_formula: "anchor:zone:Building * 0.85"
    apprentice_class: 10
  - name: Apprentice Class 9
    wage_formula: "anchor:zone:Building * 0.80"
    apprentice_class: 9
  - name: Apprentice Class 8
    wage_formula: "anchor:zone:Building * 0.75"
    apprentice_class: 8
  - name: Apprentice Class 7
    wage_formula: "anchor:zone:Building * 0.70"
    apprentice_class: 7
  - name: Apprentice Class 6
    wage_formula: "anchor:zone:Building * 0.65"
    apprentice_class: 6
  - name: Apprentice Class 5
    wage_formula: "anchor:zone:Building * 0.60"
    apprentice_class: 5
  - name: Apprentice Class 4
    wage_formula: "anchor:zone:Building * 0.55"
    apprentice_class: 4
  - name: Apprentice Class 3
    wage_formula: "anchor:zone:Building * 0.50"
    apprentice_class: 3
  - name: Apprentice Class 2
    wage_formula: "anchor:zone:Building * 0.45"
    apprentice_class: 2
  - name: Apprentice Class 1
    wage_formula: "anchor:zone:Building * 0.40"
    apprentice_class: 1

apprentice_schedule:
  unit: class
  count: 10
  rounding: 0.01
  pension_exclusion:
    cutoff_unit: class
    cutoff_value: 1
    excluded_funds: [Pension, SIS]
  cba_citation: "Article 5 §10"

foreman_premium_schedule:
  - effective: 2022-08-01
    amount: 4.00
  - effective: 2023-08-01
    amount: 4.25
  - effective: 2024-08-01
    amount: 4.50
  cba_citation: "Article 6 §14"

ot_rules:
  - column: Wage Differential
    formula: "package_wage * 1.15"
    label_in_rate_sheet: Wage Differential
    cba_citation: "Article 8 §27"
  - column: Wage 1.5x
    formula: "package_wage * 1.5"  # rate-sheet practice; see notice_correct_formula
    notice_correct_formula: "(package_wage - sum_deductions) * 1.5 + sum_deductions"
    discrepancy_known: true
    cba_citation: "Article 8 §30"
  - column: Wage 2.0x
    formula: "package_wage * 2.0"
    notice_correct_formula: "(package_wage - sum_deductions) * 2.0 + sum_deductions"
    discrepancy_known: true
    cba_citation: "Article 8 §30"

fringe_schema:
  - name: Health & Welfare
    type: dollars_per_hour
    notice_label_aliases: ["H & W", "H&W", "Health & Welfare", "Health and Welfare"]
    derive_from_notice: "Health & Welfare - subcomponent:RESA"
    apprentice_scaling: { type: constant }
    cba_citation: "Article 18 §76"

  - name: RESA
    type: dollars_per_hour
    notice_label_aliases: ["RESA"]
    extracted_as: "subcomponent:Health & Welfare"
    apprentice_scaling: { type: constant }

  - name: Pension
    type: dollars_per_hour
    notice_label_aliases: ["Pension Fund", "Pension"]
    apprentice_scaling: { type: constant_with_y1_zero }
    cba_citation: "Article 19 §79"

  - name: SIS
    type: dollars_per_hour
    notice_label_aliases:
      - "SIS"
      - "Sprinkler Fitters & Apprentices Local 704 Defined Contribution Pension Fund"
      - "Local 704 Defined Contribution Pension Fund"
    apprentice_scaling: { type: constant_with_y1_zero }
    cba_citation: "Article 27 §103"

  - name: UA International Training
    type: dollars_per_hour
    notice_label_aliases: ["I.T.F.", "I.T.F. International Training Fund", "UA ITF"]
    apprentice_scaling: { type: constant }

  - name: Apprenticeship Training
    type: dollars_per_hour
    notice_label_aliases: ["Apprentice Education Fund", "NASI Apprentice and Training Fund"]
    apprentice_scaling: { type: constant }
    cba_citation: "Article 22 §85"

  - name: S.U.B. 704
    type: dollars_per_hour
    notice_label_aliases: ["S.U.B. Fund", "Detroit S.U.B. Fund"]
    apprentice_scaling: { type: constant }
    cba_citation: "Article 23 §87"

  - name: Industry Promotion National Use
    type: dollars_per_hour
    derive_from_cba: "Industry Promotion subcomponents: Contract Administration + National Programs"
    apprentice_scaling: { type: constant }
    cba_citation: "Article 24 §92"

  - name: Industry Promotion Local Use
    type: dollars_per_hour
    derive_from_cba: "Industry Promotion subcomponent: Local Programs"
    apprentice_scaling: { type: constant }

deduction_schema:
  - name: S & E 704
    type: dollars_per_hour
    notice_label_aliases: ["S & E Fund", "S&E Fund"]
    apprentice_scaling: { type: per_class_lookup_from_notice }

  - name: Craft 704
    type: dollars_per_hour
    notice_label_aliases: ["Craft Fund", "Craft Protection Fund"]
    apprentice_scaling: { type: per_class_lookup_from_notice }

  - name: Union Dues 704
    type: dollars_per_hour
    notice_label_aliases: ["Union Assessment"]
    apprentice_scaling: { type: per_class_lookup_from_notice }

  - name: Retiree Holiday 704
    type: dollars_per_hour
    notice_label_aliases: ["Retiree Holiday Fund"]
    apprentice_scaling: { type: per_class_lookup_from_notice }

rate_change_cadence:
  - month: 1
    day: 1
    type: uniformity_adjustment
  - month: 8
    day: 1
    type: annual_increase

output_schema:
  layout: multi_sheet_workbook
  filename_pattern: "{contract_term_start_year}-{contract_term_end_year}.{local} Rate Sheet.xlsx"
  sheet_naming: end_date
  sheet_name_format: "{end_date_yyyy_mm_dd}"
  csv_mirror: true
  csv_filename_pattern: "{period_start_yyyy_mm_dd}.{local} Rate Sheet.csv"
  articles_output: inline_sheet
  articles_sheet_name: "Articles"
  fund_addresses_sheet: true
  column_set_version: "2026.01"
  columns:
    - { header: "Union Group",         field: "union.parent_international" }
    - { header: "Trade",               field: "union.trade" }
    - { header: "Union Local",         field: "union.local" }
    - { header: "Zone",                field: "row.zone" }
    - { header: "Package",             field: "row.package" }
    - { header: "Start Date",          field: "row.start_date" }
    - { header: "End Date",            field: "row.end_date" }
    - { header: "Wage",                field: "row.wage" }
    - { header: "Wage Differential",   field: "row.ot_rates.Wage Differential" }
    - { header: "Wage 1.5x",           field: "row.ot_rates.Wage 1.5x" }
    - { header: "Wage 2.0x",           field: "row.ot_rates.Wage 2.0x" }
    - { header: "Health & Welfare",    field: "row.fringes.Health & Welfare" }
    - { header: "RESA",                field: "row.fringes.RESA" }
    - { header: "Pension",             field: "row.fringes.Pension" }
    - { header: "SIS",                 field: "row.fringes.SIS" }
    - { header: "UA International Training", field: "row.fringes.UA International Training" }
    - { header: "Apprenticeship Training",   field: "row.fringes.Apprenticeship Training" }
    - { header: "S.U.B. 704",          field: "row.fringes.S.U.B. 704" }
    - { header: "Industry Promotion National Use", field: "row.fringes.Industry Promotion National Use" }
    - { header: "Industry Promotion Local Use",    field: "row.fringes.Industry Promotion Local Use" }
    - { header: "S & E 704",           field: "row.deductions.S & E 704" }
    - { header: "Craft 704",           field: "row.deductions.Craft 704" }
    - { header: "Union Dues 704",      field: "row.deductions.Union Dues 704" }
    - { header: "Retiree Holiday 704", field: "row.deductions.Retiree Holiday 704" }

provenance:
  enable_per_cell: true
  cell_comment_format: "{source_type}: {citation} (confidence={confidence})"
  articles_summary_columns: [Funds, Articles]

quality_validation:
  total_package_must_match: true
  total_package_tolerance: 0.05
  cross_check_apprentice_pct: true
  flag_wage_decreases_unless_uniformity: true
  yoy_delta_warning_threshold:
    wage: 0.20
    fringes: 0.50

ocr_quality:
  min_confidence_for_auto_publish: 0.95
  min_confidence_for_human_review: 0.70

ai_extraction_fallback:
  enable: true
  preferred_model: anthropic.claude-sonnet-4-6-v1:0

cba_knowledge_base:
  knowledge_base_id: "<set at deploy time>"
  filter_metadata: { union_local: "704" }
```

---

## 5. CanonicalRateSheet (Stage 4 output)

```json
{
  "$schema": "https://laboraid.com/schemas/canonical-rate-sheet-1.0.json",
  "rate_sheet_id": "rs-704-2026-07-01-v1",
  "published_at": null,
  "version": 1,
  "status": "draft",

  "union": {
    "local": 704,
    "trade": "Sprinkler",
    "parent_international": "UA"
  },
  "scope": null,

  "period": {
    "start": "2026-07-01",
    "end": "2026-12-31",
    "type": "annual_increase"
  },

  "source_documents": [
    {
      "type": "rate_notice",
      "file_id": "f-704-2026-07-01-001",
      "extraction_id": "x-704-2026-07-01-001"
    },
    {
      "type": "cba",
      "file_id": "f-704-cba-2022-2027",
      "manifest_id": "rm-704-2022-2027-v1"
    }
  ],

  "profile_version": "1.0",
  "profile_id": "profile-704-v1.0",

  "rows": [
    {
      "row_id": 1,
      "zone": "Building",
      "package": "General Foreman",
      "dimensions": {},
      "start_date": "2026-07-01",
      "end_date": "2026-12-31",
      "wage": 60.42,
      "ot_rates": {
        "Wage Differential": 69.48,
        "Wage 1.5x": 90.63,
        "Wage 2.0x": 120.84
      },
      "fringes": {
        "Health & Welfare": 12.60,
        "RESA": 1.35,
        "Pension": 7.45,
        "SIS": 11.50,
        "UA International Training": 0.10,
        "Apprenticeship Training": 1.00,
        "S.U.B. 704": 1.20,
        "Industry Promotion National Use": 0.20,
        "Industry Promotion Local Use": 0.10
      },
      "deductions": {
        "S & E 704": 0.20,
        "Craft 704": 0.06,
        "Union Dues 704": 4.86,
        "Retiree Holiday 704": 0.06
      },
      "provenance": {
        "wage": {
          "source": "derived",
          "formula": "package:Foreman + 2.00",
          "evaluated": "58.42 + 2.00",
          "cba_citation": "Article 6 §15",
          "confidence": 0.99
        },
        "ot_rates.Wage 1.5x": {
          "source": "derived",
          "formula": "package_wage * 1.5",
          "alternative_formula_per_notice": "(60.42 - 5.18) * 1.5 + 5.18",
          "alternative_value": 87.04,
          "discrepancy_with_notice": true,
          "cba_citation": "Article 8 §30",
          "policy_choice": "rate_sheet_simple",
          "confidence": 0.99
        },
        "fringes.Pension": {
          "source": "rate_notice",
          "file_id": "f-704-2026-07-01-001",
          "page": 1,
          "label_in_pdf": "Pension Fund",
          "confidence": 0.99
        },
        "fringes.RESA": {
          "source": "rate_notice_subcomponent",
          "file_id": "f-704-2026-07-01-001",
          "page": 1,
          "label_in_pdf": "(RESA - $1.35)",
          "confidence": 0.99
        }
      },
      "row_confidence": 0.99
    }
    // ... 12 more rows for Foreman, JW, Class 10..1
  ],

  "validations_passed": {
    "total_package_checksum": { "passed": true, "computed": 87.52, "expected": 87.52 },
    "apprentice_pct_check": { "passed": true },
    "range_checks": { "passed": true },
    "yoy_delta_sanity": { "passed": true, "warnings": [] }
  },

  "rate_sheet_confidence": 0.98,
  "auto_publish_eligible": true
}
```

---

## 6. Formula DSL

A small expression language used in Profile `wage_formula` fields. Designed to be:
- **Readable** (similar to algebraic notation)
- **Parseable** (deterministic AST)
- **Auditable** (every formula execution preserved with input values for provenance)

### Grammar (informal)

```
expression       := term (operator term)*
operator         := + | - | * | /
term             := literal | reference | function_call | parenthesized
literal          := NUMBER
reference        := scope ":" path
scope            := "anchor" | "package" | "fringe" | "deduction" | "schedule" | "manifest" | "extracted"
path             := IDENTIFIER ("." IDENTIFIER)*
function_call    := function_name "(" [arg ("," arg)*] ")"
function_name    := "round" | "min" | "max" | "sum" | "case" | "when"
parenthesized    := "(" expression ")"
```

### Reference types

| Reference | Resolves to | Example |
|---|---|---|
| `anchor:zone:Building` | The anchor wage for the named zone, from extracted Rate Notice | `49.06` (704 base wage Aug 2024) |
| `package:Foreman` | The wage of another package within the same row matrix | `58.42` |
| `fringe:Pension` | A fringe value from extracted Rate Notice | `7.45` |
| `deduction:Union Assessment` | A deduction value from extracted Rate Notice | `4.86` |
| `schedule:foreman_premium` | The amount from a date-keyed schedule based on effective_date | `4.50` |
| `manifest:wage_anchor.initial_value` | A value from the RuleManifest | `48.73` |
| `extracted:total_package_printed` | A value from the ExtractedDocument | `87.52` |
| `package_wage` | Special: the current row's wage (used in OT formulas) | `53.92` |
| `sum_deductions` | Special: sum of all deductions for current row | `5.18` |
| `effective_date` | Special: the period start date (used in date-keyed schedules) | `2026-07-01` |

### Function library

| Function | Description | Example |
|---|---|---|
| `round(expr, granularity)` | Round to nearest granularity | `round(50.55, 0.05)` = `50.55` |
| `min(a, b, ...)` | Minimum of arguments | `min(0.85, 1.0)` = `0.85` |
| `max(a, b, ...)` | Maximum | `max(0.40, 0.10)` = `0.40` |
| `sum(reference_path)` | Sum of values matching path | `sum(deductions.*)` = `5.18` |
| `case(when, when, ..., else)` | Conditional | see below |

### Conditional expressions

```yaml
foreman_premium:
  formula: |
    case(
      when(effective_date >= "2024-08-01", 4.50),
      when(effective_date >= "2023-08-01", 4.25),
      when(effective_date >= "2022-08-01", 4.00),
      else: error("foreman_premium not defined for date " + effective_date)
    )
```

### DSL examples (one per union)

**537 — Power & Gas General Foreman**
```
package:Foreman * 1.25
```
With CBA citation tag: "Yellow Book Article II §6(c)"

**704 — Standard 1.5x OT**
```
package_wage * 1.5
```

**821 — Apprentice Year 5 (multi-anchor)**
```
anchor:zone:Low-Commercial * 0.85
```

**483 — Foreman 1 with multi-year schedule**
```yaml
formula: |
  case(
    when(effective_date >= "2028-08-01", anchor:zone:Building + 13.00),
    when(effective_date >= "2027-08-01", anchor:zone:Building + 12.00),
    when(effective_date >= "2026-08-01", anchor:zone:Building + 11.00),
    when(effective_date >= "2024-08-01", anchor:zone:Building + 10.00),
    else: error()
  )
```

**281 — Year 2-A apprentice with $0.05 rounding**
```
round(anchor:zone:Building * 0.55, 0.05)
```

**704 — Notice-correct OT (alternative formula)**
```
(package_wage - sum_deductions) * 1.5 + sum_deductions
```

### Implementation: AST-based evaluator

```python
from dataclasses import dataclass

@dataclass
class Reference:
    scope: str
    path: list[str]
    
@dataclass
class FunctionCall:
    name: str
    args: list

@dataclass
class BinaryOp:
    op: str
    left: object
    right: object

@dataclass
class Literal:
    value: float

class FormulaEvaluator:
    def __init__(self, context: ResolverContext):
        self.context = context
    
    def evaluate(self, expr: str) -> tuple[float, dict]:
        """Returns (value, trace) where trace is the audit record."""
        ast = self.parse(expr)
        result, trace = self._eval(ast)
        return result, {
            "formula": expr,
            "trace": trace,
            "inputs_used": self._collect_inputs(ast)
        }
    
    def _eval(self, node):
        if isinstance(node, Literal):
            return node.value, {"literal": node.value}
        if isinstance(node, Reference):
            value = self.context.resolve(node.scope, node.path)
            return value, {"reference": f"{node.scope}:{'.'.join(node.path)}", "value": value}
        if isinstance(node, BinaryOp):
            l, lt = self._eval(node.left)
            r, rt = self._eval(node.right)
            return self._apply_op(node.op, l, r), {"op": node.op, "left": lt, "right": rt}
        if isinstance(node, FunctionCall):
            return self._call_fn(node)
```

The trace is preserved in the cell's provenance, so a user can see exactly how a value was computed.

### Why a DSL (vs Python lambdas in Profile)?
- **Safety:** no arbitrary code execution from user-authored Profiles
- **Auditability:** every evaluation is logged with inputs
- **Portability:** Profiles are language-agnostic JSON/YAML
- **AI authoring:** easier for Bedrock to suggest Profile rules in DSL than to suggest Python
- **Validation:** can statically check formulas before runtime

---

## Schema versioning

All schemas live in `docs/schemas/v1/` (JSON Schema files). Version bumps follow:
- **Major** (1.0 → 2.0): breaking change (field renamed/removed)
- **Minor** (1.0 → 1.1): backwards-compat addition (new optional field)
- **Patch** (1.0.0 → 1.0.1): clarification

Each schema artifact (`ExtractedDocument`, `CanonicalRateSheet`, etc.) declares its `$schema` URL. Engine version-checks on read.

---

## Sample files (to be created in Phase 1 of build)

```
docs/samples/
├── profile_pipefitter_537.yaml
├── profile_sprinkler_704.yaml
├── profile_sprinkler_821.yaml
├── profile_sprinkler_483.yaml
├── profile_sprinkler_281.yaml
│
├── extracted_704_2026-01.json       (ExtractedDocument fixture)
├── manifest_704_2022-2027.json      (RuleManifest fixture)
├── canonical_704_2026-01.json       (CanonicalRateSheet fixture)
│
└── (similar for the other 4 unions)
```

These are the regression test fixtures — engine output must match them byte-for-byte (or value-for-value) for the test suite to pass.

---

## Next docs in this folder
- `05_Provenance_and_Citations.md` — the provenance fields above in depth, with citation pipeline
- `06_Implementation_Plan.md` — when each schema gets implemented
