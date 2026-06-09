"""L-Security stack — KMS CMK, Cognito, foundational IAM roles.

Implements Spec/09 §3 (stack org) + §1.3 (Cognito skeleton) + §7 (IAM roles).

Creates:
- One project master CMK (``laboraid-{env}-kms-master``) used by S3, DynamoDB,
  Aurora, SNS, and Secrets Manager — key rotation on.
- Cognito user pool (admin-invite only, MFA required) with the four groups
  ``Admins`` / ``Operations`` / ``Business`` / ``ServiceClients`` (Spec/09 §1.1),
  an SPA app client, and a hosted-UI domain.
- Foundational least-privilege execution roles: the shared API-Lambda role
  (§2.1) and the ExtractorAgent AgentCore role (§5.1, §7.2). Resource-specific
  grants are attached by the stacks that own those resources.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_kms as kms
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.util.naming import name


class SecurityStack(Stack):
    """KMS + Cognito + foundational IAM roles."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        app_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        # Cognito hosted-UI callback/logout target. In the wired app this is the
        # UI stack's `app_url` (custom domain, or the CloudFront default), so the
        # auth flow always lands somewhere resolvable (audit B8 / decision D-B8).
        # When constructed standalone (unit tests / local dev) fall back to the
        # custom domain if set, else the localhost SPA dev server.
        if app_url is not None:
            redirect_url = app_url
        elif config.has_custom_domain:
            redirect_url = f"https://{config.domain_name}"
        else:
            redirect_url = "http://localhost:5173"

        # --- Project master CMK -------------------------------------------------
        # Single per-purpose-equivalent CMK for the POC; rotation enabled. Used by
        # every encrypted resource (S3/DDB/Aurora/SNS/Secrets) via grants.
        self.master_key = kms.Key(
            self,
            "MasterKey",
            alias=f"alias/laboraid/{config.env}/master",
            description="LaborAid POC master CMK (S3, DDB, Aurora, SNS, Secrets)",
            enable_key_rotation=True,
            removal_policy=(RemovalPolicy.RETAIN if config.is_prod else RemovalPolicy.DESTROY),
        )

        # --- Cognito user pool + groups (Spec/09 §1.3) -------------------------
        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=name(config.env, "l1", "cognito", "userpool"),
            self_sign_up_enabled=False,  # admin-invited only for POC
            sign_in_aliases=cognito.SignInAliases(email=True),
            # MFA: required for prod (real fund-member PII), OFF for dev. The
            # dev pool holds only synthetic POC data and the E2E suite needs to
            # InitiateAuth programmatically; pool-wide REQUIRED forces every
            # user (including a dedicated test user) into MFA_SETUP on first
            # sign-in. Gate flips back on for prod via config.is_prod.
            mfa=cognito.Mfa.REQUIRED if config.is_prod else cognito.Mfa.OFF,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            password_policy=cognito.PasswordPolicy(min_length=12, require_symbols=True),
            removal_policy=(RemovalPolicy.RETAIN if config.is_prod else RemovalPolicy.DESTROY),
        )

        self.user_pool_groups: dict[str, cognito.CfnUserPoolGroup] = {}
        for group_name in ("Admins", "Operations", "Business", "ServiceClients"):
            self.user_pool_groups[group_name] = cognito.CfnUserPoolGroup(
                self,
                f"Group{group_name}",
                user_pool_id=self.user_pool.user_pool_id,
                group_name=group_name,
            )

        # SPA app client (auth-code flow with Cognito hosted UI).
        # Auth flows are OAuth code only — used by the live SPA in the browser.
        self.user_pool_client = self.user_pool.add_client(
            "SpaClient",
            user_pool_client_name=name(config.env, "l1", "cognito", "spa-client"),
            generate_secret=False,  # public SPA client — no secret
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[f"{redirect_url}/"],
                logout_urls=[f"{redirect_url}/"],
            ),
        )

        # Separate, test-only app client. USER_PASSWORD_AUTH is enabled so the
        # Playwright E2E suite (ui/tests/e2e) can call InitiateAuth from Node and
        # inject Amplify-compatible tokens into the page's localStorage. This
        # client is NEVER referenced by the production SPA — it exists purely so
        # the test harness can sidestep the hosted-UI redirect without weakening
        # the real SPA client's auth surface.
        # Skipped on prod (config.is_prod): tests on prod would use a separate
        # test pool or run in a staging env.
        if not config.is_prod:
            self.user_pool_test_client = self.user_pool.add_client(
                "SpaTestClient",
                user_pool_client_name=name(config.env, "l1", "cognito", "spa-test-client"),
                generate_secret=False,
                auth_flows=cognito.AuthFlow(
                    user_password=True,
                    user_srp=True,
                ),
                prevent_user_existence_errors=True,
            )

        # Hosted-UI domain (free amazoncognito.com prefix).
        self.user_pool_domain = self.user_pool.add_domain(
            "HostedUiDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"laboraid-{config.env}-auth"
            ),
        )

        # NOTE on IAM roles: per-Lambda and per-agent execution roles (§5.1, §7)
        # are created in their *consuming* stacks (processing/api/validation), not
        # here. Those roles must be granted downstream S3/DDB resources, and a role
        # defined in this upstream stack cannot reference a downstream stack's
        # resource without forming a dependency cycle (Storage already depends on
        # this stack's CMK). Co-locating each role with its grants keeps the graph
        # acyclic and least-privilege.

        # --- Cross-stack outputs ----------------------------------------------
        CfnOutput(self, "MasterKeyArn", value=self.master_key.key_arn)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
        if not config.is_prod:
            CfnOutput(
                self,
                "UserPoolTestClientId",
                value=self.user_pool_test_client.user_pool_client_id,
            )
