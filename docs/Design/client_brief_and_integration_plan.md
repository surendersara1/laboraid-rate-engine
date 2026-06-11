# Client Brief and Integration Plan

> **2026-06-10 update.** M1-M6 + OCR pre-processing landed and verified
> against all 5 POC unions (see [`CTO_END_TO_END_FLOW.md`](CTO_END_TO_END_FLOW.md)
> §8 for the live test results table). The end-to-end story is now in
> three companion docs:
> - **Flow:** [`CTO_END_TO_END_FLOW.md`](CTO_END_TO_END_FLOW.md) — the 14-step walkthrough.
> - **Services:** [`CTO_SERVICE_INVENTORY.md`](CTO_SERVICE_INVENTORY.md).
> - **Errors + logs:** [`CTO_ERROR_AND_LOGGING_REFERENCE.md`](CTO_ERROR_AND_LOGGING_REFERENCE.md).

**Status:** Complete client input. **No further documents incoming from
Dan or the LaborAid team** — what's catalogued here is the full input
package they have shared, and it's everything we will be working from.

**Sources:**
- `From Customer/Master_Excels/Meeting Transcript.docx` — Dan's verbal
  walkthrough of his manual process
- `From Customer/Master_Excels/LaborAid Claude SOP 2026.06.09.pdf` —
  Dan's written Standard Operating Procedure (v 6/9/2026)
- `From Customer/Master_Excels/MASTER_DATA_REVIEW_RULES.md` — coder's
  12-rule deterministic review framework for the master lists
- `From Customer/Master_Excels/Master Fund List.xlsx` — canonical fund
  names, types, trustees, addresses (141 rows; 50 relevant to our 5
  unions)
- `From Customer/Master_Excels/Master Package List.xlsx` — canonical
  classification names with "Can Be Assigned To Employee" flag (105
  rows)
- `From Customer/Master_Excels/Master Zone List.xlsx` — canonical zone
  names by union (151 rows; 11 relevant to our 5 unions)
- `From Customer/CBAs/` — actual CBA + Rate Notice + Wage Sheet PDFs
  per union
- `From Customer/Rate Sheets/` — Dan's produced rate sheets (xlsx + csv)
  per union — the ground-truth output we should match

**There is no separate "Dan prompt" document.** The SOP IS the
authoritative framework Dan uses to instruct his Claude workspace.
Our task is to embed it as system context in the Bedrock pipeline.

---

## Part 1 — What Dan does (manual process)

From the meeting transcript and SOP §4:

1. Contractor signs on with a new union → Dan obtains the CBA + Rate
   Notice (+ for some unions, indenture-split Apprentice Wage Sheets).
2. Dan reads the CBA, ignores most of it, focuses on **wage rates and
   fringe benefit packages** (Sections 1.1 and 2.2).
3. He builds the rate sheet in Excel, choosing column headers that
   match the **Master Fund List `Fund Name`** verbatim, because the
   LaborAid calculator's upload depends on exact-name matching.
4. **CBA prose always defaults to the Journeyman rate.** Apprentice +
   Foreman come from either the addendum, the Rate Notice, or a stated
   differential/percentage (SOP §4.4 and Rule 9 of the Review Rules).
5. Originally 6 hours per union per period; with his Claude workspace,
   Dan has cut it to ~30 minutes. He calls his workspace "still a work
   in progress, maybe 80% complete".
6. Trustee/address data is mostly **NOT** in the CBA. Dan asks the
   contractor for it manually. **Explicitly out of scope for our
   automation.**

---

## Part 2 — The five master artifacts

### 2a. SOP (PDF) — the framework

The SOP is structured as a 12-page handbook with 8 main sections + an
onboarding checklist. Key sections to plug into our pipeline:

