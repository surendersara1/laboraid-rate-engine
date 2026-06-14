# Decision 6 — Learning loop: learned-corrections store + steering feed

**Status:** 🟡 DECIDED IN PRINCIPLE — **not built**. The only Phase-2 decision with real
remaining build work. Deferred until after the Tuesday CTO demo.
**Context:** Phase 2 improvement loop; closing the loop so corrections improve future runs.

## Decision (intent)
When a human **approves** a corrected cell, that confirmed correction should make the
**next** extraction/synthesis smarter — the system *learns* rather than re-asking the same
question every period. Two complementary feeds (to be designed one-at-a-time, like D1–D5):

1. **Steering feed (cheap, near-term):** the synthesizer/improver consults a
   **learned-corrections store** of past human-confirmed values for the same
   {union, package, column, context} and uses them as few-shot guidance / priors when a new
   period's source is ambiguous.
2. **Profile fix (durable, structural):** when a correction reflects a *structural* truth
   (a canonical name, a multiplier, a cohort rule) it should be folded back into the union
   **`profile_yaml`** — the backbone that steers all future runs. This is the D2 "profile-fix"
   `source` value, already reserved in `improvement_changes`, finally given a write path.

## Why
- Without it, every period repeats the same manual corrections — the loop is *human-in-the-
  loop* but not *learning*. The IP story to the CTOs is "it gets better"; D6 is that claim.
- Keeps the no-fabrication rule: learned values are **human-approved**, so feeding them
  forward is attribution-preserving, not invention.

## Open design questions (for the one-at-a-time discussion)
- Store location: new Aurora table (`learned_corrections`) vs extend the profile. Likely
  **both** — operational store for steering, profile for structural truths.
- Trigger: on **approve** (only confirmed corrections learn) vs on every correction.
- Scope/key: how broadly a learned value generalizes ({local,package,column} vs +zone +
  period-shape) — too broad risks wrong carry-over, too narrow never fires.
- Guardrail: a learned value is a **prior/suggestion**, never an auto-applied dollar value;
  it still flows through extraction/synthesis + human review.
- Profile write-back needs its own versioning + approval (changing the backbone is high-blast-radius).

## Already in place (foundations)
- `improvement_changes.source` already includes `profile-fix` as a value (reserved).
- The union `profile_yaml` is the single source of structure/multipliers — the natural
  target for structural learning.
- `cell_corrections.status` (open|applied|superseded) gives a hook for "approved → learn".

## Not started
No store, no steering read, no profile write-back path exists yet. Build deferred
post-demo; the live loop demos fully without it.
