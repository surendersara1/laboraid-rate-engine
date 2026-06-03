"""Environment-specific configuration.

Implements Spec/09 §0 (build envelope) + §2 (tagging strategy).

`Config` is a frozen dataclass holding the per-environment values every stack
needs (account, region, domain, alarm routing). `mandatory_tags` returns the 13
project-wide tags enforced by `MandatoryTagsAspect` (Spec/09 §2).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Per-environment configuration passed to every stack constructor."""

    env: str
    """Deployment environment: ``"dev"`` | ``"prod"``."""

    account: str
    """AWS account ID. Synth works with a placeholder; deploy needs a real one."""

    region: str
    """Primary region — ``us-east-1`` for Bedrock + AgentCore availability."""

    alarm_email: str
    """Email subscribed to the failures / alarm SNS topics."""

    slack_webhook_secret_name: str
    """Secrets Manager secret name holding the Slack incoming-webhook URL."""

    domain_name: str | None = None
    """Public hostname for the SPA (e.g. ``admin-dev.laboraid.app``).

    ``None`` (the default) means *no custom domain*: the UI is served from the
    CloudFront default ``*.cloudfront.net`` domain and the Cognito hosted-UI
    callbacks point there too, so a deploy never produces a broken auth flow on
    an account with no Route53 zone (audit B8 / decision D-B8). Override at deploy
    time with ``cdk deploy -c domain_name=admin-dev.laboraid.app``.
    """

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def has_custom_domain(self) -> bool:
        """True when a custom domain is configured (ACM + Route53 + custom callbacks)."""
        return self.domain_name is not None

    @property
    def mandatory_tags(self) -> dict[str, str]:
        """The 13 mandatory tags applied to every resource (Spec/09 §2).

        ``Layer`` and ``DataClassification`` carry app-wide defaults here; stacks
        and tagged constructs override them per-resource with a more specific
        (higher-priority) scope.
        """
        return {
            "Project": "LaborAid-POC",
            "Customer": "LaborAid",
            "Environment": self.env,
            "ManagedBy": "CDK",
            "Repository": "github.com/NorthBay/laboraid-rate-engine",
            "CostCenter": "NBS-POC-2026",
            "Owner": "NBS-Engineering",
            "Layer": "shared",
            "SOW": "LaborAid-POC-SOW-v1",
            "AwsPartner": "NorthBay-Premier",
            "PublicUseCase": "true",
            "PII": "false",
            "DataClassification": "internal",
        }


def get_config(env: str) -> Config:
    """Return the `Config` for ``env`` (``"dev"`` | ``"prod"``).

    Imports are lazy to avoid a circular import: ``dev``/``prod`` import `Config`
    from this module.
    """
    if env == "dev":
        from laboraid_cdk.config.dev import CONFIG

        return CONFIG
    if env == "prod":
        from laboraid_cdk.config.prod import CONFIG

        return CONFIG
    raise ValueError(f"unknown env {env!r}; expected 'dev' or 'prod'")
