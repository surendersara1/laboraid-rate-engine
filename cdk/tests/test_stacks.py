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


def _synth_api() -> Template:
    config = get_config("dev")
    app = cdk.App()
    security = SecurityStack(app, "Sec", config=config)
    storage = StorageStack(app, "Stg", config=config, master_key=security.master_key)
    from laboraid_cdk.stacks.api_stack import ApiStack

    assert storage.aurora.secret is not None
    api = ApiStack(
        app,
        "Api",
        config=config,
        user_pool=security.user_pool,
        user_pool_client=security.user_pool_client,
        inputs_bucket=storage.inputs_bucket,
        jobs_table=storage.jobs_table,
        agent_config_table=storage.agent_config_table,
        overrides_table=storage.overrides_table,
        aurora=storage.aurora,
        aurora_secret=storage.aurora.secret,
    )
    return Template.from_stack(api)


def test_api_stack() -> None:
    api = _synth_api()
    api.resource_count_is("AWS::Lambda::Function", 19)  # 19 API Lambdas
    api.resource_count_is("AWS::ApiGatewayV2::Api", 1)
    api.resource_count_is("AWS::ApiGatewayV2::Authorizer", 1)
    api.resource_count_is("AWS::WAFv2::WebACL", 1)
    api.resource_count_is("AWS::ApiGatewayV2::Route", 20)  # 20 routes (profile-list x2)


def test_ui_stack() -> None:
    config = get_config("dev")
    app = cdk.App()
    from laboraid_cdk.stacks.ui_stack import UiStack

    ui = UiStack(app, "Ui", config=config)
    template = Template.from_stack(ui)
    template.resource_count_is("AWS::S3::Bucket", 1)
    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    # SPA fallback: 403/404 -> index.html.
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        Match.object_like(
            {
                "DistributionConfig": Match.object_like(
                    {
                        "CustomErrorResponses": Match.array_with(
                            [
                                Match.object_like(
                                    {"ResponseCode": 200, "ResponsePagePath": "/index.html"}
                                )
                            ]
                        )
                    }
                )
            }
        ),
    )


def test_orchestration_stack() -> None:
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
    from laboraid_cdk.stacks.orchestration_stack import OrchestrationStack
    from laboraid_cdk.stacks.validation_stack import ValidationStack

    validation = ValidationStack(
        app,
        "Val",
        config=config,
        master_key=security.master_key,
        outputs_bucket=storage.outputs_bucket,
        review_table=storage.review_table,
    )
    orch = OrchestrationStack(
        app,
        "Orch",
        config=config,
        inputs_bucket=storage.inputs_bucket,
        classifier=processing.classifier,
        checksum=validation.checksum,
        range_fn=validation.range_fn,
        confidence=validation.confidence,
        review_router=validation.review_router,
        xlsx=validation.xlsx,
        csv=validation.csv,
        articles=validation.articles,
    )
    template = Template.from_stack(orch)
    template.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    # EventBridge rule starts the pipeline on S3 upload.
    template.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like({"EventPattern": Match.object_like({"detail-type": ["Object Created"]})}),
    )
