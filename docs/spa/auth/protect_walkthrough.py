"""Add HTTP Basic Auth in front of /product-walkthrough.html on the live
CloudFront distribution. The React UI at / is NOT affected — the auth
function is attached only to a new path-pattern behavior for the
walkthrough HTML.

Run once. Idempotent — re-running updates the function code + ensures the
behavior exists.

After this runs the password is printed to stdout — share with the
client over a secure channel (NOT email; verbal or Slack DM with auto-
delete). The password is also stored in AWS Secrets Manager so it can
be retrieved without re-running.
"""
from __future__ import annotations
import base64
import json
import secrets
import string
import sys
import time

import boto3
import botocore

PROFILE = "laboraid"
REGION = "us-east-2"
DISTRIBUTION_ID = "EYYEIRSC9DSLW"
PROTECTED_PATH = "/product-walkthrough.html"
USERNAME = "laboraid"
FUNCTION_NAME = "laboraid-dev-cf-fn-basicauth-walkthrough"
SECRET_NAME = "laboraid-dev-walkthrough-basic-auth"

s = boto3.Session(profile_name=PROFILE)
cf = s.client("cloudfront")
sm = s.client("secretsmanager", region_name=REGION)


# ---------------------------------------------------------------------------
# 1) Generate (or retrieve) the password
# ---------------------------------------------------------------------------
try:
    val = sm.get_secret_value(SecretId=SECRET_NAME)
    password = json.loads(val["SecretString"])["password"]
    print(f"[1] reused existing password from Secrets Manager: {SECRET_NAME}")
except sm.exceptions.ResourceNotFoundException:
    alphabet = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(alphabet) for _ in range(20))
    sm.create_secret(
        Name=SECRET_NAME,
        Description="HTTP Basic Auth password gating /product-walkthrough.html",
        SecretString=json.dumps({"username": USERNAME, "password": password}),
    )
    print(f"[1] generated new password + stored in Secrets Manager: {SECRET_NAME}")


# ---------------------------------------------------------------------------
# 2) Author the CloudFront Function
# ---------------------------------------------------------------------------
# Pre-compute the base64(user:pass) so we don't need crypto in the function.
expected = base64.b64encode(f"{USERNAME}:{password}".encode()).decode()

# CloudFront Function runtime: ECMA 5.1, no async, no Promise.
# Returns 401 with WWW-Authenticate: Basic so the browser pops the login dialog.
function_code = f"""function handler(event) {{
    var req = event.request;
    var auth = req.headers.authorization;
    var expected = 'Basic {expected}';
    if (!auth || auth.value !== expected) {{
        return {{
            statusCode: 401,
            statusDescription: 'Unauthorized',
            headers: {{
                'www-authenticate': {{ value: 'Basic realm="LaborAid Product Walkthrough"' }},
                'cache-control':   {{ value: 'no-store' }}
            }}
        }};
    }}
    return req;
}}
"""

# Create or update the function.
try:
    desc = cf.describe_function(Name=FUNCTION_NAME, Stage="DEVELOPMENT")
    etag = desc["ETag"]
    cf.update_function(
        Name=FUNCTION_NAME,
        IfMatch=etag,
        FunctionConfig={"Comment": "Basic Auth gate for /product-walkthrough.html",
                        "Runtime": "cloudfront-js-2.0"},
        FunctionCode=function_code.encode(),
    )
    print(f"[2] updated CloudFront Function {FUNCTION_NAME}")
except cf.exceptions.NoSuchFunctionExists:
    cf.create_function(
        Name=FUNCTION_NAME,
        FunctionConfig={"Comment": "Basic Auth gate for /product-walkthrough.html",
                        "Runtime": "cloudfront-js-2.0"},
        FunctionCode=function_code.encode(),
    )
    print(f"[2] created CloudFront Function {FUNCTION_NAME}")

# Re-fetch the ETag (it changed on create/update)
desc = cf.describe_function(Name=FUNCTION_NAME, Stage="DEVELOPMENT")
etag = desc["ETag"]
cf.publish_function(Name=FUNCTION_NAME, IfMatch=etag)
print(f"[2] published function to LIVE")

# After publish we need the LIVE ARN
desc = cf.describe_function(Name=FUNCTION_NAME, Stage="LIVE")
fn_arn = desc["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]
print(f"    ARN: {fn_arn}")


# ---------------------------------------------------------------------------
# 3) Attach as a path-pattern cache behavior on the distribution
# ---------------------------------------------------------------------------
dist = cf.get_distribution_config(Id=DISTRIBUTION_ID)
cfg = dist["DistributionConfig"]
etag_dist = dist["ETag"]

# Pick the same origin the default behavior uses.
default_origin = cfg["DefaultCacheBehavior"]["TargetOriginId"]
print(f"[3] default behavior origin: {default_origin}")

# Build the new behavior. We must include EVERY field CloudFront requires —
# inherit cache + viewer-protocol policy from the default to avoid drift.
default = cfg["DefaultCacheBehavior"]

new_behavior = {
    "PathPattern": PROTECTED_PATH,
    "TargetOriginId": default_origin,
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
        "Quantity": 2,
        "Items": ["GET", "HEAD"],
        "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
    },
    "Compress": True,
    "SmoothStreaming": False,
    "FieldLevelEncryptionId": "",
    "CachePolicyId": default.get("CachePolicyId") or "658327ea-f89d-4fab-a63d-7e88639e58f6",  # Managed-CachingOptimized
    "OriginRequestPolicyId": default.get("OriginRequestPolicyId", ""),
    "ResponseHeadersPolicyId": default.get("ResponseHeadersPolicyId", ""),
    "FunctionAssociations": {
        "Quantity": 1,
        "Items": [{"FunctionARN": fn_arn, "EventType": "viewer-request"}],
    },
    "LambdaFunctionAssociations": {"Quantity": 0},
    "TrustedSigners": {"Enabled": False, "Quantity": 0},
    "TrustedKeyGroups": {"Enabled": False, "Quantity": 0},
}

# Remove or replace any existing behavior for this exact path pattern
behaviors = cfg.get("CacheBehaviors", {"Quantity": 0, "Items": []})
items = behaviors.get("Items") or []
items = [b for b in items if b.get("PathPattern") != PROTECTED_PATH]
items.insert(0, new_behavior)  # path-pattern behaviors are evaluated in order
behaviors["Items"] = items
behaviors["Quantity"] = len(items)
cfg["CacheBehaviors"] = behaviors

cf.update_distribution(Id=DISTRIBUTION_ID, IfMatch=etag_dist, DistributionConfig=cfg)
print(f"[3] attached behavior {PROTECTED_PATH} -> CF function (viewer-request)")


# ---------------------------------------------------------------------------
# 4) Report
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("DEPLOYMENT IN PROGRESS")
print("=" * 60)
print(f"URL:      https://d3ggwschjt81wu.cloudfront.net{PROTECTED_PATH}")
print(f"USER:     {USERNAME}")
print(f"PASSWORD: {password}")
print()
print("Distribution update propagates over ~3-8 minutes. Once it reports")
print("Deployed, the URL will prompt for credentials before serving the SPA.")
print(f"Password is also retrievable via:")
print(f"  aws secretsmanager get-secret-value --secret-id {SECRET_NAME} --profile {PROFILE} --region {REGION}")
