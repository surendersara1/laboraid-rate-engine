# Runbooks

Operational + customer-facing documents the product produces and the team
uses day-to-day.

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
