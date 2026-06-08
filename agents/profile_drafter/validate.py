"""E.4 — validate_generated: pure-orchestration validator.

Runs the candidate profile + extractor through:

1. `schema_check` (D.3) — does the YAML conform to the reference schema?
2. `codegen_check` (D.4) — does the Python compile + define the right function?
3. If both pass: register the extractor in EXTRACTORS, invoke
   ``kernel/pipeline/run.py --union <union_key>`` via subprocess, capture the
   evaluator's accuracy line.

Returns:

    {
      "schema_pass": bool,
      "codegen_pass": bool,
      "syntax_pass": bool,
      "accuracy_pct": float,
      "mismatch_count": int,
      "evaluator_output": str,
      "errors": list[str],
    }

Pure orchestration, no LLM. Does NOT write anything to ``kernel/`` directly —
the candidate paths are read from disk, the registration step uses a runtime
module monkey-patch via subprocess (we shell out to a thin runner script
materialized at call time), so the kernel source on disk is untouched. (When
the drafter is invoked at *runtime* via the orchestrate.py / commit_helper.py
flow it DOES write into kernel/, but only after this validation passes.)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from codegen_check import codegen_check
from schema_check import schema_check

# Accuracy line printed by kernel.pipeline.evaluate.evaluate:
#   "=== OVERALL CELL ACCURACY: 123/456 = 27.0%  (blanks 12, wrong 34) ==="
_ACC_RE = re.compile(
    r"OVERALL\s+CELL\s+ACCURACY:\s*(\d+)\s*/\s*(\d+)\s*=\s*([\d.]+)%\s*"
    r"\(blanks\s+(\d+),\s*wrong\s+(\d+)\)",
    re.IGNORECASE,
)


def validate_generated(
    profile_path_candidate: str,
    extractor_path_candidate: str,
    union_dir: str,
    groundtruth_path: str,
) -> dict[str, Any]:
    """Validate a candidate profile + extractor end-to-end.

    Args:
        profile_path_candidate: path to the candidate profile YAML.
        extractor_path_candidate: path to the candidate extractor .py source.
        union_dir: kernel ``data/<union>/`` directory (Rate Notice PDFs live in
            ``cba/`` here, groundtruth ratesheet in ``ratesheet/``).
        groundtruth_path: path to the groundtruth CSV/xlsx the evaluator
            compares against.

    Returns:
        See module docstring.
    """
    out: dict[str, Any] = {
        "schema_pass": False,
        "codegen_pass": False,
        "syntax_pass": False,
        "accuracy_pct": 0.0,
        "mismatch_count": 0,
        "evaluator_output": "",
        "errors": [],
    }
    errors: list[str] = out["errors"]

    # --- 1) read + schema-check the profile ---------------------------------
    try:
        profile_yaml = Path(profile_path_candidate).read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"could not read profile {profile_path_candidate!r}: {exc}")
        return out

    schema_result = schema_check(profile_yaml)
    out["schema_pass"] = bool(schema_result["ok"])
    if not out["schema_pass"]:
        errors.extend(f"schema: {e}" for e in schema_result["errors"])

    # --- 2) read + codegen-check the extractor ------------------------------
    try:
        extractor_src = Path(extractor_path_candidate).read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"could not read extractor {extractor_path_candidate!r}: {exc}")
        return out

    code_result = codegen_check(extractor_src)
    out["codegen_pass"] = bool(code_result["ok"])
    out["syntax_pass"] = bool(code_result["syntax_pass"])
    if not out["codegen_pass"]:
        errors.extend(f"codegen: {e}" for e in code_result["errors"])

    if not (out["schema_pass"] and out["codegen_pass"]):
        return out

    # --- 3) invoke the kernel runner subprocess ----------------------------
    fn_name: str | None = code_result.get("function_name")
    union_key = _infer_union_key(profile_yaml, union_dir)
    if not fn_name or not union_key:
        errors.append(
            f"could not infer union_key or function name (fn={fn_name!r}, "
            f"union_key={union_key!r}); skipping evaluator run"
        )
        return out

    evaluator_out, eval_err = _run_evaluator(
        extractor_path_candidate,
        profile_path_candidate,
        union_key,
        fn_name,
        union_dir,
        groundtruth_path,
    )
    out["evaluator_output"] = evaluator_out
    if eval_err:
        errors.append(f"evaluator: {eval_err}")

    match = _ACC_RE.search(evaluator_out)
    if match:
        out["accuracy_pct"] = float(match.group(3))
        wrong = int(match.group(5))
        blank = int(match.group(4))
        out["mismatch_count"] = wrong + blank
    else:
        errors.append("evaluator output did not contain a parseable accuracy line")

    return out


def _infer_union_key(profile_yaml: str, union_dir: str) -> str | None:
    """Pull the union key from the profile YAML, falling back to union_dir basename."""
    m = re.search(r"^\s*union\s*:\s*(\S+)\s*$", profile_yaml, re.MULTILINE)
    if m:
        return m.group(1).strip("'\"")
    base = os.path.basename(os.path.normpath(union_dir))
    return base or None


def _run_evaluator(
    extractor_path: str,
    profile_path: str,
    union_key: str,
    fn_name: str,
    union_dir: str,
    groundtruth_path: str,
) -> tuple[str, str]:
    """Spawn a subprocess that imports the candidate extractor, runs the kernel
    pipeline for the union, and prints the evaluator output to stdout.

    Returns (stdout, error_message_or_empty).
    """
    runner_src = _build_runner_script(
        extractor_path=os.path.abspath(extractor_path),
        profile_path=os.path.abspath(profile_path),
        union_key=union_key,
        fn_name=fn_name,
        union_dir=os.path.abspath(union_dir),
        groundtruth_path=os.path.abspath(groundtruth_path),
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(runner_src)
        runner_path = tmp.name

    try:
        env = dict(os.environ)
        # Make sure the kernel is importable; if PYTHONPATH already has it
        # (container case), this is a no-op.
        repo_root = _find_repo_root()
        if repo_root:
            kernel_dir = os.path.join(repo_root, "kernel")
            existing = env.get("PYTHONPATH", "")
            parts = [p for p in (kernel_dir, existing) if p]
            env["PYTHONPATH"] = os.pathsep.join(parts)
        try:
            proc = subprocess.run(
                [sys.executable, runner_path],
                capture_output=True,
                text=True,
                env=env,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return "", "evaluator subprocess timed out after 180s"
        out = (proc.stdout or "") + (f"\n--- STDERR ---\n{proc.stderr}" if proc.stderr else "")
        if proc.returncode != 0:
            return out, f"evaluator exited {proc.returncode}"
        return out, ""
    finally:
        try:
            os.unlink(runner_path)
        except OSError:
            pass


def _find_repo_root() -> str | None:
    """Walk up looking for the .git directory; return None in the container."""
    cur = Path(__file__).resolve()
    for parent in (cur, *cur.parents):
        if (parent / ".git").exists():
            return str(parent)
    return None


def _build_runner_script(
    extractor_path: str,
    profile_path: str,
    union_key: str,
    fn_name: str,
    union_dir: str,
    groundtruth_path: str,
) -> str:
    """Render a self-contained runner that imports the candidate + runs eval."""
    return f"""\
