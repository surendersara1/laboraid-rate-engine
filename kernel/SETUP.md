# Claude Code harness template

A three-agent build harness for Claude Code, modeled on Anthropic's
generator/evaluator pattern: a **planner** turns a one-line brief into a spec, a
**builder** writes the code, and a skeptical **evaluator** runs the app and grades
it against written criteria. A slash command loops build → evaluate until the
work passes.

Runs entirely on your **Max subscription** — no API key, no per-token billing.

## What's in here
```
.claude/
├── agents/
│   ├── planner.md      # brief  -> spec
│   ├── builder.md      # spec   -> code   (the generator)
│   └── evaluator.md    # code   -> verdict (the skeptical critic)
├── commands/
│   └── harness.md      # /harness orchestrates the loop
└── harness/
    └── criteria.md     # shared grading criteria — your main tuning surface
```

## Install
Copy the `.claude/` directory into the root of your project (merge it with any
existing `.claude/` you have). That's it — Claude Code auto-detects subagents in
`.claude/agents/` and commands in `.claude/commands/`.

Verify with `/agents` (you should see planner, builder, evaluator) and start
typing `/harness` to see the command.

## Run
From your project, in Claude Code:
```
/harness Build a browser-based habit tracker with streaks, reminders, and an AI coach
```
The harness will plan, **pause for your approval of the spec**, then loop
build → evaluate autonomously until it passes the criteria (or hits the
iteration cap). Run artifacts land in `.claude/harness/`: `spec.md`,
`build-notes.md`, `evaluation-log.md`.

## Tuning (in rough order of impact)
1. **`criteria.md`** — wording and thresholds steer output more than anything
   else. This is where most of your time should go.
2. **Models** — all three agents default to `opus`. On a Max plan that draws
   down your usage limits faster; switch the evaluator (and maybe planner) to
   `sonnet` in their frontmatter to stretch your limit. Set `model: inherit` to
   match your main session.
3. **`MAX_ITERATIONS`** in `commands/harness.md` — cost/quality tradeoff.
4. **Agent prompts** — read `evaluation-log.md` after runs; where the evaluator's
   judgment diverges from yours, edit `evaluator.md`. Out of the box, Claude is a
   lenient QA; tightening that prompt is the highest-leverage agent edit.

## A few honest caveats
- A multi-hour harness run is far more expensive (in time and usage) than a solo
  build. It earns its keep on tasks that sit beyond what one pass does reliably —
  for simple tasks, just prompt Claude directly.
- As models improve, some scaffolding stops being load-bearing. Periodically try
  removing a piece (start with the evaluator on easy tasks) and see if quality
  holds.
- Add a Playwright MCP server for real browser-driven evaluation — it makes the
  evaluator much stronger on UI and functionality. See `evaluator.md`.

## Suggested .gitignore
```
.claude/harness/spec.md
.claude/harness/build-notes.md
.claude/harness/evaluation-log.md
```
(Keep `criteria.md` in version control — it's part of the harness.)