| § | Content | Where it plugs in |
|---|---|---|
| §2 Terminology | Worker classifications, fringe benefit categories, CBA document structure | LLM system-prompt header (Move 1 below) |
| §3 Source documents | The CBA / trustee sheet / contractor-internal hierarchy | Publisher merge `source_priority` |
| §3.4 Document Hierarchy | Newest Rate Notice > older addenda > Trustee > Contractor | Conflict-resolution rule in Publisher |
| §4 Interpretation process | 6-step recipe Claude should follow per document | Per-doc-type prompt body |
| §5 Rate sheet build standards | Two-tab Excel, formula-linked wages, gray-fill deductions, INDEX/MATCH | xlsx renderer rewrite |
| §6 Edge cases | Probationary mapping, dues split, annuity Class 1 exclusion, pre-existing discrepancy handling | Deterministic post-extraction rules |
| §8 Working with Claude | What Claude is good at + 5-item review checklist | Maps to our existing Provenance + Gap Report panels |
| Appendix | 12-item new-local onboarding checklist | New "Onboarding" workflow in Admin UI |

#### SOP §2.1 — Worker classifications

| Class | Wage / Fringe rule |
|---|---|
| Journeyman (JM) | Full wage + full fringe package. **CBA prose always defaults to JM rate.** |
| Foreman / GF | Wage **premium % above JM**. Must be formula-linked. |
| Indentured Apprentice | JATC-registered. Tier by class (1-10) OR year (1-5). Wage = **% of JM** per tier. |
| Probationary / Unindentured | Pre-JATC. Often $0 wage or flat rate. Fringe eligibility varies per CBA. |
| Office staff / member-owner | **NOT in the CBA** — only in trustee rate sheet (NASI for sprinkler). Source not catalogued in our pipeline today. |

#### SOP §2.2 — Fringe benefit categories

H&W, Pension (local + possibly UA National Pension), Annuity (separate
from pension; first-year apprentices often excluded), Education /
Training Fund (EBF), Industry Advancement Fund (IAF/SIS/ITF —
probationary may be $0), Union Dues (% or $/hr, often differs by class
— common pattern: 5% JM/upper, 2.5% Class 1-4).

**Important structural note** (verbatim from SOP §2.2):

> Some funds appear more than once in the LaborAid system. This happens
> when a union collects a fund contribution on behalf of the contractor
> and then remits to the fund office, versus situations where the
> contractor pays the fund directly. Both collection paths need to be
> captured correctly in the rate sheet and the platform.

#### SOP §2.3 — CBA document structure

- **Main CBA body** — scope, classifications, work rules. *Wage and
  benefit rates are RARELY here.*
- **Addenda / Appendices** — where wage scales, fringe rates, and
  contribution schedules actually live.
- **Rate notices / letters of understanding** — interim updates that
  *supersede figures in the addenda*.
- **Side letters** — employer-specific modifications. Easy to overlook.
- **Trust agreements** — eligibility rules sometimes only here.

#### SOP §5 — Rate sheet output format (different from what we build today)

| Element | Standard | Our current state |
|---|---|---|
| Workbook structure | Two tabs: `Articles` (CBA section/page references) + `[Start Date]` rate data | One flat sheet |
| Multi-period CBA | One rate data tab per interval | Single tab |
| Font / size | Arial 11 | Default |
| Header | Bold | Default |
| Dates | MM/DD/YY | ISO date |
| Currency cells | 2-decimal $ | 2-decimal $ ✓ |
| Percentage cells | 2-decimal % | Numeric |
| Deduction columns | **Gray fill** | No fill |
| Foreman / Apprentice wages | **Formula-linked** to JM cell (`=JM × premium%` / `=JM × class%`) | Hardcoded values |
| Education Fund | Master-cell reference (`=$S$5`) | Hardcoded |
| Negotiated Wage tab | INDEX/MATCH lookups | Not produced |

#### SOP §6 — Edge cases (must bake into rules)

