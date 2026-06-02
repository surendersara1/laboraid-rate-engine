"""L6+L7 Validation stack — validators, renderers, notifications (Spec/09 §4 L6 + §6).

Creates the pipeline's validator Lambdas (checksum/range/confidence/review-router),
the renderer Lambdas (xlsx/csv/articles), the three SNS topics (failures /
successes / review-needed) with email + Slack-notifier subscriptions, the engine
EventBridge bus, an SES configuration set, and a DLQ for async subscribers. The
orchestration stack (F.1) wires these Lambdas into the Step Functions pipeline.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ses as ses
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.constructs.sns_topic_with_subs import SnsTopicWithSubs
from laboraid_cdk.constructs.tagged_lambda import TaggedLambda
from laboraid_cdk.util.naming import name


class ValidationStack(Stack):
    """Validators + renderers + SNS/EventBridge/SES notifications (L6+L7)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        master_key: kms.IKey,
        outputs_bucket: s3.IBucket,
        review_table: ddb.ITable,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env

        def _fn(cid: str, layer: str, purpose: str, path: str) -> TaggedLambda:
            return TaggedLambda(
                self,
                cid,
                env=env,
                layer=layer,
                function_name=name(env, layer, "fn", purpose),
                handler="handler.handler",
                code=lambda_.Code.from_asset(path),
            )

        # --- Validator Lambdas (L6, §6.1) -------------------------------------
        self.checksum = _fn(
            "Checksum", "l6", "validator-checksum", "../lambdas/validation/checksum"
        )
        self.range_fn = _fn("Range", "l6", "validator-range", "../lambdas/validation/range")
        self.confidence = _fn(
            "Confidence", "l6", "validator-confidence", "../lambdas/validation/confidence"
        )
        self.review_router = _fn(
            "ReviewRouter", "l6", "review-router", "../lambdas/validation/review-router"
        )
        self.review_router.add_environment("REVIEW_TABLE", review_table.table_name)
        review_table.grant_read_write_data(self.review_router)

        # --- Renderer Lambdas (L7, §7.1-7.2) ----------------------------------
        self.xlsx = _fn("XlsxRenderer", "l7", "renderer-xlsx", "../lambdas/rendering/xlsx-renderer")
        self.csv = _fn("CsvRenderer", "l7", "renderer-csv", "../lambdas/rendering/csv-renderer")
        self.articles = _fn(
            "ArticlesRenderer", "l7", "renderer-articles", "../lambdas/rendering/articles-renderer"
        )
        for renderer in (self.xlsx, self.csv, self.articles):
            renderer.add_environment("OUTPUTS_BUCKET", outputs_bucket.bucket_name)
            outputs_bucket.grant_read_write(renderer)

        # --- Slack notifier (L6) + its DLQ ------------------------------------
        self.notifier_dlq = sqs.Queue(
            self,
            "SlackNotifierDlq",
            queue_name=name(env, "l6", "sqs", "dlq-slack-notify"),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=master_key,
            retention_period=Duration.days(14),
        )
        self.slack_notifier = TaggedLambda(
            self,
            "SlackNotifier",
            env=env,
            layer="l6",
            function_name=name(env, "l6", "fn", "slack-notify"),
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/validation/slack-notifier"),
            dead_letter_queue=self.notifier_dlq,
        )
        self.slack_notifier.add_environment(
            "SLACK_WEBHOOK_SECRET", config.slack_webhook_secret_name
        )
        slack_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "SlackWebhookSecret", config.slack_webhook_secret_name
        )
        slack_secret.grant_read(self.slack_notifier)

        # --- SNS topics (§6.1-6.2) --------------------------------------------
        self.failures_topic = SnsTopicWithSubs(
            self,
            "FailuresTopic",
            topic_name=name(env, "l6", "sns", "failures"),
            master_key=master_key,
            email_subscriptions=[config.alarm_email],
            lambda_subscriptions=[self.slack_notifier],
        )
        self.successes_topic = SnsTopicWithSubs(
            self,
            "SuccessesTopic",
            topic_name=name(env, "l6", "sns", "successes"),
            master_key=master_key,
        )
        self.review_needed_topic = SnsTopicWithSubs(
            self,
            "ReviewNeededTopic",
            topic_name=name(env, "l6", "sns", "review-needed"),
            master_key=master_key,
            email_subscriptions=[config.alarm_email],
            lambda_subscriptions=[self.slack_notifier],
        )

        # --- Engine EventBridge bus (§3.5) + failure routing rule -------------
        self.engine_bus = events.EventBus(
            self, "EngineBus", event_bus_name=name(env, "l3", "eb", "engine")
        )
        events.Rule(
            self,
            "JobFailedToFailures",
            event_bus=self.engine_bus,
            rule_name=name(env, "l6", "rule", "job-failed"),
            event_pattern=events.EventPattern(detail_type=["laboraid.job.failed"]),
            targets=[targets.SnsTopic(self.failures_topic.topic)],
        )

        # --- SES configuration set (§6.1) -------------------------------------
        self.ses_config_set = ses.ConfigurationSet(
            self,
            "NotificationsConfigSet",
            configuration_set_name=name(env, "l6", "ses", "notifications"),
        )

        CfnOutput(self, "FailuresTopicArn", value=self.failures_topic.topic.topic_arn)
        CfnOutput(self, "EngineBusArn", value=self.engine_bus.event_bus_arn)
