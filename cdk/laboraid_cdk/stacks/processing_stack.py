"""L4+L5 Processing stack — classifier Lambda, ECR, AgentCore Runtime.

Implements Spec/09 §4 L4 (§4.1-4.2 classifier) + L5 (§5.1, §5.4 AgentCore Runtime).

Creates:
- The document-classifier Lambda (§4.2) with its own least-privilege role.
- An ECR repository for the ExtractorAgent container image (§5.1).
- The ExtractorAgent AgentCore Runtime via the `StrandsAgentRuntime` construct,
  with a locally-defined execution role (§5.1, §7.2) granted the storage + Bedrock
  + guardrail permissions it needs — defined here (downstream of Storage) to keep
  the cross-stack dependency graph acyclic.
- The async extraction queue + DLQ + lifecycle SNS topic (§4.1).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.constructs.strands_agent import StrandsAgentRuntime
from laboraid_cdk.constructs.tagged_lambda import TaggedLambda
from laboraid_cdk.util.naming import name


class ProcessingStack(Stack):
    """Classifier Lambda + ECR + ExtractorAgent AgentCore Runtime."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        master_key: kms.IKey,
        inputs_bucket: s3.IBucket,
        outputs_bucket: s3.IBucket,
        files_table: ddb.ITable,
        guardrail_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env

        bedrock_models = f"arn:aws:bedrock:{config.region}::foundation-model/anthropic.claude-*"

        # --- Document classifier Lambda (§4.2) --------------------------------
        self.classifier = TaggedLambda(
            self,
            "Classifier",
            env=env,
            layer="l4",
            function_name=name(env, "l4", "fn", "classifier"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/classifier"),
        )
        inputs_bucket.grant_read(self.classifier)
        files_table.grant_read_write_data(self.classifier)
        self.classifier.add_environment("BEDROCK_GUARDRAIL_ID", guardrail_id)
        self.classifier.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[bedrock_models],
            )
        )

        # --- ECR repo for the ExtractorAgent image (§5.1) ---------------------
        # IMPORTED by name (not created here). scripts/deploy.sh creates the repo
        # and pushes the :latest image BEFORE this stack deploys. This breaks the
        # chicken-and-egg: the AgentCore runtime below references {repo}:latest at
        # deploy time, so the image must already exist -- which is impossible if
        # the same stack also creates the repo. (deploy.sh owns the repo's
        # scan-on-push/encryption settings.)
        self.extractor_repo = ecr.Repository.from_repository_name(
            self,
            "ExtractorRepo",
            name(env, "l5", "ecr", "agent-extractor"),
        )

        # --- ExtractorAgent execution role (§5.1, §7.2) -----------------------
        # Defined here (downstream of Storage) so its grants don't create a cycle.
        self.agent_role = iam.Role(
            self,
            "AgentExtractorRole",
            role_name=name(env, "l5", "role", "agent-extractor"),
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="AgentCore execution role for the ExtractorAgent",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess"),
            ],
        )
        # Bedrock — agent uses Strands which defaults to the Converse API, and our
        # model ID is a cross-region inference profile (us.anthropic.claude-sonnet-4-6)
        # that routes the underlying call to a model in us-east-1, us-east-2, or
        # us-west-2. So we need:
        #   - Converse + ConverseStream actions (Strands default API, NOT InvokeModel)
        #   - InvokeModel + WithResponseStream (older API, kept for the agent's
        #     direct escalate_to_claude_multimodal tool)
        #   - the inference-profile resource itself
        #   - the foundation-model resource in ALL 3 cross-region inference regions
        # Smoke test 2026-06-08 caught the original InvokeModel-only scope on
        # bedrock:ConverseStream AccessDeniedException.
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=[
                    # inference profile in the agent's region
                    f"arn:aws:bedrock:{config.region}:{Stack.of(self).account}:inference-profile/*",
                    # foundation-model ARN uses a WILDCARD region — when an inference
                    # profile resolves to an underlying model, Bedrock authorizes the
                    # call against arn:aws:bedrock:::foundation-model/<id> (region is
                    # an EMPTY segment, not the profile's region). A region-scoped
                    # pattern like arn:aws:bedrock:us-east-1::... will not match the
                    # empty-region request. Smoke test 2026-06-08 confirmed this.
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                ],
            )
        )
        inputs_bucket.grant_read(self.agent_role)
        outputs_bucket.grant_read_write(self.agent_role)
        files_table.grant_read_write_data(self.agent_role)
        master_key.grant_encrypt_decrypt(self.agent_role)
        self.extractor_repo.grant_pull(self.agent_role)

        # CloudWatch Logs + Metrics for the AgentCore Runtime container.
        # Sourced verbatim from
        # https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
        # — without these, the agent container fails to bootstrap and InvokeAgentRuntime
        # returns a 500 with no diagnostic log group (smoke test 2026-06-08 caught this).
        log_group_arn = (
            f"arn:aws:logs:{config.region}:{Stack.of(self).account}"
            f":log-group:/aws/bedrock-agentcore/runtimes/*"
        )
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                resources=[log_group_arn],
            )
        )
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:DescribeLogGroups"],
                resources=[
                    f"arn:aws:logs:{config.region}:{Stack.of(self).account}:log-group:*"
                ],
            )
        )
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"{log_group_arn}:log-stream:*"],
            )
        )
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={
                    "StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}
                },
            )
        )

        # --- AgentCore Runtime (§5.4) -----------------------------------------
        self.extractor_runtime = StrandsAgentRuntime(
            self,
            "ExtractorRuntime",
            runtime_name=name(env, "l5", "agent", "extractor"),
            image_uri=f"{self.extractor_repo.repository_uri}:latest",
            execution_role=self.agent_role,
            environment={
                # AgentCore Runtime does NOT auto-inject AWS_REGION the way Lambda
                # does (smoke test 2026-06-08: agent.py:44 crashed at boto3.client
                # call with NoRegionError; container ran but produced no logs since
                # Python died at module import time before OTEL collector started).
                "AWS_REGION": config.region,
                "AWS_DEFAULT_REGION": config.region,
                "ENV": env,
                "INPUTS_BUCKET": inputs_bucket.bucket_name,
                "OUTPUTS_BUCKET": outputs_bucket.bucket_name,
                "PROFILES_DIR": "/opt/profiles",
                "BEDROCK_GUARDRAIL_ID": guardrail_id,
            },
        )

        # --- Async extraction queue + DLQ + lifecycle topic (§4.1) ------------
        self.extraction_dlq = sqs.Queue(
            self,
            "ExtractionDlq",
            queue_name=name(env, "l4", "sqs", "dlq-extraction"),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=master_key,
            retention_period=Duration.days(14),
        )
        self.extraction_queue = sqs.Queue(
            self,
            "ExtractionQueue",
            queue_name=name(env, "l4", "sqs", "extraction"),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=master_key,
            visibility_timeout=Duration.minutes(15),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=self.extraction_dlq),
        )
        self.extraction_events = sns.Topic(
            self,
            "ExtractionEvents",
            topic_name=name(env, "l4", "sns", "extraction-events"),
            master_key=master_key,
        )

        CfnOutput(self, "ClassifierFnName", value=self.classifier.function_name)
        CfnOutput(self, "ExtractorRepoUri", value=self.extractor_repo.repository_uri)
