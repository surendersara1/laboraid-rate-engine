"""Stage 6 - completeness critic (advisory).

The hard failure mode we measured on 821 was not wrong values, it was *missing
breadth*: the Rate Notice listed only part of the sheet and the rest (Trainee,
Residential/Helper classes, the Market Recovery / UA Organizing funds, the second
indenture cohort) lived deep in the CBA. A value-accuracy check can't catch that --
nothing was wrong, things were simply absent.

This critic scans the union's CBA/notice text for the *vocabulary* of a ratesheet
-- classifications, zones, and fund/benefit names it mentions -- and flags any that
do not appear in the produced output. It is heuristic and ADVISORY: it writes a
`<union>.coverage.md` report and returns findings; it never fabricates or gates.
Its job is to turn "I didn't know that class/zone/fund existed" into a reviewable
list before a human signs off.

The core, find_gaps(), is pure (text + output vocabulary -> findings) so it is
unit-testable without PDFs.
"""
from __future__ import annotations

import os
import re

# Signal vocabularies. Curated to be specific enough to avoid most incidental
# matches; the report is explicitly advisory so a stray hit is cheap.
CLASSIFICATION_SIGNALS = [
    "General Foreman", "Foreman", "Journeyman", "Journeyworker", "Apprentice",
    "Trainee", "Production Worker", "Helper", "Tradesman", "Probationary",
    "Sub-Foreman", "Steward",
]
ZONE_SIGNALS = [
    "Industrial", "Commercial", "Low-Commercial", "Residential", "Building",
    "Power & Gas", "Metal Trades", "Detention",
]
# fund/benefit term -> the substring that should appear in an output column name
FUND_SIGNALS = {
    "Health & Welfare": "health", "RESA": "resa", "Pension": "pension",
    "Annuity": "annuity", "Supplemental Pension": "sis", "S.I.S": "sis",
    "Market Recovery": "market recovery", "Organizing": "organizing",
    "Industry Promotion": "promotion", "Industry Improvement": "improvement",
    "Apprentice Training": "training", "Apprenticeship Training": "training",
    "International Training": "international", "Education": "education",
    "Vacation": "vacation", "HRA": "hra", "S.U.B": "sub",
    "Labor Management": "labor", "Labor/Mgt": "labor", "LMCC": "lmcc",
    "Union Protection": "protection", "P.A.C": "pac", "C.O.P.E": "cope",
    "Public Relations": "public relations", "Holiday": "holiday",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(s).lower())


def _mentions(text: str, term: str) -> int:
    """Count case-insensitive occurrences of term as a whole phrase."""
    return len(re.findall(r"(?<![a-z])" + re.escape(term.lower()) + r"(?![a-z])",
                          text.lower()))


def find_gaps(cba_text, packages, zones, columns):
    """Pure core. Return findings: list of dicts {category, term, hits} for terms
    mentioned in cba_text but absent from the produced packages/zones/columns.

    packages/zones: iterables of the output's Package/Zone cell values.
    columns: iterable of the output's column header names.
    """
    pkg_blob = " | ".join(_norm(p) for p in packages)
    zone_blob = " | ".join(_norm(z) for z in zones)
    col_blob = " | ".join(_norm(c) for c in columns)
    findings = []

    for term in CLASSIFICATION_SIGNALS:
        hits = _mentions(cba_text, term)
        if hits and _norm(term) not in pkg_blob:
            findings.append({"category": "classification", "term": term, "hits": hits})
    for term in ZONE_SIGNALS:
        hits = _mentions(cba_text, term)
        if hits and _norm(term) not in zone_blob:
            findings.append({"category": "zone", "term": term, "hits": hits})
    for term, col_sub in FUND_SIGNALS.items():
        hits = _mentions(cba_text, term)
        if hits and _norm(col_sub) not in col_blob:
            findings.append({"category": "fund", "term": term, "hits": hits})

    findings.sort(key=lambda f: (-f["hits"], f["category"], f["term"]))
    return findings


def _read_cba_text(union_dir):
    """Best-effort: concatenate extractable text from every cba/ PDF. Empty string
    if the docs are image-only (then the critic can only say 'no text to scan')."""
    cba_dir = os.path.join(union_dir, "cba")
    if not os.path.isdir(cba_dir):
        return ""
    try:
        import pdfplumber
    except ImportError:
        return ""
    parts = []
    for name in sorted(os.listdir(cba_dir)):
        if not name.lower().endswith(".pdf"):
            continue
        try:
            with pdfplumber.open(os.path.join(cba_dir, name)) as pdf:
                for page in pdf.pages:
                    parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _read_output(out_csv):
    import csv
    with open(out_csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return [], [], []
    hdr = rows[0]
    idx = {h: i for i, h in enumerate(hdr)}
    pkgs = [r[idx["Package"]] for r in rows[1:] if "Package" in idx and len(r) > idx["Package"]]
    zones = [r[idx["Zone"]] for r in rows[1:] if "Zone" in idx and len(r) > idx["Zone"]]
    return pkgs, zones, hdr


def report(union_dir, union, out_csv):
    """Run the critic and write `<union>.coverage.md`. Returns the findings list."""
    cba_text = _read_cba_text(union_dir)
    pkgs, zones, cols = _read_output(out_csv)
    findings = find_gaps(cba_text, pkgs, zones, cols) if cba_text else []

    path = os.path.join(os.path.dirname(out_csv), f"{union}.coverage.md")
    lines = [f"# Coverage critic - {union}", "",
             "Advisory heuristic: terms the CBA/notice text mentions that do NOT "
             "appear in the produced ratesheet. Review each -- some are incidental "
             "mentions, but missing classifications/zones/funds show up here.", ""]
    if not cba_text:
        lines.append("_No extractable CBA text (image-only docs) - critic could not "
                     "scan; review coverage manually._")
    elif not findings:
        lines.append("_No gaps flagged: every classification, zone and fund the CBA "
                     "names is represented in the output._")
    else:
        lines.append("| Category | Term mentioned in CBA | CBA mentions | Note |")
        lines.append("|---|---|---|---|")
        for f in findings:
            lines.append(f"| {f['category']} | {f['term']} | {f['hits']} | "
                         f"not found in output {f['category']}s |")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    if findings:
        print(f"\n=== COVERAGE CRITIC [{union}]: {len(findings)} term(s) in the CBA "
              f"not in the output (advisory) -> {os.path.basename(path)} ===")
        for f in findings[:12]:
            print(f"    {f['category']:14} {f['term']:24} (CBA mentions: {f['hits']})")
    return findings
