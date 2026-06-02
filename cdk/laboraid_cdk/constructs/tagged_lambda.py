"""`TaggedLambda` — Lambda with project defaults + mandatory tags (Spec/09 §2.3).

Defaults (Spec/09 §2.3): Python 3.12, ARM64 (Graviton), 512 MB, 30 s timeout,
active X-Ray tracing, one-month log retention, and Powertools env vars. Callers
override any default via kwargs; the ``environment`` dict is merged, not replaced.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Tags
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
        log_retention=logs.RetentionDays.ONE_MONTH,
        environment={
            "LOG_LEVEL": "INFO" if env == "prod" else "DEBUG",
            "POWERTOOLS_SERVICE_NAME": "laboraid-api",
            "ENV": env,
        },
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
        merged: dict[str, Any] = {**defaults, **kwargs, "environment": merged_env}

        super().__init__(scope, construct_id, **merged)
        Tags.of(self).add("Layer", layer)
