#!/usr/bin/env python3
"""LaborAid Rate Engine — Lambda & Agent inventory (live).

Prints every deployed Lambda (pulled LIVE from AWS, so it's proof of what's
actually running) annotated with its purpose and active/legacy status, then the
AI agents with their tools, models, and steering.

Companion to docs/LAMBDA_AND_AGENT_INVENTORY.md (same content, runnable).

Usage:
    py -3 scripts/inventory.py
    py -3 scripts/inventory.py --no-color
    py -3 scripts/inventory.py --profile <name> --region <region>

Read-only: only calls lambda:ListFunctions.
"""
from __future__ import annotations

import argparse
import sys

import boto3

PROFILE = "laboraid"
REGION = "us-east-2"


class C:
    OK = "\033[92m"; DIM = "\033[90m"; NAVY = "\033[96m"; GOLD = "\033[33m"
    WARN = "\033[93m"; B = "\033[1m"; R = "\033[0m"

    @classmethod
    def off(cls) -> None:
        for k in ("OK", "DIM", "NAVY", "GOLD", "WARN", "B", "R"):
            setattr(cls, k, "")


# name (after 'laboraid-dev-') -> (purpose, active?)
PURPOSE: dict[str, tuple[str, bool]] = {
    # l2 · API — one per HTTP route
    "l2-fn-upload-presign":      ("POST /v1/uploads — presigned S3 URL for PDF upload", True),
    "l2-fn-batch-process":       ("POST /v1/batches/process — start processing a batch", True),
    "l2-fn-job-list":            ("GET /v1/jobs — list runs (jobs DynamoDB read-model)", True),
    "l2-fn-job-status":          ("GET /v1/jobs/{id} — one run's stage timeline", True),
    "l2-fn-job-retry":           ("POST /v1/jobs/{id}/retry — re-run a failed execution", True),
    "l2-fn-job-abort":           ("POST /v1/jobs/{id}/abort — cancel an in-flight run", True),
    "l2-fn-agent-list":          ("GET /v1/agents — read AI agent on/off config", True),
    "l2-fn-agent-toggle":        ("PATCH /v1/agents/{name} — enable/disable, pin version", True),
    "l2-fn-profile-list":        ("GET /v1/unions[/{local}/profile] — list/get union profile", True),
    "l2-fn-profile-update":      ("PUT /v1/unions/{local}/profile — edit profile in Aurora", True),
    "l2-fn-ratesheet-list":      ("GET .../rate-sheets — list periods by approval state", True),
    "l2-fn-ratesheet-get":       ("GET .../{period} — JSON + artifacts + job meta + AI change log", True),
    "l2-fn-ratesheet-approve":   ("POST .../approve — business sign-off (2nd person)", True),
    "l2-fn-ratesheet-reject":    ("POST .../reject — reject with a reason", True),
    "l2-fn-ratesheet-unapprove": ("POST .../unapprove — reverse approval before publish", True),
    "l2-fn-ratesheet-publish":   ("POST .../publish — GATED: 409 unless approved", True),
    "l2-fn-ratesheet-audit":     ("GET .../audit — full audit trail for a sheet", True),
    "l2-fn-ratesheet-rework":    ("POST .../rework — new version from manual edits", True),
    "l2-fn-ratesheet-improve":   ("POST .../improve — Phase 2, dispatch the ImproverAgent", True),
    "l2-fn-cell-override":       ("POST /v1/cells/{id}/override — save corrected value", True),
    "l2-fn-cell-comment":        ("POST /v1/cells/{id}/comment — save reviewer comment", True),
    "l2-fn-audit-list":          ("GET /v1/audit — system-wide audit feed", True),
    # l3 · infra / read-model
    "l3-fn-schema-init":         ("CFN custom resource — apply Aurora DDL (idempotent)", True),
    "l3-fn-job-writer":          ("EventBridge -> jobs DynamoDB read-model (CQRS)", True),
    # l4 · processing pipeline (Plan -> Synthesize -> Publish + onboarding)
    "l4-fn-batch-planner":       ("PLAN — classify + order docs, resolve union/period", True),
    "l4-fn-synthesizer":         ("SYNTHESIZE — Claude reads all docs vs the union profile", True),
    "l4-fn-synth-publish":       ("PUBLISH — write to Aurora, emit CSV/Excel", True),
    "l4-fn-profile-builder":     ("Onboard a union from its CBA (structure only)", True),
    "l4-fn-classifier":          ("Document classifier (CBA vs notice) — layered spec path", False),
    "l4-fn-ocr-preprocess":      ("OCR pre-processing — layered spec path", False),
    "l4-fn-llm-extractor":       ("Per-cell LLM extractor — layered spec path", False),
    "l4-fn-publisher":           ("Write a kernel/agent extraction to Aurora — layered spec path", False),
    # l6 · validation / notify
    "l6-fn-validator-checksum":  ("Total-package checksum (wage+fringes ±$0.05)", False),
    "l6-fn-validator-range":     ("Range validator — flag implausible values", False),
    "l6-fn-validator-confidence":("Confidence rollup -> route low-confidence cells", False),
    "l6-fn-review-router":       ("Route a sheet to the human review queue", False),
    "l6-fn-slack-notify":        ("Slack notifications on pipeline events", False),
    # l7 · rendering
    "l7-fn-renderer-csv":        ("Render canonical CSV", False),
    "l7-fn-renderer-xlsx":       ("Render Excel in the client's standard layout", False),
    "l7-fn-renderer-articles":   ("Render the 'articles' view", False),
}

