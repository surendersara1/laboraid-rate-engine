"""Synthesis assertion tests for Group B stacks (Spec/09 §3)."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from laboraid_cdk.config import get_config
from laboraid_cdk.stacks.security_stack import SecurityStack
from laboraid_cdk.stacks.storage_stack import StorageStack


def _synth() -> tuple[Template, Template]:
    config = get_config("dev")
    app = cdk.App()
    security = SecurityStack(app, "Sec", config=config)
    storage = StorageStack(app, "Stg", config=config, master_key=security.master_key)
    return Template.from_stack(security), Template.from_stack(storage)


def test_security_stack_resources() -> None:
    sec, _ = _synth()
    sec.resource_count_is("AWS::KMS::Key", 1)
    sec.resource_count_is("AWS::Cognito::UserPool", 1)
    sec.resource_count_is("AWS::Cognito::UserPoolGroup", 4)
    # MFA required on the pool.
    sec.has_resource_properties("AWS::Cognito::UserPool", {"MfaConfiguration": "ON"})


def test_storage_stack_resources() -> None:
    _, stg = _synth()
    stg.resource_count_is("AWS::S3::Bucket", 6)
    stg.resource_count_is("AWS::DynamoDB::Table", 7)
    stg.resource_count_is("AWS::RDS::DBCluster", 1)
    # Aurora Data API enabled (schema-init custom resource depends on it).
    stg.has_resource_properties("AWS::RDS::DBCluster", {"EnableHttpEndpoint": True})
    # TLS-only deny statement present on a bucket policy.
    stg.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Effect": "Deny",
                                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                                }
                            )
                        ]
                    )
                }
            )
        },
    )
