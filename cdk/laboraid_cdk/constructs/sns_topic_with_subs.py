"""`SnsTopicWithSubs` — KMS-encrypted SNS topic with email + Lambda subscribers.

Used by the validation stack (Spec/09 §4 L6 + §6) for the failures / successes /
review-needed topics, each fanning out to an email address and the Slack-notifier
Lambda.
"""

from __future__ import annotations

from aws_cdk import Tags
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct


class SnsTopicWithSubs(Construct):
    """An SNS topic plus its email and Lambda subscriptions."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        topic_name: str,
        master_key: kms.IKey,
        email_subscriptions: list[str] | None = None,
        lambda_subscriptions: list[lambda_.IFunction] | None = None,
        layer: str = "l6",
    ) -> None:
        super().__init__(scope, construct_id)

        self.topic = sns.Topic(
            self,
            "Topic",
            topic_name=topic_name,
            master_key=master_key,
        )

        for email in email_subscriptions or []:
            self.topic.add_subscription(subs.EmailSubscription(email))

        for fn in lambda_subscriptions or []:
            self.topic.add_subscription(subs.LambdaSubscription(fn))

        Tags.of(self).add("Layer", layer)
