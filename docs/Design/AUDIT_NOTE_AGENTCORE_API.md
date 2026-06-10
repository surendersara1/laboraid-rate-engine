# Audit Note — `StrandsAgentRuntime` API shape mismatch (post-FIX-B5 follow-up)

**Date:** 2026-06-03
**Severity:** **BLOCKER for `cdk deploy`** (synth passes; deploy will fail with `ValidationException`)
**Source:** AWS Bedrock AgentCore Control Plane API Reference, audited via the
AWS Documentation MCP server while authoring
[`F369_LLM_TEMPLATES/mlops/22b_agentcore_runtime_custom_resource.md`](../../F369_LLM_TEMPLATES/mlops/22b_agentcore_runtime_custom_resource.md).

---

## TL;DR

The FIX-B5 commit (`f4ed98a`) correctly moved away from the non-existent
`AWS::BedrockAgentCore::Runtime` CFN type to an `AwsCustomResource` invoking
the SDK directly. However, the **request body shape** passed to
`bedrock-agentcore:CreateAgentRuntime` doesn't match the published API contract.
`cdk synth` doesn't validate the body (it's an arbitrary dict), but
`cdk deploy` will fail when AWS rejects the request.

This is a follow-up fix to FIX-B5, not a regression — the audit pass identified
the problem class (no CFN type), and the fix correctly addressed that. The
**parameter shape** within the new pattern is a second-order issue surfaced only
when the partial was authored against the actual AWS API reference.

---

## What's wrong

**File:** [`cdk/laboraid_cdk/constructs/strands_agent.py`](../cdk/laboraid_cdk/constructs/strands_agent.py)

### Issue 1 — `runtimeImageUri` is not a top-level parameter

```python
# Current (lines 61-66):
parameters={
    "agentRuntimeName": runtime_name,
    "runtimeImageUri": image_uri,          # ❌ NOT a valid parameter
    "roleArn": execution_role.role_arn,
    "environmentVariables": env_vars,
    "observability": {"otelEndpoint": otel_endpoint},  # ❌ also invented
},
```

Per [`API_CreateAgentRuntime.html`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateAgentRuntime.html):

- `agentRuntimeArtifact` is **required** and is a **Union** type: must contain
  exactly one of `containerConfiguration` or `codeConfiguration`. The image URI
  lives inside: `agentRuntimeArtifact.containerConfiguration.containerUri`.
- `networkConfiguration` is **required**. Minimum: `{networkMode: "PUBLIC" | "VPC"}`.
- There is **no top-level `observability` parameter**. OTel emission is
  configured via env vars on the agent code itself.

### Issue 2 — Missing required `networkConfiguration`

The API will return `ValidationException` at deploy time because
`networkConfiguration` is omitted entirely.

### Issue 3 — `agentRuntimeName` constraint not enforced

Pattern is `[a-zA-Z][a-zA-Z0-9_]{0,47}` — **no hyphens.** The project naming
helper (`name(env, "l5", ...)`) produces kebab-case slugs like
`laboraid-prod-l5-agent-extractor`, which the API will reject.

### Issue 4 — Update parameters keyed on `agentRuntimeArn` reference

```python
# Current on_update (lines 70-79):
parameters={
    "agentRuntimeArn": cr.PhysicalResourceIdReference(),
    ...
}
```

`UpdateAgentRuntime` keys on `agentRuntimeId`, not `agentRuntimeArn`. Likewise
`DeleteAgentRuntime`. The CDK convention is to set the `physical_resource_id`
to the **ID** (not ARN); the construct's `runtime_arn` getter reads
`agentRuntimeArn` from the response field, but the lifecycle calls must use
`agentRuntimeId`.

### Issue 5 — `iam:PassRole` is unscoped

```python
# Current policy (lines 85-99):
actions=[..., "iam:PassRole"],
resources=["*"],
```

Should be scoped to `execution_role.role_arn` only, with a condition that
`iam:PassedToService = "bedrock-agentcore.amazonaws.com"`.

---

## What the fix looks like

The canonical shape lives in
[`F369_LLM_TEMPLATES/mlops/22b_agentcore_runtime_custom_resource.md`](../../F369_LLM_TEMPLATES/mlops/22b_agentcore_runtime_custom_resource.md) §3.

Surgical edits to apply:

### Edit 1 — Add a name normaliser + validator at construct entry

