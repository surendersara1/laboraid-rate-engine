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

Next lesson candidates (ask when ready):
- The CDK foundation (`cdk/app.py` + naming + tags + tagged constructs)
- One stack end-to-end (Storage stack is a good pick — 6 buckets + 7 DDB tables + Aurora)
- The orchestration Step Function (how Step Functions ties all 6 stages together)
- A representative Lambda (`ratesheet-publish` is interesting — has the Aurora gate)
- The React UI (start with `routes.tsx` + `RouteGuard.tsx`)
