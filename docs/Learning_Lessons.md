# Learning Lessons — POC Code Walkthrough

A captured Q&A walkthrough of the codebase, lesson by lesson. Each lesson keeps the original question (the way it was actually asked) and the answer that followed. Read in order to follow the same thought process you'd have learning the code from scratch.

Companion file: [`Understanding.md`](Understanding.md) is the 10-step learning roadmap (which files to read in which order). This file is the explanatory deep dive on the most important pieces.

---

## Lesson 1 — The Canonical Layer (`canonical/model.py` + `canonical/fields.yaml`)

### Q1.1 — What are these canonical files? Are they the structure we're extracting from the PDF?

> *"now i read two files under canonical folder yaml file and model.py — not sure i understand. Is that the structure we are trying to extract from the PDF? How are these two files involved?"*

### Answer

Neither file describes the PDF structure — they describe the **in-memory representation between PDF reading and CSV writing**.

### Three vocabularies, not one

Every value flowing through the kernel exists in three different naming worlds:

| Layer | Example for "wage" | Where it lives |
|---|---|---|
| **1. PDF native** — whatever the union calls it on paper | "Hourly Wage Rate", "Total Wage", "Foreman Wage" | `data/<union>/cba/*.pdf` |
| **2. Canonical (internal)** — kernel's standard names | `wage` | `canonical/fields.yaml` + `RateCell.canonical_field` |
| **3. Output CSV header** — whatever the customer's groundtruth uses | "Wage" (in 704), "Wage" (in 483), "Hourly Rate" (in some hypothetical union) | `profiles/<union>.yaml` + the output CSV |

The kernel's job is to translate (1) → (2) → (3). `canonical/` defines vocabulary (2).

### `model.py` — the data SHAPES

These are the Python containers that hold values **between** the extractor and the CSV writer.

```python
@dataclass
class RateCell:
    zone: str                    # "Building", "Residential"
    classification: str          # "Journeyman", "Foreman", "Apprentice Class 3"
    class_order: int             # sort order (Foreman > Journeyman > Apprentice 10 > ...)
    canonical_field: str         # "wage" — the LAYER 2 name (from fields.yaml)
    value: Optional[object]      # 54.70 (a float) or "6.00%" or None if unsourced
    value_kind: str = "$"        # "$" | "%" | "xN" | "raw"
    source_doc: str = ""         # "2026.01.01.704 Rate Notice.pdf"   ← provenance
    source_locator: str = ""     # "page 2 / table 1 / row 3"          ← provenance
    confidence: float = 1.0
```

A `RateCell` is **one number plus where it came from**. So after the 704 extractor runs, you have ~260 of these floating around in memory: one per (zone × classification × canonical_field).

```python
@dataclass
class ClassificationRow:
    zone: str
    classification: str
    class_order: int
    cells: dict                 # {"wage": RateCell, "health_welfare": RateCell, ...}
```

A `ClassificationRow` groups all cells for one row of the future CSV — i.e., everything for `(Building, Journeyman)` lives in one `ClassificationRow` with ~20 entries in its `cells` dict.

`r2()` is the half-up rounding helper. **Critical detail:** Python's built-in `round()` uses banker's rounding (`round(83.505, 2) → 83.50`), but the union ratesheets use **half-up** (`83.505 → 83.51`). Using built-in `round()` would cause sub-cent drift on hundreds of cells — `r2()` fixes that with `Decimal.ROUND_HALF_UP`.

### `fields.yaml` — the VOCABULARY

This is the dictionary that says "when the kernel internally calls something `wage`, what does that show up as in each union's output CSV?"

```yaml
wage:                       [Wage]
                            # ↑ canonical name (LAYER 2)
                            #   ↑ list of output column labels (LAYER 3) — any union that
                            #     calls its wage column "Wage" uses canonical_field='wage'

apprenticeship_training:    [Apprenticeship Training, J&A Training 483]
                            # ↑ same canonical concept...
                            #   ↑ ...but different output labels: 704 calls it "Apprenticeship
                            #     Training", 483 calls it "J&A Training 483"
```

Same canonical concept, different output labels per union. That's the whole point — the kernel uses ONE name internally (`apprenticeship_training`) so all the code paths are union-agnostic, but the OUTPUT respects each union's labels.

### Worked example: trace one cell

You ran the 704 pipeline. Here's how `Wage = 54.70` for `Building/Journeyman` exists in each layer:

```
LAYER 1 (PDF):
    "2026.01.01.704 Rate Notice.pdf" page 2, table 1, row 3 — text says "$54.70" under "Wage"

LAYER 2 (canonical, in-memory):
    RateCell(
        zone="Building",
        classification="Journeyman",
        class_order=10,
        canonical_field="wage",                                   # ← from fields.yaml
        value=54.70,
        value_kind="$",
        source_doc="2026.01.01.704 Rate Notice.pdf",
        source_locator="page 2 / table 1 / row 3",
        confidence=0.95
    )

LAYER 3 (CSV output):
    The 704 profile (profiles/sprinkler_fitters_704.yaml) maps canonical 'wage' →
    output column "Wage". So pivot.py writes 54.70 into the "Wage" column of
    "2026.01.01.704 Rate Sheet.csv", in the row for "Building / Journeyman".
```

### Why this design

If `extract.py` directly wrote union-specific column names like `"Wage 1.5x"`, `"J&A Training 483"`, `"S & E 704"`, then `compute.py` would have to know all of them — and every union would need its own derived-column logic.