\"\"\"Auto-generated by agents/profile_drafter/validate.py — DO NOT EDIT.\"\"\"
from __future__ import annotations

import importlib.util
import os
import sys

# Load the candidate extractor module by path.
spec = importlib.util.spec_from_file_location("candidate_extractor", {extractor_path!r})
assert spec and spec.loader, "could not load candidate extractor spec"
candidate = importlib.util.module_from_spec(spec)
sys.modules["candidate_extractor"] = candidate
spec.loader.exec_module(candidate)

# Pull the kernel pipeline + register the candidate.
from pipeline import extract as k_extract
from pipeline import pivot as k_pivot
from pipeline import evaluate as k_evaluate
import yaml

k_extract.EXTRACTORS[{union_key!r}] = getattr(candidate, {fn_name!r})

# Run the extractor on the union dir.
rows, gaps = k_extract.EXTRACTORS[{union_key!r}]({union_dir!r})

# Load the profile YAML.
with open({profile_path!r}, encoding="utf-8") as fh:
    profile = yaml.safe_load(fh)

# Write the AI output CSV next to the candidate paths.
out_dir = os.path.join({union_dir!r}, "ai_output")
os.makedirs(out_dir, exist_ok=True)
out_csv = os.path.join(out_dir, "candidate_output.csv")
k_pivot.write_csv(profile, rows, out_csv)

# Evaluate against groundtruth.
k_evaluate.evaluate({groundtruth_path!r}, out_csv)
"""
