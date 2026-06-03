"""`StrandsAgentRuntime` — AgentCore Runtime custom construct (Spec/09 §5.4).

CDK has no L1/L2 construct for AgentCore Runtime yet, and there is **no published
``AWS::BedrockAgentCore::Runtime`` CloudFormation resource type** — a raw
``CfnResource`` of that type synthesizes fine but fails ``cdk deploy`` with
``ResourceTypeNotFound`` (audit finding B5).

Per ``docs/AUDIT_DECISIONS.md`` D-B5 this construct provisions the runtime with an
``AwsCustomResource`` that calls ``bedrock-agentcore:CreateAgentRuntime`` on stack
create, ``UpdateAgentRuntime`` on update (rolled by a change to the image URI),
and ``DeleteAgentRuntime`` on stack destroy — staying entirely inside CDK so the
runtime is managed by the stack lifecycle.

TODO(when AWS ships ``AWS::BedrockAgentCore::Runtime`` L1): swap the
``AwsCustomResource`` for a native ``CfnResource``. Preserve the ``runtime_arn``
output contract so downstream stacks (``OrchestrationStack``) keep working
unchanged.

POC deployment is minimal: image URI + execution role + env vars + CloudWatch
observability. The v1.1+ Memory/Gateway/Identity/Policy blocks are intentionally
omitted (Spec/09 §15).
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

        env_vars = environment or {}

        # The runtime is created/updated/deleted through the bedrock-agentcore
        # control-plane SDK. UpdateAgentRuntime is keyed off the image URI so a new
        # image rolls the runtime automatically on the next deploy.
        self.resource = cr.AwsCustomResource(
            self,
            "Runtime",
            on_create=cr.AwsSdkCall(
                service="bedrock-agentcore",
                action="CreateAgentRuntime",
                parameters={
                    "agentRuntimeName": runtime_name,
                    "runtimeImageUri": image_uri,
                    "roleArn": execution_role.role_arn,
                    "environmentVariables": env_vars,
                    "observability": {"otelEndpoint": otel_endpoint},
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeArn"),
            ),
            on_update=cr.AwsSdkCall(
                service="bedrock-agentcore",
                action="UpdateAgentRuntime",
                parameters={
                    "agentRuntimeArn": cr.PhysicalResourceIdReference(),
                    "runtimeImageUri": image_uri,
                    "environmentVariables": env_vars,
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeArn"),
            ),
            on_delete=cr.AwsSdkCall(
                service="bedrock-agentcore",
                action="DeleteAgentRuntime",
                parameters={"agentRuntimeArn": cr.PhysicalResourceIdReference()},
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
                            "iam:PassRole",
                        ],
                        resources=["*"],
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
