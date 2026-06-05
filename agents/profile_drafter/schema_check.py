"""Profile-YAML schema validator (D.3).

Validates a candidate profile YAML against the schema of the existing
reference profile ``kernel/profiles/sprinkler_fitters_704.yaml``. Pure Python,
no LLM, ``mypy --strict`` clean.

Schema (derived from sprinkler_fitters_704.yaml + sprinkler_fitters_483.yaml +
pipe_fitters_537.yaml):

    union: str                              # union key (lower_snake_case)
    constants:                              # dict[str, str]
      Union Group: str
      Trade: str
      Union Local: str
    start_date: str                         # M/D/YY
    end_date: str                           # M/D/YY
    key_columns: list[str]                  # always [Zone, Package, Start Date, End Date]
    columns: list[str | dict]               # 7 string echoes of key columns +
                                            # dict entries for $/%/raw fields

Return shape:
    {
      "ok": bool,
      "errors": list[str],                   # human-readable error messages
      "warnings": list[str],                 # non-blocking notes
    }
"""

from __future__ import annotations

from typing import Any

import yaml  # type: ignore[import-untyped]

# Required top-level keys, in declared order matching reference profiles.
REQUIRED_TOP_KEYS: tuple[str, ...] = (
    "union",
    "constants",
    "start_date",
    "end_date",
    "key_columns",
    "columns",
)

REQUIRED_CONSTANTS: tuple[str, ...] = ("Union Group", "Trade", "Union Local")

# All three reference profiles use this exact key_columns list.
EXPECTED_KEY_COLUMNS: tuple[str, ...] = ("Zone", "Package", "Start Date", "End Date")

# The first 7 columns are always the key-column echo block.
EXPECTED_LEADING_COLUMN_NAMES: tuple[str, ...] = (
    "Union Group",
    "Trade",
    "Union Local",
    "Zone",
    "Package",
    "Start Date",
    "End Date",
)

# Valid `kind` values on dict-entry columns.
VALID_COLUMN_KINDS: frozenset[str] = frozenset({"$", "%", "raw"})


