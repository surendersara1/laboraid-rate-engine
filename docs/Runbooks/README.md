# Runbooks

Operational + customer-facing documents the product produces and the team
uses day-to-day.

## Product walkthrough (CTO / Technical Director audience — primary deck)

> **Live SPA** rendering of these three docs (with the mermaid diagram + tabs + sidebar TOC):
> **https://d3ggwschjt81wu.cloudfront.net/product-walkthrough.html**
>
> Built from these markdown sources at [`../spa/`](../spa/). Edit the MDs below and re-run
> `python docs/spa/deploy.py` to refresh.

| File | Audience | What it covers |
|---|---|---|
| [PRODUCT_END_TO_END_FLOW.md](PRODUCT_END_TO_END_FLOW.md) | NBS CTO + Technical Directors + LaborAid CTO | **14-step flow** from "user uploads a PDF" → "Calculator consumes published rates." Every service, every Lambda, every Bedrock call, every error condition, every observability hook in sequence. |
| [PRODUCT_SERVICE_INVENTORY.md](PRODUCT_SERVICE_INVENTORY.md) | Same as above (slide-deck appendix) | Every Lambda (17), every SFN state, every Bedrock/Textract surface, every DDB table + Aurora schema, recommended CloudWatch alarms, $/day cost model. |
| [PRODUCT_ERROR_AND_LOGGING_REFERENCE.md](PRODUCT_ERROR_AND_LOGGING_REFERENCE.md) | SRE / on-call | Every HTTP code with body, every SFN retry policy, every log group, Powertools schema, CloudWatch Insights replay query, production SLOs, hardening checklist. |

## Ops + customer-facing

| File | Audience | What it covers |
|---|---|---|
| [RUNBOOK.md](RUNBOOK.md) | Ops | Deploy procedure, alarms, kernel regression gate, rollback, plus a "What if…" Q&A section for the questions that come up in conversation. |
| [DEPLOY.md](DEPLOY.md) | Engineering | Step-by-step CDK + UI deploy procedure. |
| [ONBOARDING.md](ONBOARDING.md) | New developers | Clone-and-go setup for the repo. |
| [extraction_flow_for_client.md](extraction_flow_for_client.md) | Client walkthrough | End-to-end story: how a Rate Notice + CBA become one rate sheet, 11 steps. The doc to show the customer. |
| [customer_pdf_extraction_log.md](customer_pdf_extraction_log.md) | Engineering + customer success | Running journal of every customer-PDF batch we've processed, newest first. Used to track quality + identify normalization gaps. |
| [gap_report_483_2026-01-01.md](gap_report_483_2026-01-01.md) | Reviewer + customer | First-cut per-period gap analysis, plain English. Next step is auto-generating one of these for every published period. |

See also [../Design/](../Design/) for engineering specs, architecture,
and earlier design docs.
