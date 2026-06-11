# LaborAid Rate Engine — Error Conditions & Logging Reference

> **Audience.** SRE / on-call. Field-level reference; the narrative lives in
> [`CTO_END_TO_END_FLOW.md`](CTO_END_TO_END_FLOW.md).
> **Read this as.** "Something went wrong at step N — where do I look first?"

---

## 1. End-to-end error catalogue (HTTP + SFN + downstream)

### Tier 1 — Upload (UI ↔ API)

| Code | Where | Trigger | What the UI shows | Recovery |
|---|---|---|---|---|
| `401 Unauthorized` | API GW authorizer | JWT missing, expired, or signature invalid | Login redirect | Sign in again |
| `403 Forbidden` | Per-route authz Lambda | `cognito:groups` doesn't include required group | "You don't have access" banner | Group grant via Cognito console |
| `400 Bad Request` | upload-presign | Missing `filename` / `batch_id` / `content_hash` | Inline form error | Fix payload |
| `409 Duplicate` | upload-presign | DDB `file_hashes` already has `{tenant, content_hash}` | "Already uploaded" toast | Intended — dedup |
| `500 Internal` | upload-presign | KMS Decrypt denied (IAM drift) | Generic error toast | IAM fix; retry |
| `503 Service Unavailable` | API GW | Burst throttle or upstream Lambda init timeout | "Service busy — retrying" | Client backoff (8× exponential) |

### Tier 2 — Step Functions states

Every state catches its own retryable failures; unhandled failures bubble to the top-level catch which routes to `PipelineFailed`. The pipeline never silently terminates.

| State | Likely failure | Default retry policy | Final disposition |
|---|---|---|---|
| **OCRPreprocess** | Textract `UnsupportedDocumentException` | 2× on Lambda 5xx only | Handler returns `method="textract_failed"`; pipeline continues |
| **OCRPreprocess** | Textract async timeout (>13 min) | — | `textract_failed`; vision-only path runs next |
| **OCRPreprocess** | pypdf crash on malformed PDF | — | Treats as `text_layer_present` (fail open) |
| **OCRPreprocess** | Lambda 15-min timeout | 2× | Top-level catch → `PipelineFailed` |
| **Classify** | Bedrock `ThrottlingException` | 3× | If still fails → `PipelineFailed` |
| **Classify** | Claude returns "cannot classify" | (no retry — domain error) | `PipelineFailed` with cause "not a rate document" |
| **Classify** | Bedrock Guardrail blocks | (no retry) | `PipelineFailed` |
| **GetAgentConfig** | KMS Decrypt denied on agent-config CMK | DDB-native retry | `PipelineFailed` |
| **ExtractViaAgent** | AgentCore `RuntimeClientError` | 2× (5s, 10s) | `PipelineFailed` |
| **ExtractViaAgent** | Lambda 15-min timeout | (no retry — already long) | `PipelineFailed` |
| **ExtractViaAgent** | llm-extractor JSON parse error | (handler retries once with trailing-comma cleanup) | If still fails → emits CSV with `_parse_error=true`; publisher writes 0 cells; reviewer triages in UI |
| **PublishToAurora** | Aurora cold start (`BadRequestException`) | 3× | `PipelineFailed` |
| **PublishToAurora** | xlsx-renderer 5xx | (publisher swallows) | Aurora write succeeds; xlsx null in UI; run `_TMP_/backfill_xlsx.py` |
| **PublishToAurora** | Master validation crash | (publisher swallows) | Cells written without `master_dispositions`; UI shows empty panel; investigate `lambdas/shared/master_validation.py` logs |

### Tier 3 — Approval API (M6 dual-control)

