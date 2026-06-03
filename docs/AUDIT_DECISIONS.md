# Audit fix — architectural decisions

Three blocker findings require an architectural call (cannot be mechanically fixed
from the audit alone). These calls are made here so the fix runner can execute
without stopping for clarification.

---

## D-B5. AgentCore Runtime provisioning — no CFN type exists

**Audit finding (B5):** `cdk/laboraid_cdk/constructs/strands_agent.py:34-46` synthesizes
`AWS::BedrockAgentCore::Runtime`. This is not a real CloudFormation type;
`cdk synth` accepts it (raw `CfnResource` is not validated at synth time) but
`cdk deploy` will fail with `ResourceTypeNotFound`.

**Decision: replace with `AwsCustomResource`** that calls
`bedrock-agentcore:CreateAgentRuntime` directly via the AWS SDK. Stay in CDK —
do NOT split into a separate CLI-driven deploy script.

**Why:**
- Single-source IaC (no parallel deploy path)
- Stack-lifecycle managed (delete-on-stack-destroy works)
- Easy to retrofit when AWS ships the L1 — drop in CfnResource, retain ARN output

**Shape of the fix:**
```python
# cdk/laboraid_cdk/constructs/strands_agent.py
from aws_cdk import custom_resources as cr, aws_iam as iam, RemovalPolicy

class StrandsAgentRuntime(Construct):
    def __init__(self, scope, construct_id, *, runtime_name, runtime_image_uri,
                 role_arn, env_vars=None, otel_endpoint="cloudwatch", **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # AwsCustomResource calls bedrock-agentcore:CreateAgentRuntime on stack create,
        # DeleteAgentRuntime on stack destroy. UpdateAgentRuntime triggered by hash of
        # runtime_image_uri so a new image rolls automatically.
        cr_provider = cr.AwsCustomResource(
            self, "AgentRuntimeCR",
            on_create=cr.AwsSdkCall(
                service="bedrock-agentcore",
                action="CreateAgentRuntime",
                parameters={
                    "agentRuntimeName": runtime_name,
                    "runtimeImageUri": runtime_image_uri,
                    "roleArn": role_arn,
                    "environmentVariables": env_vars or {},
                    "observability": {"otelEndpoint": otel_endpoint},
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeArn"),
            ),
            on_update=cr.AwsSdkCall(
                service="bedrock-agentcore",
                action="UpdateAgentRuntime",
                parameters={
                    "agentRuntimeArn": cr.PhysicalResourceIdReference(),
                    "runtimeImageUri": runtime_image_uri,
                    "environmentVariables": env_vars or {},
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeArn"),
            ),
            on_delete=cr.AwsSdkCall(
                service="bedrock-agentcore",
                action="DeleteAgentRuntime",
                parameters={"agentRuntimeArn": cr.PhysicalResourceIdReference()},
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=[
                        "bedrock-agentcore:CreateAgentRuntime",
                        "bedrock-agentcore:UpdateAgentRuntime",
                        "bedrock-agentcore:DeleteAgentRuntime",
                        "bedrock-agentcore:GetAgentRuntime",
                        "iam:PassRole",
                    ],
                    resources=["*"],
                ),
            ]),
        )
        self.runtime_arn = cr_provider.get_response_field("agentRuntimeArn")

    # Docstring MUST include:
    # TODO(when AWS ships AWS::BedrockAgentCore::Runtime L1): swap AwsCustomResource
    # for a native CfnResource. Preserve runtime_arn output contract so downstream
    # stacks (OrchestrationStack) keep working unchanged.
```

**Acceptance:** `cdk synth` still green; lambda calling the construct gets
`runtime_arn` from the standard CDK output; deletion of the stack removes the
agent runtime cleanly.

---

## D-B7. Agent container boot — `app.run()` is gated by `if __name__ == "__main__":`

**Audit finding (B7):** `agents/extractor/agent.py:197-214` wraps `app.run()` in
`if __name__ == "__main__":`. AgentCore Runtime imports `agent.py` as a module
(it does NOT execute it as `__main__`), so `app.run()` is never called and the
container exits immediately.

**Decision: move `app.run()` OUT of the `__main__` guard.** Always call it at
module import time when the AgentCore SDK is importable. Keep no `__main__`
guard — local dev uses `python agent.py` which also triggers module-level
execution.

**Why:**
- Smallest blast radius — single-file edit, no Dockerfile change
- Matches AgentCore convention (the framework imports the user module and
  expects the entrypoint to already be registered AND running)
- Avoids the alternative (changing `Dockerfile` `CMD` to `python -m agent`)
  which is more fragile and harder to debug locally

