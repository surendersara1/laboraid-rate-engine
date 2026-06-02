"""`TaggedBucket` — S3 bucket with secure defaults + mandatory tags (Spec/09 §3.1).

Defaults: SSE-KMS with the project CMK, block-all-public-access, versioning on,
and a TLS-only bucket policy (``enforce_ssl=True`` adds the deny-non-TLS
statement). Callers add lifecycle rules / object lock per the storage spec.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Tags
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct


class TaggedBucket(s3.Bucket):
    """An `s3.Bucket` pre-wired with the project's security defaults + tags."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        encryption_key: kms.IKey,
        layer: str = "l3",
        data_classification: str = "customer-input",
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("encryption", s3.BucketEncryption.KMS)
        kwargs.setdefault("block_public_access", s3.BlockPublicAccess.BLOCK_ALL)
        kwargs.setdefault("versioned", True)
        kwargs.setdefault("enforce_ssl", True)  # TLS-only: deny non-SecureTransport
        super().__init__(scope, construct_id, encryption_key=encryption_key, **kwargs)

        # Per-resource tag overrides (more specific scope wins over app aspect).
        Tags.of(self).add("Layer", layer)
        Tags.of(self).add("DataClassification", data_classification)
