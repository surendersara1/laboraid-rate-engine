"""Create (or rotate) a team-share Cognito user for the LaborAid React UI.

Idempotent. The user gets membership in all 3 groups (Admins, Operations,
Business) so they can navigate the entire product. Password is stored in
AWS Secrets Manager so it survives terminal sessions.

Run again to rotate: it'll regenerate the password and update the user.
"""
from __future__ import annotations
import json
import secrets
import string

import boto3
import botocore

PROFILE = "laboraid"
REGION = "us-east-2"
POOL_ID = "us-east-2_CC90iICJt"
USERNAME = "team-share@laboraid.test"
GROUPS = ["Admins", "Operations", "Business"]
SECRET_NAME = "laboraid-dev-product-team-share-login"

s = boto3.Session(profile_name=PROFILE)
cidp = s.client("cognito-idp", region_name=REGION)
sm = s.client("secretsmanager", region_name=REGION)


# ---------------------------------------------------------------------------
# 1) Password — generate fresh, store in Secrets Manager
# ---------------------------------------------------------------------------
# Policy: MinLength 12, RequireSymbols true. Mix all 4 classes to be safe
# regardless of future tightening.
alphabet_letters = string.ascii_letters
alphabet_digits = string.digits
alphabet_symbols = "!@#$%^&*-_=+"
password = (
    "".join(secrets.choice(alphabet_letters) for _ in range(14)) +
    "".join(secrets.choice(alphabet_digits) for _ in range(4)) +
    "".join(secrets.choice(alphabet_symbols) for _ in range(2))
)
# Shuffle
password = "".join(secrets.SystemRandom().sample(password, len(password)))

# Store / update secret
secret_payload = {
    "username": USERNAME,
    "password": password,
    "url": "https://d3ggwschjt81wu.cloudfront.net/",
    "groups": GROUPS,
}
try:
    sm.put_secret_value(SecretId=SECRET_NAME, SecretString=json.dumps(secret_payload))
    print(f"[1] rotated password in Secrets Manager: {SECRET_NAME}")
except sm.exceptions.ResourceNotFoundException:
    sm.create_secret(
        Name=SECRET_NAME,
        Description="Shareable Cognito login for the LaborAid React UI (all-groups)",
        SecretString=json.dumps(secret_payload),
    )
    print(f"[1] created secret: {SECRET_NAME}")


# ---------------------------------------------------------------------------
# 2) Create user if missing
# ---------------------------------------------------------------------------
try:
    cidp.admin_get_user(UserPoolId=POOL_ID, Username=USERNAME)
    user_exists = True
    print(f"[2] user exists: {USERNAME}")
except cidp.exceptions.UserNotFoundException:
    user_exists = False
    cidp.admin_create_user(
        UserPoolId=POOL_ID,
        Username=USERNAME,
        UserAttributes=[
            {"Name": "email", "Value": USERNAME},
            {"Name": "email_verified", "Value": "true"},
        ],
        MessageAction="SUPPRESS",  # don't email
    )
    print(f"[2] created user: {USERNAME}")

# ---------------------------------------------------------------------------
# 3) Set permanent password (skips first-login forced reset)
# ---------------------------------------------------------------------------
cidp.admin_set_user_password(
    UserPoolId=POOL_ID,
    Username=USERNAME,
    Password=password,
    Permanent=True,
)
print(f"[3] set permanent password")

# ---------------------------------------------------------------------------
# 4) Group memberships
# ---------------------------------------------------------------------------
for g in GROUPS:
    try:
        cidp.admin_add_user_to_group(UserPoolId=POOL_ID, Username=USERNAME, GroupName=g)
    except botocore.exceptions.ClientError as e:
        if "InvalidParameterException" in str(e) and "already" in str(e):
            pass
        else:
            raise
print(f"[4] confirmed groups: {GROUPS}")

# ---------------------------------------------------------------------------
# 5) Verify auth works via USER_PASSWORD_AUTH on the SPA client
# ---------------------------------------------------------------------------
SPA_CLIENT_ID = "8mlbfdiopceq8gvflpvqvrh5u"   # web SPA client
try:
    cidp.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=SPA_CLIENT_ID,
        AuthParameters={"USERNAME": USERNAME, "PASSWORD": password},
    )
    print(f"[5] USER_PASSWORD_AUTH against SPA client: OK")
except botocore.exceptions.ClientError as e:
    # SPA client may have USER_PASSWORD_AUTH disabled — fall back to test client
    TEST_CLIENT_ID = "7g8l4dfcirofqtkafoi1u3869g"
    try:
        cidp.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=TEST_CLIENT_ID,
            AuthParameters={"USERNAME": USERNAME, "PASSWORD": password},
        )
        print(f"[5] SPA client rejected USER_PASSWORD_AUTH; test client OK")
    except Exception as e2:
        print(f"[5] auth test failed: {e2}")

# ---------------------------------------------------------------------------
# 6) Report
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("PRODUCT (REACT UI) LOGIN — share with team")
print("=" * 70)
print(f"URL:      https://d3ggwschjt81wu.cloudfront.net/")
print(f"USER:     {USERNAME}")
print(f"PASSWORD: {password}")
print(f"GROUPS:   {GROUPS}")
print()
print(f"Stored in Secrets Manager: {SECRET_NAME}")
print(f"Retrieve any time:")
print(f"  aws secretsmanager get-secret-value --secret-id {SECRET_NAME} \\")
print(f"    --profile {PROFILE} --region {REGION} --query SecretString --output text")
