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

app = cdk.App()

env_name = str(app.node.try_get_context("env") or "dev")
config = get_config(env_name)

cdk_env = cdk.Environment(account=config.account, region=config.region)

# --- Stacks (instantiated as build groups B–F complete) ---------------------
# Dependency order (Spec/09 §3):
#   Security -> Storage -> Processing -> AI -> Validation -> API -> UI -> Observability
# e.g.:
#   security = SecurityStack(app, name(config.env, "l3", "stack", "security"),
#                            config=config, env=cdk_env)
# ---------------------------------------------------------------------------

# Mandatory tags on every resource (Spec/09 §2).
cdk.Aspects.of(app).add(MandatoryTagsAspect(config.mandatory_tags))

app.synth()
