"""Synthesis assertion tests for Group B stacks (Spec/09 §3)."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from laboraid_cdk.config import get_config
from laboraid_cdk.stacks.ai_stack import AiStack
from laboraid_cdk.stacks.processing_stack import ProcessingStack
from laboraid_cdk.stacks.security_stack import SecurityStack
from laboraid_cdk.stacks.storage_stack import StorageStack


def _synth() -> tuple[Template, Template]:
    config = get_config("dev")
    app = cdk.App()
    security = SecurityStack(app, "Sec", config=config)
    storage = StorageStack(app, "Stg", config=config, master_key=security.master_key)
    return Template.from_stack(security), Template.from_stack(storage)


def _synth_processing() -> tuple[Template, Template]:
    config = get_config("dev")
    app = cdk.App()
    security = SecurityStack(app, "Sec", config=config)
    storage = StorageStack(app, "Stg", config=config, master_key=security.master_key)
    ai = AiStack(app, "Ai", config=config, master_key=security.master_key)
    processing = ProcessingStack(
        app,
        "Proc",
        config=config,
        master_key=security.master_key,
        inputs_bucket=storage.inputs_bucket,
        outputs_bucket=storage.outputs_bucket,
        files_table=storage.files_table,
        guardrail_id=ai.guardrail_id,
    )
    return Template.from_stack(ai), Template.from_stack(processing)


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


def test_ai_stack_guardrail() -> None:
    ai, _ = _synth_processing()
    ai.resource_count_is("AWS::Bedrock::Guardrail", 1)


def test_processing_stack_resources() -> None:
    _, proc = _synth_processing()
    proc.resource_count_is("AWS::ECR::Repository", 1)
    proc.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)
    # Classifier Lambda is ARM64 (Graviton) per the project defaults.
    proc.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like({"Architectures": ["arm64"], "Runtime": "python3.12"}),
    )


def _synth_validation() -> Template:
    config = get_config("dev")
    app = cdk.App()
    security = SecurityStack(app, "Sec", config=config)
    storage = StorageStack(app, "Stg", config=config, master_key=security.master_key)
    from laboraid_cdk.stacks.validation_stack import ValidationStack

    validation = ValidationStack(
        app,
        "Val",
        config=config,
        master_key=security.master_key,
        outputs_bucket=storage.outputs_bucket,
        review_table=storage.review_table,
    )
    return Template.from_stack(validation)


def test_validation_stack_topics_and_bus() -> None:
    val = _synth_validation()
    val.resource_count_is("AWS::SNS::Topic", 3)
    val.resource_count_is("AWS::Events::EventBus", 1)
    # 4 validators + 3 renderers + slack-notifier = 8 Lambdas.
    val.resource_count_is("AWS::Lambda::Function", 8)
