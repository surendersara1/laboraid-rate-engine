# Architecture diagrams

Two ways to get an AWS-icon architecture diagram of the LaborAid Rate Engine — a
**hand-authored cohesive view** and an **auto-generated-from-CDK view**. Both use the
official AWS icon set.

| | Approach B — `diagrams` (mingrammer) | Approach A — `cfn-diagram` |
|---|---|---|
| **Output** | [`laboraid_architecture.png`](laboraid_architecture.png) / `.svg` | [`cfn/`](cfn/) — one per CDK stack |
| **Source** | Hand-authored ([`scripts/arch_diagram.py`](../scripts/arch_diagram.py)) | Auto from the 9 synthesized CDK templates |
| **Best for** | **CTO / one-slide** — clean, on-brand, the story | **Engineers** — every resource, exact, editable |
| **Detail** | Curated (the components that matter) | Exhaustive (every IAM role, custom resource…) |
| **Edit** | Edit the Python, re-run | Open `.drawio` in diagrams.net / VS Code |

---

## Approach B — the clean, on-brand diagram (recommended for the deck)

`diagram/laboraid_architecture.png` (and `.svg`). One cohesive picture: users →
CloudFront/SPA + Cognito → API Gateway → Lambdas → Step Functions (Plan→Synthesize→Publish)
→ Bedrock + AgentCore agents → Aurora (system of record) / DynamoDB (telemetry) / S3, with
the EventBridge→job-writer read-model. The gold edges are the Phase-2 **Improve / v+1 change
log** flow.

**Regenerate:**
```bash
py -3 scripts/arch_diagram.py        # writes diagram/laboraid_architecture.png + .svg
```

It's hand-authored to match `docs/LAMBDA_AND_AGENT_INVENTORY.md` — update the script when the
architecture changes. (It does **not** scan AWS; it's a drawn model.)

## Approach A — auto-generated from the CDK templates

`diagram/cfn/<Stack>.drawio` + `diagram/cfn/<Stack>.html` for each of the 9 stacks
(Ai, Api, Observability, Orchestration, Processing, Security, Storage, Ui, Validation).

- **`.html/`** — a self-contained interactive viewer. Open the folder's `index.html` in a
  browser (it loads `data.js` + `icons.js` beside it). Pan/zoom every resource.
- **`.drawio`** — open in [diagrams.net](https://app.diagrams.net) or the VS Code
  *Draw.io Integration* extension to edit/export PNG/PDF.

These are exhaustive (every resource CloudFormation creates), so they're detailed/noisy —
great for an engineer verifying the build, less so for a one-slide exec view.

**Regenerate** (no AWS calls — reads the already-synthesized templates in `cdk/cdk.out/`):
```bash
for t in cdk/cdk.out/Laboraid-dev-*.template.json; do
  name=$(basename "$t" .template.json | sed 's/Laboraid-dev-//')
  npx -y @mhlabs/cfn-diagram draw.io -t "$t" -o "diagram/cfn/${name}.drawio" -c
  npx -y @mhlabs/cfn-diagram html   -t "$t" -o "diagram/cfn/${name}.html"   -c
done
```
(If `cdk/cdk.out/` is stale, run `cdk synth` first.)

---

## One-time setup (already done on this machine)

```bash
winget install Graphviz.Graphviz     # the `dot` engine (Approach B)
py -3 -m pip install diagrams        # mingrammer diagrams (Approach B)
# Approach A needs only Node/npx (cfn-diagram is run via npx, nothing to install)
```

## Other options (not used here)
- **Workload Discovery on AWS** (ex-AWS Perspective) — official AWS Solution that *scans the
  live account* and draws interactive diagrams. Heavier (a CloudFormation stack to deploy)
  but it reflects exactly what's running.
- **AWS Infrastructure Composer** — import a CDK/CFN template in the Console or VS Code to
  render it visually.
