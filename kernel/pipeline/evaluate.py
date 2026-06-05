"""Stage 5 - evaluate output vs groundtruth (generalized from compare_483.py).

Header diff, key-based row alignment, cell accuracy +/-0.01 (percent cells
compared as percents), per-column and per-zone breakdown, mismatch list.
Reads CSV or XLSX groundtruth (openpyxl). Read-only on groundtruth.
"""
from __future__ import annotations

import csv
import os

KEYS = ["Zone", "Package", "Start Date", "End Date"]
TOL = 0.01


def _load_csv(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def _load_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    hdr = [("" if c is None else str(c)) for c in rows[0]]
    out = []
    for r in rows[1:]:
        d = {hdr[i]: ("" if r[i] is None else r[i]) for i in range(len(hdr))}
        out.append(d)
    return out


def load(path):
    if path.lower().endswith(".xlsx"):
        return _load_xlsx(path)
    return _load_csv(path)


def _norm_date(s):
    """Normalize date-like strings/datetimes to M/D/YY for key alignment.

    GT XLSX stores Start/End Date as datetime ('2026-03-01 00:00:00'); the
    output CSV writes '3/1/26'. Both collapse to the same canonical form here.
    """
    import datetime
    import re
    if isinstance(s, (datetime.datetime, datetime.date)):
        return f"{s.month}/{s.day}/{s.year % 100}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(s))
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{mo}/{d}/{y % 100}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", str(s).strip())
    if m:
        y = int(m.group(3))
        return f"{int(m.group(1))}/{int(m.group(2))}/{y % 100}"
    return str(s).strip()


def norm(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, int):
        return f"{v:.2f}"
    return str(v).strip()


def cells_match(a, b):
    a, b = norm(a), norm(b)
    if a == b:
        return True
    try:
        # +1e-9 so an exact 1-cent difference counts as a match: in float,
        # abs(74.12 - 74.11) == 0.010000000000005 which is > TOL without the
        # epsilon, spuriously failing values that differ by exactly the tolerance.
        return (abs(float(a.rstrip("%")) - float(b.rstrip("%"))) <= TOL + 1e-9
                and a.endswith("%") == b.endswith("%"))
    except ValueError:
        return False


def is_blank_row(r):
    return all(norm(v) == "" for v in r.values())


def evaluate(gt_path, ai_path, verbose=True):
    gt = [r for r in load(gt_path) if not is_blank_row(r)]
    ai = [r for r in load(ai_path) if not is_blank_row(r)]
    gcols = [c for c in gt[0].keys() if c is not None]
    acols = list(ai[0].keys())

    header_ok = gcols == acols
    missing_cols = [c for c in gcols if c not in acols]
    extra_cols = [c for c in acols if c not in gcols]

    def keyfn(r):
        out = []
        for k in KEYS:
            v = r.get(k, "")
            out.append(_norm_date(v) if k in ("Start Date", "End Date") else norm(v))
        return tuple(out)

    gkey = {keyfn(r): r for r in gt}
    akey = {keyfn(r): r for r in ai}
    missing_rows = [k for k in gkey if k not in akey]
    extra_rows = [k for k in akey if k not in gkey]

    value_cols = [c for c in gcols if c not in KEYS and c in acols]
    total = correct = blank = 0
    per_col = {c: [0, 0, 0] for c in value_cols}
    per_zone = {}
    mismatches = []
    for k, grow in gkey.items():
        arow = akey.get(k)
        if not arow:
            continue
        zone = k[0]
        pz = per_zone.setdefault(zone, [0, 0])
        for c in value_cols:
            total += 1
            gv, av = norm(grow[c]), norm(arow.get(c, ""))
            pz[1] += 1
            if av == "" and gv != "":
                blank += 1
                per_col[c][2] += 1
                mismatches.append((k, c, gv, "(blank)"))
            elif cells_match(gv, av):
                correct += 1
                per_col[c][0] += 1
                pz[0] += 1
            else:
                per_col[c][1] += 1
                mismatches.append((k, c, gv, av))

    acc = (100.0 * correct / total) if total else 0.0

    if verbose:
        print("=== HEADER ===")
        print("exact match & order:", header_ok)
        if missing_cols:
            print("missing from output:", missing_cols)
        if extra_cols:
            print("extra in output    :", extra_cols)
        print(f"\n=== ROWS === groundtruth {len(gt)}  output {len(ai)}")
        if missing_rows:
            print("missing in output:", missing_rows)
        if extra_rows:
            print("extra output rows:", extra_rows)
        print("\n=== PER-COLUMN (correct/total) ===")
        for c in value_cols:
            ok, wrong, bl = per_col[c]
            n = ok + wrong + bl
            flag = "" if ok == n else ("   <-- mismatches" if wrong else "   <-- blanks")
            print(f"  {c:34} {ok}/{n}{flag}")
        print("\n=== PER-ZONE ===")
        for z, (ok, n) in per_zone.items():
            print(f"  {z:14} {ok}/{n} = {100*ok/n:.1f}%")
        print(f"\n=== OVERALL CELL ACCURACY: {correct}/{total} = {acc:.1f}%  "
              f"(blanks {blank}, wrong {total-correct-blank}) ===")
        if mismatches:
            print("\n=== MISMATCHES (key -> column: expected vs got) ===")
            for k, c, gv, av in mismatches[:80]:
                print(f"  {k[0]}/{k[1]:22} {c:30} exp {gv!r:10} got {av!r}")

    return {
        "header_ok": header_ok, "missing_cols": missing_cols, "extra_cols": extra_cols,
        "missing_rows": missing_rows, "extra_rows": extra_rows,
        "accuracy": acc, "correct": correct, "total": total, "blank": blank,
        "per_zone": {z: (ok, n) for z, (ok, n) in per_zone.items()},
        "mismatches": mismatches,
    }


if __name__ == "__main__":
    import sys
    evaluate(sys.argv[1], sys.argv[2])
