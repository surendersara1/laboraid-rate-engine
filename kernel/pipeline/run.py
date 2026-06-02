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

from pipeline import extract, pivot, evaluate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# union -> (groundtruth path [read-only header], output csv base name)
TARGETS = {
    "sprinkler_fitters_483": "2026.01.01.483 Rate Sheet",
    "sprinkler_fitters_704": "2026.01.01.704 Rate Sheet",
    "pipe_fitters_537": "2026.03.01.537 Rate Sheet",
}
GT_EXT = {
    "sprinkler_fitters_483": ".csv",
    "sprinkler_fitters_704": ".csv",
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


def run_union(union, do_eval=True):
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

    if do_eval:
        gt = os.path.join(union_dir, "ratesheet", base + GT_EXT[union])
        print()
        evaluate.evaluate(gt, out_csv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--union", choices=list(TARGETS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-eval", action="store_true")
    args = ap.parse_args()
    unions = list(TARGETS) if args.all else [args.union]
    if not unions or unions == [None]:
        ap.error("pass --union <name> or --all")
    for u in unions:
        run_union(u, do_eval=not args.no_eval)


if __name__ == "__main__":
    main()
