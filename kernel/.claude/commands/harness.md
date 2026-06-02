---
description: Run the generator/evaluator harness — plan, then loop build → evaluate until the work passes.
argument-hint: <one-line product brief>
model: opus
---

You are orchestrating a long-running build using three subagents. The product
brief is:

$ARGUMENTS

If the brief is empty, ask the user for one before continuing.

## Knobs
- MAX_ITERATIONS = 4   (build → evaluate cycles before stopping)
- Pass/fail thresholds live in `.claude/harness/criteria.md`.

## Procedure

**1. Plan.**
Delegate to the `planner` subagent with the brief. It writes
`.claude/harness/spec.md`. When it returns, show the user the scope summary and
**pause for approval before building.** If they want changes, re-run the planner
with their notes. Only continue once they approve.

**2. Build / Evaluate loop** (up to MAX_ITERATIONS):
  a. Delegate to the `builder` subagent. On iteration 1 it builds from the spec;
     on later iterations it reads the latest evaluation and revises.
  b. Delegate to the `evaluator` subagent. It runs the app and returns a verdict.
  c. Append the verdict to `.claude/harness/evaluation-log.md`, prefixed with the
     iteration number and a timestamp, so each round is inspectable.
  d. If the verdict is **PASS**, stop the loop.
  e. If **FAIL**, continue to the next iteration (the builder will pick up the
     findings). If you hit MAX_ITERATIONS without a pass, stop and report the
     remaining gaps rather than looping forever.

**3. Report.**
Summarize: final verdict, what was built, which criteria passed, and any
remaining issues the evaluator flagged. Point the user to `spec.md` and
`evaluation-log.md`.

## Notes
- The evaluator must never modify source — it only reports. The builder is the
  only agent that writes code.
- Keep the user in the loop at the planning checkpoint; after that, run the
  build/evaluate cycles autonomously until PASS or MAX_ITERATIONS.