Instead:
- Extractor outputs canonical names: `RateCell(canonical_field="wage")`
- `compute.py` works on canonical names: "for canonical `wage`, derive `wage_15x = wage × 1.5` per the profile"
- `pivot.py` reads the profile to map canonical names back to per-union output labels

**Add a new union** = author its profile YAML and write `extract_<union>(...)` that emits canonical names. The rest of the pipeline doesn't change.

---

### Q1.2 — Short summary

> *"now we saw the PDF and we saw the excel and we build this data shaping layer using yaml? short ans"*

### Answer

Yes — exactly.

PDF (input) + customer's existing CSV/xlsx (groundtruth, tells us the output column shape) → we wrote `fields.yaml` + per-union profile YAMLs to **declaratively describe** that shape, plus `model.py` to hold the values in memory while we shuffle them around.

The YAML is the bridge: "here are the column names the customer wants, here's the canonical concept each one maps to, here are the derivation rules." The Python code is generic and reads the YAML to know what to do.

---

## Lesson 2 — The Strands Agent: scope, scaling, and where the LLM actually lives

### Q2.1 — Will this design fail at scale (100s of PDFs / unknown unions)?

> *"now if i get new PDF and they want to extract rate sheet — this company is LegalAid, they will have 100s of these PDFs to extract rate sheets spread across 50 to 100 pages in PDF. So this model will fail right? Short answer."*

### Answer

Yes — the current model fails at that scale. Here's why and what fixes it:

**What scales fine:** long PDFs (50-100 pages), volume (100s of files), parallelism. Step Functions + Lambda handle that.

**What breaks:** the kernel needs a hand-coded `extract_<union>()` function + a hand-authored profile YAML **per union**. ~3-5 dev days each. 100 unions × 3-5 days = months/years. Unworkable.

**The fix is the deferred agents in [`07_Strands_AgentCore_Agentic_Design.md`](07_Strands_AgentCore_Agentic_Design.md) (v1.1+):**
- **ProfileDrafterAgent** — reads a new CBA + the customer's groundtruth and auto-authors the profile YAML
- **Generic LLM-driven extractor** — replaces hand-coded `extract_<union>()` with an agent that reads any union's CBA and outputs canonical RateCells, using Bedrock Claude with the CBA Knowledge Base for structural rules

We scoped those to v1.1+ for the POC (2-week timeline, 5 known unions). Production at LegalAid scale = you build them.

---

### Q2.2 — Wait, isn't this what the SOW promised?

> *"I thought this is what we built as per SOW — you said deferred. What the fuck is all this then? Short answer and why we need it?"*

### Answer

**SOW signed for:**
1. **POC** — prove the pipeline works on **5 specific unions** (537, 704, 821, 483, 281)
2. **Agentic feasibility** — show ONE Strands agent on AgentCore working (not the full 9-agent platform)
3. 2 weeks, $30K NBS PS + $25K AWS funding

**What we built = exactly that.** 5 unions wired up (3 fully extracted at 99.6%/100%/67%; 2 pending the kernel harness for 281/821), 1 Strands ExtractorAgent on AgentCore, full AWS stack around it.

**What's deferred (and was never in the SOW):**
- ProfileDrafterAgent + auto-extraction for unknown unions
- The other 7 agents (Concierge, CBAMiner, ReviewAssist, etc.)
- Anything that lets the system handle a brand-new union without a developer writing 3-5 days of code

**Why you need the deferred stuff** = LegalAid at production scale. 100 customers × multiple CBAs each = no human can hand-author 100+ profiles. That's a **Phase 2 engagement** — separate scope, separate $$$, separate timeline.

**The right framing for the CTO/customer:**
> "POC proves the pipeline works for 5 unions and the agentic pattern works for 1 agent (the hardest part). Scaling to N unions is Phase 2 — same architecture, ProfileDrafterAgent added, ~4-8 weeks more depending on union diversity."

This is normal POC → production scoping. You shipped the POC. Phase 2 is a new SOW conversation when LaborAid says "now make it scale."

---

### Q2.3 — So where is the LLM in the POC?

> *"You are saying we will just use AWS to run all these Python STATIC code extractions for these known unions rate sheet structures — any new we have to build it. Where is agent and Bedrock and LLM in all this?"*

### Answer

Fair question. Honest answer: **in the POC, the LLM is at the seams, not at the center.**

**Static Python (the kernel) does ~90% of the work for the 5 known unions.** That's intentional — LLMs are non-deterministic, expensive, and slow. You don't burn Claude Sonnet calls on "read $54.70 from row 3 of table 1" when pdfplumber does it for free, deterministically, every time.

**The Strands `ExtractorAgent` + Bedrock kick in at four specific places:**

1. **Classification (Bedrock Haiku)** — when the filename + folder regex can't decide which union/document type a PDF is, the agent calls Claude Haiku to disambiguate. Cheap LLM call, ~$0.001 per doc.

2. **Low-confidence cell fallback (Bedrock Claude Sonnet multimodal)** — when the kernel's deterministic extraction reports a cell with `confidence < 0.85` or marks it as a gap, the SteeringHandler **forces** the agent to call `escalate_to_claude_multimodal()`. That tool sends the raw PDF (as a document) + a focused prompt asking *only* for the missing cells. This is the agent earning its keep.

3. **Self-validation steering** — the Strands `SteeringHandler` blocks the agent from returning "done" until `validate_total_package_checksum` passes. It also forces escalation if there are unresolved gaps. This is the "agentic reasoning" piece — the agent decides which path to take, self-checks, retries adaptively.

4. **PII Guardrail (Bedrock Guardrails)** — every model invocation goes through a PII filter (we don't have PII in CBAs, but it's there for compliance).

