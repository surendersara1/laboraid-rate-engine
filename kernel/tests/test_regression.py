"""Per-union regression gate: run the full pipeline for each wired union and assert
the output reproduces the groundtruth (header exact + sourced-cell accuracy).

"Sourced accuracy" excludes intentional blanks (flagged gaps where a value is
absent from the CBA docs); it is correct / (correct + wrong). A regression that
introduces a wrong value drops this below the threshold and fails CI.

These are integration tests: they read data/<union>/cba/ PDFs (pdfplumber, and
rapidocr for 704) and write data/<union>/ai_output/. They need the project deps.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL = os.path.dirname(HERE)
sys.path.insert(0, KERNEL)

from pipeline import run as krun  # noqa: E402

MIN_SOURCED_ACCURACY = 99.0


@pytest.mark.parametrize("union", list(krun.TARGETS))
def test_union_reproduces_groundtruth(union):
    result = krun.run_union(union, do_eval=True, min_accuracy=MIN_SOURCED_ACCURACY)
    assert result is not None, f"{union}: no evaluation produced"
    assert result["header_ok"], (
        f"{union}: header differs from groundtruth — "
        f"missing {result['missing_cols']} extra {result['extra_cols']}"
    )
    assert result["gate_ok"], (
        f"{union}: sourced accuracy {result['sourced_accuracy']:.1f}% "
        f"< {MIN_SOURCED_ACCURACY}% (wrong={result['wrong']}, "
        f"blank/gaps={result['blank']}) — see mismatch list in stdout"
    )
