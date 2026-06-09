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
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.constructs.tagged_lambda import TaggedLambda
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
        extractor_runtime_arn: str,
        master_key: kms.IKey,
        publisher: lambda_.IFunction | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env

        # Stage-2 extraction: a thin Lambda invokes the ExtractorAgent on AgentCore
        # Runtime synchronously (no native SFN -> AgentCore integration) — audit B6.
        # 15-minute timeout matches AgentCore Runtime's max single-invocation
        # duration. The kernel pipeline (PDF OCR + extract + compute + checksum)
        # routinely runs 2-5 min per union; the TaggedLambda default 30s would
        # silently cut the agent mid-run, returning RuntimeClientError to SFN with
        # no agent-side error trail (smoke test 2026-06-08 — fresh log streams
        # under the agent log group were created but stayed empty).
        self.extractor_invoker = TaggedLambda(
            self,
            "ExtractorInvoker",
            env=env,
            layer="l3",
            function_name=name(env, "l3", "fn", "extractor-invoker"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/extractor-invoker"),
            environment={"EXTRACTOR_RUNTIME_ARN": extractor_runtime_arn},
            timeout=Duration.minutes(15),
        )
        self.extractor_invoker.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[extractor_runtime_arn, f"{extractor_runtime_arn}/*"],
            )
        )
        extract_task = tasks.LambdaInvoke(
            self,
            "ExtractViaAgent",
            lambda_function=self.extractor_invoker,
            payload_response_only=True,
            result_path="$.extract",
        )
        extract_task.add_retry(
            errors=[
                "Lambda.ServiceException",
                "Lambda.TooManyRequestsException",
                "States.TaskFailed",
            ],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )

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
            extract_task=extract_task,
            publisher=publisher,
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

        # DynamoGetItem on a CMK-encrypted table (agent-config) requires the SFN
        # execution role to decrypt with the same key. CDK auto-grants the table
        # actions but not the implicit KMS dependency — smoke test 2026-06-08 hit
        # AccessDeniedException on kms:Decrypt at the GetAgentConfig step.
        master_key.grant_decrypt(self.state_machine.role)

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