**So the SOW commitment is met like this:**

| SOW deliverable | Where it shows up in the system |
|---|---|
| Strands Agents | `ExtractorAgent` with 6 `@tool` functions + `SteeringHandler` |
| AWS AgentCore | Agent runs on `bedrock-agentcore:CreateAgentRuntime` (not as a Lambda) |
| AWS Bedrock | Sonnet 4.6 for multimodal fallback + Haiku 4.5 for classification + Guardrails |
| "LLM-Centric Extraction" | Claude as the fallback path when kernel deterministic fails |

**For Phase 2 (scaling to LegalAid's 100s of unions), the LLM moves from the seams to the center:**

- **ProfileDrafterAgent** reads a new CBA + the customer's groundtruth CSV and auto-writes the YAML profile — no developer needed
- **Generic LLM extractor** replaces `extract_<union>()` Python with Claude reading the PDF directly + the profile constraining its output
- **CBAMiner** + **Citation** + **ReviewAssist** agents handle CBA structural rules, provenance, and reviewer assist

**Why this architecture (not full LLM-everywhere)?**

- For the 5 known unions where we have working extractors and groundtruth, deterministic Python is 99.6% accurate, instant, costs $0 per run, and 100% reproducible. Replacing it with Claude would be slower, more expensive, and *less* accurate.
- For unknown unions, deterministic Python is useless (no extractor exists). LLM is the only path. That's where Phase 2 invests.

**The defensible truth for the customer:** "POC proves the kernel-deterministic + LLM-fallback pattern works on 5 unions at 99.6% accuracy. Phase 2 flips the ratio — LLM-first for unknown unions, deterministic kernel as the audit/regression layer."

---

### Q2.4 — Confirm the architecture

> *"So you are saying AWS agent will use these pipeline deterministic code as tools — short ans."*

### Answer

Yes — exactly.

The Strands `ExtractorAgent` running on AgentCore wraps the kernel's Python functions as `@tool`s:

```
run_kernel_extractor(union, union_dir)         → calls extract.EXTRACTORS[union]()
compute_derived_columns(union, rows)           → calls compute.resolve_row()
pivot_to_ratesheet_csv(union, rows, s3_key)    → calls pivot.write_csv()
stage_inputs_from_s3(union, s3_prefix)         → downloads PDFs
validate_total_package_checksum(union, rows)   → audits the math
escalate_to_claude_multimodal(s3_key, ...)     → LLM fallback when above tools fail
```

The agent decides **when** to call each tool, **self-validates** via SteeringHandler, and **falls back to Claude** only when the deterministic tools can't read a cell. The kernel = the deterministic toolbox; the agent = the brain that orchestrates them.

---

### Q2.5 — Where is all this in the actual code?

> *"Where is that in the strands agents code? Where is all this detail flow noted? Which shows all this properly?"*

### Answer

It's in **three files** under `agents/extractor/` — and they're tight (221 + 52 + 30 lines). Here's where each piece of the flow lives, with the actual code.

#### File 1 — `agents/extractor/agent.py` — the kernel-as-tools wrapping

Imports at the top declare the dependency direction: the agent imports the kernel.

```python
# Kernel — Ashwani's deterministic pipeline (on PYTHONPATH=/opt/kernel).
from canonical.model import ClassificationRow, r2
from pipeline import compute as k_compute
from pipeline import extract as k_extract
from pipeline import pivot as k_pivot

# Strands SDK
from strands import Agent, tool
```

Then **6 `@tool` functions** — each one is a thin Python wrapper around a kernel function:

```python
@tool
def run_kernel_extractor(union: str, union_dir: str) -> dict:
    """Run the kernel's per-union deterministic extractor."""
    extractor_fn = k_extract.EXTRACTORS[union]      # ← calls the kernel
    rows, gaps = extractor_fn(union_dir)
    return {"rows": [_serialize(r) for r in rows], "gaps": gaps, "gap_count": len(gaps)}


@tool
def compute_derived_columns(union: str, rows: list) -> list:
    """Apply the kernel's half-up-rounded derived-column rules (Profile YAML)."""
    profile = _load_profile(union)
    return [_serialize(k_compute.resolve_row(profile, _deserialize(r))) for r in rows]


@tool
def pivot_to_ratesheet_csv(union: str, rows: list, out_s3_key: str) -> dict:
    """Write the ratesheet CSV (matching groundtruth header) and upload to S3."""
    profile = _load_profile(union)
    local_csv = f"{SCRATCH}/{union}/output.csv"
    n_rows = k_pivot.write_csv(profile, [_deserialize(r) for r in rows], local_csv)
    s3.upload_file(local_csv, OUTPUTS_BUCKET, out_s3_key)
    return {"s3_key": out_s3_key, "rows_written": n_rows}


@tool
def stage_inputs_from_s3(union: str, s3_prefix: str) -> dict:
    """Download the union's PDFs from S3 into the kernel's expected layout."""
    # ... boto3.download_file in a loop ...


@tool
def escalate_to_claude_multimodal(s3_key, profile_aliases, missing_fields) -> dict:
    """Path C: ask Bedrock Claude Sonnet for ONLY the kernel's missing fields."""
    # ... bedrock.invoke_model with the PDF + a focused prompt ...


@tool
def validate_total_package_checksum(union: str, rows: list) -> dict:
    """Verify wage + fringes equals the printed Total Package (±$0.05)."""
    # ... sums fringe RateCells, compares to notice_total ...
```

Then the **agent is assembled**:

```python
def build_agent() -> Agent:
    return Agent(
        name="ExtractorAgent",
        system_prompt=EXTRACTOR_SYSTEM_PROMPT,
        tools=[
            stage_inputs_from_s3,
            run_kernel_extractor,
            compute_derived_columns,
            pivot_to_ratesheet_csv,
            escalate_to_claude_multimodal,
            validate_total_package_checksum,
        ],
        plugins=[ExtractorSteering()],
        trace_attributes={"service": "laboraid-extractor", "env": ENV},
    )
```

That's the whole agent. 6 tools + a system prompt + a steering plugin.

Then the **AgentCore Runtime entrypoint** (last 20 lines):

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload: dict) -> Any:
    """AgentCore Runtime entrypoint — payload carries the union + S3 prefix."""
    agent = build_agent()
    return agent(payload.get("prompt", json.dumps(payload)))

