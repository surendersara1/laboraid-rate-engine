"""
Compare the generated 483 ratesheet against the groundtruth, column by column.
Run: uv run --with pandas python extract/compare_483.py
Read-only on the groundtruth; never modifies any data file.
"""
import csv

GT = "data/sprinkler_fitters_483/ratesheet/2026.01.01.483 Rate Sheet.csv"
AI = "data/sprinkler_fitters_483/ai_output/2026.01.01.483 Rate Sheet.csv"
KEYS = ["Zone", "Package", "Start Date", "End Date"]
TOL = 0.01


def load(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def norm(v):
    if v is None:
        return ""
    return v.strip()


def cells_match(a, b):
    a, b = norm(a), norm(b)
    if a == b:
        return True
    try:
        return abs(float(a.rstrip("%")) - float(b.rstrip("%"))) <= TOL and a.endswith("%") == b.endswith("%")
    except ValueError:
        return False


def main():
    gt, ai = load(GT), load(AI)
    gcols, acols = list(gt[0].keys()), list(ai[0].keys())

    print("=== HEADER ===")
    print("exact match & order:", gcols == acols)
    print("missing from output:", [c for c in gcols if c not in acols])
    print("extra in output    :", [c for c in acols if c not in gcols])

    gkey = {tuple(r[k] for k in KEYS): r for r in gt}
    akey = {tuple(r[k] for k in KEYS): r for r in ai}
    print("\n=== ROWS ===")
    print(f"groundtruth rows: {len(gt)}   output rows: {len(ai)}")
    print("groundtruth rows missing in output:", [k for k in gkey if k not in akey])
    print("extra output rows:", [k for k in akey if k not in gkey])

    value_cols = [c for c in gcols if c not in KEYS and c in acols]
    total = correct = blank = 0
    per_col = {c: [0, 0, 0] for c in value_cols}  # [correct, wrong, blank]
    mismatches = []
    for k, grow in gkey.items():
        arow = akey.get(k)
        if not arow:
            continue
        for c in value_cols:
            total += 1
            gv, av = norm(grow[c]), norm(arow[c])
            if av == "" and gv != "":
                blank += 1
                per_col[c][2] += 1
                mismatches.append((k, c, gv, "(blank)"))
            elif cells_match(gv, av):
                correct += 1
                per_col[c][0] += 1
            else:
                per_col[c][1] += 1
                mismatches.append((k, c, gv, av))

    print("\n=== PER-COLUMN ACCURACY (correct / total over aligned rows) ===")
    for c in value_cols:
        ok, wrong, bl = per_col[c]
        n = ok + wrong + bl
        flag = "" if ok == n else "   <-- mismatches" if wrong else "   <-- blanks"
        print(f"  {c:34} {ok}/{n}{flag}")

    print(f"\n=== OVERALL CELL ACCURACY: {correct}/{total} = {100*correct/total:.1f}%  "
          f"(blanks: {blank}, wrong: {total-correct-blank}) ===")

    # Building-only accuracy (the fully-sourceable zone)
    b_total = b_correct = 0
    for k, grow in gkey.items():
        if k[0] != "Building":
            continue
        arow = akey.get(k)
        for c in value_cols:
            b_total += 1
            if cells_match(norm(grow[c]), norm(arow[c])):
                b_correct += 1
    print(f"=== BUILDING ZONE ONLY: {b_correct}/{b_total} = {100*b_correct/b_total:.1f}% ===")

    if mismatches:
        print("\n=== MISMATCHES (row key -> column: expected vs got) ===")
        for k, c, gv, av in mismatches:
            print(f"  {k[0]}/{k[1]:20} {c:30} expected {gv!r:10} got {av!r}")


if __name__ == "__main__":
    main()