| Edge case | Rule | Status today |
|---|---|---|
| Unindentured → Probationary Apprentice | Map "Unindentured" to "Probationary Apprentice" with note | Pass-through, no mapping |
| Union dues % split | Confirm split in CBA addenda; flag if all classes have same % | Trust LLM output |
| Annuity Class 1 exclusion | Don't assume annuity eligibility follows H&W eligibility | Trust LLM output |
| Pre-existing discrepancies | Flag, don't silently correct. Tag as "pre-existing" vs "introduced this run" | F3 conflict-tracking shipped, not framed this way |
| Referenced documents | Check Trust Agreement / UA national policy before concluding language is absent | Silent NULL today |

### 2b. Master Fund List (xlsx)

141 total rows; 50 relevant to our 5 unions. Per-row schema:

`ID` (F<local>NNN for union-specific, F000NNN for shared national funds),
`Fund Name`, `Trustee Name`, `Fund Type` (Contribution / Deduction —
drives gray-fill convention), `Optional Fund` (Y/blank — drives the
"conditional / non-NFSA only" annotation), `Fund Class`, `Group`,
`Trade`, `Location`, `Check Payable To`, `Address 1`, `Address 2`,
`City`, `State`, `Zip`, `Percentage Based Fund` (Hourly / Percent / Both
— drives $ vs % rendering), `Last Updated`.

Per-union fund counts:

| Local | # union-specific funds | Notable optional/conditional |
|---|---|---|
| 281 | 4 | — |
| 483 | 7 | NCFPCG 483, Bay Area IP Fund 483, Union Dues 1 483 (Both $/%) |
| 537 | 13 | Vacation 537, UA PAC 537 |
| 704 | 5 | — |
| 821 | 4 | PAC 821 |

Plus **17 shared `F000*` funds** referenced across multiple sprinkler
locals (Health & Welfare, RESA, SIS, Pension, UA International Training,
Apprenticeship Training, Metal variants, UA National Pension, UA LMCF,
etc.).

### 2c. Master Package List (xlsx)

105 rows total — applies across all unions; matching is by package name
WITHIN the union's naming family.

Per-row schema: `ID` (P000NNN), `Package Name`, `Can Be Assigned To
Employee` (Yes/No — drives whether a cell can be assigned to a real
worker vs a differential-only row).

Notable name families (count of variants):
- Apprentice (19): `Apprentice Class 1-10` (483 family), `Apprentice
  Year 1-5` (537/704 family), `Apprentice Year 2-A`/`2-B` (281),
  `Apprentice Year 4/5 Licensed`
- Foreman (19): `Foreman`, `General Foreman`, plus 17 union-specific
  variants by crew size
- Metal Shop (8): with SIS tier variants
- MES (7): Trainee Class + Serviceman variants
- Office Employee (5): regular, +RESA, +Pension, +SIS, +Pension&RESA
- Owner (5): various contribution combinations
- Fabricator (5): Plan A/B × SIS/No-SIS
- Singletons: Journeyman, Tradesman, Master Tradesman, Welder, etc.

### 2d. Master Zone List (xlsx)

151 rows total; only 11 relevant to our 5 unions:

- 4 generic (Union = "All"): `Building`, `Residential`, `Metal`, `Office`
- 821-specific: `Industrial`, `Commercial`, `Low Commercial` (note: not
  "Low-Commercial")
- 537-specific: `Air Conditioning`, `Air Conditioning Prefabrication`,
  `Commercial`, `Commercial Prefabrication`

Drift to handle (called out in MASTER_DATA_REVIEW_RULES.md):
- 821 sheet says `Low-Commercial`, master says `Low Commercial` (hyphen
  drift) — Rule 10 disposition: reconcile to one spelling
- 537 master rows (`Air Conditioning`, `Commercial`, prefab variants)
  do NOT cover the 537 sheet's actual zones (`Power & Gas`, `Building`)
  — Rule 10: sheet wins, update master

### 2e. MASTER_DATA_REVIEW_RULES.md (the other coder's 12-rule framework)

A deterministic post-extraction validation framework. Every rule reads
"every name resolves to a master entry, or is explicitly disposed":