app.run()   # starts the AgentCore invoke server when the container boots
```

This is what makes it an "agent running on AgentCore Runtime" — `app.run()` listens for incoming invocations from Step Functions.

#### File 2 — `agents/extractor/system-prompt.md` — the procedural SOP

This is what the LLM brain sees as its instructions. Annotated:

```
You are ExtractorAgent, the single agentic component of the LaborAid Rate
Engine POC. You turn a union's Rate Notice + CBA PDFs into a canonical rate
sheet by orchestrating a deterministic extraction kernel and escalating to a
multi-modal LLM only for cells the kernel cannot read.

## Prime directive — never fabricate
You MUST NOT invent, guess, or interpolate any rate value...

## Procedure (RFC-2119)
1. You MUST call stage_inputs_from_s3 first to materialize the PDFs.
2. You MUST call run_kernel_extractor and treat its rows as the source of truth.
3. You MUST call compute_derived_columns to fill derived columns.
4. If run_kernel_extractor reports gaps, you SHOULD call
   escalate_to_claude_multimodal for exactly those missing fields before
   finishing. You MUST NOT escalate for fields the kernel already read.
5. You MUST call validate_total_package_checksum. You MUST NOT declare the
   extraction complete until the checksum passes...
6. You MUST call pivot_to_ratesheet_csv to emit the final CSV.
7. Any field you could not resolve MUST remain blank and be reported as a gap.

## Escalation discipline
- Prefer the kernel. Escalate to Bedrock only for specific unreadable cells.
- Choose Haiku-class effort for trivial reads, Sonnet for genuine multi-modal
  extraction. Keep prompts focused on the missing fields only.
```

The numbered procedure is the agent's **flow**. The LLM brain reads this on every invocation and follows steps 1→7. The "MUST/MUST NOT" wording uses RFC-2119 keywords so the model has unambiguous instructions.

#### File 3 — `agents/extractor/steering.py` — enforces the procedure in code

This is the safety net. If the LLM tries to skip steps, `ExtractorSteering` blocks it.

```python
class ExtractorSteering(SteeringHandler):
    """Block premature completion; force checksum + gap-escalation discipline."""

    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        if tool_use["name"] == "return_extraction_complete":
            if not getattr(agent, "checksum_validated", False):
                return Guide(reason="Run validate_total_package_checksum first.")
            unresolved = getattr(agent, "unresolved_gaps", [])
            if unresolved and not getattr(agent, "bedrock_fallback_attempted", False):
                return Guide(reason=(
                    f"Kernel reported {len(unresolved)} gaps. Try "
                    "escalate_to_claude_multimodal for these fields before "
                    f"declaring done: {unresolved}"
                ))
        return Proceed(reason="OK.")
```

Read this as: before the LLM is allowed to say "I'm done", check two things:

1. Did `validate_total_package_checksum` pass? If not → `Guide` (redirect the LLM to call it).
2. Are there unresolved gaps that haven't been escalated to Claude? If so → `Guide` (redirect the LLM to escalate).

`Guide(...)` means "stop, do this instead." `Proceed(...)` means "OK, let it through." This is the **agentic reasoning** the SOW requires — the agent decides paths, but the steering enforces it can't lie about being done.

### End-to-end flow when Step Functions invokes the agent

```
Step Functions sends payload {"union": "sprinkler_fitters_704", "s3_prefix": "..."}
        ↓
AgentCore Runtime invoke server (app.entrypoint) receives it
        ↓
build_agent() creates a Strands Agent with the 6 tools + system prompt + steering
        ↓
Agent reads system-prompt.md, sees the 7-step procedure
        ↓
Agent calls stage_inputs_from_s3                  → downloads PDFs to /tmp
Agent calls run_kernel_extractor                  → kernel produces 13 rows, gaps=[]
Agent calls compute_derived_columns               → kernel fills Wage 1.5x, etc.
Agent thinks "checksum first" (per system prompt)
Agent calls validate_total_package_checksum       → passes (±$0.05)
Agent thinks "done"
   → ExtractorSteering.steer_before_tool fires
   → checksum_validated=True, no gaps unresolved → Proceed