| Code | Trigger | Body |
|---|---|---|
| `422 review_queue_not_empty` | UI sent `review_queue_empty: false` (review queue still has unresolved items) | `{"error": "review_queue_not_empty"}` |
| `409 not_approvable` | State already `approved` or `published` | `{"error": "not_approvable", "approval_state": "..."}` |
| `409 dual_control_violation` | Stage 2 attempted by same actor as `reviewed_by` | `{"error": "dual_control_violation", "reviewed_by": "..."}` |
| `404 rate_period_not_found` | The (local, period) doesn't exist | `{"error": "rate_period_not_found"}` |
| `200 OK` stage 1 | Reviewer marked sheet reviewed | `{"approval_state": "pending_approval", "reviewed_by": "<actor>"}` |
| `200 OK` stage 2 | Approver approved | `{"approval_state": "approved", "approved_by": "<actor>"}` |

### Tier 4 — Publish API

| Code | Trigger | Body |
|---|---|---|
| `409 not_approved` | Authoritative Aurora state ≠ `approved` (e.g. UI sent `approval_state: "approved"` but Aurora says `pending_review`) | `{"error": "not_approved", "approval_state": "..."}` |
| `404 not_found` | No such `(local, period)` | `{"error": "not_found"}` |
| `200 OK` | Aurora flipped to `published` | `{"approval_state": "published", "published_by": "..."}` |

---

## 2. Where every log lives

### CloudWatch Log Groups

```
/aws/lambda/laboraid-dev-l2-fn-upload-presign       # presign + dedup
/aws/lambda/laboraid-dev-l2-fn-ratesheet-list       # /v1/inbox
/aws/lambda/laboraid-dev-l2-fn-ratesheet-get        # /v1/.../rate-sheets/:p
/aws/lambda/laboraid-dev-l2-fn-ratesheet-approve    # M6 state machine
/aws/lambda/laboraid-dev-l2-fn-ratesheet-reject
/aws/lambda/laboraid-dev-l2-fn-ratesheet-unapprove
/aws/lambda/laboraid-dev-l2-fn-ratesheet-publish    # gate read
/aws/lambda/laboraid-dev-l2-fn-ratesheet-audit
/aws/lambda/laboraid-dev-l2-fn-ratesheet-rework
/aws/lambda/laboraid-dev-l2-fn-cell-override
/aws/lambda/laboraid-dev-l2-fn-cell-comment
/aws/lambda/laboraid-dev-l4-fn-classifier            # Bedrock classify
/aws/lambda/laboraid-dev-l4-fn-ocr-preprocess        # NEW — pypdf + Textract
/aws/lambda/laboraid-dev-l4-fn-llm-extractor         # Bedrock vision + OCR hint
/aws/lambda/laboraid-dev-l3-fn-extractor-invoker     # AgentCore call
/aws/lambda/laboraid-dev-l4-fn-publisher             # Aurora + M3 + renderer
/aws/lambda/laboraid-dev-l7-fn-renderer-xlsx         # M4 two-tab xlsx
aws/bedrock-agentcore/runtimes/<runtime-id>          # ExtractorAgent container
states-PipelineLogs-<hash>                           # SFN state transitions
```

### Powertools structured fields (every Lambda log line)

```json
{
  "level": "INFO",
  "location": "handler:712",
  "message": "llm-extractor: invoking Bedrock with doc_type=rate_notice local=483 ocr_hint_chars=1240 for key=laboraid/uploads/.../2026.01.01.483 Rate Notice.pdf",
  "timestamp": "2026-06-10T01:23:45.678Z",
  "service": "laboraid-llm-extractor",
  "cold_start": false,
  "function_name": "laboraid-dev-l4-fn-llm-extractor",
  "function_memory_size": 2048,
  "function_arn": "arn:...",
  "function_request_id": "abc-123",
  "correlation_id": "39ba597b-22d2-6a9c-4ff3-57652301df71",
  "xray_trace_id": "1-...-..."
}
```

`correlation_id` is set from the EventBridge event ID at SFN start, so a CloudWatch Insights query like:

```
fields @timestamp, @message, function_name
| filter correlation_id = "39ba597b-22d2-6a9c-4ff3-57652301df71"
| sort @timestamp asc
```

returns every log line from every Lambda that touched a single upload, in order. This is the primary debug tool.

---

## 3. Stats emitted at each step (the numbers the demo can quote)

