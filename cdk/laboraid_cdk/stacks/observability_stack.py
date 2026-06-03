"""Observability stack — dashboards, alarms, CloudTrail (Spec/09 §8).

Creates 5 CloudWatch dashboards (overview / pipeline / agents / storage / api),
6 named alarms (pipeline failure rate, Bedrock spend, Aurora CPU, DDB throttling,
review-queue depth, API 5xx rate) wired to the failures SNS topic, and a
CloudTrail trail. X-Ray tracing is enabled per-resource on the Lambdas and the
Step Functions pipeline.

Most metrics are addressed by deterministic resource name/ARN (built from the
naming convention). The API Gateway 5xx alarm is the exception: its ``ApiId``
dimension is the gateway-assigned random id, so the real ``api_id`` is passed in
(audit D9), alongside the alarm SNS topic.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_sns as sns
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.util.naming import name


class ObservabilityStack(Stack):
    """CloudWatch dashboards + alarms + CloudTrail."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        alarm_topic: sns.ITopic,
        api_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env
        action = cw_actions.SnsAction(alarm_topic)

        sfn_arn = (
            f"arn:aws:states:{config.region}:{config.account}:"
            f"stateMachine:{name(env, 'l3', 'sfn', 'main')}"
        )
        aurora_id = name(env, "l3", "aurora", "cluster")
        # The API GW 5xx metric is keyed by the gateway-assigned ApiId (a random
        # id), NOT the resource name — pass the real id in (audit D9).
        api_dimension_id = api_id

        # --- 5 dashboards (§8) ------------------------------------------------
        for board in ("overview", "pipeline", "agents", "storage", "api"):
            dash = cw.Dashboard(
                self, f"Dash{board.title()}", dashboard_name=f"laboraid-{env}-dashboard-{board}"
            )
            dash.add_widgets(
                cw.TextWidget(markdown=f"# LaborAid {board} ({env})", width=24, height=2)
            )

        # --- 6 named alarms (§8) ----------------------------------------------
        def alarm(
            cid: str, purpose: str, metric: cw.IMetric, threshold: float, periods: int = 1
        ) -> None:
            a = cw.Alarm(
                self,
                cid,
                alarm_name=f"laboraid-{env}-alarm-{purpose}",
                metric=metric,
                threshold=threshold,
                evaluation_periods=periods,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )
            a.add_alarm_action(action)

        alarm(
            "PipelineFailureAlarm",
            "pipeline-failure",
            cw.Metric(
                namespace="AWS/States",
                metric_name="ExecutionsFailed",
                dimensions_map={"StateMachineArn": sfn_arn},
                statistic="Sum",
                period=Duration.hours(1),
            ),
            threshold=3,
        )
        alarm(
            "BedrockSpendAlarm",
            "bedrock-spend",
            cw.Metric(
                namespace="AWS/Bedrock",
                metric_name="InvocationClientErrors",
                statistic="Sum",
                period=Duration.days(1),
            ),
            threshold=100,
        )
        alarm(
            "AuroraCpuAlarm",
            "aurora-cpu",
            cw.Metric(
                namespace="AWS/RDS",
                metric_name="CPUUtilization",
                dimensions_map={"DBClusterIdentifier": aurora_id},
                statistic="Average",
                period=Duration.minutes(15),
            ),
            threshold=80,
        )
        alarm(
            "DdbThrottleAlarm",
            "ddb-throttling",
            cw.Metric(
                namespace="AWS/DynamoDB",
                metric_name="ThrottledRequests",
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=0,
        )
        alarm(
            "ReviewQueueAlarm",
            "review-queue-depth",
            cw.Metric(
                namespace="LaborAid",
                metric_name="ReviewQueueDepth",
                statistic="Maximum",
                period=Duration.minutes(5),
            ),
            threshold=50,
        )
        alarm(
            "Api5xxAlarm",
            "api-5xx",
            cw.Metric(
                namespace="AWS/ApiGateway",
                metric_name="5xx",
                dimensions_map={"ApiId": api_dimension_id},
                statistic="Average",
                period=Duration.minutes(5),
            ),
            threshold=0.01,
        )

        # --- CloudTrail (§8 / §9 security) ------------------------------------
        self.trail = cloudtrail.Trail(
            self,
            "Trail",
            trail_name=f"laboraid-{env}-trail",
            include_global_service_events=True,
            is_multi_region_trail=False,
        )
