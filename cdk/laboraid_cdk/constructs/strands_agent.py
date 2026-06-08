"""`StrandsAgentRuntime` — AgentCore Runtime custom construct (Spec/09 §5.4).

CDK has no L1/L2 construct for AgentCore Runtime yet, and there is **no published
``AWS::BedrockAgentCore::Runtime`` CloudFormation resource type** — a raw
``CfnResource`` of that type synthesizes fine but fails ``cdk deploy`` with
``ResourceTypeNotFound`` (audit finding B5).

Per ``docs/AUDIT_DECISIONS.md`` D-B5 this construct provisions the runtime with an
``AwsCustomResource`` that calls ``bedrock-agentcore-control:CreateAgentRuntime`` on
stack create, ``UpdateAgentRuntime`` on update (rolled by a change to the image URI),
and ``DeleteAgentRuntime`` on stack destroy — staying entirely inside CDK so the
runtime is managed by the stack lifecycle.

The request shape follows the published AgentCore control-plane API contract
(FIX-B5b): ``agentRuntimeArtifact`` is the required ``containerConfiguration``
union member, ``networkConfiguration`` is required, and the lifecycle calls key on
``agentRuntimeId`` (not the ARN). See the canonical partial
``F369_LLM_TEMPLATES/mlops/22b_agentcore_runtime_custom_resource.md`` §3 and
``docs/AUDIT_NOTE_AGENTCORE_API.md``.

TODO(when AWS ships ``AWS::BedrockAgentCore::Runtime`` L1): swap the
``AwsCustomResource`` for a native ``CfnResource``. Preserve the ``runtime_arn``
output contract so downstream stacks (``OrchestrationStack``) keep working
unchanged.

POC deployment is minimal: image URI + execution role + env vars + a ``PUBLIC``
network mode. OTel/CloudWatch emission is configured by the agent code via env
vars (there is no top-level ``observability`` API parameter). The v1.1+
Memory/Gateway/Identity/Policy blocks are intentionally omitted (Spec/09 §15).
"""

from __future__ import annotations

from aws_cdk import Tags
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct


class StrandsAgentRuntime(Construct):
    """An AgentCore Runtime for a single Strands agent, via ``AwsCustomResource``."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runtime_name: str,
        image_uri: str,
        execution_role: iam.IRole,
        environment: dict[str, str] | None = None,
        agent_name: str = "ExtractorAgent",
        layer: str = "l5",
        otel_endpoint: str = "cloudwatch",
    ) -> None:
        super().__init__(scope, construct_id)

        # CreateAgentRuntime's agentRuntimeName must match [a-zA-Z][a-zA-Z0-9_]{0,47}
        # — no hyphens. The project naming helper emits kebab-case slugs, so
        # normalise to underscores here and validate before the API rejects it at
        # deploy time (FIX-B5b).
        normalized = runtime_name.replace("-", "_")
        if (
            not normalized[:1].isalpha()
            or not all(c.isalnum() or c == "_" for c in normalized)
            or len(normalized) > 48
        ):
            raise ValueError(
                f"agentRuntimeName must match [a-zA-Z][a-zA-Z0-9_]{{0,47}}; "
                f"got {runtime_name!r} -> normalized={normalized!r}"
            )

        # OTel/CloudWatch emission is driven by an env var on the agent code; there
        # is no top-level observability API parameter. A caller-supplied value wins.
        env_vars = {"OTEL_ENDPOINT": otel_endpoint, **(environment or {})}

        # The runtime is created/updated/deleted through the bedrock-agentcore
        # control-plane SDK. UpdateAgentRuntime is keyed off the image URI so a new
        # image rolls the runtime automatically on the next deploy.
        self.resource = cr.AwsCustomResource(
            self,
            "Runtime",
            on_create=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="CreateAgentRuntime",
                parameters={
                    "agentRuntimeName": normalized,
                    "agentRuntimeArtifact": {
                        "containerConfiguration": {"containerUri": image_uri},
                    },
                    "networkConfiguration": {"networkMode": "PUBLIC"},
                    "roleArn": execution_role.role_arn,
                    "environmentVariables": env_vars,
                    "protocolConfiguration": {"serverProtocol": "HTTP"},
                    "lifecycleConfiguration": {
                        "idleRuntimeSessionTimeout": 900,
                        "maxLifetime": 28800,
                    },
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId"),
            ),
            on_update=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="UpdateAgentRuntime",
                parameters={
                    "agentRuntimeId": cr.PhysicalResourceIdReference(),
                    "agentRuntimeArtifact": {
                        "containerConfiguration": {"containerUri": image_uri},
                    },
                    "environmentVariables": env_vars,
                    "networkConfiguration": {"networkMode": "PUBLIC"},
                    "roleArn": execution_role.role_arn,
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId"),
            ),
            on_delete=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="DeleteAgentRuntime",
                parameters={"agentRuntimeId": cr.PhysicalResourceIdReference()},
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=[
                            "bedrock-agentcore:CreateAgentRuntime",
                            "bedrock-agentcore:UpdateAgentRuntime",
                            "bedrock-agentcore:DeleteAgentRuntime",
                            "bedrock-agentcore:GetAgentRuntime",
                        ],
                        resources=["*"],
                    ),
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["iam:PassRole"],
                        resources=[execution_role.role_arn],
                        conditions={
                            "StringEquals": {
                                "iam:PassedToService": "bedrock-agentcore.amazonaws.com"
                            }
                        },
                    ),
                ]
            ),
            # bedrock-agentcore is newer than Lambda's bundled AWS SDK, so the
            # custom-resource handler must pull the latest SDK to find the API.
            install_latest_aws_sdk=True,
        )

        Tags.of(self).add("Layer", layer)
        Tags.of(self).add("AgentName", agent_name)

    @property
    def runtime_arn(self) -> str:
        """ARN of the AgentCore Runtime, for IAM grants + Step Functions wiring."""
        return self.resource.get_response_field("agentRuntimeArn")
