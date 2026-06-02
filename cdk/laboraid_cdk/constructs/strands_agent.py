"""`StrandsAgentRuntime` — AgentCore Runtime custom construct (Spec/09 §5.4).

CDK has no L2 construct for AgentCore Runtime yet, so this wraps a raw
``CfnResource`` of type ``AWS::BedrockAgentCore::Runtime``. POC deployment is
minimal: image URI + execution role + env vars + CloudWatch observability. The
v1.1+ Memory/Gateway/Identity/Policy blocks are intentionally omitted (Spec/09
§15).
"""

from __future__ import annotations

from aws_cdk import CfnResource, Tags
from aws_cdk import aws_iam as iam
from constructs import Construct


class StrandsAgentRuntime(Construct):
    """An ``AWS::BedrockAgentCore::Runtime`` for a single Strands agent."""

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
    ) -> None:
        super().__init__(scope, construct_id)

        self.resource = CfnResource(
            self,
            "Runtime",
            type="AWS::BedrockAgentCore::Runtime",
            properties={
                "AgentRuntimeName": runtime_name,
                "RuntimeImageUri": image_uri,
                "Environment": environment or {},
                "Observability": {"Enabled": True, "OtelEndpoint": "cloudwatch"},
                "ExecutionRoleArn": execution_role.role_arn,
                # v1.1+ (deferred per Spec/09 §15): Memory / Gateway / Identity / Policy.
            },
        )

        Tags.of(self).add("Layer", layer)
        Tags.of(self).add("AgentName", agent_name)

    @property
    def runtime_arn(self) -> str:
        """ARN of the AgentCore Runtime, for IAM grants + Step Functions wiring."""
        return self.resource.get_att("AgentRuntimeArn").to_string()
