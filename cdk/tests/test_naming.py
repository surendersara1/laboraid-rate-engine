"""Tests for the naming helper (Spec/09 §1)."""

from __future__ import annotations

import pytest

from laboraid_cdk.util.naming import name


def test_name_happy_path() -> None:
    assert name("prod", "l3", "bucket", "inputs") == "laboraid-prod-l3-bucket-inputs"
    assert name("dev", "l5", "agent", "extractor") == "laboraid-dev-l5-agent-extractor"


def test_name_allows_kebab_purpose() -> None:
    assert name("dev", "l6", "sns", "review-needed") == "laboraid-dev-l6-sns-review-needed"


@pytest.mark.parametrize(
    ("env", "layer", "type_", "purpose"),
    [
        ("staging", "l3", "bucket", "inputs"),  # bad env
        ("prod", "l9", "bucket", "inputs"),  # bad layer
        ("prod", "l3", "Bucket", "inputs"),  # uppercase type
        ("prod", "l3", "bucket", "Inputs"),  # uppercase purpose
        ("prod", "l3", "bucket", "in puts"),  # space
    ],
)
def test_name_rejects_invalid(env: str, layer: str, type_: str, purpose: str) -> None:
    with pytest.raises(ValueError):
        name(env, layer, type_, purpose)
