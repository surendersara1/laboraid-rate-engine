# ⛔ DEPLOY FREEZE — do NOT run `cdk deploy` or `cdk destroy`

The live AWS environment has **~27 out-of-band (boto3) changes** across all layers
that are NOT yet in CDK, plus **5 Lambdas not in any CloudFormation stack**
(synthesizer, synth-publish, profile-builder, batch-planner, batch-process).

A `cdk deploy` today will **revert the live Step Functions definition, IAM grants,
the Bedrock guardrail, and the disabled upload trigger** — breaking the running
system — and will **collide on the boto3-created Lambda names**.

**Until CDK reconciliation is complete and `cdk diff` is empty, deploy ONLY via
boto3 `update_function_code` (see docs/RUNBOOK.md).**

Live specs captured for the reconciliation: `_TMP_/cdk_capture/`.
