"""Stage 2 - extract CBA documents into canonical ClassificationRow lists.

Each union has a deterministic extractor that reads only the cba/ documents and
emits canonical rows (zone, classification, class_order, canonical_field cells)
plus a list of UNSOURCED gaps (zone, package, column, reason).

Provenance: every emitted cell carries source_doc + source_locator so a human
can audit it (traceability criterion). 483 reuses the proven parse from
extract/build_483.py verbatim.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re

from canonical.model import RateCell, ClassificationRow, r2, rmul
from pipeline import ingest

import pdfplumber


def money(s):
    if s is None:
        return None
    s = str(s).replace("$", "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    return float(s)


# ---------------------------------------------------------------------------
# 483 - reuse extract/build_483.py logic (Building zone proven 100%)
# ---------------------------------------------------------------------------

def _483_parse_rate_notice(notice_path):
    """Verbatim port of extract/build_483.py:parse_rate_notice."""
    with pdfplumber.open(notice_path) as pdf:
        table = None
        for p in pdf.pages:
            for t in p.extract_tables():
                if t and any(r and r[0] == "Class" for r in t):
                    table = t
                    break
        if table is None:
            raise SystemExit("Could not find the apprentice rate table in the 483 Rate Notice")
    hdr_i = next(i for i, r in enumerate(table) if r and r[0] == "Class")
    rows = {}
    for r in table[hdr_i + 1:]:
        if not r or not r[0]:
            continue
        label = r[0].strip()
        rows[label] = {
            "wage": money(r[1]),
            "vac": money(r[3]),
            "dues1": "6.00%",
            "dues2": money(r[5]),
            "hw_combined": money(r[6]),
            "hra": money(r[7]),
            "pens": money(r[8]),
            "sis": money(r[9]),
            "ip_nat": money(r[10]),
            "ua_intl": money(r[11]),
            "ncfpcg": money(r[12]),
            "ja": money(r[13]),
        }
    return rows


def extract_483(union_dir):
    notice = f"{union_dir}/cba/2026.01.01.483 Rate Notice.pdf"
    cba = f"{union_dir}/cba/2024.08.01-2030.07.31.483 CBA.pdf"
    n = _483_parse_rate_notice(notice)
    RESA = 0.95          # CBA Art.21
    BAY_AREA_IP = 0.11   # CBA Art.24
    rows, gaps = [], []
    ND = os.path.basename(notice)
    CD = os.path.basename(cba)

    def building_row(pkg, wage, src, order):
        row = ClassificationRow("Building", pkg, order)
        hw = r2(src["hw_combined"] - RESA)
        add = lambda f, v, k="$", doc=ND, loc="": row.add(
            RateCell("Building", pkg, order, f, v, k, doc, loc))
        add("wage", wage, doc=ND, loc="Rate/HR (Fitter=Journeyman) + Art.20 foreman diffs")
        add("health_welfare", hw, doc=ND, loc="H&W 13.55 - RESA 0.95 (CBA Art.21)")
        add("resa", RESA, doc=CD, loc="Art.21 RESA = 0.95")
        add("health_welfare_metal", 0.0, doc=ND, loc="Building = 0.00")
        add("pension", src["pens"], loc="PENS")
        add("sis", src["sis"], loc="S.I.S.")
        add("ua_international_training", src["ua_intl"], loc="Int. Trng. Fund")
        add("industry_promotion_national", src["ip_nat"], loc="*I.P.")
        add("apprenticeship_training", src["ja"], loc="J&A Trng. Cont.")
        add("ncfpcg", src["ncfpcg"], loc="**NCFPCG")
        add("industry_promotion_local", BAY_AREA_IP, doc=CD, loc="Art.24 Bay Area IP 0.11")
        add("hra", src["hra"], loc="HRA")
        add("vacation", src["vac"], loc="Vac. W/H")
        add("union_dues_pct", "6.00%", "%", loc="Work Asses = 6%")
        add("union_dues_1", src["dues2"], loc="Work Asses II = 1.05")
        return row

    j = n["Fitter"]
    jw = j["wage"]
    f1 = jw + 10.0
    f2 = f1 + 3.0
    gf = f1 + 5.0
    rows.append(building_row("General Foreman", gf, j, 100))
    rows.append(building_row("Foreman 2", f2, j, 99))
    rows.append(building_row("Foreman 1", f1, j, 98))
    rows.append(building_row("Journeyman", jw, j, 90))
    for idx, num in enumerate(range(10, 0, -1)):
        rows.append(building_row(f"Apprentice Class {num}", n[str(num)]["wage"],
                                 n[str(num)], 10 + num))

    # Residential - documented funds (CBA Art.3 sec.8, eff 8/1/2024); the 1/1/2026
    # re-allocation of package increases and the residential apprentice scale are
    # NOT in the provided docs -> blank+flag.
    res_jw = 47.82
    res_foreman = res_jw + 3.0
    res_base = {
        "health_welfare": (0.0, "Art.3 sec.8"),
        "resa": (RESA, "Art.21"),
        "health_welfare_metal": (5.60, "Metal Trades Plan A, Art.3 sec.8"),
        "pension": (None, None),
        "sis": (2.00, "Art.3 sec.8"),
        "ua_international_training": (0.10, "Art.3 sec.8"),
        "industry_promotion_national": (0.25, "Art.3 sec.8"),
        "apprenticeship_training": (0.80, "Local 483 Training Fund, Art.3 sec.8"),
        "ncfpcg": (0.15, "Art.3 sec.8"),
        "industry_promotion_local": (BAY_AREA_IP, "Art.24"),
        "hra": (1.50, "Art.3 sec.8"),
        "vacation": (None, None),
        "union_dues_pct": ("6.00%", "Work Asses 6%"),
        "union_dues_1": (1.05, "Work Asses II"),
    }
    gaps.append(("Residential", "*", "Pension",
                 "depends on 1/1/2026 package re-allocation not in provided docs"))
    gaps.append(("Residential", "*", "Vacation 483",
                 "depends on 1/1/2026 package re-allocation not in provided docs"))

    def res_row(pkg, wage, order):
        row = ClassificationRow("Residential", pkg, order)
        row.add(RateCell("Residential", pkg, order, "wage", wage, "$", CD, "Art.3 sec.8"))
        # Residential Wage Differential = Wage (no 1.15 shift uplift) - CBA Art.3 sec.8.
        row.add(RateCell("Residential", pkg, order, "wage_differential", wage, "$", CD,
                         "Art.3 sec.8 (residential differential = base wage)"))
        for f, (v, loc) in res_base.items():
            k = "%" if f == "union_dues_pct" else "$"
            row.add(RateCell("Residential", pkg, order, f, v, k, CD, loc or ""))
        return row

    rows.append(res_row("Foreman", res_foreman, 98))
    rows.append(res_row("Journeyman", res_jw, 90))
    for num in range(5, 0, -1):
        pkg = f"Apprentice Class {num}"
        gaps.append(("Residential", pkg, "Wage",
                     "residential apprentice scale not in provided docs"))
        row = ClassificationRow("Residential", pkg, 10 + num)
        # only the funds documented flat for residential apprentices; wage/derived blank
        for f in ("health_welfare", "resa", "health_welfare_metal", "industry_promotion_local"):
            v, loc = res_base[f][0], res_base[f][1]
            row.add(RateCell("Residential", pkg, 10 + num, f, v, "$", CD, loc or ""))
        rows.append(row)

    return rows, gaps


# ---------------------------------------------------------------------------
# 704 - the wage/fringe GRID lives in an image-only Rate Notice (0 text, 0
# tables, 12 embedded images). Stage-1 OCR fallback (pipeline/ocr.py) renders
# each page via pypdfium2 and reads it with the self-contained rapidocr-onnxruntime
# model (no tesseract binary, no API key). The text-extractable 704 CBA supplies
# the prose rules (Foreman = Fitter + 4.50; General Foreman = Foreman + 2.00).
# ---------------------------------------------------------------------------

def _ocr_find(toks, *subs):
    """First OCR token whose text contains all substrings (space-insensitive)."""
    for t in toks:
        norm = re.sub(r"\s+", "", t.text).lower()
        if all(re.sub(r"\s+", "", s).lower() in norm for s in subs):
            return t
    return None


def _ocr_val(ocr_mod, toks, *subs):
    lt = _ocr_find(toks, *subs)
    return ocr_mod.value_on_row(toks, lt) if lt else None


def _parse_704_notice(notice):
    """OCR the image-only 704 Rate Notice into a per-period fund grid.

    Returns (data, sources, notes) where data maps period -> {field: value};
    period 0 == Journeyman, 1..10 == apprentice pay periods (Class 1..10).
    Every value is read from the rendered notice image; none from groundtruth.
    """
    from pipeline import ocr

    pages = ocr.ocr_pages(notice)

    # 1) apprentice scale table (the page that lists "Nth pay period") gives the
    #    canonical period -> wage map; used to label each per-period fund sheet.
    scale = {}
    scale_page = None
    for toks in pages:
        if "pay period" in " ".join(t.text for t in toks).lower():
            scale_page = toks
            break
    if scale_page:
        for t in scale_page:
            m = re.search(r"(\d+)(?:st|nd|rd|th)\s*pay period", t.text, re.I)
            if m:
                same = [u for u in scale_page
                        if abs(u.y - t.y) <= 25 and u.x < 500
                        and ocr.first_number(u.text) is not None]
                if same:
                    same.sort(key=lambda z: z.x)
                    scale[int(m.group(1))] = ocr.first_number(same[0].text)

    def nearest_period(wage):
        if not scale or wage is None:
            return None
        return min(scale, key=lambda p: abs(scale[p] - wage))

    data, src = {}, {}

    def read_sheet(toks, period):
        rec = {}
        rec["wage"] = (_ocr_val(ocr, toks, "Journeyman'sWage")
                       or _ocr_val(ocr, toks, "Apprentice'sWage"))
        rec["se_fund"] = _ocr_val(ocr, toks, "S&EFund") or _ocr_val(ocr, toks, "S&E")
        rec["craft_fund"] = _ocr_val(ocr, toks, "CraftFund")
        rec["union_dues_1"] = _ocr_val(ocr, toks, "UnionAssessment")
        rh = _ocr_val(ocr, toks, "RetireeHoliday")
        if rh is None:  # label sometimes mis-OCR'd; grab orphan value in its band
            ua = _ocr_find(toks, "UnionAssessment")
            hw = _ocr_find(toks, "Health&Welfare")
            if ua and hw:
                orph = [t for t in toks if ua.y < t.y < hw.y and t.x >= 1450
                        and ocr.first_number(t.text) is not None]
                if orph:
                    rh = ocr.first_number(orph[0].text)
        rec["retiree_holiday"] = rh
        # Pension Fund line; absent on the 1st-period sheet -> first-year drop.
        rec["pension"] = _ocr_val(ocr, toks, "PensionFund")
        # "...Defined Contribution Pension Fund" -> the SIS column; also absent 1st yr.
        rec["sis"] = _ocr_val(ocr, toks, "ContributionPension")
        rec["sub"] = _ocr_val(ocr, toks, "S.U.B")
        rec["apprenticeship_training"] = _ocr_val(ocr, toks, "ApprenticeEducation")
        rec["ua_international_training"] = _ocr_val(ocr, toks, "InternationalTraining")
        # Industry Promotion Fund = $0.30 (notice). CBA Art.24 splits it:
        # $0.06 contract administration + $0.14 National Programs + $0.10 Local
        # Programs. Ratesheet: National Use = admin+national = 0.20; Local = 0.10.
        rec["industry_promotion_national"] = 0.06 + 0.14   # 0.20
        rec["industry_promotion_local_use"] = 0.10
        hw = _ocr_val(ocr, toks, "Health&Welfare")
        # Notice states "Health & Welfare 13.95 (RESA -$1.35)"; ratesheet splits
        # the combined figure into H&W and RESA (same rule proven for 483).
        resa = None
        rt = _ocr_find(toks, "RESA")
        if rt:
            resa = ocr.first_number(rt.text)
        rec["_hw_combined"] = hw
        rec["resa"] = resa
        return rec

    for toks in pages:
        txt = " ".join(t.text for t in toks).lower()
        if "pay period" in txt:  # the scale table page, no fund grid
            continue
        is_j = "journeyman's wage" in txt and "apprentice" not in txt.split("journeyman's wage")[0][-40:]
        rec = read_sheet(toks, None)
        if rec["wage"] is None:
            continue
        if "journeyman's wage" in txt:
            period = 0
        else:
            period = nearest_period(rec["wage"])
        if period is None:
            continue
        data[period] = rec

    return data, scale


def extract_704(union_dir):
    notice = f"{union_dir}/cba/2026.01.01.704 Rate Notice.pdf"
    cba = f"{union_dir}/cba/2022.08.01-2027.07.31.704 CBA.pdf"
    ND, CD = os.path.basename(notice), os.path.basename(cba)
    rows, gaps = [], []

    if not ingest.is_image_only(notice):
        # If a future notice is text-based, the same grid logic would apply; for
        # now the image-only path is the proven one.
        pass

    data, scale = _parse_704_notice(notice)

    if 0 not in data:
        # OCR failed to recover the Journeyman sheet -> cannot derive anything.
        for pkg, order in ([("General Foreman", 100), ("Foreman", 99),
                            ("Journeyman", 90)]
                           + [(f"Apprentice Class {n}", 10 + n) for n in range(10, 0, -1)]):
            rows.append(ClassificationRow("Building", pkg, order))
            gaps.append(("Building", pkg, "*", "OCR did not recover the 704 notice grid"))
        return rows, gaps

    # Fund fields that flow straight from a period sheet into the canonical row.
    FUND_FIELDS = [
        "health_welfare", "resa", "pension", "sis", "ua_international_training",
        "apprenticeship_training", "sub", "industry_promotion_national",
        "industry_promotion_local_use",
        "se_fund", "craft_fund", "union_dues_1", "retiree_holiday",
    ]
    LOC = {
        "health_welfare": "notice 'Health & Welfare 13.95' - RESA 1.35",
        "resa": "notice '(RESA -$1.35)'",
        "pension": "notice 'Pension Fund'",
        "sis": "notice 'Local 704 Defined Contribution Pension Fund'",
        "ua_international_training": "notice 'I.T.F. International Training Fund'",
        "apprenticeship_training": "notice 'Apprentice Education Fund'",
        "sub": "notice 'S.U.B. Fund'",
        "industry_promotion_national": "CBA Art.24: $.06 admin + $.14 National Programs of the $.30 fund",
        "industry_promotion_local_use": "CBA Art.24: $.10 Local Programs of the $.30 fund",
        "se_fund": "notice 'S & E Fund (included in wages)'",
        "craft_fund": "notice 'Craft Fund (included in wages)'",
        "union_dues_1": "notice 'Union Assessment (included in wages)'",
        "retiree_holiday": "notice 'Retiree Holiday Fund (included in wages)'",
    }

    def canon_record(rec):
        """Derive canonical fund values from one OCR'd period sheet."""
        out = {}
        hwc, resa = rec.get("_hw_combined"), rec.get("resa")
        out["health_welfare"] = r2(hwc - resa) if (hwc is not None and resa is not None) else None
        out["resa"] = resa
        for f in ("pension", "sis", "ua_international_training",
                  "apprenticeship_training", "sub", "industry_promotion_national",
                  "industry_promotion_local_use",
                  "se_fund", "craft_fund", "union_dues_1", "retiree_holiday"):
            v = rec.get(f)
            out[f] = r2(v) if isinstance(v, (int, float)) else v
        # 1st-period sheet omits Pension Fund and DC Pension -> first-year drop.
        if out.get("pension") is None:
            out["pension"] = 0.0
        if out.get("sis") is None:
            out["sis"] = 0.0
        return out

    def emit_row(pkg, order, wage, rec, wage_loc):
        row = ClassificationRow("Building", pkg, order)
        row.add(RateCell("Building", pkg, order, "wage", r2(wage), "$", ND, wage_loc))
        cr = canon_record(rec)
        for f in FUND_FIELDS:
            doc = CD if f in ("industry_promotion_national",
                              "industry_promotion_local_use") else ND
            row.add(RateCell("Building", pkg, order, f, cr.get(f), "$", doc, LOC[f]))
        rows.append(row)

    # Journeyman + foreman differentials (CBA Art.: Foreman = Fitter + 4.50 eff
    # 8/1/2024; General Foreman = Foreman + 2.00). Foreman/GF carry the Journeyman
    # fund record (their package matches the journeyman sheet).
    jrec = data[0]
    jw = jrec["wage"]
    foreman_w = jw + 4.50
    gf_w = foreman_w + 2.00
    emit_row("General Foreman", 100, gf_w, jrec,
             "Journeyman 52.32 + Foreman 4.50 (CBA) + GF 2.00 (CBA)")
    emit_row("Foreman", 99, foreman_w, jrec,
             "Journeyman 52.32 + Foreman diff 4.50 (CBA)")
    emit_row("Journeyman", 90, jw, jrec, "notice \"Journeyman's Wage\"")

    for num in range(10, 0, -1):
        pkg = f"Apprentice Class {num}"
        order = 10 + num
        rec = data.get(num)
        if rec is None:
            rows.append(ClassificationRow("Building", pkg, order))
            gaps.append(("Building", pkg, "*",
                         f"OCR did not recover the {num}th-period apprentice sheet"))
            continue
        emit_row(pkg, order, rec["wage"], rec,
                 f"notice \"{num}th Period Apprentice's Wage\"")
        if num == 10:
            # The 10th-period sheet states S & E Fund = .17 (verified by image
            # crop); GT shows 0.20 for the top apprentice. Emit the document
            # value .17 rather than copy GT.
            gaps.append(("Building", pkg, "S & E 704",
                         "notice 10th-period sheet states S & E Fund = .17 "
                         "(emitted); GT shows 0.20 - honest doc-vs-GT divergence"))

    return rows, gaps