def schema_check(candidate_yaml: str) -> dict[str, Any]:
    """Validate a profile YAML string against the reference schema.

    Args:
        candidate_yaml: the YAML document as a string.

    Returns:
        ``{"ok": bool, "errors": list[str], "warnings": list[str]}``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        data: Any = yaml.safe_load(candidate_yaml)
    except yaml.YAMLError as exc:
        return {"ok": False, "errors": [f"YAML parse error: {exc}"], "warnings": []}

    if not isinstance(data, dict):
        return {
            "ok": False,
            "errors": [f"top-level must be a mapping, got {type(data).__name__}"],
            "warnings": [],
        }

    # Required top-level keys.
    for key in REQUIRED_TOP_KEYS:
        if key not in data:
            errors.append(f"missing required top-level key: {key!r}")

    # union: str (lower_snake_case, contains a local-number suffix).
    union = data.get("union")
    if union is not None and not isinstance(union, str):
        errors.append(f"'union' must be a string, got {type(union).__name__}")
    elif isinstance(union, str) and not union:
        errors.append("'union' must be non-empty")

    # constants: dict containing Union Group / Trade / Union Local.
    constants = data.get("constants")
    if constants is not None:
        if not isinstance(constants, dict):
            errors.append(f"'constants' must be a mapping, got {type(constants).__name__}")
        else:
            for c_key in REQUIRED_CONSTANTS:
                if c_key not in constants:
                    errors.append(f"constants missing required key: {c_key!r}")
                else:
                    val = constants[c_key]
                    if not isinstance(val, str):
                        # ints (e.g., the local number) are sometimes emitted by
                        # YAML — accept ints but record a warning.
                        if isinstance(val, int):
                            warnings.append(
                                f"constants['{c_key}'] is int ({val}); reference "
                                "profiles use a quoted string like \"704\""
                            )
                        else:
                            errors.append(
                                f"constants['{c_key}'] must be a string, got "
                                f"{type(val).__name__}"
                            )

    # start_date / end_date — non-empty strings.
    for date_key in ("start_date", "end_date"):
        v = data.get(date_key)
        if v is not None and not isinstance(v, str):
            errors.append(f"'{date_key}' must be a string, got {type(v).__name__}")
        elif isinstance(v, str) and not v.strip():
            errors.append(f"'{date_key}' must be non-empty")

    # key_columns: list of strings, must equal the reference set.
    key_columns = data.get("key_columns")
    if key_columns is not None:
        if not isinstance(key_columns, list):
            errors.append(
                f"'key_columns' must be a list, got {type(key_columns).__name__}"
            )
        else:
            if [c for c in key_columns] != list(EXPECTED_KEY_COLUMNS):
                errors.append(
                    f"'key_columns' must equal {list(EXPECTED_KEY_COLUMNS)!r}, "
                    f"got {key_columns!r}"
                )

    # columns: list of (str | dict). First 7 must echo the key-column names in
    # the reference order; remaining must be dict entries with name + kind.
    columns = data.get("columns")
    if columns is not None:
        if not isinstance(columns, list):
            errors.append(f"'columns' must be a list, got {type(columns).__name__}")
        else:
            errors.extend(_check_columns(columns, warnings))

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _check_columns(columns: list[Any], warnings: list[str]) -> list[str]:
    """Validate the columns list. Errors are appended to the returned list."""
    errors: list[str] = []

    if len(columns) < len(EXPECTED_LEADING_COLUMN_NAMES):
        errors.append(
            f"'columns' has {len(columns)} entries; need at least "
            f"{len(EXPECTED_LEADING_COLUMN_NAMES)} (the key-column echo block)"
        )
        return errors

    # First N must be plain strings matching the key-column echo block.
    for idx, expected in enumerate(EXPECTED_LEADING_COLUMN_NAMES):
        actual = columns[idx]
        if not isinstance(actual, str):
            errors.append(
                f"columns[{idx}] must be the string {expected!r} (the key-column "
                f"echo), got {type(actual).__name__}: {actual!r}"
            )
        elif actual != expected:
            errors.append(
                f"columns[{idx}] must be {expected!r}, got {actual!r}"
            )

    # Remaining entries: dict {name, kind, optional multiplier_of+factor}.
    seen_names: set[str] = set()
    for idx, entry in enumerate(
        columns[len(EXPECTED_LEADING_COLUMN_NAMES) :],
        start=len(EXPECTED_LEADING_COLUMN_NAMES),
    ):
        if not isinstance(entry, dict):
            errors.append(
                f"columns[{idx}] must be a mapping like {{name, kind}}, "
                f"got {type(entry).__name__}: {entry!r}"
            )
            continue

        name = entry.get("name")
        kind = entry.get("kind")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"columns[{idx}].name must be a non-empty string")
            continue
        if name in seen_names:
            errors.append(f"columns[{idx}].name duplicate: {name!r}")
        seen_names.add(name)

        if not isinstance(kind, str) or kind not in VALID_COLUMN_KINDS:
            errors.append(
                f"columns[{idx}] ({name!r}).kind must be one of "
                f"{sorted(VALID_COLUMN_KINDS)}, got {kind!r}"
            )

        # Optional derived columns: multiplier_of must reference a known column
        # name; factor must be numeric.
        if "multiplier_of" in entry or "factor" in entry:
            mult_of = entry.get("multiplier_of")
            factor = entry.get("factor")
            if not isinstance(mult_of, str) or not mult_of:
                errors.append(
                    f"columns[{idx}] ({name!r}).multiplier_of must be a non-empty "
                    f"string, got {mult_of!r}"
                )
            elif mult_of not in seen_names and mult_of != name:
                # The reference (e.g., Wage) must already appear above this row.
                warnings.append(
                    f"columns[{idx}] ({name!r}).multiplier_of={mult_of!r} not "
                    "seen earlier in columns; ordering may be wrong"
                )
            if not isinstance(factor, (int, float)):
                errors.append(
                    f"columns[{idx}] ({name!r}).factor must be numeric, "
                    f"got {factor!r}"
                )

    return errors
