"""Tests for commit_helper.py (F.2) — helper logic only.

We don't invoke real git/gh commands here. The unit tests cover the pure
helpers (URL extraction, message building, local-number parsing). The
integration test running `gh pr create` happens during the real overnight
drafter run, not in unit tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import commit_helper  # noqa: E402


def test_local_extracted_from_union_key() -> None:
    assert commit_helper._local("sprinkler_fitters_120") == "120"
    assert commit_helper._local("pipe_fitters_537") == "537"
    assert commit_helper._local("solo") == "solo"


def test_commit_message_contains_drafted_marker() -> None:
    msg = commit_helper._commit_message(
        "sprinkler_fitters_120",
        {"accuracy_pct": 82.5, "schema_pass": True, "codegen_pass": True},
    )
    assert "[DRAFTED-by-ProfileDrafterAgent]" in msg
    assert "82.5%" in msg
    assert "kernel/profiles/sprinkler_fitters_120.yaml" in msg
    assert "kernel/pipeline/extract_120.py" in msg


def test_pr_body_has_reviewer_checklist() -> None:
    body = commit_helper._pr_body(
        "sprinkler_fitters_120",
        {
            "accuracy_pct": 82.5,
            "mismatch_count": 17,
            "schema_pass": True,
            "codegen_pass": True,
            "syntax_pass": True,
        },
    )
    assert "Reviewer checklist" in body
    assert "EXTRACTORS" in body
    assert "extract_120" in body
    assert "82.5%" in body
    assert "17" in body


def test_extract_pr_url_pulls_github_link() -> None:
    out = "Some preamble\n" "https://github.com/Acme/laboraid-rate-engine/pull/42\n" "trailing\n"
    assert (
        commit_helper._extract_pr_url(out) == "https://github.com/Acme/laboraid-rate-engine/pull/42"
    )


def test_extract_pr_url_falls_back_to_raw_when_no_match() -> None:
    out = "no link here\n"
    assert commit_helper._extract_pr_url(out) == "no link here"


def test_find_repo_root_locates_git_dir() -> None:
    root = commit_helper._find_repo_root()
    assert root is not None
    assert (root / ".git").exists()