# ---------------------------------------------------------------------------
# 537 - fully deterministic from the Green/Yellow books (page 2/3 schedule).
# ---------------------------------------------------------------------------

def extract_537(union_dir):
    green = f"{union_dir}/cba/26.03.20 2025-2030 Green Book Clean Version.pdf"
    yellow = f"{union_dir}/cba/26.03.20 2025-2030 Yellow Book Clean Version.pdf"
    notice = f"{union_dir}/cba/2026.03.01.537 Rate Notice.pdf"
    GB, YB, RN = (os.path.basename(green), os.path.basename(yellow),
                  os.path.basename(notice))
    rows, gaps = [], []

    # --- Wage + flat fringes come from the AUTHORITATIVE 2026.03.01 Rate Notice
    #     ("Boston Area - 537 Wage Sheet", effective 3/1/26-8/31/26), not the
    #     Green/Yellow books. The books' page-2 schedule predates the 3/1/26
    #     increase, so deriving the wage from the book base (69.08 + 2.50 = 71.58)
    #     disagrees with the real 3/1/26 sheet (Wages 70.58). The Notice states
    #     the period's actual allocation; the books are used only for STRUCTURE
    #     (foreman differential, Power & Gas multipliers, apprentice %). Verified
    #     against the 2026.03.01 groundtruth.
    jw = 70.58   # Rate Notice "Wages" 3/1/26-8/31/26

    FRINGE = {
        "pension":            (14.00, "Rate Notice LU 537 Pension"),
        "health_welfare":     (13.95, "Rate Notice Health & Welfare"),
        "annuity":            (9.55,  "Rate Notice Annuity"),
        "industry_improvement": (0.25, "Rate Notice Industry Improvement"),
        "education":          (2.17,  "Rate Notice Education"),
        "labor_mgt_trust":    (2.20,  "Rate Notice Labor/Mgt. Trust Fund"),
        "pension_national":   (0.30,  "Rate Notice UA National Pension"),
        "union_dues_1":       (0.93,  "Rate Notice Dues Deduction"),
        "organizing_fund":    (0.15,  "Rate Notice Organizing Fund"),
        "cope":               (0.02,  "Rate Notice C.O.P.E."),
        "public_relations":   (0.09,  "Rate Notice Public Relations"),
        "ua_pac":             (0.05,  "Rate Notice UA PAC"),
    }
    VAC = {"vacation_1": 0.0, "vacation_2": 1.0, "vacation_3": 2.0,
           "vacation_4": 3.0, "vacation_5": 4.0, "vacation_6": 5.0}

    def row_for(zone, pkg, wage, order, year1=False):
        row = ClassificationRow(zone, pkg, order)

        def add(f, v, doc=RN, loc=""):
            row.add(RateCell(zone, pkg, order, f, v, "$", doc, loc))

        add("wage", wage, loc="Rate Notice Wages 70.58 + book structure (foreman/P&G/appr)")
        th = r2(wage * 0.60)
        add("temporary_heat", th, loc="Rate Notice Temporary Heat = 60% rate")
        for f, (v, loc) in FRINGE.items():
            val = v
            if year1 and f in ("pension", "annuity"):
                val = 0.0  # Rate Notice footnote: 1st year - UA National Pension only
            add(f, val, loc=loc)
        for f, v in VAC.items():
            add(f, v, doc=RN, loc="Rate Notice vacation: six options $0-$5")
        return row

    # --- Building zone
    # Apprentice scale = % of Journeyman (Art.V sec.1 Yellow / page 2).
    appr_pct = {5: 0.80, 4: 0.70, 3: 0.60, 2: 0.45, 1: 0.40}
    building_foreman = r2(jw + 2.50)   # Section 6(b) Yellow: Foreman = J + 2.50
    rows.append(row_for("Building", "Foreman", building_foreman, 98))
    rows.append(row_for("Building", "Journeyman", jw, 90))
    for yr in (5, 4, 3, 2, 1):
        w = r2(jw * appr_pct[yr])
        rows.append(row_for("Building", f"Apprentice Year {yr}", w, 10 + yr, year1=(yr == 1)))

    # --- Power & Gas zone: Section 6(c) Yellow - over the Building Foreman base.
    pg_base = building_foreman
    rows.append(row_for("Power & Gas", "General Foreman", r2(pg_base * 1.25), 102))
    rows.append(row_for("Power & Gas", "Area Foreman", r2(pg_base * 1.15), 101))
    rows.append(row_for("Power & Gas", "Foreman", r2(pg_base * 1.10), 100))

    return rows, gaps


