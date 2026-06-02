"""Stage 3 - compute derived columns deterministically with half-up rounding.

Reads the profile's column specs. For any column with `multiplier_of`/`factor`
(e.g. Wage Differential = Wage x 1.15, Temporary Heat 1.1x = Temporary Heat x
1.1), it computes the value from the already-resolved base column on the same
row. Base (non-derived) columns come straight from the canonical cells.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from canonical.model import r2

import yaml

FIELDS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "canonical", "fields.yaml")


def _label_to_field():
    """Build {output_label -> canonical_field} from fields.yaml."""
    with open(FIELDS_PATH) as fh:
        fields = yaml.safe_load(fh)
    out = {}
    for cf, labels in fields.items():
        for label in labels:
            out[label] = cf
    return out


LABEL_TO_FIELD = _label_to_field()


def resolve_row(profile, classrow):
    """Return {output_label -> value} for one canonical ClassificationRow.

    Two passes: (1) base columns from canonical cells; (2) multiplier columns
    computed from base columns on the same row.
    """
    consts = profile["constants"]
    out = {}
    derived = []
    for col in profile["columns"]:
        if isinstance(col, str):
            # constant / key column
            if col in consts:
                out[col] = consts[col]
            elif col == "Zone":
                out[col] = classrow.zone
            elif col == "Package":
                out[col] = classrow.classification
            elif col == "Start Date":
                out[col] = profile["start_date"]
            elif col == "End Date":
                out[col] = profile["end_date"]
            else:
                out[col] = ""
            continue
        name = col["name"]
        if "multiplier_of" in col:
            derived.append(col)
            continue
        cf = LABEL_TO_FIELD.get(name)
        out[name] = classrow.get(cf) if cf else None

    for col in derived:
        cf = LABEL_TO_FIELD.get(col["name"])
        explicit = classrow.get(cf) if cf else None
        if explicit is not None:
            # extractor supplied an explicit value (e.g. residential differential
            # = wage, no 1.15 uplift) -> respect it over the generic multiplier.
            out[col["name"]] = explicit
            continue
        base = out.get(col["multiplier_of"])
        if isinstance(base, (int, float)):
            out[col["name"]] = r2(base * col["factor"])
        else:
            out[col["name"]] = None
    return out
