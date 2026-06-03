"""Dev environment config (Spec/09 §0).

Account/region default to the CDK ambient env vars so `cdk synth` works without
hardcoding an account. Set ``CDK_DEFAULT_ACCOUNT`` (or edit below) before deploy.
"""

from __future__ import annotations

import os

from laboraid_cdk.config import Config

CONFIG = Config(
    env="dev",
    account=os.environ.get("CDK_DEFAULT_ACCOUNT", "000000000000"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    alarm_email="laboraid-alerts@northbaysolutions.com",
    slack_webhook_secret_name="laboraid-dev-l6-secret-slack-webhook",
    # No custom domain by default — UI + Cognito callbacks use the CloudFront
    # default domain (audit B8 / decision D-B8). Override at deploy:
    #   npx cdk deploy -c domain_name=admin-dev.laboraid.app
    domain_name=None,
)
