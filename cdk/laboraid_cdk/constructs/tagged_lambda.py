"""`TaggedLambda` — Lambda with project defaults + mandatory tags (Spec/09 §2.3).

Defaults (Spec/09 §2.3): Python 3.12, ARM64 (Graviton), 512 MB, 30 s timeout,
active X-Ray tracing, an explicit one-month-retention log group, and Powertools
env vars. Callers override any default via kwargs; the ``environment`` dict is
merged, not replaced.

The log group is created explicitly (rather than via the deprecated
``log_retention`` prop) — the latter injects a late, singleton custom resource
that makes app-level Aspects re-enter and trips CDK's infinite-loop guard once
many Lambdas exist.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, RemovalPolicy, Tags
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct


def lambda_defaults(env: str) -> dict[str, Any]:
    """Return the shared Lambda keyword defaults for ``env`` (Spec/09 §2.3)."""
    return dict(
        runtime=lambda_.Runtime.PYTHON_3_12,
        architecture=lambda_.Architecture.ARM_64,
        memory_size=512,
        timeout=Duration.seconds(30),
        tracing=lambda_.Tracing.ACTIVE,
        environment={
            "LOG_LEVEL": "INFO" if env == "prod" else "DEBUG",
            "POWERTOOLS_SERVICE_NAME": "laboraid-api",
            "ENV": env,
        },
    )


# AWS-managed Powertools v3 layer for Python 3.12 / arm64 (us-east-2). The
# requirements.txt files in lambdas/**/ all start with aws-lambda-powertools;
# Code.from_asset never pip-installs, so without this layer each lambda dies
# with "Runtime.ImportModuleError: No module named 'aws_lambda_powertools'".
# Smoke test 2026-06-08 caught this on the classifier. Version 25 verified via
# get-layer-version-by-arn; bump as Powertools releases.
_POWERTOOLS_LAYER_ARN = (
    "arn:aws:lambda:us-east-2:017000801446:layer:"
    "AWSLambdaPowertoolsPythonV3-python312-arm64:25"
)


class TaggedLambda(lambda_.Function):
    """An `lambda_.Function` pre-wired with project defaults + tags."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env: str,
        layer: str = "l2",
        **kwargs: Any,
    ) -> None:
        defaults = lambda_defaults(env)

        # Merge environment dicts so callers can add vars without dropping defaults.
        merged_env = {**defaults.pop("environment"), **kwargs.pop("environment", {})}

        # Explicit one-month log group (avoids the deprecated log_retention CR).
        if "log_group" not in kwargs:
            fn_name = kwargs.get("function_name")
            kwargs["log_group"] = logs.LogGroup(
                scope,
                f"{construct_id}LogGroup",
                log_group_name=f"/aws/lambda/{fn_name}" if fn_name else None,
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            )

        # Attach the AWS-managed Powertools layer; callers can pass extra layers
        # via kwargs["layers"] and they get prepended (Lambda merges by order).
        powertools = lambda_.LayerVersion.from_layer_version_arn(
            scope, f"{construct_id}PowertoolsLayer", _POWERTOOLS_LAYER_ARN
        )
        kwargs["layers"] = [*kwargs.get("layers", []), powertools]

        merged: dict[str, Any] = {**defaults, **kwargs, "environment": merged_env}

        super().__init__(scope, construct_id, **merged)
        Tags.of(self).add("Layer", layer)
