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
from aws_cdk import aws_iam as iam
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
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

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
            mfa=cognito.Mfa.REQUIRED,
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
        self.user_pool_client = self.user_pool.add_client(
            "SpaClient",
            user_pool_client_name=name(config.env, "l1", "cognito", "spa-client"),
            generate_secret=False,  # public SPA client — no secret
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[f"https://{config.domain_name}/"],
                logout_urls=[f"https://{config.domain_name}/"],
            ),
        )

        # Hosted-UI domain (free amazoncognito.com prefix).
        self.user_pool_domain = self.user_pool.add_domain(
            "HostedUiDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"laboraid-{config.env}-auth"
            ),
        )

        # --- Foundational IAM roles (Spec/09 §7) ------------------------------
        # Shared API-Lambda execution role (§2.1). Specific S3/DDB/Bedrock grants
        # are added by the API stack against the concrete resources.
        self.api_lambda_role = iam.Role(
            self,
            "ApiLambdaRole",
            role_name=name(config.env, "l2", "role", "api-lambdas"),
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Shared execution role for L2 API Lambdas",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess"),
            ],
        )
        self.master_key.grant_encrypt_decrypt(self.api_lambda_role)

        # ExtractorAgent AgentCore execution role (§5.1, §7.2).
        self.agent_extractor_role = iam.Role(
            self,
            "AgentExtractorRole",
            role_name=name(config.env, "l5", "role", "agent-extractor"),
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="AgentCore execution role for the ExtractorAgent",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess"),
            ],
        )
        # Bedrock model access (Claude Sonnet 4.x + Haiku) for the fallback path.
        self.agent_extractor_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:{config.region}::foundation-model/anthropic.claude-*",
                ],
            )
        )
        self.master_key.grant_encrypt_decrypt(self.agent_extractor_role)

        # --- Cross-stack outputs ----------------------------------------------
        CfnOutput(self, "MasterKeyArn", value=self.master_key.key_arn)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