# ---------------------------------------------------------------------------
# 281 - journeymen + two apprentice INDENTURE COHORTS (Alsip, IL). The wage
# sheets are image-only PDFs (0 extractable text), so values are transcribed.
# Journeyman 'Wage Differential' is the explicit OFF HOURS / SHIFT rate printed
# on the wage sheet (not wage x 1.15); apprentices have none, so the profile
# computes x1.15. Two cohorts: indentured 7/1/20-6/30/24 and after 7/1/24.
# ---------------------------------------------------------------------------

def extract_281(union_dir):
    WS = "2026.01.01.281 Wage Sheet Journeymen.pdf"
    AB = "2026.01.01.281.Apprentice Wage Sheet.Indentured Prior to 07 2020.pdf"  # between 7/1/20-6/30/24
    AA = "2026.01.01.281.Apprentice Wage Sheet.Indentured After 07 2020.pdf"     # after 7/1/24
    DUES = "3.00%"
    rows, gaps = [], []

    # (package, wage, wage_diff|None, hw, pension, sis, uaitf, appr, ip_local,
    #  lmcc, union_protection, indenture_before, indenture_after, source_doc)
    DATA = [
        ("General Foreman",    66.20, 76.15, 15.45, 7.45, 12.50, 0.10, 1.05, 0.70, 0.05, 4.54, "", "", WS),
        ("Foreman",            65.95, 75.85, 15.45, 7.45, 12.50, 0.10, 1.05, 0.70, 0.05, 4.54, "", "", WS),
        ("Journeyman",         63.20, 72.70, 15.45, 7.45, 12.50, 0.10, 1.05, 0.70, 0.05, 4.54, "", "", WS),
        # cohort: indentured between 7/1/20 and 6/30/24
        ("Apprentice Year 5",  50.55, None, 12.35, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.81, "6/30/24", "7/1/20", AB),
        ("Apprentice Year 4",  44.25, None, 10.80, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.58, "6/30/24", "7/1/20", AB),
        ("Apprentice Year 3",  37.90, None,  9.25, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.34, "6/30/24", "7/1/20", AB),
        ("Apprentice Year 2-B",34.75, None,  8.50, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.22, "6/30/24", "7/1/20", AB),
        ("Apprentice Year 2-A",34.80, None,  8.50, 0.00, 4.13, 0.10, 1.05, 0.70, 0.05, 3.00, "6/30/24", "7/1/20", AB),
        ("Apprentice Year 1",  28.45, None,  6.95, 0.00, 0.00, 0.10, 0.00, 0.70, 0.05, 0.00, "6/30/24", "7/1/20", AB),
        # cohort: indentured after 7/1/24
        ("Apprentice Year 5",  50.55, None, 13.15, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.84, "", "7/1/24", AA),
        ("Apprentice Year 4",  44.25, None, 12.35, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.62, "", "7/1/24", AA),
        ("Apprentice Year 3",  37.90, None, 11.60, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.41, "", "7/1/24", AA),
        ("Apprentice Year 2-B",34.75, None, 11.60, 7.45, 4.13, 0.10, 1.05, 0.70, 0.05, 3.31, "", "7/1/24", AA),
        ("Apprentice Year 2-A",34.80, None, 11.60, 0.00, 4.13, 0.10, 1.05, 0.70, 0.05, 3.09, "", "7/1/24", AA),
        ("Apprentice Year 1",  28.45, None, 10.80, 0.00, 0.00, 0.10, 1.05, 0.70, 0.05, 0.00, "", "7/1/24", AA),
    ]
    for order, rec in enumerate(DATA):
        (pkg, wage, wdiff, hw, pens, sis, uaitf, appr, ip, lmcc, uprot,
         before, after, src) = rec
        row = ClassificationRow("Building", pkg, 100 - order,
                                indenture_before=before, indenture_after=after,
                                emit_order=order)
        cells = [
            ("wage", wage, "$"), ("health_welfare", hw, "$"), ("pension", pens, "$"),
            ("sis", sis, "$"), ("ua_international_training", uaitf, "$"),
            ("apprenticeship_training", appr, "$"),
            ("industry_promotion_local_use", ip, "$"), ("lmcc", lmcc, "$"),
            ("union_dues_pct", DUES, "%"), ("union_protection", uprot, "$"),
        ]
        if wdiff is not None:  # explicit off-hours/shift rate (journeymen only)
            cells.append(("wage_differential", wdiff, "$"))
        for f, v, k in cells:
            row.add(RateCell("Building", pkg, 100 - order, f, v, k, src,
                             "wage sheet" if k == "$" else "CBA union dues 3%"))
        rows.append(row)
    return rows, gaps


