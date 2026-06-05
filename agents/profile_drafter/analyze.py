"""E.1 — analyze_groundtruth: classify a customer's existing ratesheet.

Pure Python, no LLM. Reads a CSV (stdlib `csv`) or xlsx (`openpyxl` read-only),
classifies each column's value-kind by sampling rows, matches column names
against ``kernel/canonical/fields.yaml`` aliases, identifies key columns by
typical names, and returns a structured analysis dict the drafter can feed to
the Sonnet profile-drafting prompt.

Return shape (mirrors the BUILD_PROFILE_DRAFTER.md §3 E.1 spec):

    {
      "columns": [{"name": "Wage", "kind": "$", "canonical_field": "wage"}, ...],
      "key_columns": ["Zone", "Package", ...],
      "sample_rows": [{"Zone": "Building", "Package": "Journeyman", ...}, ...],
      "unknown_fields": ["Some New Fund 120", ...],
    }
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

# Typical key-column names across the reference profiles.
_KEY_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "Union Group",
        "Trade",
        "Union Local",
        "Zone",
        "Package",
        "Start Date",
        "End Date",
    }
)

# Hard-coded fallback canonical map — used if fields.yaml is not on disk
# (defensive; the kernel canonical/fields.yaml is the source of truth at
# runtime). Path to fields.yaml is resolved relative to the kernel under the
# repo root when present; otherwise this empty mapping skips canonical
# resolution gracefully.
_DEFAULT_FIELDS_YAML_PATHS: tuple[str, ...] = (
    "/opt/kernel/canonical/fields.yaml",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "kernel",
        "canonical",
        "fields.yaml",
    ),
)


def analyze_groundtruth(ratesheet_path: str) -> dict[str, Any]:
    """Open the customer's existing ratesheet and produce a structured analysis.

    Args:
        ratesheet_path: path to a ``.csv`` or ``.xlsx`` file.

    Returns:
        Dict with keys ``columns``, ``key_columns``, ``sample_rows``,
        ``unknown_fields``. See module docstring for the exact shape.

    Raises:
        FileNotFoundError: if the path does not exist.
        ValueError: if the file extension is not .csv or .xlsx.
    """
    path = Path(ratesheet_path)
    if not path.exists():
        raise FileNotFoundError(f"ratesheet not found: {ratesheet_path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        header, sample_rows = _read_csv(path)
    elif suffix == ".xlsx":
        header, sample_rows = _read_xlsx(path)
    else:
        raise ValueError(f"unsupported ratesheet extension {suffix!r}; expected .csv or .xlsx")

    canonical_lookup = _build_canonical_lookup()

    columns: list[dict[str, Any]] = []
    unknown_fields: list[str] = []
    for col in header:
        kind = _classify_kind(col, sample_rows)
        canonical = canonical_lookup.get(col)
        entry: dict[str, Any] = {"name": col, "kind": kind}
        if canonical is not None:
            entry["canonical_field"] = canonical
        else:
            # Key columns are structural — they're not "unknown fields" even
            # though they don't have a canonical_field entry in fields.yaml.
            if col not in _KEY_COLUMN_NAMES:
                unknown_fields.append(col)
        columns.append(entry)

    key_columns = [c for c in header if c in _KEY_COLUMN_NAMES]

    return {
        "columns": columns,
        "key_columns": key_columns,
        "sample_rows": sample_rows[:3],
        "unknown_fields": unknown_fields,
    }


# ---------------------------------------------------------------------------
# CSV / XLSX readers
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (header, sample_rows). Sample rows are dicts keyed by column."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = [c.strip() for c in next(reader)]
        except StopIteration:
            return [], []
        sample_rows: list[dict[str, Any]] = []
        for raw in reader:
            if not raw or not any(cell.strip() for cell in raw):
                continue
            row: dict[str, Any] = {}
            for i, col in enumerate(header):
                row[col] = raw[i].strip() if i < len(raw) else ""
            sample_rows.append(row)
            if len(sample_rows) >= 5:
                break
        return header, sample_rows


def _read_xlsx(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """xlsx variant via openpyxl read-only mode."""
    import openpyxl  # type: ignore[import-untyped]

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return [], []

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_raw = next(rows_iter)
    except StopIteration:
        return [], []
    header = [("" if c is None else str(c).strip()) for c in header_raw]

    sample_rows: list[dict[str, Any]] = []
    for raw in rows_iter:
        if raw is None or all(c is None for c in raw):
            continue
        row: dict[str, Any] = {}
        for i, col in enumerate(header):
            if not col:
                continue
            cell = raw[i] if i < len(raw) else None
            row[col] = "" if cell is None else cell
        sample_rows.append(row)
        if len(sample_rows) >= 5:
            break

    return [c for c in header if c], sample_rows


# ---------------------------------------------------------------------------
# Column-kind classification + canonical lookup
# ---------------------------------------------------------------------------


_PCT_RE = re.compile(r"^-?\d+(\.\d+)?\s*%$")
_NUMERIC_RE = re.compile(r"^-?\$?\d+(\.\d+)?$")


def _classify_kind(column: str, sample_rows: list[dict[str, Any]]) -> str:
    """Inspect the column's values in the sample rows; return one of $, %, raw."""
    if column in _KEY_COLUMN_NAMES:
        return "raw"

    seen_pct = False
    seen_num = False
    seen_text = False
    for row in sample_rows:
        v = row.get(column, "")
        if v is None or v == "":
            continue
        s = str(v).strip()
        if _PCT_RE.match(s):
            seen_pct = True
        elif _NUMERIC_RE.match(s.replace(",", "")):
            seen_num = True
        else:
            seen_text = True

    if seen_pct and not seen_num:
        return "%"
    if seen_num and not seen_text:
        return "$"
    if seen_text and not (seen_pct or seen_num):
        return "raw"
    # Mixed or empty — default to "$" for any cell that EVER showed a number.
    if seen_num:
        return "$"
    if seen_pct:
        return "%"
    return "raw"


def _build_canonical_lookup() -> dict[str, str]:
    """Return {column_label: canonical_field_id} from kernel/canonical/fields.yaml.

    fields.yaml shape:
        canonical_id: [Column Label 1, Column Label 2, ...]

    We invert it: every column label maps back to its canonical id.
    """
    fields_path: str | None = None
    for candidate in _DEFAULT_FIELDS_YAML_PATHS:
        if os.path.exists(candidate):
            fields_path = candidate
            break
    if fields_path is None:
        return {}

    try:
        with open(fields_path, encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}

    lookup: dict[str, str] = {}
    if isinstance(raw, dict):
        for canonical_id, labels in raw.items():
            if not isinstance(canonical_id, str):
                continue
            if not isinstance(labels, list):
                continue
            for label in labels:
                if isinstance(label, str):
                    lookup[label] = canonical_id
    return lookup
