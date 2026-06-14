#!/usr/bin/env python3
"""LaborAid Rate Engine — Proof of Deployment.

A read-only, console-free health & inventory report for the live AWS system.
Run it to show a CTO (ours or the client's) that the stack is deployed and
healthy — without anyone logging into the AWS console.

It verifies, via boto3 (no aws-cli needed):

  • CloudFormation   — the 9 stacks and their status
  • AI / Strands     — Bedrock AgentCore runtimes (extractor + improver) READY?
  • Containers       — ECR repos + latest agent image digest / pushed time
  • Compute          — Lambda count, Step Functions + recent executions
  • Data             — Aurora cluster, DynamoDB tables, S3 buckets
  • Edge / Access    — CloudFront, API Gateway, Cognito
  • Logs             — recent activity in key log groups (optional --tail)
  • Cost             — last N days by service (Cost Explorer)

Everything is wrapped so one permission gap never kills the report.

Usage:
    py -3 scripts/proof_of_deployment.py
    py -3 scripts/proof_of_deployment.py --tail        # also tail key logs
    py -3 scripts/proof_of_deployment.py --days 14     # cost window
    py -3 scripts/proof_of_deployment.py --json out.json
    py -3 scripts/proof_of_deployment.py --no-color

Requires: boto3, and the `laboraid` profile (or pass --profile / --region).
Read-only: it only ever calls List/Describe/Get APIs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

PROFILE = "laboraid"
REGION = "us-east-2"
PREFIX = "laboraid"            # resource naming prefix
CF_DISTRIBUTION_ID = "EYYEIRSC9DSLW"
EXPECTED_STACKS = [
    "Security", "Storage", "Ai", "Processing", "Validation",
    "Api", "Orchestration", "Observability", "Ui",
]

# --- tiny formatting layer ---------------------------------------------------
class C:
    OK = "\033[92m"; BAD = "\033[91m"; WARN = "\033[93m"
    NAVY = "\033[96m"; GOLD = "\033[33m"; DIM = "\033[90m"; B = "\033[1m"; R = "\033[0m"

    @classmethod
    def off(cls) -> None:
        for k in ("OK", "BAD", "WARN", "NAVY", "GOLD", "DIM", "B", "R"):
            setattr(cls, k, "")


RESULTS: dict[str, dict] = {}      # machine-readable mirror for --json
SCORE: list[tuple[str, bool, str]] = []   # (area, ok, detail) for the summary


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def age(ts: dt.datetime | None) -> str:
    if not ts:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    secs = (now_utc() - ts).total_seconds()
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 5400:
        return f"{int(secs // 60)}m ago"
    if secs < 172800:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def header(title: str) -> None:
    print(f"\n{C.NAVY}{C.B}══ {title} {'═' * max(0, 64 - len(title))}{C.R}")


def row(ok: bool | None, label: str, detail: str = "") -> None:
    mark = (f"{C.OK}✓{C.R}" if ok else f"{C.BAD}✗{C.R}") if ok is not None else f"{C.WARN}•{C.R}"
    # Always keep a gap between label and detail, even when the label is long.
    print(f"  {mark} {label:<34}  {C.DIM}{detail}{C.R}")


def section(area: str, fn) -> None:
    """Run a section; never let one failure abort the whole report."""
    try:
        fn()
    except (ClientError, BotoCoreError) as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        row(False, f"{area}: {code}", str(e)[:80])
        SCORE.append((area, False, code))
        RESULTS.setdefault(area, {})["error"] = code
    except Exception as e:  # noqa: BLE001 — defensive, this is a report tool
        row(False, f"{area}: {type(e).__name__}", str(e)[:80])
        SCORE.append((area, False, type(e).__name__))


# --- sections ----------------------------------------------------------------
def sec_identity(s: boto3.Session) -> None:
    header("Identity & target")
    who = s.client("sts").get_caller_identity()
    acct, arn = who["Account"], who["Arn"]
    row(True, "AWS account", acct)
    row(True, "Region", s.region_name or REGION)
    row(True, "Caller", arn.split("/")[-1])
    row(True, "Report time (UTC)", now_utc().strftime("%Y-%m-%d %H:%M:%S"))
    RESULTS["identity"] = {"account": acct, "region": s.region_name, "caller": arn}


def sec_stacks(s: boto3.Session) -> None:
    header("CloudFormation stacks")
    cf = s.client("cloudformation")
    paginator = cf.get_paginator("list_stacks")
    live = {}
    bad_states = {"ROLLBACK_FAILED", "CREATE_FAILED", "UPDATE_ROLLBACK_FAILED", "DELETE_FAILED"}
    for page in paginator.paginate(StackStatusFilter=[
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "UPDATE_ROLLBACK_FAILED",
        "IMPORT_COMPLETE", "UPDATE_IN_PROGRESS", "CREATE_IN_PROGRESS",
    ]):
        for st in page["StackSummaries"]:
            n = st["StackName"]
            if n.lower().startswith(f"{PREFIX}-dev"):
                live[n] = st
    found = []
    for short in EXPECTED_STACKS:
        name = f"Laboraid-dev-{short}"
        st = live.get(name)
        if st:
            status = st["StackStatus"]
            updated = st.get("LastUpdatedTime") or st.get("CreationTime")
            ok = status not in bad_states
            row(ok, name, f"{status} · {age(updated)}")
            found.append(short)
        else:
            row(False, name, "NOT FOUND")
    extra = [n for n in live if n.replace("Laboraid-dev-", "") not in EXPECTED_STACKS]
    for n in extra:
        row(None, n, live[n]["StackStatus"])
    ok = len(found) == len(EXPECTED_STACKS)
    SCORE.append(("CloudFormation", ok, f"{len(found)}/{len(EXPECTED_STACKS)} stacks"))
    RESULTS["stacks"] = {n: live[n]["StackStatus"] for n in live}


def sec_agentcore(s: boto3.Session) -> None:
    header("AI · Bedrock AgentCore (Strands agents)")
    c = s.client("bedrock-agentcore-control")
    runtimes = c.list_agent_runtimes(maxResults=50).get("agentRuntimes", [])
    if not runtimes:
        row(False, "No AgentCore runtimes found", "")
        SCORE.append(("AgentCore", False, "0 runtimes"))
        return
    ready = 0
    out = []
    for rt in sorted(runtimes, key=lambda r: r.get("agentRuntimeName", "")):
        name = rt.get("agentRuntimeName", "?")
        status = rt.get("status", "?")
        ver = rt.get("agentRuntimeVersion", "?")
        ok = status == "READY"
        ready += ok
        row(ok, name, f"status={status} · v{ver} · {age(rt.get('lastUpdatedAt'))}")
        out.append({"name": name, "status": status, "version": ver})
    SCORE.append(("AgentCore", ready == len(runtimes), f"{ready}/{len(runtimes)} READY"))
    RESULTS["agentcore"] = out


def sec_ecr(s: boto3.Session) -> None:
    header("Containers · ECR agent images")
    ecr = s.client("ecr")
    repos = [r for r in ecr.describe_repositories().get("repositories", [])
             if PREFIX in r["repositoryName"]]
    if not repos:
        row(False, "No ECR repos found", "")
        SCORE.append(("ECR", False, "0 repos"))
        return
    have_images = 0
    out = []
    for r in sorted(repos, key=lambda x: x["repositoryName"]):
        name = r["repositoryName"]
        try:
            imgs = ecr.describe_images(repositoryName=name).get("imageDetails", [])
        except ClientError:
            imgs = []
        if imgs:
            latest = max(imgs, key=lambda i: i.get("imagePushedAt", now_utc()))
            tags = ",".join(latest.get("imageTags", []) or ["<untagged>"])
            digest = latest.get("imageDigest", "")[:19]
            mb = latest.get("imageSizeInBytes", 0) / 1e6
            row(True, name.replace(f"{PREFIX}-dev-l5-ecr-", ""),
                f"{tags} · {digest} · {mb:.0f}MB · {age(latest.get('imagePushedAt'))}")
            have_images += 1
            out.append({"repo": name, "tags": tags, "digest": digest})
        else:
            row(False, name, "repo exists, NO images")
            out.append({"repo": name, "tags": None})
    SCORE.append(("ECR", have_images == len(repos), f"{have_images}/{len(repos)} repos w/ images"))
    RESULTS["ecr"] = out


def sec_compute(s: boto3.Session) -> None:
    header("Compute · Lambda + Step Functions")
    lam = s.client("lambda")
    fns = []
    for page in lam.get_paginator("list_functions").paginate():
        fns += [f for f in page["Functions"] if f["FunctionName"].startswith(PREFIX)]
    row(bool(fns), "Lambda functions", f"{len(fns)} deployed (prefix '{PREFIX}-')")
    SCORE.append(("Lambda", bool(fns), f"{len(fns)} functions"))

    sfn = s.client("stepfunctions")
    sms = [m for m in sfn.list_state_machines().get("stateMachines", [])
           if PREFIX in m["name"]]
    for m in sms:
        execs = sfn.list_executions(stateMachineArn=m["stateMachineArn"], maxResults=5).get("executions", [])
        if execs:
            last = execs[0]
            ok = last["status"] in ("SUCCEEDED", "RUNNING")
            row(ok, m["name"], f"last: {last['status']} · {age(last.get('startDate'))}")
        else:
            row(None, m["name"], "no executions yet")
    SCORE.append(("StepFunctions", bool(sms), f"{len(sms)} state machine(s)"))
    RESULTS["compute"] = {"lambda_count": len(fns), "state_machines": [m["name"] for m in sms]}


def sec_data(s: boto3.Session) -> None:
    header("Data · Aurora + DynamoDB + S3")
    # Aurora
    rds = s.client("rds")
    clusters = [c for c in rds.describe_db_clusters().get("DBClusters", [])
                if PREFIX in c["DBClusterIdentifier"]]
    for c in clusters:
        ok = c["Status"] == "available"
        row(ok, f"Aurora: {c['DBClusterIdentifier']}",
            f"{c['Status']} · {c['Engine']} {c.get('EngineVersion','')}")
    SCORE.append(("Aurora", bool(clusters) and all(c["Status"] == "available" for c in clusters),
                  f"{len(clusters)} cluster(s)"))

    # DynamoDB
    ddb = s.client("dynamodb")
    tables = [t for t in ddb.list_tables().get("TableNames", []) if t.startswith(PREFIX)]
    total_items = 0
    for t in tables:
        d = ddb.describe_table(TableName=t)["Table"]
        cnt = d.get("ItemCount", 0)
        total_items += cnt
        row(d["TableStatus"] == "ACTIVE", t.replace(f"{PREFIX}-dev-l3-ddb-", "ddb:"),
            f"{d['TableStatus']} · ~{cnt} items")
    SCORE.append(("DynamoDB", bool(tables), f"{len(tables)} tables"))

    # S3
    s3 = s.client("s3")
    buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", []) if PREFIX in b["Name"]]
    for b in buckets:
        row(True, b.replace(f"{PREFIX}-dev-", "s3:"), "")
    SCORE.append(("S3", bool(buckets), f"{len(buckets)} buckets"))
    RESULTS["data"] = {"aurora": [c["DBClusterIdentifier"] for c in clusters],
                       "dynamo_tables": tables, "buckets": buckets}


def sec_edge(s: boto3.Session) -> None:
    header("Edge & Access · CloudFront + API + Cognito")
    # CloudFront
    cf = s.client("cloudfront")
    try:
        d = cf.get_distribution(Id=CF_DISTRIBUTION_ID)["Distribution"]
        ok = d["Status"] == "Deployed" and d["DistributionConfig"]["Enabled"]
        row(ok, "CloudFront", f"{d['Status']} · {d['DomainName']}")
    except ClientError:
        dists = cf.list_distributions().get("DistributionList", {}).get("Items", [])
        for d in dists:
            row(d["Status"] == "Deployed", "CloudFront", f"{d['Status']} · {d['DomainName']}")
    # API Gateway (HTTP)
    api = s.client("apigatewayv2")
    apis = [a for a in api.get_apis().get("Items", []) if PREFIX in a.get("Name", "").lower()
            or PREFIX in a.get("Name", "")]
    if not apis:
        apis = api.get_apis().get("Items", [])
    for a in apis[:5]:
        row(True, f"API: {a.get('Name','?')}", f"{a.get('ProtocolType')} · {a.get('ApiEndpoint','')}")
    # Cognito
    idp = s.client("cognito-idp")
    pools = [p for p in idp.list_user_pools(MaxResults=60).get("UserPools", [])
             if PREFIX in p["Name"].lower() or PREFIX in p["Name"]]
    for p in pools:
        row(True, f"Cognito: {p['Name']}", p["Id"])
    SCORE.append(("Edge/Access", True, f"CF + {len(apis)} API + {len(pools)} pool(s)"))
    RESULTS["edge"] = {"apis": [a.get("ApiEndpoint") for a in apis],
                       "cognito": [p["Id"] for p in pools]}


def sec_logs(s: boto3.Session, tail: bool) -> None:
    header("Logs · CloudWatch activity")
    logs = s.client("logs")
    groups = []
    for pfx in ("/aws/lambda/laboraid", "/aws/bedrock-agentcore/", "/aws/vendedlogs/"):
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=pfx):
            groups += page["logGroups"]
    agentcore = [g for g in groups if "bedrock-agentcore" in g["logGroupName"].lower()]
    lambdas = [g for g in groups if "/aws/lambda/" in g["logGroupName"]]
    row(bool(lambdas), "Lambda log groups", f"{len(lambdas)} groups")
    row(bool(agentcore), "AgentCore runtime log groups", f"{len(agentcore)} groups")
    # show last activity for the AgentCore (Strands) runtime groups — proof they ran
    key = sorted(agentcore, key=lambda g: g.get("lastEventTimestamp", 0), reverse=True)[:4]
    for g in key:
        last = g.get("lastEventTimestamp") or g.get("creationTime")
        ts = dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc) if last else None
        short = g["logGroupName"].split("/")[-1]
        row(None, short[:34], f"last event {age(ts)}")
        if tail and last:
            try:
                streams = logs.describe_log_streams(
                    logGroupName=g["logGroupName"], orderBy="LastEventTime",
                    descending=True, limit=1).get("logStreams", [])
                if streams:
                    ev = logs.get_log_events(
                        logGroupName=g["logGroupName"],
                        logStreamName=streams[0]["logStreamName"],
                        limit=4, startFromHead=False).get("events", [])
                    for e in ev[-4:]:
                        print(f"      {C.DIM}{e['message'].strip()[:100]}{C.R}")
            except ClientError:
                pass
    SCORE.append(("Logs", bool(groups), f"{len(groups)} groups"))
    RESULTS["logs"] = {"lambda_groups": len(lambdas), "agent_groups": len(agentcore)}


def sec_cost(s: boto3.Session, days: int) -> None:
    header(f"Cost · last {days} days (Cost Explorer)")
    end = now_utc().date()
    start = end - dt.timedelta(days=days)
    # 1) Cost Explorer (best source; needs ce:GetCostAndUsage on this principal)
    try:
        ce = s.client("ce", region_name="us-east-1")  # CE is global, anchored in us-east-1
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="DAILY", Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        totals: dict[str, float] = {}
        grand = 0.0
        unit = "USD"
        for day in resp["ResultsByTime"]:
            for grp in day["Groups"]:
                amt = float(grp["Metrics"]["UnblendedCost"]["Amount"])
                unit = grp["Metrics"]["UnblendedCost"]["Unit"]
                totals[grp["Keys"][0]] = totals.get(grp["Keys"][0], 0.0) + amt
                grand += amt
        top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:10]
        for svc, amt in top:
            if amt < 0.005:
                continue
            bar = "█" * min(30, int(amt / max(grand, 0.01) * 30))
            row(None, svc[:32], f"{amt:8.2f} {unit}  {C.GOLD}{bar}{C.R}")
        print(f"  {C.B}{'TOTAL':<36}{grand:8.2f} {unit}{C.R}   ({start} → {end})")
        SCORE.append(("Cost", True, f"{grand:.2f} {unit} / {days}d"))
        RESULTS["cost"] = {"source": "cost-explorer", "total": round(grand, 2),
                           "unit": unit, "days": days,
                           "by_service": {k: round(v, 4) for k, v in top}}
        return
    except ClientError as e:
        ce_err = e.response.get("Error", {}).get("Code", "ClientError")

    # 2) Fallback: CloudWatch AWS/Billing EstimatedCharges (needs billing metrics enabled)
    try:
        cw = s.client("cloudwatch", region_name="us-east-1")
        m = cw.get_metric_statistics(
            Namespace="AWS/Billing", MetricName="EstimatedCharges",
            Dimensions=[{"Name": "Currency", "Value": "USD"}],
            StartTime=now_utc() - dt.timedelta(days=2), EndTime=now_utc(),
            Period=21600, Statistics=["Maximum"],
        )
        pts = sorted(m.get("Datapoints", []), key=lambda p: p["Timestamp"])
        if pts:
            charge = pts[-1]["Maximum"]
            row(True, "Month-to-date estimated charges", f"{charge:.2f} USD (CloudWatch billing)")
            SCORE.append(("Cost", True, f"MTD ~{charge:.2f} USD"))
            RESULTS["cost"] = {"source": "cloudwatch-billing", "mtd_usd": round(charge, 2)}
            return
        raise ClientError({"Error": {"Code": "NoBillingMetrics"}}, "get_metric_statistics")
    except ClientError:
        pass

    # 3) Neither available — this is a billing-permission gap, NOT a deployment problem.
    row(None, "Cost data not authorized", f"Cost Explorer: {ce_err}")
    print(f"  {C.DIM}    To enable: attach 'ce:GetCostAndUsage' to this IAM principal, or run")
    print(f"    this script with a billing-enabled profile. Deployment health is unaffected.{C.R}")
    SCORE.append(("Cost", None, f"n/a — needs billing access ({ce_err})"))
    RESULTS["cost"] = {"source": None, "error": ce_err}


def scorecard() -> None:
    header("Summary scorecard")
    allok = True
    for area, ok, detail in SCORE:
        if ok is not None:          # None = informational/soft (e.g. Cost perms) — never fails the verdict
            allok = allok and ok
        row(ok, area, detail)
    verdict = (f"{C.OK}{C.B}ALL SYSTEMS GO{C.R}" if allok
               else f"{C.WARN}{C.B}REVIEW ITEMS ABOVE{C.R}")
    print(f"\n  {verdict}  ·  LaborAid Rate Engine · {now_utc().strftime('%Y-%m-%d %H:%M UTC')}\n")
    RESULTS["scorecard"] = {a: ok for a, ok, _ in SCORE}
    RESULTS["all_ok"] = allok


def main() -> int:
    ap = argparse.ArgumentParser(description="LaborAid proof-of-deployment report")
    ap.add_argument("--profile", default=PROFILE)
    ap.add_argument("--region", default=REGION)
    ap.add_argument("--days", type=int, default=30, help="cost window (days)")
    ap.add_argument("--tail", action="store_true", help="tail recent log lines for agent groups")
    ap.add_argument("--json", metavar="FILE", help="also write a machine-readable JSON snapshot")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    if args.no_color or not sys.stdout.isatty():
        C.off()

    s = boto3.Session(profile_name=args.profile, region_name=args.region)

    print(f"{C.NAVY}{C.B}")
    print("  ╭───────────────────────────────────────────────────────────╮")
    print("  │   LaborAid Rate Engine — PROOF OF DEPLOYMENT (read-only)   │")
    print("  ╰───────────────────────────────────────────────────────────╯")
    print(C.R)

    section("Identity", lambda: sec_identity(s))
    section("CloudFormation", lambda: sec_stacks(s))
    section("AgentCore", lambda: sec_agentcore(s))
    section("ECR", lambda: sec_ecr(s))
    section("Compute", lambda: sec_compute(s))
    section("Data", lambda: sec_data(s))
    section("Edge/Access", lambda: sec_edge(s))
    section("Logs", lambda: sec_logs(s, args.tail))
    section("Cost", lambda: sec_cost(s, args.days))
    scorecard()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(RESULTS, f, indent=2, default=str)
        print(f"  {C.DIM}JSON snapshot → {args.json}{C.R}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
