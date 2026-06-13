# Decision 2 — Re-synthesis granularity: "surgical change, full context"

**Status:** ✅ DECIDED — build pending (part of the Stage-2 improver).
**Context:** when the human comments/overrides cells and clicks "Improve", how much does
the agent re-do?

## Decision
**Change only what was flagged; reason over everything.** The agent emits the flagged
cell(s) + their derived dependents; it never silently changes a cell the reviewer didn't
touch. It *reads* the whole sheet + profile + sources to reason well.

## Build spec
1. **Change scope** = only the flagged cell(s) + their deterministically-derived
   dependents (OT 1.5×/2×, differential, P&G). Never modify an unflagged, independent cell.
2. **Reasoning context** = full sheet + union profile + source PDFs + the human comment as
   steering. The agent reads everything; it emits only the flagged cells.
3. **Two correction depths — classify each:**
   - **Value-level** (wrong number) → fix within the existing profile. *(first build)*
   - **Structural** (missing fund/column, wrong cohort window, wrong canonical name, wrong
     multiplier) → update **`profile_yaml`**, then re-synthesize the affected region against
     the corrected profile. *(scope: phase 2b — see Scope below)*
4. **Override (deterministic):** apply the human value verbatim; never LLM-reguess it;
   recompute its derived columns via the shared `rate_math` core.
5. **Comment (LLM):** targeted re-synthesis of the flagged cell(s) via Bedrock, steered by
   the comment + profile + sources → corrected value **+ source citation**; gaps stay gaps.
6. **Derived recompute:** any base-wage change → deterministic recompute of its derived
   columns via `rate_math` (single source of truth, **cohort-aware**, Decimal half-up);
   logged as "recomputed from your override/fix."
7. **Profile = the contract:** the result must conform to the union profile (funds, columns,
   cohorts, canonical names); the **Critic validates conformance** (the check Phase 1 dropped).
8. **Untouched cells:** carried over byte-identical, provably unchanged (auditable).
9. **Output:** a new version **v_{n+1}** + a per-cell **change log** — `prior → new`, source
   (`override | resynth | recompute | profile-fix`), provenance/citation, confidence.
10. **Consistency prerequisite (Stage-2 step 0):** derived-column + checksum math lives in
    **one shared `rate_math` module** used by *both* the synthesizer and the improver,
    **verified to reproduce the 5 POC unions' stored sheets** — so an improved sheet is
    guaranteed consistent with how the original was produced.

## The profile's role (backbone)
The profile is not just a multiplier lookup — it is (a) the **steering** for re-synthesis,
(b) the **multiplier** source, (c) the **validation contract** the result must conform to,
and (d) itself a **target** of correction (structural fixes update `profile_yaml`, which
improves every future synthesis for that union — the structural learning loop).

## Scope for the first build
- **In:** value-level corrections (override + comment), deterministic recompute, critic
  conformance check, new-version write-back, the shared `rate_math` core + its 5-union
  verification.
- **Deferred to 2b:** structural / `profile_yaml` editing (item #3 structural branch).

## Why not the alternatives
- **Pure per-cell, no context:** too blind — corrections ripple into derived columns and
  the LLM needs row/sheet structure.
- **Full-sheet re-run with steering:** expensive every Improve, and — the real defect —
  can silently change cells the human already accepted. Re-deriving an approved value you
  weren't asked to touch is unacceptable in a legal/financial system.
