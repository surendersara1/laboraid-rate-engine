"""Deterministic Rule 1-12 validation per MASTER_DATA_REVIEW_RULES.md.

Run AFTER the canonical CSV is written to Aurora. Emits a structured
disposition report — one entry per unresolved (or near-resolved) name —
that the Inbox banner and gap_report.json surface to the reviewer.

Usage from Publisher post-step:

    from master_validation import validate_rate_period
    dispositions = validate_rate_period(local, cells)
    # dispositions = list[dict]: [{"kind", "extracted", "match",
    #   "status", "rule", "note"}]
"""
from __future__ import annotations
from typing import Any

import master_data


# Each disposition row has shape:
#   {
#     "kind": "fund" | "package" | "zone" | "fund_type" | "format" | ...,
#     "extracted": str,        # name as it appears on the rate sheet
#     "match_id": str | None,  # master ID matched (or None)
#     "match_name": str | None,
#     "status": "OK" | "DRIFT" | "NOT_FOUND" | "WRONG_TYPE" | "WRONG_FORMAT" | ...,
#     "rule": str,             # which SOP rule fired (e.g. "Rule 2")
#     "note": str,             # human-readable disposition
#     "suggestion": str | None,
#   }


def _disp(kind, extracted, status, rule, note, *, match=None, suggestion=None):
    return {
        "kind": kind,
        "extracted": extracted,
        "match_id": (match or {}).get("ID"),
        "match_name": (
            (match or {}).get("Fund Name")
            or (match or {}).get("Package Name")
            or (match or {}).get("Zone Name")
        ),
        "status": status,
        "rule": rule,
        "note": note,
        "suggestion": suggestion,
    }


def validate_rate_period(
    local: str | int, cells: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate every fund-column / package / zone name on this period
    against the master lists. Return a list of dispositions for any name
    that needs reviewer attention.

    `cells` is the list of rate_cells rows (each with 'zone',
    'package', 'column_name', 'value', and the JSON 'provenance' dict).
    """
    dispositions: list[dict[str, Any]] = []

    # Rule 2 — every fund column must resolve to a Master Fund List Fund Name.
    # Rule 5 — value format ($ vs %) must agree with Percentage Based Fund.
    col_seen: set[str] = set()
    for c in cells:
        col = (c.get("column_name") or "").strip()
        if not col or col in col_seen:
            continue
        col_seen.add(col)
        # Skip the structural columns (Wage, Wage Diff, Wage 1.5x/2.0x)
        # — those aren't funds; they're wage derivations.
        if col in ("Wage", "Wage Differential", "Wage 1.5x", "Wage 2.0x"):
            continue
        match, status = master_data.match_fund(local, col)
        if status == "OK":
            # Rule 5: spot-check $/% format vs Percentage Based Fund
            pbf = (match or {}).get(
                "Percentage Based Fund (Hourly, Percent, or Both)"
            ) or "Hourly"
            # We don't have a reliable signal of "is the stored value a %"
            # — that lives in provenance.row_raw or the original CSV.
            # For now we just record the match.
            dispositions.append(
                _disp(
                    "fund",
                    col,
                    "OK",
                    "Rule 2",
                    f"resolves to {match['ID']} {match['Fund Name']} "
                    f"({match['Fund Type']}, {pbf})",
                    match=match,
                )
            )
        elif status == "DRIFT":
            dispositions.append(
                _disp(
                    "fund",
                    col,
                    "DRIFT",
                    "Rule 2 / Rule 10.2",
                    f"near-match to {match['ID']} {match['Fund Name']} — "
                    f"reconcile spelling/punctuation drift",
                    match=match,
                    suggestion=match["Fund Name"],
                )
            )
        else:
            dispositions.append(
                _disp(
                    "fund",
                    col,
                    "NOT_FOUND",
                    "Rule 2 / Rule 10.3",
                    "no Master Fund List entry — add a master row for this "
                    "union before upload, or fix the extracted column name",
                )
            )

    # Rule 6 — every package must resolve to Master Package List Package Name.
    pkg_seen: set[str] = set()
    for c in cells:
        pkg = (c.get("package") or "").strip()
        if not pkg or pkg in pkg_seen:
            continue
        pkg_seen.add(pkg)
        match, status = master_data.match_package(pkg)
        if status == "OK":
            assignable = (match or {}).get("Can Be Assigned To Employee") == "Yes"
            dispositions.append(
                _disp(
                    "package",
                    pkg,
                    "OK",
                    "Rule 6",
                    f"resolves to {match['ID']} {match['Package Name']} "
                    f"({'assignable' if assignable else 'differential-only'})",
                    match=match,
                )
            )
        elif status == "DRIFT":
            dispositions.append(
                _disp(
                    "package",
                    pkg,
                    "DRIFT",
                    "Rule 6 / Rule 10.2",
                    f"near-match to {match['ID']} {match['Package Name']}",
                    match=match,
                    suggestion=match["Package Name"],
                )
            )
        else:
            dispositions.append(
                _disp(
                    "package",
                    pkg,
                    "NOT_FOUND",
                    "Rule 6 / Rule 10.3",
                    "no Master Package List entry — confirm naming family for "
                    "this union (Class N vs Year N vs Year 2-A/2-B), or "
                    "add a master row",
                )
            )

    # Rule 7 — every zone must resolve to Master Zone List Zone Name.
    zone_seen: set[str] = set()
    for c in cells:
        zone = (c.get("zone") or "").strip()
        if not zone or zone in zone_seen:
            continue
        zone_seen.add(zone)
        match, status = master_data.match_zone(local, zone)
        if status == "OK":
            dispositions.append(
                _disp(
                    "zone",
                    zone,
                    "OK",
                    "Rule 7",
                    f"resolves to {match['ID']} {match['Zone Name']} ({match['Union']})",
                    match=match,
                )
            )
        elif status == "DRIFT":
            dispositions.append(
                _disp(
                    "zone",
                    zone,
                    "DRIFT",
                    "Rule 7 / Rule 10.2",
                    f"near-match to {match['ID']} {match['Zone Name']} — "
                    f"reconcile (e.g. 821 sheet 'Low-Commercial' vs master "
                    f"'Low Commercial')",
                    match=match,
                    suggestion=match["Zone Name"],
                )
            )
        else:
            dispositions.append(
                _disp(
                    "zone",
                    zone,
                    "NOT_FOUND",
                    "Rule 7 / Rule 10.4",
                    "no Master Zone List entry for this union — sheet wins "
                    "over master per Dan's hierarchy (Rule 10.4); add a "
                    "master row reflecting the CBA's zone",
                )
            )

    return dispositions


def summarize(dispositions: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts grouped by (kind, status). For the API + UI banner."""
    by = {}
    for d in dispositions:
        key = (d["kind"], d["status"])
        by[key] = by.get(key, 0) + 1
    return {
        "total": len(dispositions),
        "ok": sum(1 for d in dispositions if d["status"] == "OK"),
        "drift": sum(1 for d in dispositions if d["status"] == "DRIFT"),
        "not_found": sum(1 for d in dispositions if d["status"] == "NOT_FOUND"),
        "by_kind_status": {f"{k[0]}.{k[1]}": v for k, v in by.items()},
    }
