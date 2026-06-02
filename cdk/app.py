#!/usr/bin/env python3
"""CDK app entry point for the LaborAid Rate Engine POC (Spec/09 §3, §11).

Instantiates the eight stacks (added as build groups B–F land) and applies the
mandatory-tags Aspect at app level so every resource inherits the 13 tags.

Select the environment with CDK context: ``cdk synth -c env=prod`` (default dev).
"""

from __future__ import annotations

import aws_cdk as cdk

from laboraid_cdk.aspects.mandatory_tags import MandatoryTagsAspect
from laboraid_cdk.config import get_config
from laboraid_cdk.stacks.ai_stack import AiStack
from laboraid_cdk.stacks.processing_stack import ProcessingStack
from laboraid_cdk.stacks.security_stack import SecurityStack
from laboraid_cdk.stacks.storage_stack import StorageStack

app = cdk.App()

env_name = str(app.node.try_get_context("env") or "dev")
config = get_config(env_name)

# Stacks are environment-agnostic so `cdk synth` works without AWS credentials.
# Deploy binds them to a concrete account/region via `CDK_DEFAULT_*` /
# `cdk deploy` (the dev/prod split is carried by `config.env`, not the CDK env).

# --- Stacks (instantiated as build groups B–F complete) ---------------------
# Dependency order (Spec/09 §3):
#   Security -> Storage -> Processing -> AI -> Validation -> API -> UI -> Observability
security = SecurityStack(app, f"Laboraid-{config.env}-Security", config=config)
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
# ---------------------------------------------------------------------------

# Mandatory tags on every resource (Spec/09 §2).
cdk.Aspects.of(app).add(MandatoryTagsAspect(config.mandatory_tags))

app.synth()
