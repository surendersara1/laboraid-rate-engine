# CDK ↔ Live Reconciliation

The live env drifted from CDK via ~27 out-of-band boto3 changes + 5 Lambdas not
in any CloudFormation stack. **Do not `cdk deploy` until this is complete** (see
`/DEPLOY_FREEZE.md`). Captured live specs: `cdk/reconciliation/*.json`.

## Drift to fold into CDK source
| Stack | Resource | Live state to reproduce |
|---|---|---|
| Orchestration | MainPipeline StateMachine | def = `Plan → Synthesize → SynthPublish` (sfn_definition.json) |
| Orchestration | MainPipelineRole | + inline `invoke-synthesizer`, `invoke-profile-builder` |
| Orchestration | OnInputUpload rule | **DISABLED** |
| Processing | LlmExtractorServiceRole | + inline `rds-data-profiles`, `invoke-profile-builder` |
| Api | ProfileList/ProfileUpdate roles | + inline `rds-data-profiles` |
| Ai | PiiGuardrail | ANONYMIZE (already in source; needs deploy) |
| Storage/Validation | S3 buckets / SNS topics | review (likely benign config drift) |

## New resources to ADD + `cdk import` (not in any stack)
synthesizer, synth-publish, profile-builder (Processing) · batch-planner
(Processing) · batch-process (Api) · `POST /v1/batches/process` route ·
their IAM roles. Configs: `cdk/reconciliation/new_lambda_configs.json`.
Runtime deps to declare: **pypdf + openpyxl** (synthesizer/profile-builder).

## Safe execution order
1. Author CDK to match live (above) — validate `cdk synth`.
2. `cdk diff <stack>` per stack — confirm the delta == the live state.
3. `cdk import` the 5 new Lambdas (adopt existing physical resources — no recreate).
4. `cdk deploy` per stack **only when diff is reviewed** — converges CFN to live.

Steps 1–2 change nothing in AWS. Steps 3–4 are the only AWS-touching steps.
