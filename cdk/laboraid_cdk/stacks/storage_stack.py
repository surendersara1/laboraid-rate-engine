"""L3 Storage stack — S3 buckets, DynamoDB tables, Aurora (Spec/09 §4 L3 §3.1-3.5).

Creates:
- 6 S3 buckets (inputs/processed/outputs/profiles/audit/cba-corpus) via
  `TaggedBucket` (KMS + TLS-only + versioned), with lifecycle + object lock per
  retention rules; all server-access-log to the audit bucket.
- 7 DynamoDB tables (§3.2) — on-demand, PITR, SSE-KMS; streams on files + jobs.
- Aurora Serverless v2 Postgres cluster (§3.3) with the RDS Data API enabled and
  a schema-init custom resource that applies the DDL at stack create/update.

Note: Spec/09 §3.2 defines 7 tables (the BUILD table's "6" predates the
`agent-config` table, which §4.4 SOW match requires) — all 7 are created here.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, CustomResource, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import custom_resources as cr
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.constructs.tagged_bucket import TaggedBucket
from laboraid_cdk.util.naming import name


class StorageStack(Stack):
    """S3 + DynamoDB + Aurora storage layer (L3)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        master_key: kms.IKey,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        env = config.env
        retain = RemovalPolicy.RETAIN if config.is_prod else RemovalPolicy.DESTROY

        # --- S3 buckets (§3.1) -------------------------------------------------
        # Audit bucket first: it is the server-access-log target for the others.
        self.audit_bucket = TaggedBucket(
            self,
            "AuditBucket",
            bucket_name=name(env, "l3", "bucket", "audit"),
            encryption_key=master_key,
            data_classification="audit-log",
            object_lock_enabled=config.is_prod,
            removal_policy=retain,
        )

        def _bucket(
            cid: str,
            purpose: str,
            classification: str,
            *,
            lifecycle: list[s3.LifecycleRule] | None = None,
            object_lock: bool = False,
            event_bridge: bool = False,
        ) -> TaggedBucket:
            return TaggedBucket(
                self,
                cid,
                bucket_name=name(env, "l3", "bucket", purpose),
                encryption_key=master_key,
                data_classification=classification,
                server_access_logs_bucket=self.audit_bucket,
                server_access_logs_prefix=f"{purpose}/",
                object_lock_enabled=object_lock and config.is_prod,
                lifecycle_rules=lifecycle,
                # Emit S3 events to EventBridge so the orchestration stack's rule
                # can trigger the Step Functions pipeline on upload (Spec/09 §3.4).
                event_bridge_enabled=event_bridge,
                removal_policy=retain,
            )

        archive_lifecycle = [
            s3.LifecycleRule(
                transitions=[
                    s3.Transition(
                        storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                        transition_after=Duration.days(30),
                    ),
                    s3.Transition(
                        storage_class=s3.StorageClass.DEEP_ARCHIVE,
                        transition_after=Duration.days(365),
                    ),
                ]
            )
        ]
        processed_lifecycle = [s3.LifecycleRule(expiration=Duration.days(90))]

        self.inputs_bucket = _bucket(
            "InputsBucket",
            "inputs",
            "customer-input",
            lifecycle=archive_lifecycle,
            object_lock=True,
            event_bridge=True,
        )
        self.processed_bucket = _bucket(
            "ProcessedBucket",
            "processed",
            "engine-intermediate",
            lifecycle=processed_lifecycle,
        )
        self.outputs_bucket = _bucket(
            "OutputsBucket",
            "outputs",
            "engine-output",
            lifecycle=archive_lifecycle,
            object_lock=True,
        )
        self.profiles_bucket = _bucket("ProfilesBucket", "profiles", "ops-config")
        self.cba_corpus_bucket = _bucket("CbaCorpusBucket", "cba-corpus", "customer-input")

        # --- DynamoDB tables (§3.2) -------------------------------------------
        def _table(
            cid: str,
            purpose: str,
            pk: str,
            sk: str | None = None,
            *,
            stream: bool = False,
            ttl_attr: str | None = None,
        ) -> ddb.Table:
            return ddb.Table(
                self,
                cid,
                table_name=name(env, "l3", "ddb", purpose),
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                sort_key=(ddb.Attribute(name=sk, type=ddb.AttributeType.STRING) if sk else None),
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                encryption=ddb.TableEncryption.CUSTOMER_MANAGED,
                encryption_key=master_key,
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True
                ),
                time_to_live_attribute=ttl_attr,
                stream=(ddb.StreamViewType.NEW_AND_OLD_IMAGES if stream else None),
                removal_policy=retain,
            )

        self.files_table = _table(
            "FilesTable", "files", "tenant#union", "period#filename", stream=True
        )
        self.jobs_table = _table("JobsTable", "jobs", "job_id", stream=True)
        self.review_table = _table("ReviewTable", "review", "tenant", "created_at#cell_id")
        self.overrides_table = _table(
            "OverridesTable", "overrides", "tenant#union#period", "cell_id#timestamp"
        )
        self.cadence_table = _table("CadenceTable", "cadence", "tenant#union")
        self.idempotency_table = _table(
            "IdempotencyTable", "idempotency", "request_hash", ttl_attr="ttl"
        )
        self.agent_config_table = _table("AgentConfigTable", "agent-config", "agent_name")

        # --- Aurora Serverless v2 Postgres (§3.3) -----------------------------
        # Minimal VPC (no NAT): Aurora sits in isolated subnets. The schema-init
        # Lambda uses the RDS Data API (public endpoint) so it needs no VPC.
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="db", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24
                )
            ],
        )

        self.aurora = rds.DatabaseCluster(
            self,
            "Aurora",
            cluster_identifier=name(env, "l3", "aurora", "cluster"),
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.of("16.6", "16")
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2,
            writer=rds.ClusterInstance.serverless_v2("Writer"),
            readers=[rds.ClusterInstance.serverless_v2("Reader", scale_with_writer=True)],
            credentials=rds.Credentials.from_generated_secret(
                "laboraid_admin", secret_name=name(env, "l3", "secret", "aurora")
            ),
            default_database_name="laboraid",
            storage_encryption_key=master_key,
            enable_data_api=True,
            removal_policy=retain,
        )

        self._add_schema_init()

        # --- Outputs ----------------------------------------------------------
        CfnOutput(self, "InputsBucketName", value=self.inputs_bucket.bucket_name)
        CfnOutput(self, "OutputsBucketName", value=self.outputs_bucket.bucket_name)
        CfnOutput(self, "AuroraClusterArn", value=self.aurora.cluster_arn)

    def _add_schema_init(self) -> None:
        """Custom resource that applies the Aurora DDL via the RDS Data API."""
        secret = self.aurora.secret
        assert secret is not None  # from_generated_secret always sets this

        on_event = lambda_.Function(
            self,
            "SchemaInitFn",
            function_name=name(self.config.env, "l3", "fn", "schema-init"),
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.on_event",
            code=lambda_.Code.from_asset("assets/schema_init"),
            timeout=Duration.minutes(5),
            log_group=logs.LogGroup(
                self,
                "SchemaInitLogGroup",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
            environment={
                "CLUSTER_ARN": self.aurora.cluster_arn,
                "SECRET_ARN": secret.secret_arn,
                "DB_NAME": "laboraid",
            },
        )
        secret.grant_read(on_event)
        self.aurora.grant_data_api_access(on_event)

        provider = cr.Provider(self, "SchemaInitProvider", on_event_handler=on_event)
        resource = CustomResource(
            self,
            "SchemaInit",
            service_token=provider.service_token,
            properties={"schemaVersion": "1"},
        )
        # Ensure the cluster exists before the DDL runs.
        resource.node.add_dependency(self.aurora)