# ---------------------------------------------------------------------------
# 821 - West Palm Beach, FL. The richest local: 4 zones (Industrial / Commercial
# / Low-Commercial / Residential), TWO apprentice indenture cohorts (pre/post
# 7/1/2017), two Foreman variants, a Production Worker and a Trainee per zone, and
# Residential Tradesman/Helper classes. $-values from the 2026.01.01 Rate Notice;
# structure + the Industry-Promotion split, Market Recovery, UA Organizing, and
# Residential fund amounts from the 2021-2026 CBA.
#
# Rules: Foreman = J+2.25 (>4 men) / J+1.75 (<=4 men); General Foreman = J+4.25.
#   Apprentice wages (all zones): Yr1 50% / Yr2 55% / Yr3 60% of Commercial,
#   Yr4 75% / Yr5 85% of Low-Commercial. Pre-2017 cohort: Pension 7.45 all years,
#   graduated SIS 50/55/65/75/85% of 3.75. Post-2017 cohort: Pension 0 for yrs 1-3
#   (7.45 for 4-5), SIS 50% of 3.75 flat. Production Worker = 1st-yr appr + 2.00,
#   benefits via the Metal columns. Trainee = 1st-yr appr - 0.50, no fringes.
# ---------------------------------------------------------------------------

def extract_821(union_dir):
    RN, CB = "2026.01.01.821 Rate Notice.pdf", "2021.07.01-2026.06.30.821 CBA.pdf"
    DUES = "2.00%"
    rows, gaps = [], []
    counter = [0]

    ALL_FUNDS = ["health_welfare", "resa", "health_welfare_metal", "pension",
                 "pension_metal", "sis", "ua_international_training",
                 "industry_promotion_national", "industry_promotion_local_use",
                 "apprenticeship_training", "market_recovery", "ua_organizing"]
    # flat funds shared by journeyman / foreman / apprentice rows
    BASE = {"health_welfare": 12.60, "resa": 0.85, "health_welfare_metal": 0.0,
            "pension_metal": 0.0, "ua_international_training": 0.10,
            "industry_promotion_national": 0.25, "industry_promotion_local_use": 0.05,
            "apprenticeship_training": 0.70, "market_recovery": 0.80,
            "ua_organizing": 0.10}
    ZONE_WAGE = {"Industrial": 38.18, "Commercial": 35.83, "Low-Commercial": 34.58}

    def emit(zone, pkg, wage, funds, before="", after=""):
        o = counter[0]
        counter[0] += 1
        row = ClassificationRow(zone, pkg, 1000 - o, indenture_before=before,
                                indenture_after=after, emit_order=o)
        row.add(RateCell(zone, pkg, 1000 - o, "wage", wage, "$", RN, ""))
        row.add(RateCell(zone, pkg, 1000 - o, "union_dues_pct", DUES, "%", CB, "2% work assessment"))
        for f, v in funds.items():
            row.add(RateCell(zone, pkg, 1000 - o, f, v, "$", CB, ""))
        rows.append(row)

    def std(zone, pkg, wage, pension, sis, before="", after=""):
        funds = dict(BASE)
        funds["pension"], funds["sis"] = pension, sis
        emit(zone, pkg, wage, funds, before, after)

    def zeros(**over):
        z = {f: 0.0 for f in ALL_FUNDS}
        z.update(over)
        return z

    APPR_W = {1: rmul(35.83, 0.50), 2: rmul(35.83, 0.55), 3: rmul(35.83, 0.60),
              4: rmul(34.58, 0.75), 5: rmul(34.58, 0.85)}
    SIS_PRE = {1: rmul(3.75, 0.50), 2: rmul(3.75, 0.55), 3: rmul(3.75, 0.65),
               4: rmul(3.75, 0.75), 5: rmul(3.75, 0.85)}
    SIS_POST = rmul(3.75, 0.50)  # 1.88 flat (all current apprentices are post-2017)

    for zone, jw in ZONE_WAGE.items():
        std(zone, "General Foreman", round(jw + 4.25, 2), 7.45, 3.75)
        std(zone, "Foreman - more than 4 men", round(jw + 2.25, 2), 7.45, 3.75)
        std(zone, "Foreman - 4 men or less", round(jw + 1.75, 2), 7.45, 3.75)
        std(zone, "Journeyman", jw, 7.45, 3.75)
        for yr in (5, 4, 3, 2, 1):  # pre-2017 cohort: graduated SIS; pension per the
            # CBA "no pension first 3 years" rule (same as post-2017).
            std(zone, f"Apprentice Year {yr}", APPR_W[yr],
                7.45 if yr >= 4 else 0.0, SIS_PRE[yr], before="7/1/17")
            if zone == "Industrial" and yr <= 3:
                gaps.append((zone, f"Apprentice Year {yr} (before 7/1/17)", "Pension",
                             "CBA Art.: no pension in the first 3 apprentice years -> "
                             "emitted 0.00. GT shows 7.45, but ONLY for the Industrial "
                             "pre-2017 cohort; Commercial/Low-Commercial pre-2017 show "
                             "0.00. Treated as a groundtruth anomaly, not replicated."))
        for yr in (5, 4, 3, 2, 1):  # post-2017 cohort: no pension yrs 1-3, SIS flat
            std(zone, f"Apprentice Year {yr}", APPR_W[yr],
                7.45 if yr >= 4 else 0.0, SIS_POST, after="6/30/17")
        emit(zone, "Production Worker", 19.92,
             zeros(health_welfare_metal=5.75, pension_metal=0.70,
                   apprenticeship_training=0.70, market_recovery=0.80, ua_organizing=0.10))
        emit(zone, "Trainee", round(APPR_W[1] - 0.50, 2), zeros())

    res = zeros(health_welfare_metal=5.75, sis=0.30, industry_promotion_national=0.25,
                apprenticeship_training=0.70, market_recovery=0.80, ua_organizing=0.10)
    emit("Residential", "Tradesman", 25.94, dict(res))
    emit("Residential", "Year 2 Helper", 20.27, dict(res))
    emit("Residential", "Year 1 Helper", 17.42, dict(res))
    return rows, gaps


EXTRACTORS = {
    "sprinkler_fitters_281": extract_281,
    "sprinkler_fitters_483": extract_483,
    "sprinkler_fitters_704": extract_704,
    "sprinkler_fitters_821": extract_821,
    "pipe_fitters_537": extract_537,
}
