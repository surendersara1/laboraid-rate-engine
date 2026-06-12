# LaborAid Rate Engine — Overview

## The problem

Union benefit funds publish wage and fringe rates in PDFs — a collective
bargaining agreement (CBA) plus periodic rate notices and wage sheets. Turning
those into the structured **rate sheets** that remittance and benefits systems
consume is slow, manual, and error-prone, and every union formats theirs
differently.

## What LaborAid does

LaborAid reads a union's PDFs and produces a clean, structured rate sheet —
classifications (Journeyman, Foreman, apprentice years) × funds (Health &
Welfare, Pension, SIS, …), with overtime multipliers and indenture-cohort
handling — stored in a database and run through a two-person review and approval
workflow before it's published.

```
Union PDFs  ──►  AI extraction + mapping  ──►  structured rate sheet
(CBA + rate notices)                            (reviewed, approved, published)
```

## The principle: extraction + mapping, never fabrication

This is a benefit-fund product — the numbers govern members' money — so the
system is built to be **trustworthy by construction**:

- **Every dollar value is extracted by the AI from the source PDFs.** The system
  never copies answers from a pre-existing output sheet.
- **Values are mapped to each union's canonical schema** — the union's own fund
  and classification names — so the output drops straight into downstream systems.
- **Nothing is invented.** A value that isn't in the documents is **flagged as a
  gap**, not guessed — and the reviewer is told exactly which document to upload to
  fill it.

## Why it scales

Each union is described by a **profile** — its rate-sheet structure, learned by
the AI from its CBA. Onboarding a new union is uploading its documents; the
system learns the structure and starts producing rate sheets **with no code
changes**. Adding a union is a data change, not an engineering project.

## What you get

- **Accurate, structured rate sheets** in your canonical format (CSV + Excel).
- **Full provenance** — every value traces to the source PDFs that produced it.
- **Two-person control** — no single person can push rates to production.
- **A complete audit trail** of every extraction, review, approval, and publish.