| Step | Key metrics in the result payload | Where to graph |
|---|---|---|
| OCRPreprocess | `method`, `page_count`, `text_chars_sampled`, `table_count`, `kv_count`, `duration_ms` | CloudWatch custom metric `laboraid/ocr_method` (EMF — to be wired) |
| Classify | `classify_method` (`filename` vs `claude`), Claude `input_tokens`, `output_tokens` | `AWS/Bedrock` |
| LLM Extractor | `gap_count`, `extracted_rows`, `method` (`kernel`/`llm_claude`), Claude tokens | `AWS/Bedrock` + custom |
| Publisher | `cells_written`, `master_disposition_summary {total, ok, drift, not_found}` | UI inline panel |
| xlsx Renderer | size, n_rows, n_cols (in handler logs) | log search |
| Approve | actor, stage, transition_from→transition_to | `audit_log` |

`audit_log` schema:

```sql
SELECT at, tenant, actor, action, details
FROM audit_log
WHERE details->>'local' = '483'
  AND details->>'period' = '2026-01-01'
ORDER BY at ASC;
```

returns the full human-action timeline for any rate sheet — submit, review, approve, publish, override, comment.

---

## 4. Replay playbook

| Failure | Replay path |
|---|---|
| SFN execution failed at a specific state | Step Functions console → "Redrive" (available on STANDARD workflows) — picks up at the failed state with original input |
| SFN succeeded but Aurora write looks wrong | `python _TMP_/nuke_all.py --yes` (clears state) → re-upload PDFs via UI or `_TMP_/upload_all_5.py` |
| xlsx never produced | `python _TMP_/backfill_xlsx.py` — pivots Aurora cells, calls renderer, updates `rate_periods.source_files` |
| Master validation needs re-run after schema fix | `python _TMP_/backfill_master_validation.py` (when needed — run on individual period_id) |
| Onboarding checklist needs reset | UI: clear localStorage key `laboraid:onboard:<local>` |
| OCR layout JSON missing for a key | Direct-invoke `ocr-preprocess` with `{"s3_key": "<key>"}` — writes layout.json |

---

## 5. SLOs we'll commit to in production (not POC)

| Slice | SLO | Measurement window |
|---|---|---|
| API GW 5xx rate | < 0.1% | Rolling 30 days |
| SFN execution success rate (excluding domain-error PipelineFailed) | > 99.5% | Rolling 30 days |
| OCRPreprocess duration p95 | < 30 s for digital PDFs · < 10 min for async Textract | Rolling 7 days |
| Classify duration p95 | < 8 s | Rolling 7 days |
| Extract (kernel) p95 | < 60 s | Rolling 7 days |
| Extract (LLM) p95 | < 180 s | Rolling 7 days |
| Aurora p95 read latency | < 200 ms | Rolling 7 days |
| End-to-end upload → `pending_review` p95 | < 5 min (kernel path) · < 8 min (LLM path) | Rolling 30 days |

Each SLO gets a CloudWatch composite alarm + a Slack channel. PagerDuty for 5xx + extract-failure-rate; Slack-only for the latency SLOs.

---

## 6. Production hardening checklist (the gap between POC and prod)

- [ ] Move `_TMP_/` cleanup + replay scripts to durable runbooks (Lambda + SFN backfill flow)
- [ ] Add DLQ on EventBridge → SFN rule + alarm on DLQ depth
- [ ] WAF in front of API GW with rate-limit rule + geo block
- [ ] CloudFront distribution for the UI + custom domain + ACM cert
- [ ] Aurora Multi-AZ + read replica + 7-day PITR
- [ ] Bedrock per-tenant budget guardrail + per-user spend cap
- [ ] Per-route API GW throttle config (currently account-default)
- [ ] Move `Onboard.tsx` checklist to DDB `tenants.onboarding_checklist`
- [ ] Master sheets: admin UI to refresh + per-version pin on `rate_periods.master_version_id`
- [ ] CloudWatch alarms (the table in §5 above) + PagerDuty integration
- [ ] X-Ray sampling tuned (100% in dev → 10% with priority sampling in prod)
- [ ] SOC2 evidence pack: AWS Audit Manager mapping + scheduled report
