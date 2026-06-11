# Shared Auth Credentials

Two artefacts share the same `laboraid-dev` AWS account and CloudFront
distribution but have separate auth surfaces:

| Artefact | URL | Auth | Credentials in |
|---|---|---|---|
| **Product Walkthrough SPA** (this directory's docs) | `https://d3ggwschjt81wu.cloudfront.net/product-walkthrough.html` | HTTP Basic Auth at CloudFront edge | Secrets Manager `laboraid-dev-walkthrough-basic-auth` |
| **LaborAid React Product UI** (Admin + Business panels) | `https://d3ggwschjt81wu.cloudfront.net/` | Cognito (Hosted UI) | Secrets Manager `laboraid-dev-product-team-share-login` |

Retrieve either:

```bash
# Walkthrough SPA basic-auth password
aws secretsmanager get-secret-value --secret-id laboraid-dev-walkthrough-basic-auth \
  --profile laboraid --region us-east-2 --query SecretString --output text

# React UI Cognito login (team-share account)
aws secretsmanager get-secret-value --secret-id laboraid-dev-product-team-share-login \
  --profile laboraid --region us-east-2 --query SecretString --output text
```

---

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

---

# LaborAid React Product UI — Team-Share Cognito Login

Sharing the full Admin + Business product UI with the team. Backed by the
real Cognito user pool (`us-east-2_CC90iICJt`); the user has membership in
all three role groups so they can navigate every page.

## Architecture

```
viewer ─► CloudFront / (React UI bundle)
            └─► React app calls Cognito Hosted UI
                  └─► auth.laboraid-dev-auth.us-east-2.amazoncognito.com
                       └─► validate user/password → return JWT
                            └─► JWT carries cognito:groups [Admins, Operations, Business]
                                  └─► UI routes by group, API GW authorizes by group
```

## Account details

| Field | Value |
|---|---|
| Cognito user pool | `us-east-2_CC90iICJt` |
| Pool name | `laboraid-dev-l1-cognito-userpool` |
| Hosted-UI domain | `laboraid-dev-auth.auth.us-east-2.amazoncognito.com` |
| SPA client | `8mlbfdiopceq8gvflpvqvrh5u` |
| Username | `team-share@laboraid.test` |
| Groups | `Admins`, `Operations`, `Business` |
| Password | in Secrets Manager — `laboraid-dev-product-team-share-login` |

## Rotate the password

```bash
python docs/spa/auth/create_team_share_user.py
```

The script is idempotent — re-running:

1. Generates a fresh 20-char password (12+ chars, mixed letters + digits +
   symbols, matching the pool's password policy).
2. Updates the Secrets Manager entry in place.
3. Calls `admin_set_user_password` (Permanent=true) so the user isn't
   forced through a first-login reset.
4. Re-confirms group memberships.
5. Verifies `USER_PASSWORD_AUTH` against the test client returns a valid JWT.

## Add another user for a specific person

If a team member needs their own login (audit-attributable actions) instead
of sharing the team-share account, copy this script and change `USERNAME`
+ `SECRET_NAME`. Drop them in the groups they need (Business-only for SMEs,
Admins for ops).

---

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
