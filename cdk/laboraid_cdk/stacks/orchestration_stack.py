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
        batch_planner: lambda_.IFunction,
        synthesizer: lambda_.IFunction,
        synth_publish: lambda_.IFunction,
        master_key: kms.IKey,
        jobs_table: ddb.ITable,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env

        # Plan -> Synthesize -> SynthPublish. CDK's LambdaInvoke tasks auto-grant
        # the state-machine role permission to invoke each Lambda.
        definition = build_definition(
            self,
            batch_planner=batch_planner,
            synthesizer=synthesizer,
            synth_publish=synth_publish,
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
            timeout=Duration.minutes(60),
            tracing_enabled=True,
            logs=sfn.LogOptions(destination=log_group, level=sfn.LogLevel.ALL),
        )

        # S3-upload EventBridge trigger — kept for reference but DISABLED. Batches
        # are started explicitly via POST /v1/batches/process ("Process this
        # batch"), not auto-triggered on upload.
        events.Rule(
            self,
            "OnInputUpload",
            rule_name=name(env, "l3", "rule", "input-uploaded"),
            enabled=False,
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={"bucket": {"name": [inputs_bucket.bucket_name]}},
            ),
            targets=[targets.SfnStateMachine(self.state_machine)],
        )

        # --- Read-model: job-writer (SFN state -> jobs DynamoDB table) ---------
        # EventBridge delivers "Step Functions Execution Status Change" events for
        # this SFN; the job-writer upserts the jobs table so the Admin dashboard
        # reads one indexed DynamoDB query instead of live ListExecutions+Describe.
        self.job_writer = TaggedLambda(
            self,
            "JobWriter",
            env=env,
            layer="l3",
            function_name=name(env, "l3", "fn", "job-writer"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/job-writer"),
            timeout=Duration.seconds(30),
        )
        self.job_writer.add_environment("JOBS_TABLE", jobs_table.table_name)
        self.job_writer.add_environment(
            "STATE_MACHINE_ARN", self.state_machine.state_machine_arn
        )
        jobs_table.grant_write_data(self.job_writer)
        self.job_writer.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["states:DescribeExecution", "states:GetExecutionHistory"],
                resources=[
                    f"arn:aws:states:{config.region}:{Stack.of(self).account}"
                    f":execution:{name(env, 'l3', 'sfn', 'main')}:*"
                ],
            )
        )
        # ListExecutions (backfill mode) is scoped to the state machine ARN.
        self.job_writer.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["states:ListExecutions"],
                resources=[self.state_machine.state_machine_arn],
            )
        )
        events.Rule(
            self,
            "JobStateChange",
            rule_name=name(env, "l3", "rule", "job-state-change"),
            event_pattern=events.EventPattern(
                source=["aws.states"],
                detail_type=["Step Functions Execution Status Change"],
                detail={"stateMachineArn": [self.state_machine.state_machine_arn]},
            ),
            targets=[targets.LambdaFunction(self.job_writer)],
        )

        CfnOutput(self, "StateMachineArn", value=self.state_machine.state_machine_arn)
