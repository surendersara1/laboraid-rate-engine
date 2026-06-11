"""Build + upload the Product Walkthrough SPA to the LaborAid web bucket.

Hosted at: https://d3ggwschjt81wu.cloudfront.net/product-walkthrough.html

Usage:
    python docs/spa/deploy.py                # build + upload + invalidate
    python docs/spa/deploy.py --dry-run      # build + report, no upload
    python docs/spa/deploy.py --no-invalidate  # skip CF invalidation
"""
from __future__ import annotations
import argparse
import pathlib
import subprocess
import sys
import time

import boto3

HERE = pathlib.Path(__file__).resolve().parent
DIST = HERE / "dist" / "index.html"

BUCKET = "laboraid-dev-l1-bucket-spa"
KEY = "product-walkthrough.html"
CF_DISTRIBUTION_ID = "EYYEIRSC9DSLW"
CF_DOMAIN = "d3ggwschjt81wu.cloudfront.net"
PROFILE = "laboraid"
REGION = "us-east-2"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-invalidate", action="store_true")
    args = ap.parse_args()

    # 1) Build
    print(">> building")
    subprocess.run([sys.executable, str(HERE / "build.py")], check=True)
    assert DIST.exists(), f"build output missing: {DIST}"
    body = DIST.read_bytes()
    print(f"   built: {len(body):,} bytes")

    if args.dry_run:
        print("\n[dry-run] would upload to:")
        print(f"   s3://{BUCKET}/{KEY}")
        print(f"   public URL: https://{CF_DOMAIN}/{KEY}")
        return

    # 2) Upload
    s = boto3.Session(profile_name=PROFILE)
    s3 = s.client("s3", region_name=REGION)
    print(f">> uploading to s3://{BUCKET}/{KEY}")
    s3.put_object(
        Bucket=BUCKET,
        Key=KEY,
        Body=body,
        ContentType="text/html; charset=utf-8",
        CacheControl="public, max-age=300, must-revalidate",
        ServerSideEncryption="AES256",
    )
    head = s3.head_object(Bucket=BUCKET, Key=KEY)
    print(f"   uploaded: ETag={head['ETag']}  Last-Modified={head['LastModified']}")

    # 3) CloudFront invalidation so the next viewer sees the new build
    if args.no_invalidate:
        print(">> skipped invalidation (--no-invalidate)")
    else:
        print(">> invalidating CloudFront cache")
        cf = s.client("cloudfront")
        inv = cf.create_invalidation(
            DistributionId=CF_DISTRIBUTION_ID,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": [f"/{KEY}"]},
                "CallerReference": f"product-walkthrough-{int(time.time())}",
            },
        )
        print(f"   invalidation: {inv['Invalidation']['Id']}  ({inv['Invalidation']['Status']})")

    print()
    print(f">> live at: https://{CF_DOMAIN}/{KEY}")


if __name__ == "__main__":
    main()
