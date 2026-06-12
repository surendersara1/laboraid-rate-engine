"""L2 API stack — HTTP API Gateway + Cognito authorizer + WAF (Spec/09 §4 L2).

Creates the 19 API Lambdas (E.1 admin + E.2 business/shared), an HTTP API with a
Cognito user-pool JWT authorizer on every route, the per-route wiring of §2.2,
and a regional WAF Web ACL (AWS managed common rules + rate limit) associated
with the API stage.

Per-route *group* authorization (Admins/Operations/Business) is enforced inside
each Lambda from the ``cognito:groups`` JWT claim; the authorizer here enforces
authentication. POC scope (Spec/09 §4 L1 §1.4/1.5).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.constructs.tagged_lambda import TaggedLambda
from laboraid_cdk.util.naming import name

# (method, path, lambda-dir). Some routes share a Lambda (e.g. profile-list).
ROUTES: list[tuple[str, str, str]] = [
    ("POST", "/v1/uploads", "upload-presign"),
    ("GET", "/v1/jobs", "job-list"),
    ("GET", "/v1/jobs/{id}", "job-status"),
    ("POST", "/v1/jobs/{id}/retry", "job-retry"),
    ("POST", "/v1/jobs/{id}/abort", "job-abort"),
    ("GET", "/v1/agents", "agent-list"),
    ("PATCH", "/v1/agents/{name}", "agent-toggle"),
    ("GET", "/v1/unions", "profile-list"),
    ("GET", "/v1/unions/{local}/profile", "profile-list"),
    ("PUT", "/v1/unions/{local}/profile", "profile-update"),
    ("GET", "/v1/unions/{local}/rate-sheets", "ratesheet-list"),
    ("GET", "/v1/unions/{local}/rate-sheets/{period}", "ratesheet-get"),
    ("POST", "/v1/unions/{local}/rate-sheets/{period}/approve", "ratesheet-approve"),
    ("POST", "/v1/unions/{local}/rate-sheets/{period}/reject", "ratesheet-reject"),
    ("POST", "/v1/unions/{local}/rate-sheets/{period}/unapprove", "ratesheet-unapprove"),
    ("POST", "/v1/unions/{local}/rate-sheets/{period}/publish", "ratesheet-publish"),
    ("GET", "/v1/unions/{local}/rate-sheets/{period}/audit", "ratesheet-audit"),
    ("POST", "/v1/unions/{local}/rate-sheets/{period}/rework", "ratesheet-rework"),
    ("POST", "/v1/cells/{cell_id}/override", "cell-override"),
    ("POST", "/v1/cells/{cell_id}/comment", "cell-comment"),
    ("GET", "/v1/audit", "audit-list"),
    ("POST", "/v1/batches/process", "batch-process"),
]

# Lambda dir -> resource categories it needs grants for. The "events" category
# grants PutEvents on the engine bus (the approve/reject/unapprove workflow fns
# emit rate-sheet lifecycle events — audit B2).
GRANTS: dict[str, set[str]] = {
    "upload-presign": {"inputs"},
    "job-list": {"jobs"},
    "job-status": {"jobs"},
    "job-retry": {"jobs"},
    "job-abort": {"jobs"},
    "agent-list": {"agents"},
    "agent-toggle": {"agents"},
    # Profiles read/write Aurora unions.profile_yaml (system of record).
    "profile-list": {"aurora"},
    "profile-update": {"aurora"},
    "audit-list": {"aurora"},
    "ratesheet-list": {"aurora"},
    "ratesheet-get": {"aurora"},
    "ratesheet-approve": {"aurora", "events"},
    "ratesheet-reject": {"aurora", "events"},
    "ratesheet-unapprove": {"aurora", "events"},
    "ratesheet-publish": {"aurora"},
    "ratesheet-audit": {"aurora"},
    # Rework needs Aurora (rate_periods + rate_cells + audit_log), the
    # overrides DDB table (read the user's manual overrides), events (emit
    # the lifecycle event), and invoke permission on the xlsx renderer to
    # regenerate the v2 spreadsheet.
    "ratesheet-rework": {
        "aurora",
        "overrides",
        "events",
        "invoke-renderers",
        "invoke-extractor-agent",
    },
    "cell-override": {"overrides"},
    "cell-comment": {"aurora"},
    # batch-process starts the main Step Functions pipeline.
    "batch-process": {"states"},
}


class ApiStack(Stack):
    """HTTP API + Cognito authorizer + WAF + the 19 API Lambdas."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        user_pool: cognito.IUserPool,
        user_pool_client: cognito.IUserPoolClient,
        user_pool_test_client: cognito.IUserPoolClient | None,
        inputs_bucket: s3.IBucket,
        jobs_table: ddb.ITable,
        agent_config_table: ddb.ITable,
        overrides_table: ddb.ITable,
        aurora: rds.IDatabaseCluster,
        aurora_secret: secretsmanager.ISecret,
        engine_bus: events.IEventBus,
        xlsx_renderer: lambda_.IFunction,
        outputs_bucket: s3.IBucket,
        extractor_runtime_arn: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env

        # Shared authz layer (`/opt/python/authz.py`) imported by every gated
        # handler for per-route Cognito group enforcement (audit B3).
        authz_layer = lambda_.LayerVersion(
            self,
            "AuthzLayer",
            layer_version_name=name(env, "l2", "layer", "authz"),
            code=lambda_.Code.from_asset("../lambdas/api/_shared"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.ARM_64],
            description="Shared per-route Cognito group authorization helper.",
        )

        common_env = {
            "INPUTS_BUCKET": inputs_bucket.bucket_name,
            "JOBS_TABLE": jobs_table.table_name,
            "AGENT_CONFIG_TABLE": agent_config_table.table_name,
            "OVERRIDES_TABLE": overrides_table.table_name,
            "AURORA_CLUSTER_ARN": aurora.cluster_arn,
            "AURORA_SECRET_ARN": aurora_secret.secret_arn,
            "ENGINE_BUS_NAME": engine_bus.event_bus_name,
            "OUTPUTS_BUCKET": outputs_bucket.bucket_name,
            "XLSX_RENDERER_FN": xlsx_renderer.function_name,
            "EXTRACTOR_RUNTIME_ARN": extractor_runtime_arn or "",
        }

        # --- Create one Lambda per unique dir, applying its grants -------------
        # Per-route extras: routes that need longer execution time than the
        # 30s default (e.g. rework[mode=ai] synchronously invokes the agent).
        per_fn_timeout: dict[str, Duration] = {
            "ratesheet-rework": Duration.minutes(5),
        }

        self.functions: dict[str, TaggedLambda] = {}
        for key in {dir_ for _, _, dir_ in ROUTES}:
            extra_kwargs: dict[str, Any] = {}
            if key in per_fn_timeout:
                extra_kwargs["timeout"] = per_fn_timeout[key]
            fn = TaggedLambda(
                self,
                _pascal(key),
                env=env,
                layer="l2",
                function_name=name(env, "l2", "fn", key),
                handler="handler.handler",
                code=lambda_.Code.from_asset(f"../lambdas/api/{key}"),
                environment=dict(common_env),
                layers=[authz_layer],
                **extra_kwargs,
            )
            cats = GRANTS[key]
            if "inputs" in cats:
                inputs_bucket.grant_put(fn)
            if "jobs" in cats:
                jobs_table.grant_read_write_data(fn)
            if "agents" in cats:
                agent_config_table.grant_read_write_data(fn)
            if "overrides" in cats:
                overrides_table.grant_read_write_data(fn)
            if "aurora" in cats:
                aurora.grant_data_api_access(fn)
                aurora_secret.grant_read(fn)
            if "events" in cats:
                engine_bus.grant_put_events_to(fn)
            if "states" in cats:
                # Start the main pipeline. The SFN lives in the Orchestration
                # stack (created after Api), so reference it by its deterministic
                # ARN to avoid a cross-stack cycle.
                sfn_arn = (
                    f"arn:aws:states:{config.region}:{Stack.of(self).account}"
                    f":stateMachine:{name(env, 'l3', 'sfn', 'main')}"
                )
                fn.add_environment("STATE_MACHINE_ARN", sfn_arn)
                fn.add_to_role_policy(
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["states:StartExecution", "states:DescribeExecution"],
                        resources=[
                            sfn_arn,
                            f"arn:aws:states:{config.region}:{Stack.of(self).account}"
                            f":execution:{name(env, 'l3', 'sfn', 'main')}:*",
                        ],
                    )
                )
            if "invoke-renderers" in cats:
                xlsx_renderer.grant_invoke(fn)
                # Rework reads parent CSV from outputs, writes patched v2 CSV
                # back, then HEAD-checks both the CSV + the xlsx output.
                outputs_bucket.grant_read_write(fn)
            if "invoke-extractor-agent" in cats and extractor_runtime_arn:
                fn.add_to_role_policy(
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["bedrock-agentcore:InvokeAgentRuntime"],
                        resources=[
                            extractor_runtime_arn,
                            extractor_runtime_arn + "/*",
                        ],
                    )
                )
                # API Gateway HTTP API caps integration timeout at 29s; the
                # AI rework path takes ~60s. The handler self-dispatches via
                # `InvocationType: Event` on its own ARN to release the
                # synchronous request, then completes the work in the
                # background. Grant that loopback.
                fn.add_to_role_policy(
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["lambda:InvokeFunction"],
                        resources=[fn.function_arn],
                    )
                )
            self.functions[key] = fn

        # --- HTTP API + Cognito authorizer ------------------------------------
        # Both the live SPA client and the E2E test-only client are allowed.
        # The authorizer just validates issuer + aud; accepting two clients
        # means Playwright can sign in via USER_PASSWORD_AUTH against the test
        # client without weakening the production SPA client's auth flows.
        allowed_clients = [user_pool_client]
        if user_pool_test_client is not None:
            allowed_clients.append(user_pool_test_client)
        authorizer = authorizers.HttpUserPoolAuthorizer(
            "CognitoAuthorizer",
            user_pool,
            user_pool_clients=allowed_clients,
        )
        self.http_api = apigw.HttpApi(
            self,
            "HttpApi",
            api_name=name(env, "l2", "apigw", "main"),
            default_authorizer=authorizer,
        )
        for method, path, key in ROUTES:
            self.http_api.add_routes(
                path=path,
                methods=[apigw.HttpMethod(method)],
                integration=integrations.HttpLambdaIntegration(
                    f"Int{_pascal(key)}{method}{_slug(path)}",
                    self.functions[key],
                ),
            )

        # --- WAF (regional, managed common rules + rate limit) ----------------
        self.web_acl = wafv2.CfnWebACL(
            self,
            "ApiWaf",
            name=name(env, "l2", "waf", "api"),
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=name(env, "l2", "waf", "api"),
                sampled_requests_enabled=True,
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSCommonRules",
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=(
                            wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS", name="AWSManagedRulesCommonRuleSet"
                            )
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="common-rules",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimit",
                    priority=2,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000, aggregate_key_type="IP"
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="rate-limit",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )
        # WAFv2 cannot associate directly with API Gateway v2 (HTTP API) — the
        # supported list is REST API, CloudFront, ALB, AppSync, App Runner, and
        # Cognito User Pool. Iter 7 confirmed this with a 400 from CFN at the
        # ApiWafAssociation step. The WebACL is left as a standalone resource so
        # v1.1 (CloudFront in front of HTTP API) can attach it without rework.
        # POC security is enforced by the Cognito JWT authorizer above plus IAM.

        CfnOutput(self, "ApiEndpoint", value=self.http_api.api_endpoint)


def _pascal(slug: str) -> str:
    return "".join(p.capitalize() for p in slug.replace("/", "-").split("-"))


def _slug(path: str) -> str:
    return "".join(c for c in path.title() if c.isalnum())
