"""Resource naming helper (Spec/09 §1).

All AWS resource names follow ``laboraid-{env}-{layer}-{type}-{purpose}``, e.g.
``laboraid-prod-l3-bucket-inputs``. Use `name()` exclusively — no hardcoded
resource names anywhere in the stacks.
"""

from __future__ import annotations

import re

_LAYERS = frozenset(f"l{n}" for n in range(1, 8))  # l1..l7
_SEGMENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def name(env: str, layer: str, type_: str, purpose: str) -> str:
    """Build a convention-compliant resource name.

    Args:
        env: ``"dev"`` | ``"prod"``.
        layer: SOW layer ``"l1"``..``"l7"``.
        type_: resource type token, e.g. ``"bucket"``, ``"fn"``, ``"sns"``.
        purpose: purpose token, e.g. ``"inputs"``, ``"classifier"``.

    Returns:
        ``laboraid-{env}-{layer}-{type_}-{purpose}`` — all lowercase, kebab-case.

    Raises:
        ValueError: if any segment is not lowercase kebab-case, or ``layer`` is
            not one of ``l1``..``l7``.
    """
    if env not in ("dev", "prod"):
        raise ValueError(f"env must be 'dev' or 'prod', got {env!r}")
    if layer not in _LAYERS:
        raise ValueError(f"layer must be one of l1..l7, got {layer!r}")
    for label, value in (("type_", type_), ("purpose", purpose)):
        if not _SEGMENT.match(value):
            raise ValueError(f"{label} must be lowercase kebab-case, got {value!r}")
    return f"laboraid-{env}-{layer}-{type_}-{purpose}"