1. Scope review by F<local>* + referenced F000* funds
2. Every fund column header resolves to a Master Fund List `Fund Name`
3. One document line ↔ possibly several master funds (verify splits
   sum); one master fund ↔ possibly several sheet columns (tier
   expansions)
4. `Fund Type` (Contribution/Deduction) must agree with document
   framing (withholding ⇒ Deduction; employer-paid ⇒ Contribution)
5. `Percentage Based Fund` ($ vs %) must match document language
6. Package names resolve to master, in the right naming family
7. Zone names resolve to master (Union=<local> rows + Union=All rows)
8. Indenture-date variants add `Indentured Date is Before/After` key
   columns and one row per package per window
9. Values come ONLY from documents; master holds names only
10. Every name mismatch gets an explicit disposition: fix sheet,
    reconcile drift, add master row, or update master (sheet wins over
    master)
11. Trustee/address validation per Master Fund List (out of our scope
    per Dan)
12. Optional funds carry their applicability condition

---

## Part 3 — Integration plan for the Bedrock pipeline

Three architectural moves. None implemented yet — they are the next
work block.

### Move 1 — System prompt for every LLM extraction = SOP §2 + §4 verbatim

Replace our four ad-hoc prompts (`_RATE_NOTICE_PROMPT`, `_CBA_PROMPT`,
`_WAGE_RATE_SHEET_PROMPT`, `_APPRENTICE_SCALE_PROMPT`) with a shared
header that carries Dan's SOP §2 (Terminology) + §4 (Interpretation
Process) verbatim, then a per-doc-type body specifying the specific
extraction step.

Concrete structure:

```
SYSTEM PROMPT (every LLM call):

  You are a CBA interpreter for LaborAid. The full domain context is:
  
  [SOP §2 verbatim — Worker classifications, fringe benefit categories,
   document structure]
  
  [SOP §4 verbatim — Six-step interpretation process]
  
  For this specific document, your task is [per-doc-type body]:
  
  [Rate Notice prompt body OR CBA prompt body OR Wage Rate Sheet prompt
   body OR Apprentice Scale prompt body]
  
  Master lists for THIS union (Local <NNN>):
  
  - Master Fund List: [filtered JSON]
  - Master Package List: [filtered JSON]
  - Master Zone List: [filtered JSON]
```

This gives Claude the same context Dan operates with on every call, so
the output will match Master List naming by construction.

### Move 2 — Inject the relevant master sheets as structured context

At extraction time, look up the union being processed and inject:

- Master Fund List rows where `Union = Local <NNN>` + the F000* shared
  rows the trade references
- Master Package List rows whose `Package Name` is in the union's
  naming family (e.g., `Apprentice Class N` for 483; `Apprentice Year
  N` for 537/704)
- Master Zone List rows where `Union = Local <NNN>` + `Union = All`

Storage: ship the 3 master xlsx as flat JSON dictionaries embedded in
a `master_data` Python module, OR upload to S3 and have Lambda load
them at cold start. The simplest first cut is embed-in-Python (data is
small, change cadence is monthly).

### Move 3 — Deterministic Rule 1-12 validation post-extraction

After the LLM emits the canonical CSV, run the 12 rules from
`MASTER_DATA_REVIEW_RULES.md` deterministically and emit a structured
**disposition report**:

Each column / package / zone gets one of:
- `OK` — resolved cleanly to master
- `NEAR_MATCH(suggestion)` — fuzzy match found; reviewer confirms
- `NOT_FOUND` — genuinely new entity → "needs master update" pill
- `DRIFT(suggestion)` — small spelling/punctuation difference (`Low-Commercial` vs `Low Commercial`)

Surface in the Inbox gap banner + the cell Provenance panel + the
gap_report.json artifact.

### Move 4 — Two-tab xlsx renderer with formula linking

Replace the current single-sheet xlsx generator with Dan's two-tab
format (§5.1):

- Tab `Articles` (the reference tab) — emit each fund/wage and its
  citation. The kernel/LLM should record page+section as it extracts,
  so we can render this.