**Shape of the fix:**
```python
# agents/extractor/agent.py — end of file

try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload, context):
        # ... existing entrypoint body ...
        return {"status": "ok", "result": result}

    # Run unconditionally when the SDK is importable.
    # AgentCore loads this module on container start; we MUST be listening here,
    # not gated by __name__ == "__main__" (which never holds in the container).
    app.run()
except ImportError:
    # Local dev without the SDK installed — agent runs as a callable tool only.
    # The Strands @tool functions remain importable for unit tests.
    pass
```

**Acceptance:** `docker run --rm <image>` keeps the container alive listening
on the AgentCore-Runtime invoke port. Unit tests in `agents/extractor/tests/`
still import the file cleanly without the SDK installed.

---

## D-B8. UI domain — hardcoded `admin-{env}.laboraid.app` with no Route53 zone

**Audit finding (B8):** Configs hardcode `admin-dev.laboraid.app` /
`admin.laboraid.app`; `UiStack` accepts an optional `hosted_zone` but
`app.py` doesn't pass one; cert + Route53 are silently skipped; Cognito
callback URLs point at an unresolvable DNS name.

**Decision: make `domain_name` OPTIONAL in `Config`, default to None.** When
None, all custom-domain wiring (ACM cert, Route53 record, Cognito custom
callback) is skipped. UiStack falls back to the CloudFront default domain
(`*.cloudfront.net`); Cognito callbacks use the CloudFront default too. When
set, look up the existing hosted zone via `HostedZone.from_lookup(...)` and
provision the cert + record + callbacks.

**Why:**
- POC ships with no custom domain — `.cloudfront.net` works for demo
- Prod sets the domain at deploy time with `-c domain_name=admin.laboraid.app`,
  no code change
- Cognito callback URLs computed from `domain_name OR cloudfront_default_url`
  so a deploy never produces a broken auth flow

**Shape of the fix:**
```python
# cdk/laboraid_cdk/config/__init__.py
@dataclass(frozen=True, slots=True)
class Config:
    env: str
    account: str
    region: str
    # ... existing fields ...
    domain_name: str | None = None   # if None, use CloudFront default *.cloudfront.net

    @property
    def has_custom_domain(self) -> bool:
        return self.domain_name is not None
```

```python
# cdk/laboraid_cdk/config/dev.py
dev_cfg = Config(env="dev", account="...", region="...", domain_name=None)
# Override at deploy:  npx cdk deploy -c domain_name=admin-dev.laboraid.app
```

```python
# cdk/laboraid_cdk/stacks/ui_stack.py — UiStack.__init__
if config.has_custom_domain:
    hosted_zone = route53.HostedZone.from_lookup(self, "Zone", domain_name=config.domain_name.split(".", 1)[1])
    cert = acm.Certificate(self, "Cert", domain_name=config.domain_name,
                            validation=acm.CertificateValidation.from_dns(hosted_zone))
    distribution = cloudfront.Distribution(self, "Dist", ...,
                            domain_names=[config.domain_name], certificate=cert)
    route53.ARecord(self, "Alias", zone=hosted_zone, record_name=config.domain_name,
                    target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(distribution)))
    self.app_url = f"https://{config.domain_name}"
else:
    distribution = cloudfront.Distribution(self, "Dist", ...)
    self.app_url = f"https://{distribution.domain_name}"
```

```python
# cdk/laboraid_cdk/stacks/security_stack.py — Cognito callback URLs read from
# ui_stack.app_url instead of from config.domain_name directly.
# Pass ui_stack.app_url into SecurityStack via cross-stack ref OR compute the
# CloudFront default URL up-front and pass it into both stacks.
```

**Acceptance:** With `domain_name=None`, `cdk deploy` succeeds on a fresh
account with no Route53 zone; Cognito callbacks land at the CloudFront URL;
sign-in flow works end-to-end. With `-c domain_name=...`, the existing zone
lookup succeeds and the full custom-domain wiring works.

---

## Notes for the runner

- Each decision is paired with a "Shape of the fix" code skeleton — treat it
  as the canonical reference; deviate only if Spec/09 or BUILD_INSTRUCTIONS
  contradicts.
- The decisions are written here so the fix-pass runner does NOT stop to ask
  clarification. Make these calls verbatim; if a downstream blocker reveals
  a fundamental issue, log it in BUILD_LOG.md and proceed.
- The companion file `docs/AUDIT_FIX_PROMPT.md` defines the FIX queue order
  and per-fix workflow. Read it after this one.
