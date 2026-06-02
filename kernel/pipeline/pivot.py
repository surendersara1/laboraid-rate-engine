"""Stage 4 - pivot resolved rows into the wide groundtruth-shaped CSV.

Applies the profile's column order and per-column formatting ($ -> %.2f,
% -> the stored percent string, blank -> ""). Writes only to ai_output/.
"""
from __future__ import annotations

import csv
import os

from pipeline.compute import resolve_row


def fmt(col, value):
    if value is None or value == "":
        return ""
    kind = col["kind"] if isinstance(col, dict) and "kind" in col else "$"
    if kind == "%":
        return value if isinstance(value, str) else f"{value:.2f}%"
    if kind == "$":
        return f"{float(value):.2f}"
    return str(value)


def header(profile):
    return [c if isinstance(c, str) else c["name"] for c in profile["columns"]]


def write_csv(profile, classrows, out_csv):
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    cols = profile["columns"]
    hdr = header(profile)
    # stable sort: zone groups in first-seen order, classifications by class_order desc
    zone_order = {}
    for r in classrows:
        zone_order.setdefault(r.zone, len(zone_order))
    ordered = sorted(classrows, key=lambda r: (zone_order[r.zone], -r.class_order))

    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        for r in ordered:
            resolved = resolve_row(profile, r)
            line = []
            for col in cols:
                if isinstance(col, str):
                    line.append(resolved.get(col, ""))
                else:
                    line.append(fmt(col, resolved.get(col["name"])))
            w.writerow(line)
    return len(ordered)