- Tab `[Start Date]` (the rate data tab) — Arial 11 bold header,
  MM/DD/YY dates, 2-decimal $/%, gray fill for deduction columns,
  **formula-linked** Foreman and Apprentice wage cells.

Multi-period CBAs get multiple `[Start Date]` tabs (one per rate
change interval).

### Move 5 — Add an "Onboarding" workflow in the Admin UI

The SOP Appendix is a 12-item new-local onboarding checklist:

1. Obtain full CBA package
2. Obtain trustee rate sheet(s)
3. Gather fund logistics from contractor (trustee, contribution vs
   deduction, address, payment method)
4. Identify all classifications → map to master classification list
5. Confirm zone structure + count
6. Identify all funds + eligibility per class
7. Note any referenced docs not in package
8. Cross-reference CBA vs trustee + document discrepancies
9. Build rate sheet per §5 standards; validate zero formula errors
10. Dual-control review before upload
11. Confirm resolution of any discrepancies before going live
12. Add the local to Section 7 of the SOP

Wire this into the UI as a gated checklist per union. Block "production
upload" until the 12 items are all green.

### Move 6 — Dual-control gate in the UI

The SOP §5.6 explicitly says rate sheets are **dual control**. We have
approve/reject/override today, but no second-reviewer gate.

- Add a `reviewed_by` field on `rate_periods`, separate from
  `approved_by`
- Block `approve` until a different user marks `reviewed`
- Surface "needs review" vs "needs approval" as separate states in the
  Inbox

---

## Part 4 — What's still missing (and what's NOT coming)

| Item | Status |
|---|---|
| Customer's Claude prompt | **Not coming separately.** The SOP IS the prompt context. |
| Customer's prompt-engineering examples | **Not coming.** SOP §8 has the framework but no concrete examples. |
| NASI trustee rate sheet | Dan mentioned sending it; not yet seen. **Out of scope per his SOP §2.1 note** unless we want to handle office-staff/owner classifications. |
| Trustee addresses | **Out of scope per Dan** (contractor provides manually). |
| Side letters | None catalogued. **Out of scope unless customer surfaces them per-union.** |
| Trust agreements | None catalogued. **Out of scope unless customer surfaces them.** |
| Customer's worked examples of dual-control review | None catalogued. Will work from SOP §5.6. |

**Bottom line: input phase is closed.** What's in this brief is what
we will be building from.

---

## Part 5 — Suggested execution order

If/when greenlit for the next implementation push:

| Phase | Items | Estimated effort |
|---|---|---|
| Phase 1 | Move 2 (inject master sheets) + Move 3 (deterministic Rule 1-12 validation) | 2 days |
| Phase 2 | Move 1 (system prompt = SOP §2 + §4) + retest 5 unions | 1 day |
| Phase 3 | Move 4 (two-tab xlsx with formula linking) | 2-3 days |
| Phase 4 | Move 6 (dual-control gate) | 1 day |
| Phase 5 | Move 5 (onboarding workflow UI) | 2 days |

Total ~8-9 days of focused engineering to bring our pipeline up to Dan's
SOP standard. Comparable to the sprint we just completed.

Move 1-3 are the highest-impact (lift output quality + auditability to
Dan's standard). Moves 4-6 are polish that matters for production
operations but not for demo defensibility.

---

## Files in this brief

All located under `From Customer/Master_Excels/`:

- `Meeting Transcript.docx` (verbal walkthrough, ~3,500 words)
- `LaborAid Claude SOP 2026.06.09.pdf` (12 pages, 8 sections + appendix)
- `MASTER_DATA_REVIEW_RULES.md` (12 rules, illustrated with 483/537/281/821 examples)
- `Master Fund List.xlsx` (141 rows × 17 columns)
- `Master Package List.xlsx` (105 rows × 3 columns)
- `Master Zone List.xlsx` (151 rows × 3 columns)

Per-union source documents under `From Customer/CBAs/` and reference
output under `From Customer/Rate Sheets/`.
