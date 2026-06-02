"""Mandatory-tag enforcement Aspect (Spec/09 §2).

Applied once at app level (``Aspects.of(app).add(MandatoryTagsAspect(...))``);
visits every node in the construct tree and stamps the 13 mandatory tags onto
each taggable AWS resource. Stacks/constructs may override ``Layer`` and
``DataClassification`` per-resource — those apply at a more specific scope and
therefore win over the app-level defaults.
"""

from __future__ import annotations

import jsii
from aws_cdk import IAspect, Resource, Tags
from constructs import IConstruct


@jsii.implements(IAspect)
class MandatoryTagsAspect:
    """Stamp the mandatory tag set on every `Resource` in the tree."""

    def __init__(self, tags: dict[str, str]) -> None:
        """Args: tags — the mandatory tag set, e.g. ``Config.mandatory_tags``."""
        self._tags = dict(tags)

    def visit(self, node: IConstruct) -> None:
        # Only taggable AWS resources carry tags; skip pure logical constructs.
        if isinstance(node, Resource):
            tagger = Tags.of(node)
            for key, value in self._tags.items():
                tagger.add(key, value)
