"""L3 Orchestration stack — Step Functions main pipeline (Spec/09 §4 L3 §3.4).

Creates the Standard-workflow state machine (definition in ``sfn/main_pipeline``)
wiring the classifier + validators + renderers, and an EventBridge rule that
starts an execution on every S3 ``Object Created`` in the inputs bucket (the
bucket emits to EventBridge — see storage stack).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.sfn.main_pipeline import build_definition
from laboraid_cdk.util.naming import name


class OrchestrationStack(Stack):
    """Step Functions main pipeline + S3-upload trigger."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        inputs_bucket: s3.IBucket,
        classifier: lambda_.IFunction,
        checksum: lambda_.IFunction,
        range_fn: lambda_.IFunction,
        confidence: lambda_.IFunction,
        review_router: lambda_.IFunction,
        xlsx: lambda_.IFunction,
        csv: lambda_.IFunction,
        articles: lambda_.IFunction,
        agent_config_table: ddb.ITable,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env

        definition = build_definition(
            self,
            classifier=classifier,
            checksum=checksum,
            range_fn=range_fn,
            confidence=confidence,
            review_router=review_router,
            xlsx=xlsx,
            csv=csv,
            articles=articles,
            agent_config_table=agent_config_table,
        )

        log_group = logs.LogGroup(
            self,
            "PipelineLogs",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        self.state_machine = sfn.StateMachine(
            self,
            "MainPipeline",
            state_machine_name=name(env, "l3", "sfn", "main"),
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(30),
            tracing_enabled=True,
            logs=sfn.LogOptions(destination=log_group, level=sfn.LogLevel.ALL),
        )

        # S3 ObjectCreated (via EventBridge) -> start an execution.
        events.Rule(
            self,
            "OnInputUpload",
            rule_name=name(env, "l3", "rule", "input-uploaded"),
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={"bucket": {"name": [inputs_bucket.bucket_name]}},
            ),
            targets=[targets.SfnStateMachine(self.state_machine)],
        )

        CfnOutput(self, "StateMachineArn", value=self.state_machine.state_machine_arn)
