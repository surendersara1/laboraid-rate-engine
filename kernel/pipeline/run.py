"""Entrypoint: generic CBA -> ratesheet pipeline.

Dependencies are declared in pyproject.toml / uv.lock; uv manages the env:
    uv run python pipeline/run.py --union <name>
    uv run python pipeline/run.py --all

Reads only data/<union>/cba/ (and the groundtruth header for column names);
writes only data/<union>/ai_output/<groundtruth-base-name>.csv + <union>.gaps.md.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from pipeline import extract, pivot, evaluate, critic

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# union -> (groundtruth path [read-only header], output csv base name)
TARGETS = {
    "sprinkler_fitters_281": "2026.01.01.281 Rate Sheet",
    "sprinkler_fitters_483": "2026.01.01.483 Rate Sheet",
    "sprinkler_fitters_704": "2026.01.01.704 Rate Sheet",
    "sprinkler_fitters_821": "2026.01.01.821 Rate Sheet",
    "pipe_fitters_537": "2026.03.01.537 Rate Sheet",
}
GT_EXT = {
    "sprinkler_fitters_281": ".csv",
    "sprinkler_fitters_483": ".csv",
    "sprinkler_fitters_704": ".csv",
    "sprinkler_fitters_821": ".csv",
    "pipe_fitters_537": ".xlsx",
}


def load_profile(union):
    with open(os.path.join(ROOT, "profiles", f"{union}.yaml")) as fh:
        return yaml.safe_load(fh)


def write_gaps(union_dir, union, gaps):
    path = os.path.join(union_dir, "ai_output", f"{union}.gaps.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [f"# Unsourced / divergent cells - {union}", "",
             "Cells that are either left BLANK (value absent from the CBA docs) "
             "or carry a document-derived value that DIVERGES from the "
             "groundtruth because the specific allocation is not stated in the "
             "docs. No value is fabricated or copied from the groundtruth. "
             "Each row: key (Zone / Package), column, reason.", ""]
    if not gaps:
        lines.append("_None - every output cell is sourced from the CBA docs._")
    else:
        lines.append("| Zone | Package | Column | Reason |")
        lines.append("|---|---|---|---|")
        for zone, pkg, col, why in gaps:
            lines.append(f"| {zone} | {pkg} | {col} | {why} |")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def run_union(union, do_eval=True, min_accuracy=None, do_critic=True):
    """Run the pipeline for one union. Returns the evaluate() result dict (or None
    when do_eval is False). When min_accuracy is set, the returned dict's `gate_ok`
    flag records whether header matched AND accuracy met the threshold."""
    profile = load_profile(union)
    union_dir = os.path.join(ROOT, "data", union)
    base = TARGETS[union]
    out_csv = os.path.join(union_dir, "ai_output", f"{base}.csv")

    rows, gaps = extract.EXTRACTORS[union](union_dir)
    n = pivot.write_csv(profile, rows, out_csv)
    gaps_path = write_gaps(union_dir, union, gaps)

    print(f"\n########## {union} ##########")
    print(f"wrote {out_csv}  ({n} rows)")
    print(f"wrote {gaps_path}  ({len(gaps)} gap entries)")

    if do_critic:
        try:
            critic.report(union_dir, union, out_csv)
        except Exception as e:  # advisory only; never break the run
            print(f"(coverage critic skipped: {e})")

    if not do_eval:
        return None

    gt = os.path.join(union_dir, "ratesheet", base + GT_EXT[union])
    print()
    result = evaluate.evaluate(gt, out_csv)
    # Gate on accuracy over SOURCED cells (exclude intentional blanks/gaps): an
    # unsourced cell is a flagged gap, never a wrong value. wrong = genuine
    # mismatches against the groundtruth.
    correct, total, blank = result["correct"], result["total"], result["blank"]
    wrong = total - correct - blank
    sourced = (100.0 * correct / (correct + wrong)) if (correct + wrong) else 100.0
    result["wrong"] = wrong
    result["sourced_accuracy"] = sourced
    if min_accuracy is not None:
        gate_ok = result["header_ok"] and sourced >= min_accuracy
        result["gate_ok"] = gate_ok
        status = "PASS" if gate_ok else "FAIL"
        print(f"\n=== GATE [{union}]: {status} | header_ok {result['header_ok']} | "
              f"sourced accuracy {sourced:.1f}% (correct {correct}, wrong {wrong}, "
              f"blank/gaps {blank}) >= {min_accuracy:.1f}% ===")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--union", choices=list(TARGETS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-eval", action="store_true")
    ap.add_argument("--no-critic", action="store_true",
                    help="skip the advisory completeness-coverage critic")
    ap.add_argument("--min-accuracy", type=float, default=None,
                    help="fail (exit 1) if any union's cell accuracy is below this "
                         "percent or its header does not match the groundtruth")
    args = ap.parse_args()
    unions = list(TARGETS) if args.all else [args.union]
    if not unions or unions == [None]:
        ap.error("pass --union <name> or --all")

    failures = []
    for u in unions:
        result = run_union(u, do_eval=not args.no_eval, min_accuracy=args.min_accuracy,
                           do_critic=not args.no_critic)
        if args.min_accuracy is not None and result is not None and not result.get("gate_ok"):
            failures.append((u, result["accuracy"], result["header_ok"]))

    if failures:
        print("\n########## GATE FAILURES ##########")
        for u, acc, hok in failures:
            print(f"  {u}: accuracy {acc:.1f}%, header_ok {hok}")
        sys.exit(1)
    if args.min_accuracy is not None:
        print(f"\n########## GATE PASSED for all {len(unions)} union(s) "
              f"(>= {args.min_accuracy:.1f}%) ##########")


if __name__ == "__main__":
    main()
