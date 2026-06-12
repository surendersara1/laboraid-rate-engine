# Current Status & Validation

## Validated against your sheets

The engine's output is checked **cell-by-cell against the client's own rate
sheets** (the validation oracle), keyed by zone, classification, and indenture
cohort across every fund column.

| Union | Result |
|---|---|
| **Sprinkler 281** (indenture cohorts — the hardest case) | **Row-for-row, cell-exact** vs the client sheet, including the two indenture cohorts split out of a single apprentice wage sheet and the per-cohort fund differences |
| **Sprinkler 704** | All **13 classifications** correct (General Foreman, Foreman, Journeyman, Apprentice Class 1–10) |

## Onboarding proven on unseen unions

The system was given unions it had **never seen** — only their CBAs — and built
their profiles and extracted their rate sheets with **no code changes**:

- **Sprinkler 709** — onboarded from its CBA; all classifications extracted.
- **Pipefitter 12** — onboarded from a **130-page** CBA (handled by splitting the
  document to fit the AI's input limits).

This is the scalability proof: adding a union is a document upload, not an
engineering task.

## The trust model in action

- **Provenance:** every published rate sheet links to the exact source PDFs that
  produced it, and each value records the documents and AI model behind it.
- **Gaps, not guesses:** where a value genuinely isn't in the uploaded documents
  (for example, a national fund published on a separate schedule), the system
  **flags it** and tells the reviewer which document to add — it never fabricates.
- **Dual control:** review and approval require two different people; the database
  enforces it.

## In progress

A few unions with complex wage structures (multiple work zones, apprentice
percentage ladders) extract the structure perfectly but still mis-derive some
per-classification wages; these are being tuned and re-validated against the
client sheets. As above, anything the documents don't contain is flagged rather
than guessed.

## At a glance

- **Pipeline:** Plan → Synthesize → Publish, live on AWS
- **AI:** Claude Opus 4.5 (Amazon Bedrock)
- **Outputs:** canonical CSV + Excel, with full provenance
- **Workflow:** two-person review, approval, and publish — fully audited
- **Scaling:** new unions onboard from their CBA, no code changes
