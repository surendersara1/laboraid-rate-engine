# Walkthrough SPA — Basic Auth Gate

The Product Walkthrough SPA at
`https://d3ggwschjt81wu.cloudfront.net/product-walkthrough.html` is
gated by HTTP Basic Auth so it can be shared with the client without
making the customer's architecture, cost model, and open issues
visible to anyone with the URL.

## Architecture

```
viewer ─► CloudFront distribution EYYEIRSC9DSLW
            │
            ├── path "/" + everything else
            │      └─► public React UI (Cognito-authenticated inside the app)
            │
            └── path "/product-walkthrough.html"
                   ├─► CloudFront Function "laboraid-dev-cf-fn-basicauth-walkthrough"
                   │       viewer-request: validates Basic Auth header,
                   │       returns 401 + WWW-Authenticate on mismatch
                   └─► same S3 origin as default
```

- The CloudFront Function only runs on the walkthrough path.
- The React UI at `/` and any other static asset is unaffected.
- Password is stored in AWS Secrets Manager (secret name
  `laboraid-dev-walkthrough-basic-auth`) under the `laboraid` AWS profile
  in `us-east-2`.

## Retrieve the password

```bash
aws secretsmanager get-secret-value \
  --secret-id laboraid-dev-walkthrough-basic-auth \
  --profile laboraid --region us-east-2 \
  --query SecretString --output text | jq -r .password
```

Username is always `laboraid`.

## Rotate the password

```bash
# Delete the secret then re-run the protection script — it'll generate
# a fresh 20-char password and re-publish the CloudFront Function.
aws secretsmanager delete-secret --secret-id laboraid-dev-walkthrough-basic-auth \
  --force-delete-without-recovery --profile laboraid --region us-east-2
python docs/spa/auth/protect_walkthrough.py
```

Distribution update propagates in 3-8 minutes.

## What this script does (idempotent)

1. Reads or creates a 20-char alphanumeric password in Secrets Manager.
2. Computes `base64("laboraid:<password>")` and embeds in a CloudFront
   Function (ECMA 5.1 viewer-request handler).
3. Creates / updates the function `laboraid-dev-cf-fn-basicauth-walkthrough`
   and publishes to LIVE.
4. Adds a cache behavior for path pattern `/product-walkthrough.html` on
   CloudFront distribution `EYYEIRSC9DSLW`, pointing at the same S3 origin
   as the default behavior, with the function attached as
   `viewer-request`.
5. Waits for the distribution to redeploy.

## Verify

After deploy, this should hold:

| Request | Expected |
|---|---|
| `GET /product-walkthrough.html` (no auth) | `401` + `WWW-Authenticate: Basic` |
| `GET /product-walkthrough.html` (wrong password) | `401` |
| `GET /product-walkthrough.html` (`Authorization: Basic <user:pass>`) | `200` + SPA HTML |
| `GET /` | `200` + React UI (no auth prompt) |
| `GET /index.html` | `200` + React UI (no auth prompt) |

Test command:

```bash
python _TMP_/verify_auth.py
```

## Security posture (audit, 2026-06-11)

Underlying S3 surface — zero RED findings across all 8 `laboraid-*` buckets:

- Public Access Block all 4 settings ON
- TLS-only bucket policy (aws:SecureTransport=false → Deny)
- No public ACL grants
- Default encryption — SSE-KMS (CMK) on data buckets, SSE-S3 on
  web/observability buckets
- Versioning enabled on every data bucket
- ObjectOwnership=BucketOwnerEnforced (ACLs disabled)

Open YELLOW items (operational, not security-critical):

- 2 buckets missing server-access logging (the audit bucket itself + the
  SPA bucket). Logging on the audit bucket would self-loop; logging on
  the SPA bucket is reasonable to add in production.
- The CloudTrail observability bucket has versioning off — CloudTrail
  writes are immutable by default so this isn't a real exposure.
