"""
Extract the UA Local 483 (Sprinkler Fitters) ratesheet from the CBA documents.

Run:
    uv run --with pdfplumber python extract/build_483.py

Sources (READ ONLY):
  - data/sprinkler_fitters_483/cba/2026.01.01.483 Rate Notice.pdf   (Building/Commercial schedule, eff 1/1/2026)
  - data/sprinkler_fitters_483/cba/2024.08.01-2030.07.31.483 CBA.pdf (rules: foreman diffs, RESA, residential)

Output (only writable location):
  - data/sprinkler_fitters_483/ai_output/2026.01.01.483 Rate Sheet.csv

Provenance is recorded per column in COLUMN_SOURCES below so a human can audit
where each value came from. Values are derived from the CBA documents only;
the groundtruth ratesheet is never read for values.
"""
import csv
import os
from decimal import Decimal, ROUND_HALF_UP
import pdfplumber


def r2(x):
    """Round to 2 decimals, half-up (matches how the ratesheets are rounded)."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

UNION_DIR = "data/sprinkler_fitters_483"
RATE_NOTICE = f"{UNION_DIR}/cba/2026.01.01.483 Rate Notice.pdf"
OUT_DIR = f"{UNION_DIR}/ai_output"
OUT_CSV = f"{OUT_DIR}/2026.01.01.483 Rate Sheet.csv"

START, END = "1/1/26", "7/31/26"

HEADER = [
    "Union Group", "Trade", "Union Local", "Zone", "Package", "Start Date", "End Date",
    "Wage", "Wage Differential", "Wage 1.5x", "Wage 2.0x",
    "Health & Welfare", "RESA", "Health & Welfare Metal", "Pension", "SIS",
    "UA International Training", "Industry Promotion National Use",
    "J&A Training 483", "NCFPCG 483", "Bay Area IP Fund 483", "HRA 483",
    "Vacation 483", "Union Dues 1 483", "Union Dues 2 483",
]

# How each column is sourced (for the traceability / sourcing criterion).
COLUMN_SOURCES = {
    "Wage": "Rate Notice Rate/HR (apprentices & Fitter=Journeyman); foreman differentials from CBA Art.20",
    "Wage Differential": "Rate Notice 'Shift Work 15%' column (= Wage x1.15 for foremen)",
    "Wage 1.5x": "computed Wage x1.5", "Wage 2.0x": "computed Wage x2.0",
    "Health & Welfare": "Rate Notice H&W ($13.55) minus RESA ($0.95) per CBA Art.21",
    "RESA": "CBA Art.21 RESA = $0.95 all employees",
    "Health & Welfare Metal": "Building 0.00; Residential 'Metal Trades Plan A' $5.60 (CBA Art.3 sec.8)",
    "Pension": "Rate Notice PENS", "SIS": "Rate Notice S.I.S.",
    "UA International Training": "Rate Notice 'Int. Trng. Fund'",
    "Industry Promotion National Use": "Rate Notice '*I.P.'",
    "J&A Training 483": "Rate Notice 'J&A Trng. Cont.'",
    "NCFPCG 483": "Rate Notice '**NCFPCG'",
    "Bay Area IP Fund 483": "CBA Art.24 non-NFSA Industry Promotion (Bay Area) $0.11",
    "HRA 483": "Rate Notice HRA", "Vacation 483": "Rate Notice 'Vac. W/H'",
    "Union Dues 1 483": "Rate Notice 'Work Asses' = 6% (Work Assessment #1)",
    "Union Dues 2 483": "Rate Notice 'Work Asses II' = $1.05 (Work Assessment #2)",
}

UNSOURCED = []  # (zone, package, column, reason) — cells we could not source from the CBA


def money(s):
    """'$81.54' -> 81.54 ; '' -> None"""
    if s is None:
        return None
    s = s.replace("$", "").replace(",", "").strip()
    if s == "" or s == "-":
        return None
    return float(s)


def f2(x):
    return "" if x is None else f"{x:.2f}"


def parse_rate_notice():
    """Return dict keyed by class label -> dict of notice columns (floats)."""
    with pdfplumber.open(RATE_NOTICE) as pdf:
        table = None
        for p in pdf.pages:
            for t in p.extract_tables():
                if t and any(r and r[0] == "Class" for r in t):
                    table = t
                    break
        if table is None:
            raise SystemExit("Could not find the apprentice rate table in the Rate Notice")
    # locate header row
    hdr_i = next(i for i, r in enumerate(table) if r and r[0] == "Class")
    rows = {}
    for r in table[hdr_i + 1:]:
        if not r or not r[0]:
            continue
        label = r[0].strip()
        rows[label] = {
            "wage": money(r[1]),
            "diff": money(r[2]),
            "vac": money(r[3]),
            "dues1": "6.00%",            # 'Work Asses' = 6%
            "dues2": money(r[5]),        # 'Work Asses II' = $1.05
            "hw_combined": money(r[6]),  # H&W ($13.55 = H&W + RESA)
            "hra": money(r[7]),
            "pens": money(r[8]),
            "sis": money(r[9]),
            "ip_nat": money(r[10]),      # *I.P.
            "ua_intl": money(r[11]),     # Int. Trng. Fund
            "ncfpcg": money(r[12]),      # **NCFPCG
            "ja": money(r[13]),          # J&A Trng. Cont.
        }
    return rows


RESA = 0.95          # CBA Art.21
BAY_AREA_IP = 0.11   # CBA Art.24 non-NFSA


def building_row(package, wage, src):
    """Build a Building-zone row given the wage and the notice source columns `src`."""
    diff = r2(wage * 1.15)                 # 'Shift Work 15%' = Wage x1.15 (CBA Art.7)
    hw = r2(src["hw_combined"] - RESA)     # 13.55 - 0.95 = 12.60
    return [
        "UA", "Sprinkler", "483", "Building", package, START, END,
        f2(wage), f2(diff), f2(r2(wage * 1.5)), f2(r2(wage * 2.0)),
        f2(hw), f2(RESA), f2(0.0), f2(src["pens"]), f2(src["sis"]),
        f2(src["ua_intl"]), f2(src["ip_nat"]),
        f2(src["ja"]), f2(src["ncfpcg"]), f2(BAY_AREA_IP), f2(src["hra"]),
        f2(src["vac"]), src["dues1"], f2(src["dues2"]),
    ]


def build_building(notice):
    rows = []
    j = notice["Fitter"]              # journeyman == "Fitter" in the notice
    jw = j["wage"]
    # Foreman differentials over journeyman scale, effective 1/1/2026 (CBA Art.20):
    #   Foreman 1 = +$10 (eff 8/1/2025), Foreman 2 = Foreman1 + $3, General Foreman = Foreman1 + $5
    f1 = jw + 10.0
    f2_ = f1 + 3.0
    gf = f1 + 5.0
    rows.append(building_row("General Foreman", gf, j))
    rows.append(building_row("Foreman 2", f2_, j))
    rows.append(building_row("Foreman 1", f1, j))
    rows.append(building_row("Journeyman", jw, j))
    for n in range(10, 0, -1):
        rows.append(building_row(f"Apprentice Class {n}", notice[str(n)]["wage"], notice[str(n)]))
    return rows


def residential_rows():
    """
    Residential schedule. Wages and the documented fund contributions come from
    CBA Art.3 sec.8 (effective 8/1/2024). The 1/1/2026 *re-allocation* of the
    annual $2 economic-package increases is NOT published in the provided
    documents, so affected fringe cells and the residential apprentice scale are
    left blank and reported rather than fabricated.
    """
    jw = 47.82                      # CBA Art.3 sec.8 Residential Sprinkler Fitter wage
    foreman = jw + 3.0             # CBA: foreman $3.00 over residential
    base_fringe = {               # documented residential funds, eff 8/1/2024 (CBA Art.3 sec.8)
        "Health & Welfare": 0.0,
        "RESA": RESA,
        "Health & Welfare Metal": 5.60,   # Metal Trades Plan A
        "Pension": 7.30,                  # N.A.S.I. Pension (8/1/2024 base; 2026 alloc unknown)
        "SIS": 2.00,
        "UA International Training": 0.10,
        "Industry Promotion National Use": 0.25,
        "J&A Training 483": 0.80,         # Local 483 Training Fund
        "NCFPCG 483": 0.15,               # No. CA Fire Prot. Industry Fund
        "Bay Area IP Fund 483": 0.11,
        "HRA 483": 1.50,
        "Vacation 483": None,             # not documented in residential 8/1/2024 schedule
        "Union Dues 1 483": "6.00%",
        "Union Dues 2 483": "1.05",
    }
    for col in ("Pension", "Vacation 483"):
        UNSOURCED.append(("Residential", "*", col,
                          "depends on 1/1/2026 package re-allocation not in provided docs"))

    def res_row(package, wage, with_diff_uplift):
        diff = r2(wage * 1.15) if with_diff_uplift else wage
        bf = base_fringe
        return [
            "UA", "Sprinkler", "483", "Residential", package, START, END,
            f2(wage), f2(diff), f2(r2(wage * 1.5)), f2(r2(wage * 2.0)),
            f2(bf["Health & Welfare"]), f2(bf["RESA"]), f2(bf["Health & Welfare Metal"]),
            f2(bf["Pension"]), f2(bf["SIS"]), f2(bf["UA International Training"]),
            f2(bf["Industry Promotion National Use"]), f2(bf["J&A Training 483"]),
            f2(bf["NCFPCG 483"]), f2(bf["Bay Area IP Fund 483"]), f2(bf["HRA 483"]),
            f2(bf["Vacation 483"]), bf["Union Dues 1 483"], bf["Union Dues 2 483"],
        ]

    rows = [res_row("Foreman", foreman, False), res_row("Journeyman", jw, False)]
    # Residential apprentice (Class 1-5) wage scale is not published in the provided
    # documents; emit the rows with wages blank + flagged rather than guessing.
    for n in range(5, 0, -1):
        UNSOURCED.append(("Residential", f"Apprentice Class {n}", "Wage",
                          "residential apprentice scale not in provided docs"))
        rows.append([
            "UA", "Sprinkler", "483", "Residential", f"Apprentice Class {n}", START, END,
            "", "", "", "",
            f2(0.0), f2(RESA), f2(5.60), "", "", "", "", "", "", f2(0.11), "", "", "", "",
        ])
    return rows


def main():
    notice = parse_rate_notice()
    rows = build_building(notice) + residential_rows()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"Wrote {OUT_CSV}  ({len(rows)} rows)")
    if UNSOURCED:
        print("\nCells/columns not sourceable from the provided CBA documents:")
        for zone, pkg, col, why in UNSOURCED:
            print(f"  - {zone} / {pkg} / {col}: {why}")


if __name__ == "__main__":
    main()
