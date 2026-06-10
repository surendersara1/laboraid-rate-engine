# Demo Slides Outline (M4)

Single-deck Keynote/PowerPoint to drive the demo. ~10 slides, no
animations. Build Sunday/Monday. Live demo (described in
[demo_dry_run.md](./demo_dry_run.md)) is the main attraction; slides
are scaffolding around it.

---

## Slide 1 — Title

```
LaborAid Rate Engine
Automated PDF → Aurora → Reviewer → xlsx

NBS Solutions
Demo · 2026-06-16
```

Visual: simple. Logo top-left, date bottom-right. No clutter.

---

## Slide 2 — The problem (1 sentence)

```
Today: 1 union × 1 period × 1 rate sheet = 30 min of human time
You have ~600 locals × 12 periods/year = 3,600 rate sheets to produce.
That's 1,800 person-hours/year just on data entry.
```

Visual: bar chart, x-axis "# locals", y-axis "annual hours". Single big
bar at 1,800.

---

## Slide 3 — What we built

```
A rate-engine pipeline that converts customer PDFs into structured
rate sheets in Aurora, with human-in-the-loop review and full audit.

  PDF batch → S3 → SFN → [Kernel | LLM] → Publisher → Aurora → Inbox → xlsx
```

Visual: the pipeline arrow diagram.

---

## Slide 4 — Architecture

Three rectangles:
- **Inputs**: PDF upload + staging UI
- **Extraction**: deterministic kernel (5 unions) + Bedrock Claude
  Sonnet (everything else) + Publisher merge
- **Review**: Inbox, override, rework, approve, audit log

Below: "All five extraction paths (A/B/C/D/E) documented in
extraction_flow_for_client.md."

---

## Slide 5 — Live demo

Just a header: "Live demo — 6 minutes, 5 unions, 11 PDFs, $2.50".

Run the script in [demo_dry_run.md](./demo_dry_run.md).

---

## Slide 6 — Result table

The headline table from [5union_report.md](./5union_report.md):

```
Union    Period       PDFs  Coverage    Wall-clock  Cost
483      2026-01-01   3     100%        120s        $0.90
537      2026-03-01   1     100%        17s         $0.00
704      2026-01-01   2     100%        87s         $0.50
821      2026-01-01   2     100%        77s         $0.50
281      2026-01-01   3     95%         53s         $0.60
----------------------------------------------------------
TOTAL    —            11    avg 98.9%   5.9 min     $2.50
```

Highlight: **98.9% avg coverage. $2.50 total spend.**

---

## Slide 7 — Provenance + audit

Screenshot of the Provenance panel showing one cell with method icon,
source PDF, derived_from, confidence, conflicts.

Caption:
```
Every cell traces back to a source PDF + page + extraction method,
a derivation formula, or a documented Local convention.
Reviewer can comment, override, or trigger AI rework on any cell.
```

---

## Slide 8 — Reviewer workflow

Three screenshots side-by-side:
1. Approve/Reject action bar
2. Override dialog
3. Rework with AI dialog

Caption:
```
Human-in-the-loop is a first-class feature, not an afterthought.
Every action is audit-logged. Version chain on rate_periods so
nothing is silently overwritten.
```

---

## Slide 9 — Cost projection at scale

```
600 locals × 12 periods × $0.50 Bedrock avg = $3,600/year
Lambda + Aurora + S3 + DDB                  =   ~$500/month
                                              -------------
Total infra at customer's full scale: ~$10,000/year

(Compare: 1,800 hours/year of human time)
```

---

## Slide 10 — What's next

```
- Plug in customer's own prompts (today's prompts are 95-100%;
  customer's are 100% on the unions they've tuned). Strategic pivot.
- Multi-tenant isolation (today: single dev environment).
- Cost dashboard (per-period spend, monthly burndown).
- 6th and 7th kernel unions (after the LLM-only path is verified at
  scale).
- SOC 2 / audit hardening.

Demo ready: today.  Production ready: Q3.
```

---

## Speaker notes — talking points to weave in

- **Open with the math**: the 1,800 hours/year number. Customer feels
  the pain.
- **Mid-demo, name-drop architecture**: "kernel + LLM split is what
  makes this cheap and accurate. Kernel handles your high-volume known
  unions deterministically; LLM picks up the long tail."
- **Provenance is the audit story**: every claim about a value is
  traceable. Don't skim past the Provenance panel — that's the
  compliance argument.
- **Close with cost + scale**: $10K/year for 600 locals is the
  business-case headline.

---

## Backups

- Loom recording (M5) playing on a second screen if live demo breaks.
- All 5 rate_periods pre-staged in Aurora (already done).
- If a Bedrock cold start makes the live upload slow, jump straight to
  the Inbox; the 5 cards are already there.
