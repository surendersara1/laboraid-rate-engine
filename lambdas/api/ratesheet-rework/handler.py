"""Rate sheet rework Lambda (Tier 3, Spec/09 §4 L2 + docs/feature_improvement_1).

Triggered after a Business reviewer rejects a rate sheet and adds per-cell
overrides + comments. The handler:

  1. Reads the latest rate_period row (and its rate_cells) for {local, period}.
  2. Collects the rejection feedback (reason + tags) and per-cell overrides from
     DDB so they can be embedded into the new period's `rework_context`.
  3. Inserts a new `rate_periods` row with `version = N+1`, `parent_version = N`,
     state = 'pending_review', and the same source_files + canonical_json as
     the parent (so the artifact cards keep working).
  4. Copies rate_cells into the new period, applying overrides where present.
     The new cell rows carry their own UUIDs (used by future overrides/comments
     so v2's history doesn't leak into v1).
  5. Re-invokes the xlsx-renderer to refresh the Excel artifact for v2.
  6. Appends an `audit_log` row with action='rework' so the activity timeline
     surfaces the event.

Demo-time variant: the agent is NOT re-invoked. Doing so for the deterministic
704 path would produce an identical extraction anyway; the value of the rework
is the human corrections being baked in. The architectural target (SFN re-run
with `rework_context` passed to ExtractViaAgent) is preserved as a TODO so the
Path-C unions can re-prompt Claude with the rejection feedback in a follow-up.
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from typing import Any

import authz  # shared Lambda layer (/opt/python/authz.py)

ENGINE_BUS_NAME = os.environ.get("ENGINE_BUS_NAME", "")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")
INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
XLSX_RENDERER_FN = os.environ.get(
    "XLSX_RENDERER_FN", "laboraid-dev-l7-fn-renderer-xlsx"
)
EXTRACTOR_RUNTIME_ARN = os.environ.get("EXTRACTOR_RUNTIME_ARN", "")

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-api")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-api")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


def _resp(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _actor(event: dict[str, Any]) -> str:
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    return (
        claims.get("email")
        or claims.get("cognito:username")
        or claims.get("sub")
        or "unknown"
    )


def _invoke_extractor_runtime(
    union_local: str,
    s3_prefix: str,
    out_s3_key: str,
    rework_context: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """Synchronously invoke the ExtractorAgent on AgentCore Runtime in direct
    mode. The agent's invoke entrypoint forwards `direct=True` straight to the
    fat kernel tool, so for the 5 kernel unions this is deterministic — the
    returned CSV is identical to the parent's. We pass `rework_context` along
    too: the agent ignores it today (Path-A is deterministic so re-extraction
    converges anyway), but the field is required when a Path-C union later
    needs Claude re-prompted with the reviewer's feedback.

    Returns the agent's parsed JSON: {s3_key, rows, gaps, gap_count,
    extracted_rows, checksum}.
    """
    if not EXTRACTOR_RUNTIME_ARN:
        raise RuntimeError("EXTRACTOR_RUNTIME_ARN env var is not set")
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "bedrock-agentcore",
        config=Config(
            read_timeout=900, connect_timeout=10, retries={"max_attempts": 1}
        ),
    )
    payload = {
        "direct": True,
        "union": str(union_local),
        "s3_prefix": s3_prefix,
        "out_s3_key": out_s3_key,
        "rework_context": rework_context,
    }
    # AgentCore needs session id >= 33 chars.
    sid = (session_id + "-" + "0" * 33)[:64]
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=EXTRACTOR_RUNTIME_ARN,
        runtimeSessionId=sid,
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp.get("response")
    raw = (
        body.read()
        if hasattr(body, "read")
        else (body if isinstance(body, (bytes, bytearray)) else b"")
    )
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        return {
            "_raw": raw[:1000].decode("utf-8", errors="replace") if raw else "",
        }


def _comments_since_last_rework(
    rds: Any, common: dict[str, Any], local: str, period: str
) -> list[dict[str, Any]]:
    """Pull every per-cell comment for this {local, period} that landed after
    the most recent rework (or all of them on the first rework). Each entry
    keeps the cell + the comment text + actor + ts so the rework_context is a
    self-contained record of what the reviewer said about the parent version.
    Enriches each comment with the cell's package + column_name so the agent
    (or a human reading the JSON) doesn't have to cross-reference cell_ids.
    """
    sql = (
        "WITH last_rework AS ( "
        "  SELECT COALESCE(MAX(ts), 'epoch'::timestamptz) AS ts "
        "    FROM audit_log "
        "   WHERE action = 'rework' "
        "     AND details->>'local' = :local "
        "     AND details->>'period' = :period "
        ") "
        "SELECT a.id, "
        "       to_char(a.ts AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"'), "
        "       a.actor, "
        "       a.details->>'cell_id' AS cell_id, "
        "       a.details->>'text' AS text, "
        "       rc.package, rc.column_name "
        "  FROM audit_log a "
        "  LEFT JOIN rate_cells rc "
        "    ON rc.id::text = a.details->>'cell_id' "
        " WHERE a.action = 'comment' "
        "   AND a.details->>'local' = :local "
        "   AND a.details->>'period' = :period "
        "   AND a.ts > (SELECT ts FROM last_rework) "
        " ORDER BY a.ts ASC"
    )
    r = rds.execute_statement(
        **common,
        sql=sql,
        parameters=[
            {"name": "local", "value": {"stringValue": local}},
            {"name": "period", "value": {"stringValue": period}},
        ],
    )
    out: list[dict[str, Any]] = []
    for row in r.get("records", []):
        out.append({
            "id": row[0].get("longValue"),
            "ts": row[1].get("stringValue"),
            "actor": row[2].get("stringValue"),
            "cell_id": row[3].get("stringValue"),
            "text": row[4].get("stringValue"),
            "package": row[5].get("stringValue") if not row[5].get("isNull") else None,
            "column_name": row[6].get("stringValue") if not row[6].get("isNull") else None,
        })
    return out


def _rebuild_csv_with_overrides(
    s3: Any,
    src_bucket: str,
    src_key: str,
    overrides_by_position: dict[tuple[str, str], str],
) -> bytes:
    """Read the parent version's canonical CSV and emit a new CSV with the
    overridden values patched in. We preserve the column order + classification
    rows exactly — only specific (classification, column) cells change. This
    keeps the xlsx-renderer downstream code unchanged.

    `overrides_by_position` is keyed by (package, column_name) → new_value (str).
    The kernel CSV puts the classification value under a "Package" header
    (consistently across unions); we locate it by name rather than position so
    the function works regardless of how many lead context columns each profile
    prepends (Union Group, Trade, Local, Zone, …).
    """
    raw = s3.get_object(Bucket=src_bucket, Key=src_key)["Body"].read()
    reader = csv.reader(io.StringIO(raw.decode("utf-8")))
    rows = list(reader)
    if not rows:
        return raw
    header = rows[0]
    if "Package" not in header:
        print(
            f"[rework] CSV header lacks 'Package' column; cannot patch overrides. "
            f"header={header[:8]}..."
        )
        return raw
    pkg_idx = header.index("Package")
    col_lookup = {col: idx for idx, col in enumerate(header) if idx != pkg_idx}
    patched = 0
    for r in rows[1:]:
        if not r:
            continue
        classification = r[pkg_idx] if pkg_idx < len(r) else ""
        for col_name, idx in col_lookup.items():
            key = (classification, col_name)
            if key in overrides_by_position and idx < len(r):
                # Use the override value as-is (already string-formatted by the
                # cell-override Lambda's `str(float(value))` path).
                r[idx] = overrides_by_position[key]
                patched += 1
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows[1:])
    print(f"[rework] patched {patched} CSV cells (Package col={pkg_idx})")
    return out.getvalue().encode("utf-8")


def _latest_overrides_for_period(local: str, period: str) -> dict[str, dict[str, Any]]:
    """Read all manual overrides for {local, period} from Aurora cell_corrections
    (kind='override'); newest per cell wins. Returns {cell_id: {value, package,
    column_name, justification, actor}} — the shape the CSV rebuilder consumes."""
    import boto3

    rds = boto3.client("rds-data")
    resp = rds.execute_statement(
        resourceArn=os.environ["AURORA_CLUSTER_ARN"],
        secretArn=os.environ["AURORA_SECRET_ARN"],
        database="laboraid",
        sql=(
            "SELECT cell_id::text, new_value, package, column_name, reason, actor "
            "  FROM cell_corrections "
            " WHERE union_local = :local AND period = :period AND kind = 'override' "
            " ORDER BY created_at DESC"
        ),
        parameters=[
            {"name": "local", "value": {"stringValue": str(local)}},
            {"name": "period", "value": {"stringValue": period}},
        ],
    )
    by_cell: dict[str, dict[str, Any]] = {}
    for row in resp.get("records", []):
        cell_id = row[0].get("stringValue")
        if not cell_id or cell_id in by_cell:
            continue  # newest already kept (ORDER BY created_at DESC)
        by_cell[cell_id] = {
            "value": None if row[1].get("isNull") else row[1].get("stringValue"),
            "package": "" if row[2].get("isNull") else row[2].get("stringValue", ""),
            "column_name": "" if row[3].get("isNull") else row[3].get("stringValue", ""),
            "justification": "" if row[4].get("isNull") else row[4].get("stringValue", ""),
            "actor": "" if row[5].get("isNull") else row[5].get("stringValue", ""),
        }
    return by_cell


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        # Distinguish API-Gateway-routed invocations from the self-async dispatch
        # we use for mode=ai. The self-async path carries `_async: true` in the
        # event root (not nested under requestContext / body) and skips authz
        # because the calling principal is the Lambda's own role.
        is_self_async = event.get("_async") is True

        if not is_self_async:
            denied = authz.enforce_groups(event, ["Business"])
            if denied:
                return denied

        if is_self_async:
            # Repacked event from the initial dispatch: path params + body are
            # passed as top-level fields, not nested under API Gateway shape.
            params = event.get("pathParameters") or {}
            body = event.get("body_json") or {}
            actor = event.get("actor") or "system"
        else:
            params = event.get("pathParameters") or {}
            body = json.loads(event.get("body") or "{}")
            actor = _actor(event)

        local = str(params.get("local") or "")
        period = str(params.get("period") or "")
        if not local or not period:
            return _resp({"error": "missing_path_params"}, 400)

        note = body.get("note") or ""
        # mode: "merge" (default, inline merge) or "ai" (re-invoke ExtractorAgent
        # so the kernel re-extracts fresh from the source PDF, then overrides
        # are applied on top). For 704/483/537/281/821 the kernel is
        # deterministic so the ai output equals merge for now — the path
        # exists so future Path-C unions can re-prompt Claude with
        # rework_context in its message body.
        mode = (body.get("mode") or "merge").lower()
        if mode not in ("merge", "ai"):
            return _resp({"error": "invalid_mode", "got": mode}, 400)

        # API Gateway HTTP API has a hard 29s integration timeout. The AI path
        # routes through AgentCore Runtime which takes ~45-90s. So when called
        # from API Gateway with mode=ai, we dispatch the actual work to a
        # self-async Lambda invocation and return 202 immediately. The browser
        # polls the rate-sheet GET endpoint until a new version appears.
        if mode == "ai" and not is_self_async:
            import boto3

            lc_async = boto3.client("lambda")
            async_event = {
                "_async": True,
                "pathParameters": params,
                "body_json": body,
                "actor": actor,
            }
            try:
                lc_async.invoke(
                    FunctionName=os.environ.get(
                        "AWS_LAMBDA_FUNCTION_NAME",
                        "laboraid-dev-l2-fn-ratesheet-rework",
                    ),
                    InvocationType="Event",
                    Payload=json.dumps(async_event).encode("utf-8"),
                )
                logger.info(
                    "rework[ai]: dispatched self-async for %s/%s", local, period
                )
            except Exception as e:
                logger.exception("rework[ai]: self-async dispatch failed")
                return _resp(
                    {"error": "dispatch_failed", "detail": str(e)}, 500
                )
            return _resp(
                {
                    "accepted": True,
                    "mode": "ai",
                    "eta_seconds": 60,
                    "message": (
                        "AI rework dispatched. Poll the rate-sheet endpoint; "
                        "a new version will appear when extraction completes."
                    ),
                },
                202,
            )

        import boto3

        rds = boto3.client("rds-data")
        s3 = boto3.client("s3")
        lc = boto3.client("lambda")
        common = {
            "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
            "secretArn": os.environ["AURORA_SECRET_ARN"],
            "database": "laboraid",
        }

        # 1. Find the latest version row for this {local, period}.
        head = rds.execute_statement(
            **common,
            sql=(
                "SELECT rp.id::text, rp.version, rp.approval_state, "
                "       COALESCE(rp.source_files::text,'{}'), "
                "       COALESCE(rp.canonical_json::text,'{}'), "
                "       COALESCE(rp.rejection_reason,''), "
                "       COALESCE(rp.rejection_tags, '{}'::TEXT[])::text, "
                "       rp.union_id::text "
                "  FROM rate_periods rp "
                "  JOIN unions u ON u.id = rp.union_id "
                " WHERE u.local = :local::int AND rp.start_date = :period::date "
                " ORDER BY rp.version DESC LIMIT 1"
            ),
            parameters=[
                {"name": "local", "value": {"stringValue": local}},
                {"name": "period", "value": {"stringValue": period}},
            ],
        )
        if not head.get("records"):
            return _resp({"error": "rate_sheet_not_found"}, 404)
        row = head["records"][0]
        prev_period_id = row[0]["stringValue"]
        prev_version = row[1].get("longValue") or 1
        prev_state = row[2]["stringValue"]
        source_files = row[3].get("stringValue") or "{}"
        canonical_json = row[4].get("stringValue") or "{}"
        rejection_reason = row[5].get("stringValue") or ""
        rejection_tags_raw = row[6].get("stringValue") or "{}"
        union_id = row[7]["stringValue"]

        # rework only makes sense on a rejected sheet — otherwise the reviewer
        # hasn't given the system anything new to incorporate.
        if prev_state not in ("rejected", "pending_review"):
            return _resp(
                {"error": "not_reworkable", "approval_state": prev_state}, 409
            )

        # 2. Read overrides for this {local, period} from DDB.
        overrides_by_cell = _latest_overrides_for_period(local, period)

        # 2b. Pull cell-level comments that landed since the last rework. These
        # are not currently used to mutate cells (only overrides do that), but
        # they ARE folded into rework_context so the v2 row carries a verbatim
        # record of every reviewer note about the parent. The Path-C agent
        # re-prompt (T3.D) reads them out of rework_context.comments[] to
        # incorporate human feedback into the re-extraction.
        rework_comments = _comments_since_last_rework(rds, common, local, period)

        # 3. Pull every cell of the parent version.
        cells = rds.execute_statement(
            **common,
            sql=(
                "SELECT id::text, zone, package, dimensions::text, column_name, "
                "       value::text, value_type, provenance::text, confidence::text "
                "  FROM rate_cells WHERE period_id = :pid::uuid"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": prev_period_id}}],
        )

        # 4. Insert the new rate_period row (v+1).
        new_period_id = str(uuid.uuid4())
        new_version = int(prev_version) + 1
        rework_ctx = {
            "mode": mode,
            "previous_version": prev_version,
            "previous_period_id": prev_period_id,
            "rejection_reason": rejection_reason,
            "rejection_tags_raw": rejection_tags_raw,
            "applied_overrides": [
                {
                    "cell_id": c,
                    "package": v.get("package"),
                    "column_name": v.get("column_name"),
                    "old_value": v.get("old_value"),
                    "new_value": v.get("value"),
                    "justification": v.get("justification"),
                    "actor": v.get("actor"),
                }
                for c, v in overrides_by_cell.items()
            ],
            # Verbatim reviewer comments on cells of the parent version. The
            # Path-C agent re-prompt reads this to fold human feedback into
            # its next extraction; for Path-A unions it's preserved purely
            # for audit + UI diff context.
            "comments": rework_comments,
            "note": note,
        }
        rds.execute_statement(
            **common,
            sql=(
                "INSERT INTO rate_periods "
                "  (id, union_id, start_date, end_date, status, approval_state, "
                "   canonical_json, source_files, version, parent_version, "
                "   rework_context) "
                "SELECT :nid::uuid, :uid::uuid, start_date, end_date, status, "
                "       'pending_review', canonical_json, source_files, :ver, "
                "       :pver, :ctx::jsonb "
                "  FROM rate_periods WHERE id = :pid::uuid"
            ),
            parameters=[
                {"name": "nid", "value": {"stringValue": new_period_id}},
                {"name": "uid", "value": {"stringValue": union_id}},
                {"name": "ver", "value": {"longValue": new_version}},
                {"name": "pver", "value": {"longValue": int(prev_version)}},
                {"name": "ctx", "value": {"stringValue": json.dumps(rework_ctx)}},
                {"name": "pid", "value": {"stringValue": prev_period_id}},
            ],
        )

        # 5. Copy rate_cells into the new period, applying overrides per cell_id.
        # We also collect a (package, column_name) → new_value map so we can
        # patch the canonical CSV in step 6 — the xlsx-renderer just rewrites
        # whatever CSV it's pointed at, so if we patch the CSV both artifacts
        # agree on v2 values.
        applied_count = 0
        overrides_by_position: dict[tuple[str, str], str] = {}
        for crow in cells.get("records", []):
            old_cell_id = crow[0]["stringValue"]
            zone = crow[1].get("stringValue") or ""
            package = crow[2].get("stringValue") or ""
            dimensions = crow[3].get("stringValue") or "{}"
            column_name = crow[4].get("stringValue") or ""
            old_value = crow[5].get("stringValue") or "0"
            value_type = crow[6].get("stringValue") or ""
            provenance = crow[7].get("stringValue") or "{}"
            confidence = crow[8].get("stringValue") or "1.0"

            new_value = old_value
            new_provenance = provenance
            override = overrides_by_cell.get(old_cell_id)
            if override and override.get("value") is not None:
                new_value = str(override["value"])
                overrides_by_position[(package, column_name)] = new_value
                applied_count += 1
                # Mark the provenance so the diff view can highlight rework cells.
                try:
                    p = json.loads(provenance) if provenance else {}
                except Exception:
                    p = {}
                p["rework"] = {
                    "from_value": old_value,
                    "actor": override.get("actor"),
                    "justification": override.get("justification"),
                    "previous_version": prev_version,
                }
                new_provenance = json.dumps(p)

            new_cell_id = str(uuid.uuid4())
            rds.execute_statement(
                **common,
                sql=(
                    "INSERT INTO rate_cells "
                    "  (id, period_id, zone, package, dimensions, column_name, "
                    "   value, value_type, provenance, confidence) "
                    "VALUES (:id::uuid, :pid::uuid, :zone, :pkg, :dim::jsonb, "
                    "        :col, :val::numeric, :vt, :prov::jsonb, :conf::numeric)"
                ),
                parameters=[
                    {"name": "id", "value": {"stringValue": new_cell_id}},
                    {"name": "pid", "value": {"stringValue": new_period_id}},
                    {"name": "zone", "value": {"stringValue": zone}},
                    {"name": "pkg", "value": {"stringValue": package}},
                    {"name": "dim", "value": {"stringValue": dimensions}},
                    {"name": "col", "value": {"stringValue": column_name}},
                    {"name": "val", "value": {"stringValue": new_value}},
                    {"name": "vt", "value": {"stringValue": value_type}},
                    {"name": "prov", "value": {"stringValue": new_provenance}},
                    {"name": "conf", "value": {"stringValue": confidence}},
                ],
            )

        # 6. Re-render the xlsx artifact for v2 if the v1 csv exists. We bump
        # the key by suffix so the v1 file survives for diff/audit.
        sf = json.loads(source_files) if source_files else {}
        csv_key = sf.get("output_csv")
        rate_notice_key = sf.get("rate_notice", "")
        new_sf = dict(sf)
        agent_summary: dict[str, Any] = {}

        if mode == "ai" and rate_notice_key:
            # Re-invoke the ExtractorAgent on AgentCore Runtime. The agent
            # writes a fresh CSV based on the source PDF; we use THAT as the
            # base for the override patch below. For Path-A unions the agent's
            # CSV is byte-identical to the parent's (deterministic kernel) —
            # the value here is that the demo demonstrably routed rework
            # through the agent, and the rework_context is in the agent's
            # CloudWatch log + the audit trail.
            ai_csv_key = csv_key.replace(
                "/output.csv", f"/output.v{new_version}.ai.csv"
            ) if csv_key else (
                rate_notice_key.rsplit("/", 1)[0] + f"/output.v{new_version}.ai.csv"
            )
            s3_prefix = (
                rate_notice_key.rsplit("/", 1)[0] + "/" if "/" in rate_notice_key else ""
            )
            try:
                agent_summary = _invoke_extractor_runtime(
                    union_local=local,
                    s3_prefix=s3_prefix,
                    out_s3_key=ai_csv_key,
                    rework_context=rework_ctx,
                    session_id=f"rework-{new_period_id}",
                )
                logger.info(
                    "rework[ai]: agent returned %s", json.dumps(agent_summary)[:300]
                )
                if agent_summary.get("s3_key"):
                    # Use the agent's CSV as the source for the override patch.
                    csv_key = agent_summary["s3_key"]
                    new_sf["output_csv_ai"] = csv_key
            except Exception as e:  # pragma: no cover - lambda runtime only
                logger.warning("rework[ai]: agent invoke failed: %s", e)
                agent_summary = {"error": str(e)}

        if csv_key:
            new_csv_key = (
                csv_key.replace("/output.v{new_version}.ai.csv", f"/output.v{new_version}.csv")
                .replace(".ai.csv", ".csv")
                if mode == "ai"
                else csv_key.replace("/output.csv", f"/output.v{new_version}.csv")
            )
            new_xlsx_key = new_csv_key.replace(".csv", ".xlsx")
            try:
                # 6a. Build the v2 CSV by patching the (agent's or parent's) CSV
                # with every (classification, column) override we just applied.
                patched_csv = _rebuild_csv_with_overrides(
                    s3, OUTPUTS_BUCKET, csv_key, overrides_by_position
                )
                s3.put_object(
                    Bucket=OUTPUTS_BUCKET,
                    Key=new_csv_key,
                    Body=patched_csv,
                    ContentType="text/csv",
                    ServerSideEncryption="aws:kms",
                )
                s3.head_object(Bucket=OUTPUTS_BUCKET, Key=new_csv_key)
                new_sf["output_csv"] = new_csv_key
                logger.info(
                    "rework[%s]: wrote v%d csv key=%s", mode, new_version, new_csv_key
                )

                # 6b. Re-render the xlsx from the v2 CSV so the spreadsheet
                # download agrees with the canonical CSV.
                lc.invoke(
                    FunctionName=XLSX_RENDERER_FN,
                    InvocationType="RequestResponse",
                    Payload=json.dumps({
                        "csv_s3_key": new_csv_key,
                        "out_s3_key": new_xlsx_key,
                    }).encode(),
                )
                s3.head_object(Bucket=OUTPUTS_BUCKET, Key=new_xlsx_key)
                new_sf["output_xlsx"] = new_xlsx_key
            except Exception as e:  # pragma: no cover - lambda runtime only
                logger.warning("rework artifact regeneration failed: %s", e)

        # Update the v2 row with refreshed source_files.
        rds.execute_statement(
            **common,
            sql=(
                "UPDATE rate_periods SET source_files = :sf::jsonb "
                " WHERE id = :pid::uuid"
            ),
            parameters=[
                {"name": "sf", "value": {"stringValue": json.dumps(new_sf)}},
                {"name": "pid", "value": {"stringValue": new_period_id}},
            ],
        )

        # 7. Audit log entry — scope by {local, period} so the timeline shows it.
        details = {
            "local": local,
            "period": period,
            "from_version": prev_version,
            "to_version": new_version,
            "mode": mode,
            "applied_overrides": applied_count,
            "comments_incorporated": len(rework_comments),
            "note": note,
            "rejection_reason": rejection_reason,
        }
        if mode == "ai":
            details["agent"] = {
                "rows": agent_summary.get("rows"),
                "extracted_rows": agent_summary.get("extracted_rows"),
                "gap_count": agent_summary.get("gap_count"),
                "checksum_passed": (agent_summary.get("checksum") or {}).get("passed"),
                "error": agent_summary.get("error"),
            }
        rds.execute_statement(
            **common,
            sql=(
                "INSERT INTO audit_log (tenant, actor, action, details) "
                "VALUES ('laboraid', :actor, 'rework', :details::jsonb)"
            ),
            parameters=[
                {"name": "actor", "value": {"stringValue": actor}},
                {"name": "details", "value": {"stringValue": json.dumps(details)}},
            ],
        )

        # 8. Emit a lifecycle event so anything observing the bus sees the rework.
        if ENGINE_BUS_NAME:
            try:
                boto3.client("events").put_events(
                    Entries=[
                        {
                            "Source": "laboraid.api",
                            "DetailType": "laboraid.rate-sheet.reworked",
                            "Detail": json.dumps({
                                "local": local,
                                "period": period,
                                "from_version": prev_version,
                                "to_version": new_version,
                                "reworked_by": actor,
                            }),
                            "EventBusName": ENGINE_BUS_NAME,
                        }
                    ]
                )
            except Exception as e:  # pragma: no cover
                logger.warning("rework event emit failed: %s", e)

        return _resp({
            "from_version": prev_version,
            "to_version": new_version,
            "mode": mode,
            "applied_overrides": applied_count,
            "comments_incorporated": len(rework_comments),
            "new_period_id": new_period_id,
            "source_files": new_sf,
            "agent_summary": agent_summary if mode == "ai" else None,
        })
    except Exception:
        logger.exception("ratesheet-rework failed")
        raise
