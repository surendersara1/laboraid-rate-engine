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
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
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
        aurora: rds.IDatabaseCluster | None = None,
        aurora_secret: secretsmanager.ISecret | None = None,
        engine_bus: events.IEventBus | None = None,
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

        # --- LLM extractor Lambda (Path-C fallback for any union with NO
        # kernel profile). Receives the SFN classify state, downloads the
        # source PDF, sends it to Bedrock Claude Sonnet 4.6, and emits a
        # canonical CSV that the Publisher consumes identically to the
        # kernel's output.
        self.llm_extractor = TaggedLambda(
            self,
            "LlmExtractor",
            env=env,
            layer="l4",
            function_name=name(env, "l4", "fn", "llm-extractor"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/llm-extractor"),
            timeout=Duration.minutes(15),
            memory_size=2048,
        )
        self.llm_extractor.add_environment(
            "INPUTS_BUCKET", inputs_bucket.bucket_name
        )
        self.llm_extractor.add_environment(
            "OUTPUTS_BUCKET", outputs_bucket.bucket_name
        )
        self.llm_extractor.add_environment("BEDROCK_GUARDRAIL_ID", guardrail_id)
        inputs_bucket.grant_read(self.llm_extractor)
        outputs_bucket.grant_read_write(self.llm_extractor)
        master_key.grant_encrypt_decrypt(self.llm_extractor)
        # Bedrock invoke — same actions + ARNs the agent role has, scoped to
        # Claude foundation models + the cross-region inference profile.
        self.llm_extractor.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{config.region}:{Stack.of(self).account}:inference-profile/*",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                ],
            )
        )
        # Guardrail apply permission so guardrailIdentifier= works on the
        # InvokeModel call (Bedrock denies otherwise even though the request
        # syntax accepts it).
        if guardrail_id:
            self.llm_extractor.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["bedrock:ApplyGuardrail"],
                    resources=[
                        f"arn:aws:bedrock:{config.region}:{Stack.of(self).account}:guardrail/{guardrail_id}"
                    ],
                )
            )

        # --- OCR pre-processing Lambda (text-layer detect + AWS Textract) -----
        # First stage of the SFN main pipeline. Digital PDFs pass through (no
        # Textract spend); scanned PDFs get analyze_document FORMS+TABLES and
        # the response is dropped next to the source as `<key>.layout.json`.
        # The llm-extractor reads that layout and feeds the cell grid + KV
        # pairs to Claude as structured ground truth.
        self.ocr_preprocess = TaggedLambda(
            self,
            "OcrPreprocess",
            env=env,
            layer="l4",
            function_name=name(env, "l4", "fn", "ocr-preprocess"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/ocr-preprocess"),
            timeout=Duration.minutes(15),
            memory_size=1024,
        )
        self.ocr_preprocess.add_environment("INPUTS_BUCKET", inputs_bucket.bucket_name)
        self.ocr_preprocess.add_environment("OUTPUTS_BUCKET", outputs_bucket.bucket_name)
        inputs_bucket.grant_read(self.ocr_preprocess)
        outputs_bucket.grant_read_write(self.ocr_preprocess)
        master_key.grant_encrypt_decrypt(self.ocr_preprocess)
        # Textract analyze_document (sync) + start_document_analysis +
        # get_document_analysis (async, for >5-page PDFs). Resource-scoped to
        # nothing (Textract is regional, doesn't support ARN scoping).
        self.ocr_preprocess.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "textract:AnalyzeDocument",
                    "textract:StartDocumentAnalysis",
                    "textract:GetDocumentAnalysis",
                ],
                resources=["*"],
            )
        )

        # --- Publisher Lambda (writes the agent's extraction into Aurora) -----
        # This is the missing piece the pipeline used to lack: the SFN's
        # "Publish" state was a literal sfn.Succeed() — the agent CSV ended up
        # in S3 and the pipeline terminated. Without this Lambda the product
        # never produces rate sheets; every demo row was hand-loaded.
        # Optional aurora/aurora_secret/engine_bus to keep the unit-test stub
        # simple (test_stacks doesn't construct an Aurora cluster).
        self.publisher = TaggedLambda(
            self,
            "Publisher",
            env=env,
            layer="l4",
            function_name=name(env, "l4", "fn", "publisher"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/publisher"),
            timeout=Duration.minutes(5),
            memory_size=1024,
        )
        self.publisher.add_environment(
            "OUTPUTS_BUCKET", outputs_bucket.bucket_name
        )
        # read_write, not read: the publisher WRITES the final artifacts
        # (final_ratesheet.csv, gap_report.json) to the outputs bucket at the
        # end of every publish. With grant_read only, every PutObject was
        # AccessDenied — swallowed by the non-fatal try/except, so the UI
        # showed "— not produced" while the SFN run still SUCCEEDED.
        outputs_bucket.grant_read_write(self.publisher)
        master_key.grant_encrypt_decrypt(self.publisher)
        if aurora is not None and aurora_secret is not None:
            aurora.grant_data_api_access(self.publisher)
            aurora_secret.grant_read(self.publisher)
            self.publisher.add_environment(
                "AURORA_CLUSTER_ARN", aurora.cluster_arn
            )
            self.publisher.add_environment(
                "AURORA_SECRET_ARN", aurora_secret.secret_arn
            )
        if engine_bus is not None:
            engine_bus.grant_put_events_to(self.publisher)
            self.publisher.add_environment(
                "ENGINE_BUS_NAME", engine_bus.event_bus_name
            )

        # ===================================================================
        # Objective-driven synthesizer pipeline (replaces the per-doc kernel +
        # AgentCore extraction path). Plan -> Synthesize -> SynthPublish.
        # ===================================================================
        synth_model = "us.anthropic.claude-opus-4-5-20251101-v1:0"
        # Cross-region inference profile + foundation models + guardrail.
        bedrock_synth_resources = [
            "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
            f"arn:aws:bedrock:{config.region}:{Stack.of(self).account}:inference-profile/*",
            f"arn:aws:bedrock:{config.region}:{Stack.of(self).account}:guardrail/*",
        ]

        # Shared layer: pypdf + openpyxl + the master_data / pdf_utils modules
        # (Code.from_asset never pip-installs; the layer vendors them).
        synth_deps_layer = lambda_.LayerVersion(
            self,
            "SynthDepsLayer",
            layer_version_name=name(env, "l4", "layer", "synth-deps"),
            code=lambda_.Code.from_asset("../cdk/layers/synth-deps"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.ARM_64],
        )

        # Profile-builder: CBA -> union structure -> Aurora (unions.profile_yaml).
        # Role: reuses the LlmExtractor execution role to MIRROR LIVE (the boto3
        # build wired profile-builder + synthesizer onto LlmExtractorServiceRole;
        # synth-publish onto PublisherServiceRole). Reusing the same roles here
        # makes the Phase-3 cdk import clean with ZERO IAM change. The grants
        # below add to that shared role (matches the live inline policies).
        self.profile_builder = TaggedLambda(
            self, "ProfileBuilder", env=env, layer="l4",
            function_name=name(env, "l4", "fn", "profile-builder"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/profile-builder"),
            memory_size=1024, timeout=Duration.seconds(900),
            layers=[synth_deps_layer],
            role=self.llm_extractor.role,
        )
        self.profile_builder.add_environment("INPUTS_BUCKET", inputs_bucket.bucket_name)
        self.profile_builder.add_environment("BEDROCK_GUARDRAIL_ID", guardrail_id)
        self.profile_builder.add_environment("SYNTH_MODEL_ID", synth_model)
        inputs_bucket.grant_read(self.profile_builder)
        self.profile_builder.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=bedrock_synth_resources))

        # Synthesizer: all docs + the union's profile -> rate sheet (CSV/XLSX/JSON).
        self.synthesizer_fn = TaggedLambda(
            self, "Synthesizer", env=env, layer="l4",
            function_name=name(env, "l4", "fn", "synthesizer"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/synthesizer"),
            memory_size=1024, timeout=Duration.seconds(900),
            layers=[synth_deps_layer],
            role=self.llm_extractor.role,  # mirror live (see profile-builder note)
        )
        self.synthesizer_fn.add_environment("INPUTS_BUCKET", inputs_bucket.bucket_name)
        self.synthesizer_fn.add_environment("OUTPUTS_BUCKET", outputs_bucket.bucket_name)
        self.synthesizer_fn.add_environment("BEDROCK_GUARDRAIL_ID", guardrail_id)
        self.synthesizer_fn.add_environment("SYNTH_MODEL_ID", synth_model)
        self.synthesizer_fn.add_environment("PROFILE_BUILDER_FN", self.profile_builder.function_name)
        inputs_bucket.grant_read(self.synthesizer_fn)
        outputs_bucket.grant_read_write(self.synthesizer_fn)
        master_key.grant_encrypt_decrypt(self.synthesizer_fn)
        self.synthesizer_fn.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=bedrock_synth_resources))
        # auto-onboard: synthesizer invokes profile-builder. We grant via a
        # CONSTRUCTED ARN string (not profile_builder.function_arn) on purpose:
        # synthesizer + profile-builder SHARE the LlmExtractor role (Option A), so a
        # token-based grant_invoke would make that role's policy depend on the
        # ProfileBuilder resource which itself depends on the role -> CloudFormation
        # circular dependency. A static ARN breaks the cycle while keeping the grant.
        self.synthesizer_fn.add_to_role_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["lambda:InvokeFunction"],
            resources=[
                f"arn:aws:lambda:{config.region}:{Stack.of(self).account}:function:"
                f"{name(env, 'l4', 'fn', 'profile-builder')}"
            ],
        ))

        # Synth-publish: write the synthesized rate sheet to Aurora (clean replace,
        # cohorts in rate_cells.dimensions).
        self.synth_publish = TaggedLambda(
            self, "SynthPublish", env=env, layer="l4",
            function_name=name(env, "l4", "fn", "synth-publish"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/synth-publish"),
            memory_size=512, timeout=Duration.seconds(300),
            role=self.publisher.role,  # mirror live (see profile-builder note)
        )
        self.synth_publish.add_environment("OUTPUTS_BUCKET", outputs_bucket.bucket_name)
        outputs_bucket.grant_read_write(self.synth_publish)
        master_key.grant_encrypt_decrypt(self.synth_publish)

        # Batch-planner: classify + order the uploaded PDFs; resolve union + period.
        self.batch_planner = TaggedLambda(
            self, "BatchPlanner", env=env, layer="l4",
            function_name=name(env, "l4", "fn", "batch-planner"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/processing/batch-planner"),
            memory_size=1024, timeout=Duration.seconds(300),
        )
        self.batch_planner.add_environment("INPUTS_BUCKET", inputs_bucket.bucket_name)
        self.batch_planner.add_environment("CLASSIFIER_FN", self.classifier.function_name)
        inputs_bucket.grant_read(self.batch_planner)
        self.classifier.grant_invoke(self.batch_planner)

        # Aurora (RDS Data API) for the three DB-touching new Lambdas.
        if aurora is not None and aurora_secret is not None:
            for fn in (self.synthesizer_fn, self.profile_builder, self.synth_publish):
                aurora.grant_data_api_access(fn)
                aurora_secret.grant_read(fn)
                fn.add_environment("AURORA_CLUSTER_ARN", aurora.cluster_arn)
                fn.add_environment("AURORA_SECRET_ARN", aurora_secret.secret_arn)

        CfnOutput(self, "ClassifierFnName", value=self.classifier.function_name)
        CfnOutput(self, "ExtractorRepoUri", value=self.extractor_repo.repository_uri)
        CfnOutput(self, "PublisherFnName", value=self.publisher.function_name)
        CfnOutput(self, "LlmExtractorFnName", value=self.llm_extractor.function_name)
        CfnOutput(self, "OcrPreprocessFnName", value=self.ocr_preprocess.function_name)
        CfnOutput(self, "SynthesizerFnName", value=self.synthesizer_fn.function_name)
        CfnOutput(self, "SynthPublishFnName", value=self.synth_publish.function_name)
        CfnOutput(self, "ProfileBuilderFnName", value=self.profile_builder.function_name)
        CfnOutput(self, "BatchPlannerFnName", value=self.batch_planner.function_name)

        # --- Migration-only export retention (Phase 3 reconciliation) ----------
        # The live Orchestration stack still imports the Classifier / Publisher /
        # LlmExtractor ARN+Ref exports from this stack. The new Orchestration drops
        # those references, but CloudFormation REFUSES to delete an export that is
        # still in use, and Processing must deploy BEFORE Orchestration (the new
        # SFN needs this stack's new exports). exportValue() forces these exports
        # to persist through the Processing deploy so the subsequent Orchestration
        # deploy can stop importing them. REMOVE these lines in the post-IN_SYNC
        # cleanup PR once nothing imports them. See cdk/reconciliation/PHASE3_EXECUTION.md.
        self.export_value(self.classifier.function_arn)
        self.export_value(self.publisher.function_arn)
        self.export_value(self.llm_extractor.function_arn)
        self.export_value(self.llm_extractor.function_name)
