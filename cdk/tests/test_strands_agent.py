"""Unit tests for the `StrandsAgentRuntime` construct's CreateAgentRuntime shape.

FIX-B5b: the request body passed to ``bedrock-agentcore-control:CreateAgentRuntime``
must match the published API contract — ``agentRuntimeArtifact`` (container union),
a required ``networkConfiguration``, lifecycle calls keyed on ``agentRuntimeId``,
and a name matching ``[a-zA-Z][a-zA-Z0-9_]{0,47}`` (no hyphens). See
``docs/AUDIT_NOTE_AGENTCORE_API.md``.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk import aws_iam as iam
from aws_cdk.assertions import Template

from laboraid_cdk.constructs.strands_agent import StrandsAgentRuntime


def _stack_with_role() -> tuple[cdk.Stack, iam.Role]:
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack")
    role = iam.Role(
        stack,
        "ExecRole",
        assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
    )
    return stack, role


def test_create_call_uses_artifact_union() -> None:
    """on_create parameters must contain agentRuntimeArtifact.containerConfiguration,
    NOT a flat runtimeImageUri.
    """
    stack, role = _stack_with_role()
    StrandsAgentRuntime(
        stack,
        "Runtime",
        runtime_name="laboraid-dev-l5-agent-extractor",
        image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/repo:latest",
        execution_role=role,
    )
    t = Template.from_stack(stack)
    custom_res = list(t.find_resources("Custom::AWS").values())[0]
    create_str = str(custom_res["Properties"]["Create"])
    assert '"agentRuntimeArtifact"' in create_str
    assert '"containerConfiguration"' in create_str
    assert '"runtimeImageUri"' not in create_str  # the old wrong shape


def test_create_call_has_required_network_configuration() -> None:
    """networkConfiguration is required by the API; omitting it fails at deploy."""
    stack, role = _stack_with_role()
    StrandsAgentRuntime(
        stack,
        "Runtime",
        runtime_name="laboraid-dev-l5-agent-extractor",
        image_uri="x:y",
        execution_role=role,
    )
    t = Template.from_stack(stack)
    custom_res = list(t.find_resources("Custom::AWS").values())[0]
    create_str = str(custom_res["Properties"]["Create"])
    assert '"networkConfiguration"' in create_str
    assert '"networkMode":"PUBLIC"' in create_str
    # Uses the bedrock-agentcore-control service, not the bare bedrock-agentcore.
    assert '"service":"bedrock-agentcore-control"' in create_str


def test_name_is_normalized_to_underscores() -> None:
    """Kebab-case names are normalised to the underscore-only API pattern."""
    stack, role = _stack_with_role()
    StrandsAgentRuntime(
        stack,
        "Runtime",
        runtime_name="laboraid-dev-l5-agent-extractor",
        image_uri="x:y",
        execution_role=role,
    )
    t = Template.from_stack(stack)
    custom_res = list(t.find_resources("Custom::AWS").values())[0]
    create_str = str(custom_res["Properties"]["Create"])
    assert "laboraid_dev_l5_agent_extractor" in create_str
    assert "laboraid-dev-l5-agent-extractor" not in create_str


def test_lifecycle_calls_key_on_runtime_id() -> None:
    """Update/Delete must key on agentRuntimeId, not agentRuntimeArn."""
    stack, role = _stack_with_role()
    StrandsAgentRuntime(
        stack,
        "Runtime",
        runtime_name="extractor_runtime",
        image_uri="x:y",
        execution_role=role,
    )
    t = Template.from_stack(stack)
    props = list(t.find_resources("Custom::AWS").values())[0]["Properties"]
    update_str, delete_str = str(props["Update"]), str(props["Delete"])
    assert '"agentRuntimeId"' in update_str
    assert '"agentRuntimeId"' in delete_str
    assert "agentRuntimeArn" not in update_str
    assert "agentRuntimeArn" not in delete_str


def test_pass_role_is_scoped_to_execution_role() -> None:
    """iam:PassRole must be scoped to the execution role with a service condition,
    not granted on '*'.
    """
    stack, role = _stack_with_role()
    StrandsAgentRuntime(
        stack,
        "Runtime",
        runtime_name="extractor_runtime",
        image_uri="x:y",
        execution_role=role,
    )
    t = Template.from_stack(stack)
    policies = t.find_resources("AWS::IAM::Policy")
    statements = [
        s for p in policies.values() for s in p["Properties"]["PolicyDocument"]["Statement"]
    ]
    pass_role = [s for s in statements if s.get("Action") == "iam:PassRole"]
    assert pass_role, "expected a dedicated iam:PassRole statement"
    (stmt,) = pass_role
    assert stmt["Resource"] != "*"
    assert stmt["Condition"] == {
        "StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}
    }


def test_overlong_name_raises_value_error() -> None:
    """Hyphens are normalised to underscores (not rejected), but the resulting name
    must still satisfy the <=48-char pattern.
    """
    stack, role = _stack_with_role()
    with pytest.raises(ValueError, match=r"agentRuntimeName must match"):
        StrandsAgentRuntime(
            stack,
            "Runtime",
            runtime_name="my-kebab-case-name" + "-x" * 30,  # >48 after normalising
            image_uri="x:y",
            execution_role=role,
        )


def test_name_starting_with_digit_raises() -> None:
    stack, role = _stack_with_role()
    with pytest.raises(ValueError, match=r"agentRuntimeName must match"):
        StrandsAgentRuntime(
            stack,
            "Runtime",
            runtime_name="9bad_start",
            image_uri="x:y",
            execution_role=role,
        )
