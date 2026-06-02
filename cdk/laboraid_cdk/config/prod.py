"""Prod (UAT) environment config (Spec/09 §0).

POC has no staging — production *is* the UAT environment. Account/region default
to the CDK ambient env vars; set ``CDK_DEFAULT_ACCOUNT`` before deploy.
"""

from __future__ import annotations

import os

from laboraid_cdk.config import Config

CONFIG = Config(
    env="prod",
    account=os.environ.get("CDK_DEFAULT_ACCOUNT", "000000000000"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    domain_name="admin.laboraid.app",
    alarm_email="laboraid-alerts@northbaysolutions.com",
    slack_webhook_secret_name="laboraid-prod-l6-secret-slack-webhook",
)
