"""F4: Per-union column-name normalization.

Different unions use slightly different fund/column names for the same
underlying benefit. The Publisher normalizes them to canonical names so
downstream queries + the xlsx exporter produce a consistent schema even
when the kernel/LLM emits union-specific labels.

Keep this file small and obvious — one map per union, fallthrough to
identity. Add a new union here, no other code change needed.
"""
from __future__ import annotations

# Each union's map: { extracted_column_name -> canonical_column_name }
# Empty/missing → no change.
_BY_LOCAL: dict[str, dict[str, str]] = {
    "483": {
        # 483-specific
        "Work Assessment 1 483": "Union Dues 1 483",
        "Work Assesment 2 483": "Union Dues 2 483",  # customer xlsx typo
        "Work Assessment 2 483": "Union Dues 2 483",
    },
    "704": {
        # 704 uses different fund names
        "Apprenticeship Training": "J&A Training 704",
        "S.U.B. 704": "SUB 704",
        "Craft 704": "Craft Fund 704",
    },
    "281": {
        "Local 281 Training Fund": "J&A Training 281",
    },
    "537": {
        # Pipefitter 537 uses different fund names
        "Apprenticeship Training": "J&A Training 537",
        "Supplemental Pension": "Supplemental Pension 537",
        "Yellow Book Fund": "Trust 537",
    },
    "821": {
        "Local 821 Training Fund": "J&A Training 821",
    },
}


def canonicalize(local: str, col: str) -> str:
    """Map a possibly union-specific column name to its canonical form."""
    if not col:
        return col
    overrides = _BY_LOCAL.get(local, {})
    return overrides.get(col, col)