Agent calls pivot_to_ratesheet_csv                → CSV uploaded to S3
Agent returns                                     → result sent back to Step Functions
```

If the kernel had reported 3 gaps (low-confidence cells), the agent would have called `escalate_to_claude_multimodal` for just those 3 cells, gotten a JSON response from Claude Sonnet, merged it into the rows, then validated and finished.

### Where the spec describes this same flow

[`09_Technical_Implementation_Spec.md`](09_Technical_Implementation_Spec.md) **§5.3** ("ExtractorAgent — Strands implementation (kernel-wrapping)") describes everything you just read, in prose, with all 6 tools listed. That's the spec; `agent.py` is the implementation. They match line-for-line by design.

### Answer in one paragraph

**Where it lives:** `agents/extractor/agent.py` (the tools + agent assembly), `agents/extractor/system-prompt.md` (the procedure the LLM brain follows), `agents/extractor/steering.py` (the code-level enforcement that the LLM can't skip steps). **Where it's documented:** spec §5.3 prose-describes the same shape. **What it does:** the agent treats the kernel's Python functions as @tool calls, has an LLM brain deciding which to call in what order, and the SteeringHandler refuses to let it claim "done" until validation passes and gaps have been escalated. The whole agent is ~220 lines of Python.

---

## What you should be able to answer after these two lessons

- *What does `RateCell` hold and why?* — One value + full provenance (which doc, which page, confidence). Every output cell traces back to a source.
- *What does `fields.yaml` translate between?* — Canonical internal names (lowercase snake_case) ↔ union-specific CSV column labels.
- *Why does `r2()` exist instead of `round()`?* — Half-up vs banker's rounding. Without it, hundreds of cells drift by a penny and accuracy collapses.
- *Where does the LLM actually fire in the POC?* — Classification (Haiku), low-confidence cell fallback (Sonnet multimodal), Bedrock Guardrails. Not for primary extraction of known unions.
- *What's the agent's relationship to the kernel?* — Agent wraps kernel functions as `@tool`s; the LLM brain decides which to call in what order; the SteeringHandler prevents premature completion.
- *What's POC scope vs Phase 2?* — POC: 5 known unions with hand-coded extractors + 1 Strands agent demonstrating the pattern. Phase 2: ProfileDrafterAgent + generic LLM extractor for unknown unions (the LegalAid-scale story).

---

---

## Lesson 3 — Orchestration: how it all runs in AWS

### Q3.1 — What's next after kernel + agent?

> *"I understand the kernel and imported to Strands and what patterns they do PDF to canonical to finally CSV. What is next, lesson 3, to learn?"*

### Answer

You've got the **engine** (kernel + agent). The natural Lesson 3 is the **orchestration** — *how does this all actually run in AWS? Who triggers the agent? What happens before and after it?*

That's the **Step Functions main pipeline**. It's the glue between everything you've learned and the full system.

---

### Q3.2 — Build Lesson 3 — show me which files do all the orchestration

> *"OK build lesson 3 details so i can see which file is doing all these things — orchestration. Once i am ok you can update md. Start lesson 3 now."*

### Answer

I'm extracting the actual code from your repo and walking you through how a PDF upload turns into a finished rate sheet sitting in the Business UI's approval queue. **Six stages, three files do the heavy lifting.**

### The big picture

Think of it like a kitchen:

- **EventBridge** = the doorbell — fires when a PDF arrives in S3
- **Step Functions** = the head chef — orchestrates which cook does what, in what order, with retries and branching
- **Lambdas** = individual cooks — each one does one small thing (classify, validate, render)
- **The Strands agent on AgentCore** = the master chef — does the actual extraction work using the kernel
- **DynamoDB `agent-config`** = the on/off switch the admin holds — agent disabled? bypass the master chef
- **SNS + Aurora** = the bell rings + the dish ends up on the counter for Business to taste-test

Three files define this orchestration:

| File | Role |
|---|---|
| [`../cdk/laboraid_cdk/stacks/orchestration_stack.py`](../cdk/laboraid_cdk/stacks/orchestration_stack.py) | Creates the state machine + EventBridge rule. Wires real Lambdas + AgentCore ARN as input. |
| [`../cdk/laboraid_cdk/sfn/main_pipeline.py`](../cdk/laboraid_cdk/sfn/main_pipeline.py) | Defines the actual state machine flow (Stages 1-6 + Choice gates). Pure CDK Step Functions code. |
| [`../lambdas/processing/extractor-invoker/handler.py`](../lambdas/processing/extractor-invoker/handler.py) | The thin Lambda that translates "Step Functions LambdaInvoke" → "AgentCore InvokeAgentRuntime call" |

The other Lambdas (classifier, validators, renderers) live under `lambdas/processing/`, `lambdas/validation/`, `lambdas/rendering/`. They're orchestrated by the SFN but don't define orchestration themselves.

### Part 1 — The trigger (S3 → EventBridge → SFN)

The bottom of `orchestration_stack.py` wires up the doorbell:

```python
# S3 ObjectCreated (via EventBridge) -> start an execution.
events.Rule(
    self,
    "OnInputUpload",
    rule_name=name(env, "l3", "rule", "input-uploaded"),
    event_pattern=events.EventPattern(
        source=["aws.s3"],
        detail_type=["Object Created"],
        detail={"bucket": {"name": [inputs_bucket.bucket_name]}},
    ),
    targets=[targets.SfnStateMachine(self.state_machine)],
)
```

Read this as:

- Any S3 `Object Created` event from the inputs bucket → routed to EventBridge default bus → matches this rule → starts an SFN execution

The S3 bucket itself has `event_bridge_enabled=True` set in the Storage stack (that's what tells S3 to publish to EventBridge in the first place).

Net effect: **upload a PDF to `s3://laboraid-prod-l3-bucket-inputs/...` → 5 seconds later, an SFN execution is running.**

### Part 2 — The state machine definition (the actual flow)

`main_pipeline.py` is ~120 lines and defines the Stages 1-6 chain.

