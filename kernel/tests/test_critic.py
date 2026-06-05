"""Unit tests for the completeness critic's pure core (no PDFs needed)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL = os.path.dirname(HERE)
sys.path.insert(0, KERNEL)

from pipeline.critic import find_gaps  # noqa: E402


def test_flags_cba_terms_missing_from_output():
    # mirrors the real 821 failure: the CBA names a Trainee, a Residential zone and
    # a Market Recovery fund that a shallow (notice-only) extraction would omit.
    cba = (
        "General Foreman, Foreman and Journeyman rates apply. The Apprentice scale "
        "follows. A Trainee shall be paid fifty cents less than a first-year "
        "Apprentice. Residential work is covered separately. The Market Recovery "
        "Fund and the UA Organizing Fund contributions are due. Industrial and "
        "Commercial zones apply."
    )
    packages = ["General Foreman", "Foreman", "Journeyman", "Apprentice Year 1"]
    zones = ["Industrial", "Commercial"]
    columns = ["Wage", "Health & Welfare", "Pension", "SIS"]

    gaps = find_gaps(cba, packages, zones, columns)
    flagged = {(g["category"], g["term"]) for g in gaps}

    assert ("classification", "Trainee") in flagged
    assert ("zone", "Residential") in flagged
    assert ("fund", "Market Recovery") in flagged
    assert ("fund", "Organizing") in flagged
    # things that ARE present must NOT be flagged
    assert ("classification", "Journeyman") not in flagged
    assert ("classification", "General Foreman") not in flagged
    assert ("zone", "Industrial") not in flagged


def test_no_gaps_when_everything_present():
    cba = "Journeyman and Apprentice rates in the Building zone, with Pension."
    gaps = find_gaps(cba, ["Journeyman", "Apprentice Year 1"], ["Building"],
                     ["Wage", "Pension"])
    flagged = {(g["category"], g["term"]) for g in gaps}
    assert ("classification", "Journeyman") not in flagged
    assert ("classification", "Apprentice") not in flagged
    assert ("zone", "Building") not in flagged
    assert ("fund", "Pension") not in flagged


def test_hit_count_reported_and_sorted():
    cba = "Trainee Trainee Trainee. Residential."
    gaps = find_gaps(cba, [], [], [])
    trainee = next(g for g in gaps if g["term"] == "Trainee")
    assert trainee["hits"] == 3
    # sorted by descending hits -> Trainee (3) before Residential (1)
    assert gaps[0]["term"] == "Trainee"
