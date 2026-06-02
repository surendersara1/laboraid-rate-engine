"""Tests for config + mandatory-tags Aspect (Spec/09 §2)."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_s3 as s3
from aws_cdk.assertions import Template

from laboraid_cdk.aspects.mandatory_tags import MandatoryTagsAspect
from laboraid_cdk.config import get_config


def test_config_has_13_mandatory_tags() -> None:
    config = get_config("dev")
    assert len(config.mandatory_tags) == 13
    assert config.mandatory_tags["Environment"] == "dev"
    assert config.mandatory_tags["Project"] == "LaborAid-POC"


def test_aspect_applies_tags_to_resources() -> None:
    config = get_config("prod")
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    s3.Bucket(stack, "B")
    cdk.Aspects.of(app).add(MandatoryTagsAspect(config.mandatory_tags))

    template = Template.from_stack(stack)
    buckets = template.find_resources("AWS::S3::Bucket")
    (bucket,) = buckets.values()
    tags = {t["Key"]: t["Value"] for t in bucket["Properties"]["Tags"]}
    assert tags["Project"] == "LaborAid-POC"
    assert tags["Environment"] == "prod"
    assert tags["PublicUseCase"] == "true"