```python
# Pattern: [a-zA-Z][a-zA-Z0-9_]{0,47}. Kebab-case → underscore.
normalized = runtime_name.replace("-", "_")
if not normalized[:1].isalpha() or not all(
    c.isalnum() or c == "_" for c in normalized
) or len(normalized) > 48:
    raise ValueError(
        f"agentRuntimeName must match [a-zA-Z][a-zA-Z0-9_]{{0,47}}; "
        f"got {runtime_name!r} -> normalized={normalized!r}"
    )
```

### Edit 2 — Rebuild `parameters` for `on_create` per the verified shape

```python
on_create=cr.AwsSdkCall(
    service="bedrock-agentcore-control",   # NOT "bedrock-agentcore"
    action="CreateAgentRuntime",
    parameters={
        "agentRuntimeName": normalized,
        "agentRuntimeArtifact": {
            "containerConfiguration": {"containerUri": image_uri},
        },
        "networkConfiguration": {"networkMode": "PUBLIC"},  # POC default
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
```

### Edit 3 — Rebuild `on_update` to use `agentRuntimeId`

```python
on_update=cr.AwsSdkCall(
    service="bedrock-agentcore-control",
    action="UpdateAgentRuntime",
    parameters={
        "agentRuntimeId": cr.PhysicalResourceIdReference(),     # was: agentRuntimeArn
        "agentRuntimeArtifact": {
            "containerConfiguration": {"containerUri": image_uri},
        },
        "environmentVariables": env_vars,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "roleArn": execution_role.role_arn,
    },
    physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId"),
),
```

### Edit 4 — Rebuild `on_delete` likewise

```python
on_delete=cr.AwsSdkCall(
    service="bedrock-agentcore-control",
    action="DeleteAgentRuntime",
    parameters={"agentRuntimeId": cr.PhysicalResourceIdReference()},
),
```

### Edit 5 — Scope `iam:PassRole` to the execution role

```python
policy=cr.AwsCustomResourcePolicy.from_statements([
    iam.PolicyStatement(
        effect=iam.Effect.ALLOW,
        actions=[
            "bedrock-agentcore-control:CreateAgentRuntime",
            "bedrock-agentcore-control:UpdateAgentRuntime",
            "bedrock-agentcore-control:DeleteAgentRuntime",
            "bedrock-agentcore-control:GetAgentRuntime",
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
]),
```

### Edit 6 — Caller in `processing_stack.py` must pass a valid name

The caller currently passes whatever `name(env, "l5", "agent", "extractor")`
produces (kebab-case). With Edit 1, the construct itself normalises it.
Verify the resulting name is unique enough (`<env>_<...>_extractor` won't
collide because envs are different).

### Edit 7 — Add tests proving the new shape

`cdk/tests/test_strands_agent.py`:

```python
def test_create_call_uses_artifact_union():
    """on_create parameters must contain agentRuntimeArtifact.containerConfiguration,
    NOT a flat runtimeImageUri.
    """
    t = Template.from_stack(stack)
    custom_res = list(t.find_resources("Custom::AWS").values())[0]
    create_str = custom_res["Properties"]["Create"]
    assert '"agentRuntimeArtifact"' in str(create_str)
    assert '"containerConfiguration"' in str(create_str)
    assert '"runtimeImageUri"' not in str(create_str)  # the old wrong shape

def test_hyphenated_name_raises_value_error():
    with pytest.raises(ValueError, match=r"agentRuntimeName must match"):
        StrandsAgentRuntime(stack, "Runtime",
            runtime_name="my-kebab-case-name",  # pattern violation
            image_uri="x:y",
            execution_role=role,
        )
```

---

## Suggested fix commit

Run the FIX-pass runner with this prompt:

```
Read docs/AUDIT_NOTE_AGENTCORE_API.md and docs/AUDIT_REPORT.md (B5).
Apply edits 1-7 to cdk/laboraid_cdk/constructs/strands_agent.py and
cdk/tests/test_strands_agent.py. Run cdk synth + pytest. Commit as
[FIX-B5b] correct CreateAgentRuntime API shape per AWS docs reference.
Append a one-line entry to docs/BUILD_LOG.md. Do not stop for questions.
```

---

## References

- [`CreateAgentRuntime` API Reference](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateAgentRuntime.html)
- [`AgentRuntimeArtifact`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_AgentRuntimeArtifact.html)
- [`NetworkConfiguration`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_NetworkConfiguration.html)
- [`UpdateAgentRuntime`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_UpdateAgentRuntime.html)
- Canonical partial: [`F369_LLM_TEMPLATES/mlops/22b_agentcore_runtime_custom_resource.md`](../../F369_LLM_TEMPLATES/mlops/22b_agentcore_runtime_custom_resource.md)
