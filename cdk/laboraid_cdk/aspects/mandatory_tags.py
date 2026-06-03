"""Mandatory-tag enforcement Aspect (Spec/09 §2).

Applied once at app level (``Aspects.of(app).add(MandatoryTagsAspect(...))``);
visits every node in the construct tree and stamps the 13 mandatory tags onto
each taggable CloudFormation resource. Stacks/constructs may override ``Layer``
and ``DataClassification`` per-resource — those apply at a more specific scope and
therefore win over the app-level defaults.

Tags are applied to the L1 ``CfnResource`` nodes (which expose a `tags`
`TagManager` when the resource type is taggable) rather than to L2 ``Resource``
wrappers. Tagging an L2 construct triggers CDK's internal tag-*propagation*
aspect; under aspect stabilization that keeps mutating the tree every pass and
trips the infinite-loop guard. Tagging the CfnResource directly sets the
resource's ``Tags`` property in a single converging pass.
"""

from __future__ import annotations

import jsii
from aws_cdk import CfnResource, IAspect, TagManager
from constructs import IConstruct


@jsii.implements(IAspect)
class MandatoryTagsAspect:
    """Stamp the mandatory tag set on every taggable `CfnResource` in the tree."""

    def __init__(self, tags: dict[str, str]) -> None:
        """Args: tags — the mandatory tag set, e.g. ``Config.mandatory_tags``."""
        self._tags = dict(tags)

    def visit(self, node: IConstruct) -> None:
        # Tag L1 CloudFormation resources that actually expose a TagManager via a
        # `.tags` attribute. We check the attribute directly rather than trusting
        # `TagManager.is_taggable`, which can report True for raw `CfnResource`s of
        # non-standard types (e.g. AWS::BedrockAgentCore::Runtime) that have no
        # `.tags` — accessing it then raises. Skip L2 wrappers + logical nodes.
        if not isinstance(node, CfnResource):
            return
        tag_manager = getattr(node, "tags", None)
        if isinstance(tag_manager, TagManager):
            for key, value in self._tags.items():
                # Lower priority so per-resource overrides (Layer/DataClass) win.
                tag_manager.set_tag(key, value, 100)
