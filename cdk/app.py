#!/usr/bin/env python3
"""CDK app entry point for the LaborAid Rate Engine POC (Spec/09 §3, §11).

Instantiates the eight stacks (added as build groups B–F land) and applies the
mandatory-tags Aspect at app level so every resource inherits the 13 tags.

Select the environment with CDK context: ``cdk synth -c env=prod`` (default dev).
"""

from __future__ import annotations

from dataclasses import replace

import aws_cdk as cdk

from laboraid_cdk.aspects.mandatory_tags import MandatoryTagsAspect
from laboraid_cdk.config import get_config
from laboraid_cdk.stacks.ai_stack import AiStack
from laboraid_cdk.stacks.api_stack import ApiStack
from laboraid_cdk.stacks.observability_stack import ObservabilityStack
from laboraid_cdk.stacks.orchestration_stack import OrchestrationStack
from laboraid_cdk.stacks.processing_stack import ProcessingStack
from laboraid_cdk.stacks.security_stack import SecurityStack
from laboraid_cdk.stacks.storage_stack import StorageStack
from laboraid_cdk.stacks.ui_stack import UiStack
from laboraid_cdk.stacks.validation_stack import ValidationStack

app = cdk.App()

env_name = str(app.node.try_get_context("env") or "dev")
config = get_config(env_name)

# Optional custom-domain override (audit B8 / decision D-B8). Default is no custom
# domain (CloudFront default *.cloudfront.net). At deploy on the account that owns
# the Route53 zone:  npx cdk deploy -c domain_name=admin-dev.laboraid.app
domain_override = app.node.try_get_context("domain_name")
if domain_override:
    config = replace(config, domain_name=str(domain_override))

# Stacks are environment-agnostic so `cdk synth` works without AWS credentials.
# Deploy binds them to a concrete account/region via `CDK_DEFAULT_*` /
# `cdk deploy` (the dev/prod split is carried by `config.env`, not the CDK env).

# --- Stacks (instantiated as build groups B–F complete) ---------------------
# Dependency order (Spec/09 §3):
#   Security -> Storage -> Processing -> AI -> Validation -> API -> UI -> Observability
# UI is constructed first so its `app_url` (custom domain or CloudFront default)
# can feed the Cognito hosted-UI callbacks in the security stack (audit B8).
# When a custom domain is configured the UI stack must be account/region-bound so
# `HostedZone.from_lookup` can resolve the zone; otherwise it stays env-agnostic
# (credential-free synth). With no custom domain there is no lookup, so no binding.
ui_env = (
    cdk.Environment(account=config.account, region=config.region)
    if config.has_custom_domain
    else None
)
ui = UiStack(app, f"Laboraid-{config.env}-Ui", config=config, env=ui_env)

security = SecurityStack(
    app,
    f"Laboraid-{config.env}-Security",
    config=config,
    app_url=ui.app_url,
)
security.add_dependency(ui)
storage = StorageStack(
    app,
    f"Laboraid-{config.env}-Storage",
    config=config,
    master_key=security.master_key,
)
storage.add_dependency(security)

# AI before Processing: the ExtractorAgent runtime injects the guardrail ID.
ai = AiStack(
    app,
    f"Laboraid-{config.env}-Ai",
    config=config,
    master_key=security.master_key,
)
ai.add_dependency(security)

processing = ProcessingStack(
    app,
    f"Laboraid-{config.env}-Processing",
    config=config,
    master_key=security.master_key,
    inputs_bucket=storage.inputs_bucket,
    outputs_bucket=storage.outputs_bucket,
    files_table=storage.files_table,
    guardrail_id=ai.guardrail_id,
)
processing.add_dependency(storage)
processing.add_dependency(ai)

validation = ValidationStack(
    app,
    f"Laboraid-{config.env}-Validation",
    config=config,
    master_key=security.master_key,
    outputs_bucket=storage.outputs_bucket,
    review_table=storage.review_table,
)
validation.add_dependency(storage)

assert storage.aurora.secret is not None
api = ApiStack(
    app,
    f"Laboraid-{config.env}-Api",
    config=config,
    user_pool=security.user_pool,
    user_pool_client=security.user_pool_client,
    inputs_bucket=storage.inputs_bucket,
    jobs_table=storage.jobs_table,
    agent_config_table=storage.agent_config_table,
    overrides_table=storage.overrides_table,
    aurora=storage.aurora,
    aurora_secret=storage.aurora.secret,
    engine_bus=validation.engine_bus,
)
api.add_dependency(security)
api.add_dependency(storage)
api.add_dependency(validation)

# Orchestration — Step Functions pipeline over the processing + validation fns.
orchestration = OrchestrationStack(
    app,
    f"Laboraid-{config.env}-Orchestration",
    config=config,
    inputs_bucket=storage.inputs_bucket,
    classifier=processing.classifier,
    checksum=validation.checksum,
    range_fn=validation.range_fn,
    confidence=validation.confidence,
    review_router=validation.review_router,
    xlsx=validation.xlsx,
    csv=validation.csv,
    articles=validation.articles,
    agent_config_table=storage.agent_config_table,
)
orchestration.add_dependency(processing)
orchestration.add_dependency(validation)
orchestration.add_dependency(storage)

observability = ObservabilityStack(
    app,
    f"Laboraid-{config.env}-Observability",
    config=config,
    alarm_topic=validation.failures_topic.topic,
)
observability.add_dependency(validation)
# ---------------------------------------------------------------------------

# Mandatory tags on every resource (Spec/09 §2).
cdk.Aspects.of(app).add(MandatoryTagsAspect(config.mandatory_tags))

app.synth()