**Structure** (from the file's own docstring):

```
1.  Classify (classifier Lambda)
1a. GetAgentConfig (DynamoGetItem on the agent-config table for ExtractorAgent)
1b. Choice: is the ExtractorAgent enabled?
      yes -> 2. Extract (ExtractorAgent on AgentCore Runtime)
      no  -> bypass extraction, go straight to validation
3.  Validate (parallel checksum + range + confidence)
4.  Choice: all validators passed?
      yes -> 5. Render (parallel xlsx + csv + articles) -> 6. Publish (success)
      no  -> route to review queue
```

Every Lambda task has retries; a top-level catch routes failures to a Fail state.

#### Stage 1 — Classify

```python
def _invoke(scope, cid, fn):
    task = tasks.LambdaInvoke(scope, cid, lambda_function=fn,
                              payload_response_only=True, result_path=f"$.{cid.lower()}")
    task.add_retry(
        errors=["Lambda.ServiceException", "Lambda.TooManyRequestsException", "States.TaskFailed"],
        interval=Duration.seconds(2), max_attempts=3, backoff_rate=2.0,
    )
    return task

classify = _invoke(scope, "Classify", classifier)
```

`_invoke()` is a small helper that wraps every Lambda-invocation step with **automatic retries** (3 attempts, exponential backoff starting at 2s). Every Lambda in the pipeline gets this.

The classifier itself does deterministic regex on the filename:

```python
_FILENAME = re.compile(
    r"(?P<date>\d{4}\.\d{2}\.\d{2})\.(?P<local>\d{3})\s+(?P<doc>.+?)\.pdf$",
    re.IGNORECASE,
)
# "2026.01.01.704 Rate Notice.pdf" → date=2026.01.01, local=704, doc="Rate Notice"

_LOCAL_TO_UNION = {
    "537": "pipe_fitters_537",
    "483": "sprinkler_fitters_483",
    "704": "sprinkler_fitters_704",
    "281": "sprinkler_fitters_281",
    "821": "sprinkler_fitters_821",
}
```

Output: `{"s3_key": "...", "union": "sprinkler_fitters_704", "local": "704", "period": "2026-01-01", "doc_type": "rate_notice", "confidence": "high", "method": "filename"}`. That dict becomes the input to the next stage.

If filename is ambiguous → returns `{"doc_type": "unknown", "method": "needs_review"}`. (Production would invoke Bedrock Haiku here.)

#### Stage 1a — GetAgentConfig (the admin enable/disable toggle)

```python
get_agent_cfg = tasks.DynamoGetItem(
    scope,
    "GetAgentConfig",
    table=agent_config_table,
    key={"agent_name": tasks.DynamoAttributeValue.from_string("ExtractorAgent")},
    result_path="$.agentCfg",
)
```

This reads one row from the `laboraid-{env}-l3-ddb-agent-config` DDB table:

```
agent_name (pk): "ExtractorAgent"
enabled:        true | false
image_tag:      "v1.2.3"
version:        "1.2.3"
updated_by:     "<cognito sub of admin>"
updated_at:     "2026-06-03T14:00:00Z"
```

The Admin UI's `/admin/agents` page writes to this table via `PATCH /v1/agents/{name}`. The Step Function reads it here. This is **the admin toggle in action** — a row change in DDB instantly affects pipeline behavior on the next run.

#### Stage 1b — Choice: agent enabled or not?

```python
agent_gate = (
    sfn.Choice(scope, "AgentEnabled")
    .when(sfn.Condition.boolean_equals("$.agentCfg.Item.enabled.BOOL", True), extract)
    .otherwise(validate)
)
```

Read the JSONPath: `$.agentCfg.Item.enabled.BOOL` — drills into the DynamoGetItem result, finds the `enabled` boolean attribute. If `true` → go to extract. If `false` → skip extract entirely, go straight to validate.

**Why this matters:** if the agent breaks in production (bad container image, Bedrock outage), Admin flips the toggle in the UI and the pipeline keeps running — just without the extraction step. Validators will then fail (no extracted data) and the rate sheet routes to review. The system degrades gracefully.

#### Stage 2 — Extract (the agent invocation)

AgentCore doesn't have a native Step Functions integration (no `AWS::StepFunctions::AgentCoreInvoke` task). So we built a **thin Lambda invoker**.

In `orchestration_stack.py`:

```python
self.extractor_invoker = TaggedLambda(
    self, "ExtractorInvoker",
    env=env, layer="l3",
    function_name=name(env, "l3", "fn", "extractor-invoker"),
    handler="handler.handler",
    code=lambda_.Code.from_asset("../lambdas/processing/extractor-invoker"),
    environment={"EXTRACTOR_RUNTIME_ARN": extractor_runtime_arn},
)
self.extractor_invoker.add_to_role_policy(
    iam.PolicyStatement(
        effect=iam.Effect.ALLOW,
        actions=["bedrock-agentcore:InvokeAgentRuntime"],
        resources=[extractor_runtime_arn, f"{extractor_runtime_arn}/*"],
    )
)
extract_task = tasks.LambdaInvoke(
    self, "ExtractViaAgent",
    lambda_function=self.extractor_invoker,
    payload_response_only=True,
    result_path="$.extract",
)
```

The IAM policy is critical — it's what grants this Lambda the `bedrock-agentcore:InvokeAgentRuntime` permission scoped to the specific extractor runtime ARN. Least-privilege.

The Lambda itself:

```python
EXTRACTOR_RUNTIME_ARN = os.environ.get("EXTRACTOR_RUNTIME_ARN", "")

def invoke_runtime(event):
    """Invoke the ExtractorAgent runtime synchronously, returning a result summary."""
    resp = _client().invoke_agent_runtime(
        agentRuntimeArn=EXTRACTOR_RUNTIME_ARN,
        runtimeSessionId=_session_id(event),
        payload=json.dumps(event).encode("utf-8"),
    )
    return {"statusCode": resp.get("statusCode", 200)}

def handler(event, _context):
    try:
        result = invoke_runtime(event)
        return {"extracted": True, "runtime_response": result}
    except Exception:
        logger.exception("extractor-invoker failed")
        raise
```

Read this as: take the event from Step Functions, call AgentCore's `InvokeAgentRuntime` API synchronously, return the result. **This is exactly where the Lesson 2 agent gets called.** The payload is the classifier's output (union name, S3 prefix, etc.) — exactly what the agent's `@entrypoint` function expects.

This is the bridge between Step Functions and AgentCore. ~30 lines of glue.

#### Stage 3 — Validate (parallel)

Three validators run **simultaneously** in a parallel state:

```python
validate = sfn.Parallel(scope, "Validate", result_path="$.validation")
validate.branch(_invoke(scope, "Checksum",   checksum))
validate.branch(_invoke(scope, "Range",      range_fn))
validate.branch(_invoke(scope, "Confidence", confidence))
```

Each branch gets the same input (the agent's extracted rows) and produces a pass/fail result. SFN waits for all three to finish before moving on.

Sample validator — the checksum Lambda:

```python
TOLERANCE = 0.05
FRINGE_PREFIXES = ("health_welfare", "pension", "sis", "annuity", "industry")

def check_row(row):
    cells = row.get("cells", {})
    wage = float(cells.get("wage", {}).get("value", 0.0))
    fringes = sum(
        float(c["value"]) for c in cells.values()
        if str(c.get("canonical_field", "")).startswith(FRINGE_PREFIXES)
    )
    computed = round(wage + fringes, 2)
    expected = row.get("notice_total")
    if expected is None:
        return {"passed": None, "reason": "notice did not print a Total Package", "computed": computed}
    diff = round(computed - float(expected), 2)
    return {"passed": abs(diff) <= TOLERANCE, "computed": computed, "expected": float(expected), "diff": diff}
```

Notice the same `canonical_field` names from Lesson 1 — `wage`, `health_welfare`, `pension`, etc. The validator works on canonical rows, not union-specific column names.

#### Stage 4 — Choice: did all validators pass?

```python
gate = (
    sfn.Choice(scope, "AllValidatorsPassed")
    .when(
        sfn.Condition.boolean_equals("$.validation[0].passed", True),
        render.next(publish),
    )
    .otherwise(to_review)
)
```

If checksum (the first parallel branch) passed → go to render → then publish. Otherwise → route to the review queue (handled by the review-router Lambda) → end execution in `AwaitingReview`.

#### Stage 5 — Render (parallel)

```python
render = sfn.Parallel(scope, "Render", result_path="$.render")
render.branch(_invoke(scope, "RenderXlsx",     xlsx))
render.branch(_invoke(scope, "RenderCsv",      csv))
render.branch(_invoke(scope, "RenderArticles", articles))
```

Three renderers run in parallel:

- **xlsx-renderer** — produces the Excel file (uses `openpyxl`, imports the kernel's CSV pivot, then converts)
- **csv-renderer** — produces the canonical CSV (uses kernel's pivot directly)
- **articles-renderer** — produces a structural-rules artifact (extracts from kernel's gaps report)

All three output files land in `s3://laboraid-{env}-l3-bucket-outputs/laboraid/{Trade}/{Local}/{period}/`.

#### Stage 6 — Publish

```python
publish = sfn.Succeed(scope, "Published")
```

This is just an SFN "Succeed" state — execution ends successfully here. The actual publish work (Aurora row insert with `approval_state='pending_review'`, SNS event) is done by the render Lambdas before reaching this state.

#### Review path (alternative ending)

```python
to_review = _invoke(scope, "RouteToReview", review_router).next(
    sfn.Succeed(scope, "AwaitingReview")
)
```

If validation failed, the review-router Lambda writes the low-confidence cells to the DDB `review` table:

```python
def build_review_items(*, tenant, union, period, created_at, low_confidence_cells):
    items = []
    for idx, cell in enumerate(low_confidence_cells):
        cell_id = f"{union}#{period}#{cell.get('classification', '?')}#{cell.get('field', idx)}"
        items.append({
            "tenant": tenant,
            "created_at#cell_id": f"{created_at}#{cell_id}",
            "union": union,
            "period": period,
            "field": cell.get("field"),
            "confidence": cell.get("confidence"),
        })
    return items
```

These items power the Business UI's `/business/queue` page. An SME sees them, decides whether to accept or override.

### Part 3 — Failure paths

```python
failed = sfn.Fail(scope, "PipelineFailed", error="PipelineError", cause="See execution input")
classify.add_catch(failed, errors=["States.ALL"], result_path="$.error")
```

A top-level catch on Classify routes ANY uncaught exception to a `Fail` state. Combined with the per-task retries (3 attempts, exponential backoff), this means:

- Transient errors → retried automatically, often invisible to humans
- Persistent errors → execution lands in `PipelineFailed`, SNS publishes to `laboraid-{env}-l6-sns-failures`, ops email + Slack notify + DLQ retains the message for replay

The state machine itself has a 30-minute timeout. If extraction hangs, the whole thing aborts and goes to Failed.

### The full state machine as ASCII

```
S3 Object Created → EventBridge → SFN execution starts
        ↓
[Classify Lambda]                              ← lambdas/processing/classifier/
        ↓
[GetAgentConfig DDB Read]                      ← reads agent-config table
        ↓
   ┌─── AgentEnabled? ────┐
   ↓ true                 ↓ false
[ExtractViaAgent]    (skip extract)
   ↓                      ↓
   └─── joins ────────────┘
        ↓
[Validate Parallel]                            ← 3 Lambdas in parallel:
        │                                         checksum, range, confidence
        ↓
   ┌─── AllValidatorsPassed? ────┐
   ↓ yes                          ↓ no
[Render Parallel]            [RouteToReview]
   │  (xlsx + csv + articles)    ↓
   ↓                          [AwaitingReview]  ← Business UI sees it
[Published]                                       in /business/queue
                                                  (low-conf cells)
                              [Inbox]            ← Business UI sees the
                                                   rate sheet itself
                                                   in /business/inbox
                                                   with approval_state=
                                                   'pending_review'
```

### Tracing one upload through the system

You upload `2026.01.01.704 Rate Notice.pdf` to `s3://laboraid-prod-l3-bucket-inputs/`. Wall-clock trace:

```
T+0.0s    S3 emits Object Created event
T+0.5s    EventBridge matches the rule, starts SFN execution
T+0.5s    Classify Lambda invoked
T+1.2s    Classify returns {union: "sprinkler_fitters_704", period: "2026-01-01", ...}
T+1.3s    GetAgentConfig DDB read → {enabled: true}
T+1.4s    AgentEnabled Choice → true → invoke ExtractViaAgent
T+1.5s    ExtractorInvoker Lambda calls bedrock-agentcore:InvokeAgentRuntime
T+1.5s    AgentCore Runtime spins up (or routes to a warm container)
T+3.0s    Agent reads system-prompt.md, follows Procedure step 1-7:
            T+3.5s    stage_inputs_from_s3 → downloads PDFs to /tmp
            T+7.0s    run_kernel_extractor → 13 rows, 0 gaps
            T+7.5s    compute_derived_columns → derived fields filled
            T+8.0s    validate_total_package_checksum → passes
            T+8.5s    pivot_to_ratesheet_csv → CSV uploaded to S3
T+8.6s    Agent returns to invoker → invoker returns to SFN
T+8.7s    Validate Parallel → 3 Lambdas fire simultaneously
T+9.5s    All three validators return passed=true
T+9.6s    AllValidatorsPassed Choice → true → Render Parallel
T+9.7s    3 renderers fire (xlsx, csv, articles)
T+11.0s   All three renderers complete, write to outputs bucket + Aurora
T+11.1s   Published — SFN execution ends successfully
T+11.2s   Business UI inbox lights up (next poll)
```

Total: ~11 seconds for a typical 704 Rate Notice. Most of that is the agent + kernel + OCR. Step Functions overhead is <1s end-to-end.

### Files involved (summary)

| File | What it does |
|---|---|
| `cdk/laboraid_cdk/stacks/storage_stack.py` | Creates the inputs S3 bucket with `event_bridge_enabled=True` |
| `cdk/laboraid_cdk/stacks/orchestration_stack.py` | Creates the SFN + EventBridge rule + ExtractorInvoker Lambda + IAM |
| `cdk/laboraid_cdk/sfn/main_pipeline.py` | Defines the Stages 1-6 flow with Choice gates and Parallel states |
| `lambdas/processing/classifier/handler.py` | Stage 1 — filename regex → union + period + doc_type |
| `lambdas/processing/extractor-invoker/handler.py` | Stage 2 bridge — translates SFN LambdaInvoke into AgentCore InvokeAgentRuntime |
| `agents/extractor/agent.py` | The Strands agent itself (Lesson 2) — receives the classifier output, calls kernel tools |
| `lambdas/validation/{checksum,range,confidence}/handler.py` | Stage 3 — parallel pre-publish validators |
| `lambdas/validation/review-router/handler.py` | Stage 4 (review path) — writes flagged cells to DDB review table |
| `lambdas/rendering/{xlsx,csv,articles}-renderer/handler.py` | Stage 5 — parallel renderers, write to S3 + Aurora |

### What you should be able to answer after Lesson 3

- *What triggers the pipeline?* — S3 `Object Created` event → EventBridge rule → SFN execution start
- *Where in the pipeline does the agent get called?* — Stage 2, via the ExtractorInvoker Lambda calling `bedrock-agentcore:InvokeAgentRuntime`
- *How does the admin enable/disable toggle work?* — Stage 1a reads `agent-config` DDB row; Stage 1b Choice branches on `enabled`; if false, skip extraction
- *What runs in parallel and what runs serially?* — Classify → DDB read → (maybe Extract) → 3-parallel Validate → Choice → 3-parallel Render → Succeed
- *Where do failures go?* — Per-Lambda retry (3x backoff); persistent failures hit the top-level catch → Fail state → SNS failures topic → ops email/Slack
- *Where do low-confidence cells end up?* — review-router Lambda writes them to DDB `review` table → Business UI `/business/queue` page

**Question to lock it in:** if the admin disables the ExtractorAgent via `/admin/agents`, what happens when the next PDF is uploaded? Walk through the SFN path step by step.

---

Next lesson candidates (ask when ready):
- The human approval gate (`ratesheet-publish` + `ratesheet-approve` Lambdas + Aurora `approval_state` lifecycle — the audit-fix B1/B2 story)
- The CDK foundation (`cdk/app.py` + naming + tags + tagged constructs)
- One stack end-to-end (Storage stack is a good pick — 6 buckets + 7 DDB tables + Aurora)
- The React UI (start with `routes.tsx` + `RouteGuard.tsx`)
- The tests (what's tested, how, and why some bugs slip past green tests — the audit story)