LAYER_TITLE = {
    "l2": "API · one Lambda per HTTP route (API Gateway + Cognito)",
    "l3": "Infra / read-model",
    "l4": "Processing pipeline (Plan -> Synthesize -> Publish + onboarding)",
    "l6": "Validation / notify",
    "l7": "Rendering",
}

AGENTS = [
    ("ExtractorAgent", "Strands · AgentCore", "Claude Sonnet 4.6",
     "8 tools (kernel fast-path + per-cell Bedrock fallback). Paths A/B/C. "
     "Steering blocks 'complete' until checksum passes + gaps escalated. "
     "Prompt: never invent a value — blank-and-flagged beats fabricated."),
    ("ImproverAgent", "Strands · AgentCore", "Claude Opus 4.5",
     "Phase 2. Override -> applied + derived recomputed in code. Comment -> "
     "re-synthesize that cell from source PDF (temp 0); null if unconfirmed "
     "(keeps prior value). Writes v+1 + improvement_changes (the change log)."),
    ("Synthesizer", "LLM-in-Lambda", "Claude Sonnet 4.6",
     "Production extraction core. Loads union profile from Aurora as the exact "
     "target schema; reads all docs together; derived columns computed in code."),
    ("ProfileDrafter", "LLM-in-Lambda", "Claude Opus 4.5",
     "Onboards an unseen union from its CBA — structure only (zones, classes, "
     "cohorts, multipliers, canonical fund names). No code changes per union."),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="LaborAid Lambda & agent inventory (live)")
    ap.add_argument("--profile", default=PROFILE)
    ap.add_argument("--region", default=REGION)
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    if args.no_color or not sys.stdout.isatty():
        C.off()

    s = boto3.Session(profile_name=args.profile, region_name=args.region)
    lam = s.client("lambda")
    live = []
    for page in lam.get_paginator("list_functions").paginate():
        live += [f["FunctionName"] for f in page["Functions"]
                 if f["FunctionName"].startswith("laboraid")]
    live.sort()

    print(f"\n{C.NAVY}{C.B}  LaborAid Rate Engine — Lambda & Agent inventory{C.R}")
    print(f"{C.DIM}  {len(live)} functions live in {args.region} · pulled from AWS{C.R}\n")

    by_layer: dict[str, list[str]] = {}
    for fn in live:
        short = fn.replace("laboraid-dev-", "")
        by_layer.setdefault(short.split("-")[0], []).append(short)

    for layer in sorted(by_layer):
        print(f"{C.GOLD}{C.B}━━ {layer.upper()} · {LAYER_TITLE.get(layer, '')}{C.R}")
        for short in by_layer[layer]:
            purpose, is_active = PURPOSE.get(short, ("(no description on file)", None))
            badge = (f"{C.OK}●{C.R}" if is_active is True
                     else f"{C.WARN}○{C.R}" if is_active is False
                     else f"{C.DIM}?{C.R}")
            name = short.replace("fn-", "")
            print(f"  {badge} {name:<26}{C.DIM}{purpose}{C.R}")
        print()

    active = sum(1 for fn in live if PURPOSE.get(fn.replace("laboraid-dev-", ""), (None, None))[1] is True)
    legacy = sum(1 for fn in live if PURPOSE.get(fn.replace("laboraid-dev-", ""), (None, None))[1] is False)
    unknown = len(live) - active - legacy

    print(f"{C.NAVY}{C.B}━━ AI AGENTS · tools · models{C.R}")
    for nm, where, model, desc in AGENTS:
        print(f"  {C.GOLD}{C.B}{nm}{C.R} {C.DIM}({where} · {model}){C.R}")
        print(f"      {desc}")
    print(f"\n  {C.DIM}All Bedrock calls pass a PII guardrail. Trust rule: extract from source "
          f"or flag a gap — never fabricate.{C.R}")

    print(f"\n{C.B}  Summary:{C.R} {C.OK}● {active} active{C.R} · "
          f"{C.WARN}○ {legacy} legacy/spec{C.R}"
          + (f" · {unknown} unmapped" if unknown else "")
          + f" · {C.B}{len(live)} total{C.R}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
