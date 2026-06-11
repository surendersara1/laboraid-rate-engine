"""Publisher Lambda — write the agent's extraction into Aurora.

The Step Functions pipeline's old "Publish" state was just `sfn.Succeed()` — it
terminated the workflow without ever writing the canonical rate sheet to Aurora.
This Lambda fills that gap. It reads the agent's CSV from S3, parses it into
classification rows + cells, and UPSERTs:

  1. `unions`        — (local, trade) pair from the CSV's metadata columns.
  2. `rate_periods`  — one row per {union, period}, version=1.
  3. `rate_cells`    — N cells per period (zone, package, column_name, value)
                       with provenance carrying the agent's method + source.

After this runs, the same PDF can be uploaded twice without producing two rows
(idempotent by (union, period)). Existing rate_periods are SKIPPED — the rework
loop is the documented path to revise a published period.

Input shape (SFN state at "Publish" time — orchestration_stack uses
result_path="$.extract" for the ExtractViaAgent task):
  {
    "classify": {
      "s3_key": "<source PDF key>",
      "union": "sprinkler_fitters_704" | "local_<N>",
      "local": "704",
      "period": "YYYY-MM-DD",
      "doc_type": "rate_notice",
      ...
    },
    "extract": {
      "canonical": {
        "s3_key": "<agent's CSV S3 key>",
        "rows": 13,
        "gaps": [["zone","package","column","reason"], ...],
        "gap_count": 1,
        ...
      },
      "runtime_response": { ... }
    },
    ...
  }
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-publisher")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-publisher")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")

# Columns the kernel/agent emits as leading metadata before the rate-value
# columns. Detected by name (not position) because the order is fixed but new
# profiles could prepend more.
METADATA_COLUMNS = {
    "Union Group",
    "Trade",
    "Union Local",
    "Zone",
    "Package",
    "Start Date",
    "End Date",
}


def _parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _metadata_column_indices(header: list[str]) -> dict[str, int]:
    return {name: idx for idx, name in enumerate(header) if name in METADATA_COLUMNS}


def _rate_column_indices(header: list[str]) -> list[tuple[int, str]]:
    return [(idx, name) for idx, name in enumerate(header) if name not in METADATA_COLUMNS]


def _coerce_float(s: str) -> float | None:
    """Convert a CSV cell to a numeric. Returns None for blanks / unparseable.
    The schema column is NUMERIC so we either insert a number or null."""
    if s is None:
        return None
    s = s.strip()
    if not s or s.lower() in ("null", "none", "n/a"):
        return None
    # Strip $ and commas if any user-driven path emitted them.
    s = s.replace("$", "").replace(",", "")
    # Percentages — strip and divide by 100 to keep the column NUMERIC.
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _upsert_union(
    rds: Any,
    common: dict[str, Any],
    local: str,
    trade: str,
    parent_intl: str,
) -> str:
    """Return the UUID of the unions row for (local). Race-safe: uses
    INSERT ... ON CONFLICT (local) backed by a UNIQUE(local) constraint, so
    two parallel Publisher invocations on the same local don't both create
    a row. Without this fix, N parallel uploads for a previously-unseen
    local produced N separate unions rows — and the unique constraint on
    rate_periods (union_id, start_date, version) couldn't catch the
    downstream duplicate rate_periods.
    """
    new_id = str(uuid.uuid4())
    ins = rds.execute_statement(
        **common,
        sql=(
            "INSERT INTO unions (id, local, trade, parent_intl) "
            "VALUES (:id::uuid, :local::int, :trade, :parent) "
            "ON CONFLICT (local) DO NOTHING "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id", "value": {"stringValue": new_id}},
            {"name": "local", "value": {"stringValue": str(local)}},
            {"name": "trade", "value": {"stringValue": trade}},
            {"name": "parent", "value": {"stringValue": parent_intl}},
        ],
    )
    if ins.get("records"):
        logger.info("publisher: created unions row local=%s trade=%s", local, trade)
        return ins["records"][0][0]["stringValue"]
    # ON CONFLICT skipped the insert — someone else won the race. Read back.
    sel = rds.execute_statement(
        **common,
        sql="SELECT id::text FROM unions WHERE local = :local::int",
        parameters=[{"name": "local", "value": {"stringValue": str(local)}}],
    )
    return sel["records"][0][0]["stringValue"]


def _publish(
    *,
    rds: Any,
    s3: Any,
    common: dict[str, Any],
    classify: dict[str, Any],
    canonical: dict[str, Any],
) -> dict[str, Any]:
    """Read CSV, classify metadata, write Aurora rows. Returns a summary."""
    csv_key = canonical.get("s3_key") or ""
    if not csv_key:
        raise RuntimeError("publisher: no canonical CSV key in agent response")
    body = s3.get_object(Bucket=OUTPUTS_BUCKET, Key=csv_key)["Body"].read()
    header, data_rows = _parse_csv(body.decode("utf-8"))
    if not header or not data_rows:
        # Empty CSV is a legitimate "I found nothing to extract from
        # this PDF" result — e.g. a CBA with no Residential section,
        # or an Apprentice Scale PDF where the LLM correctly refused
        # to fabricate. Log + succeed gracefully; don't fail the SFN
        # run. The reviewer will see the source PDF was uploaded but
        # produced no cells (visible in the source-contribution panel).
        logger.info(
            "publisher: empty CSV at s3://%s/%s — no cells to insert (PDF "
            "produced no extractable data). Skipping cleanly.",
            OUTPUTS_BUCKET, csv_key,
        )
        return {
            "published": True,
            "empty": True,
            "csv_key": csv_key,
            "source_pdf": classify.get("s3_key", ""),
        }

    meta_idx = _metadata_column_indices(header)
    rate_cols = _rate_column_indices(header)
    if "Package" not in meta_idx:
        raise RuntimeError("publisher: CSV missing required 'Package' column")

    # Derive union/trade/local/period from the CSV's first data row (the kernel
    # writes the same metadata on every row), falling back to classify.
    first = data_rows[0]
    def col(name: str, default: str = "") -> str:
        idx = meta_idx.get(name)
        return first[idx].strip() if idx is not None and idx < len(first) else default

    # LLM sometimes returns "UA Local 281" or "Local 281" in the Union
    # Local column instead of just "281". Strip the prose so the int
    # coercion downstream doesn't blow up.
    raw_local = col("Union Local") or str(classify.get("local") or "")
    import re as _re

    _local_digits = _re.search(r"\d{3,4}", raw_local)
    local = _local_digits.group(0) if _local_digits else raw_local.strip()
    # Date source-of-truth: the classifier's filename-derived period beats
    # Claude's PDF-content date. Reason: Journeymen Rates PDFs often print
    # a full step schedule (multiple effective dates) and Claude picks the
    # latest visible step — landing N PDFs at the same wrong period instead
    # of at their filename dates. Filename dates are explicit and authoritative
    # to the customer who named the file. Caught 2026-06-10 on a 6-file 692
    # upload where 3 Journeymen files all collided at 2025-01-01.
    classify_period = classify.get("period")
    # Trade source-of-truth priority:
    #   1. Upload folder structure (laboraid/<Trade>/<Local>/...) — what the
    #      customer organizes by. Most reliable for unknown unions where the
    #      LLM might guess wrong (UA Local 120 covers Plumbers/Pipefitters AND
    #      Sprinkler Fitters; the folder picks the right one).
    #   2. Whatever the LLM/kernel put in the CSV's Trade column.
    #   3. Fallback derived from the kernel union name.
    pdf_key = classify.get("s3_key") or ""
    pdf_parts = pdf_key.split("/")
    folder_trade = ""
    # Only use the path's 2nd segment as trade when it looks like a real
    # trade name. The Admin upload Lambda dumps files at
    # `laboraid/uploads/<filename>` for ad-hoc uploads — "uploads" is a
    # storage convention, not a trade. When the path segment is a reserved
    # storage word, defer to whatever Claude/kernel wrote in the CSV.
    _RESERVED_PATH_SEGMENTS = {"uploads", "tmp", "scratch", "unknown"}
    if len(pdf_parts) >= 3 and pdf_parts[0] == "laboraid":
        cand = pdf_parts[1]
        if cand.lower() not in _RESERVED_PATH_SEGMENTS:
            folder_trade = cand
    trade = (
        folder_trade
        or col("Trade")
        or (classify.get("union") or "").split("_")[-1].title()
    )
    parent_intl = col("Union Group") or "UA"
    # Prefer classifier's filename-derived period (see comment above on
    # local). Falls back to CSV Start Date only when filename gave us
    # nothing — e.g. the CBA case where the filename is `2019-2024.<local>
    # CBA.pdf` without a YYYY.MM.DD prefix.
    start_date = (
        _normalize_date(classify_period)
        or _normalize_date(col("Start Date"))
    )
    end_date = _normalize_date(col("End Date"))

    if not local or not start_date:
        raise RuntimeError(
            f"publisher: cannot determine union/local + period — local={local!r}, "
            f"start_date={start_date!r}"
        )

    # 1) Union UPSERT.
    union_id = _upsert_union(rds, common, local, trade, parent_intl)

    # 2) Idempotency on (union_id, start_date) — skip if a row already exists.
    existing = rds.execute_statement(
        **common,
        sql=(
            "SELECT id::text, version FROM rate_periods "
            " WHERE union_id = :uid::uuid AND start_date = :sd::date "
            " ORDER BY version DESC LIMIT 1"
        ),
        parameters=[
            {"name": "uid", "value": {"stringValue": union_id}},
            {"name": "sd", "value": {"stringValue": start_date}},
        ],
    )
    # Derive the source PDF basename — every cell carries it in
    # provenance.source_pdf so reviewers see exactly which uploaded file
    # produced each value (matters when N PDFs for the same period are
    # merged via the multi-PDF flow).
    source_pdf_key = classify.get("s3_key") or ""
    source_pdf_name = source_pdf_key.rsplit("/", 1)[-1] if source_pdf_key else ""

    merge_mode = False
    if existing.get("records"):
        period_id = existing["records"][0][0]["stringValue"]
        existing_v = existing["records"][0][1].get("longValue") or 1
        merge_mode = True
        logger.info(
            "publisher: rate_period exists for union=%s period=%s (v%d, id=%s) "
            "— MERGE MODE: appending cells from %s",
            local, start_date, existing_v, period_id, source_pdf_name,
        )
        # Append the new PDF to rate_periods.source_files. Both old and new
        # shapes are tolerated: legacy single-PDF rows wrote a dict
        # {"rate_notice": "...", "output_csv": "..."}; merge mode promotes
        # rate_notice to a list (preserves the original) and adds new PDFs
        # under source_files.uploads[].
        ex_sf = rds.execute_statement(
            **common,
            sql=(
                "SELECT COALESCE(source_files::text, '{}') FROM rate_periods "
                " WHERE id = :pid::uuid"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
        try:
            prior_sf = json.loads(
                ex_sf["records"][0][0].get("stringValue") or "{}"
            )
        except Exception:
            prior_sf = {}
        uploads = prior_sf.get("uploads") or []
        # Promote any legacy single rate_notice into uploads[] so the
        # provenance chain is uniform from this point forward.
        legacy_rn = prior_sf.get("rate_notice")
        if legacy_rn and legacy_rn not in uploads and isinstance(legacy_rn, str):
            uploads.append(legacy_rn)
        if source_pdf_key and source_pdf_key not in uploads:
            uploads.append(source_pdf_key)
        prior_sf["uploads"] = uploads
        prior_sf.setdefault("rate_notice", legacy_rn or source_pdf_key)
        prior_sf["output_csv"] = csv_key  # last writer wins for the CSV ptr
        rds.execute_statement(
            **common,
            sql=(
                "UPDATE rate_periods SET source_files = :sf::jsonb "
                " WHERE id = :pid::uuid"
            ),
            parameters=[
                {"name": "pid", "value": {"stringValue": period_id}},
                {"name": "sf", "value": {"stringValue": json.dumps(prior_sf)}},
            ],
        )
    else:
        # 3) Try to insert a new rate_period. Use INSERT ... ON CONFLICT to
        #    race-safely handle the case where another concurrent Publisher
        #    invocation just created the same (union_id, start_date, version).
        #    The DB has a UNIQUE(union_id, start_date, version) constraint
        #    that backs this; without it, parallel uploads of N PDFs for the
        #    same period would race past the SELECT-then-INSERT pattern and
        #    we'd end up with duplicate rate_periods rows.
        period_id = str(uuid.uuid4())
        source_files = {
            "rate_notice": classify.get("s3_key") or "",
            "output_csv": csv_key,
            "uploads": [source_pdf_key] if source_pdf_key else [],
        }
        # Preserve the FULL gap list with reasons (kernel emits
        # [zone, package, column, reason] tuples). Reviewers need the
        # reasons to know what supporting doc to upload — a bare count
        # is useless. gap_count is kept for back-compat dashboards.
        gaps_list = canonical.get("gaps") or []
        gap_count = canonical.get("gap_count")
        if gap_count is None:
            gap_count = len(gaps_list)
        canonical_json = {
            "rows": canonical.get("rows"),
            "extracted_rows": canonical.get("extracted_rows"),
            "gap_count": gap_count,
            "gaps_detail": gaps_list,
            "checksum": canonical.get("checksum"),
            "extracted_at": canonical.get("extracted_at"),
            "doc_type": classify.get("doc_type"),
        }
        # When gap_count > 0 the period is technically not review-ready,
        # but the approval_state CHECK constraint only allows
        # pending_review/approved/rejected/published — we surface the
        # "needs more input" treatment in the UI via canonical_json.gap_count
        # without a schema migration.
        ins = rds.execute_statement(
            **common,
            sql=(
                "INSERT INTO rate_periods "
                "  (id, union_id, start_date, end_date, status, approval_state, "
                "   canonical_json, source_files, version) "
                "VALUES (:id::uuid, :uid::uuid, :sd::date, :ed::date, 'extracted', "
                "        'pending_review', :cj::jsonb, :sf::jsonb, 1) "
                "ON CONFLICT (union_id, start_date, version) DO NOTHING "
                "RETURNING id::text"
            ),
            parameters=[
                {"name": "id", "value": {"stringValue": period_id}},
                {"name": "uid", "value": {"stringValue": union_id}},
                {"name": "sd", "value": {"stringValue": start_date}},
                {
                    "name": "ed",
                    "value": (
                        {"stringValue": end_date}
                        if end_date
                        else {"isNull": True}
                    ),
                },
                {"name": "cj", "value": {"stringValue": json.dumps(canonical_json)}},
                {"name": "sf", "value": {"stringValue": json.dumps(source_files)}},
            ],
        )
        # If ON CONFLICT skipped the insert, look up the winning row's id
        # and switch into merge mode for the cell-insertion loop below.
        if not ins.get("records"):
            existing2 = rds.execute_statement(
                **common,
                sql=(
                    "SELECT id::text FROM rate_periods "
                    " WHERE union_id = :uid::uuid AND start_date = :sd::date "
                    " ORDER BY version DESC LIMIT 1"
                ),
                parameters=[
                    {"name": "uid", "value": {"stringValue": union_id}},
                    {"name": "sd", "value": {"stringValue": start_date}},
                ],
            )
            if existing2.get("records"):
                period_id = existing2["records"][0][0]["stringValue"]
                merge_mode = True
                logger.info(
                    "publisher: lost INSERT race for union=%s period=%s — "
                    "switching to merge mode against existing row %s",
                    local, start_date, period_id,
                )

    # 4) Insert all rate_cells. Skip blank/null rate values — they go in as
    #    NULL so the UI can show them as gaps. Provenance carries the agent's
    #    method so the UI's "Method" line in the Provenance panel reads true.
    # Method label drives confidence + the Provenance panel's "Method" line.
    # The 5 hand-coded kernel unions are explicit; anything else came from the
    # LLM path. Don't use "_" presence — every normalized union name has one.
    _KERNEL_UNIONS = {
        "pipe_fitters_537",
        "sprinkler_fitters_483",
        "sprinkler_fitters_704",
        "sprinkler_fitters_281",
        "sprinkler_fitters_821",
    }
    method = (
        "kernel"
        if (classify.get("union") or "").lower() in _KERNEL_UNIONS
        else "llm_claude"
    )
    # In merge mode we pre-load every (zone, package, column_name) triple
    # at this period AND whether its value is NULL. Two distinct rules
    # apply on collision:
    #   - existing value IS NULL → new run is allowed to FILL it (UPDATE).
    #     This is the common case for Rate Notice + CBA merging: kernel
    #     leaves Pension/Vacation NULL on Residential rows and the CBA's
    #     LLM extraction fills them.
    #   - existing value IS NOT NULL → first-write-wins, skip. Reviewer
    #     resolves real value conflicts via override.
    existing_triples: set[tuple[str, str, str]] = set()
    existing_null_cells: dict[tuple[str, str, str], str] = {}
    if merge_mode:
        ex = rds.execute_statement(
            **common,
            sql=(
                "SELECT id::text, COALESCE(zone,''), COALESCE(package,''), "
                "       COALESCE(column_name,''), (value IS NULL) AS is_null "
                "  FROM rate_cells WHERE period_id = :pid::uuid"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
        for row in ex.get("records") or []:
            triple = (
                row[1].get("stringValue") or "",
                row[2].get("stringValue") or "",
                row[3].get("stringValue") or "",
            )
            existing_triples.add(triple)
            if row[4].get("booleanValue"):
                existing_null_cells[triple] = row[0].get("stringValue") or ""

    inserted = 0
    skipped_no_package = 0
    skipped_collision = 0
    filled_null = 0
    # F4: column-name normalization map for THIS union (e.g., "Apprenticeship
    # Training" → "J&A Training 704"). Falls through to identity for any
    # column not listed.
    try:
        from column_normalization import canonicalize as _canon
    except ImportError:
        def _canon(_local: str, c: str) -> str:
            return c

    for row in data_rows:
        zone = row[meta_idx["Zone"]] if "Zone" in meta_idx and meta_idx["Zone"] < len(row) else ""
        package = row[meta_idx["Package"]] if meta_idx["Package"] < len(row) else ""
        if not package:
            skipped_no_package += 1
            continue
        for col_idx, col_name in rate_cols:
            col_name = _canon(local, col_name)
            if col_idx >= len(row):
                continue
            triple = (zone, package, col_name)
            raw = row[col_idx]
            value = _coerce_float(raw)
            confidence = 1.0 if method == "kernel" else 0.85
            prov = {
                "source": csv_key,
                "method": method,
                "source_pdf": source_pdf_name,
                "row_raw": str(raw)[:80],
            }
            if triple in existing_triples:
                # Cell already exists. Two cases:
                #  - existing value is NULL: this run is allowed to FILL it.
                #  - existing value is non-null: first-write-wins, skip.
                existing_cell_id = existing_null_cells.get(triple)
                if existing_cell_id and value is not None:
                    rds.execute_statement(
                        **common,
                        sql=(
                            "UPDATE rate_cells SET value = :val::numeric, "
                            "       value_type = :vt, "
                            "       provenance = :prov::jsonb, "
                            "       confidence = :conf::numeric "
                            " WHERE id = :id::uuid AND value IS NULL"
                        ),
                        parameters=[
                            {"name": "id", "value": {"stringValue": existing_cell_id}},
                            {"name": "val", "value": {"stringValue": str(value)}},
                            {"name": "vt", "value": {"stringValue": "currency"}},
                            {"name": "prov", "value": {"stringValue": json.dumps(prov)}},
                            {"name": "conf", "value": {"stringValue": str(confidence)}},
                        ],
                    )
                    existing_null_cells.pop(triple, None)
                    filled_null += 1
                else:
                    # F3: record value disagreements so the reviewer can
                    # see when two PDFs disagreed on the same cell. Append
                    # the rejected attempt to provenance.conflicts so it's
                    # visible in the Provenance panel without disturbing
                    # the first-write-wins value.
                    skipped_collision += 1
                    if value is not None:
                        rds.execute_statement(
                            **common,
                            sql=(
                                "UPDATE rate_cells rc SET "
                                "  provenance = jsonb_set("
                                "    COALESCE(provenance, '{}'::jsonb), "
                                "    '{conflicts}', "
                                "    COALESCE(provenance->'conflicts', '[]'::jsonb) || "
                                "      jsonb_build_array(jsonb_build_object("
                                "        'rejected_value', :rv::numeric, "
                                "        'source_pdf', :rs, "
                                "        'method', :rm)), "
                                "    true) "
                                " WHERE rc.period_id = :pid::uuid "
                                "   AND COALESCE(rc.zone,'') = :z "
                                "   AND COALESCE(rc.package,'') = :pk "
                                "   AND rc.column_name = :col "
                                "   AND rc.value IS NOT NULL "
                                "   AND rc.value <> :rv::numeric"
                            ),
                            parameters=[
                                {"name": "pid", "value": {"stringValue": period_id}},
                                {"name": "z", "value": {"stringValue": zone}},
                                {"name": "pk", "value": {"stringValue": package}},
                                {"name": "col", "value": {"stringValue": col_name}},
                                {"name": "rv", "value": {"stringValue": str(value)}},
                                {"name": "rs", "value": {"stringValue": source_pdf_name or ""}},
                                {"name": "rm", "value": {"stringValue": method}},
                            ],
                        )
                continue
            cell_id = str(uuid.uuid4())
            rds.execute_statement(
                **common,
                sql=(
                    "INSERT INTO rate_cells "
                    "  (id, period_id, zone, package, column_name, value, "
                    "   value_type, provenance, confidence) "
                    "VALUES (:id::uuid, :pid::uuid, :zone, :pkg, :col, "
                    "        :val::numeric, :vt, :prov::jsonb, :conf::numeric)"
                ),
                parameters=[
                    {"name": "id", "value": {"stringValue": cell_id}},
                    {"name": "pid", "value": {"stringValue": period_id}},
                    {"name": "zone", "value": {"stringValue": zone}},
                    {"name": "pkg", "value": {"stringValue": package}},
                    {"name": "col", "value": {"stringValue": col_name}},
                    {
                        "name": "val",
                        "value": (
                            {"stringValue": str(value)}
                            if value is not None
                            else {"isNull": True}
                        ),
                    },
                    {"name": "vt", "value": {"stringValue": "currency"}},
                    {"name": "prov", "value": {"stringValue": json.dumps(prov)}},
                    {
                        "name": "conf",
                        "value": {"stringValue": str(confidence)},
                    },
                ],
            )
            existing_triples.add(triple)  # for any future PDFs in this same Lambda invocation
            inserted += 1

    # Post-step (derived cells): some columns are deterministically
    # derivable from another column in the same row — no source PDF
    # carries them explicitly because they follow a CBA-stated formula.
    # Fill them from the row's other cells, stamping provenance.method =
    # "derived" + provenance.derived_from so the reviewer can see how
    # the value was computed.
    #
    # 1) Wage Differential = Wage × 1.15  (CBA shift-work multiplier;
    #    applies to BOTH Building and Residential).
    # 2) Wage 1.5x  = Wage × 1.5  (time-and-one-half).
    # 3) Wage 2.0x  = Wage × 2.0  (double-time).
    # 4) Residential Apprentice Pension = 0 (zero-by-rule per Local 483:
    #    Residential apprentices have no pension allocation).
    derived_rules = [
        # (target_col, source_col, multiplier, optional package-filter SQL)
        ("Wage Differential", "Wage", 1.15, None),
        ("Wage 1.5x",         "Wage", 1.5,  None),
        ("Wage 2.0x",         "Wage", 2.0,  None),
    ]
    derived_filled = 0
    for target, source, mult, pkg_filter in derived_rules:
        sql = (
            "WITH src AS ("
            "  SELECT zone, package, value AS source_value "
            "    FROM rate_cells "
            "   WHERE period_id = :pid::uuid "
            "     AND column_name = :src "
            "     AND value IS NOT NULL"
            ") "
            "UPDATE rate_cells rc "
            "   SET value = ROUND(src.source_value * :mult::numeric, 2), "
            "       value_type = 'currency', "
            "       provenance = jsonb_set("
            "         jsonb_set(COALESCE(provenance, '{}'::jsonb), "
            "                   '{method}', '\"derived\"'::jsonb), "
            "         '{derived_from}', "
            "         to_jsonb(:src || ' x ' || :mult::text), true), "
            "       confidence = 1.0 "
            "  FROM src "
            " WHERE rc.period_id = :pid::uuid "
            "   AND rc.column_name = :tgt "
            "   AND rc.value IS NULL "
            "   AND COALESCE(rc.zone,'') = COALESCE(src.zone,'') "
            "   AND COALESCE(rc.package,'') = COALESCE(src.package,'')"
        )
        r = rds.execute_statement(
            **common,
            sql=sql,
            parameters=[
                {"name": "pid", "value": {"stringValue": period_id}},
                {"name": "tgt", "value": {"stringValue": target}},
                {"name": "src", "value": {"stringValue": source}},
                {"name": "mult", "value": {"stringValue": str(mult)}},
            ],
        )
        derived_filled += r.get("numberOfRecordsUpdated", 0) or 0

    # Drop columns that don't belong in a numeric rate_cells table.
    # The 821 kernel emits "Indentured Date is Before" / "Indentured Date
    # is After" — these are dates, not currency, so the value column
    # can't hold them. Filter them out before the gap-count recount;
    # the reviewer should only see real numeric gaps.
    rds.execute_statement(
        **common,
        sql=(
            "DELETE FROM rate_cells "
            " WHERE period_id = :pid::uuid "
            "   AND column_name IN ('Indentured Date is Before', "
            "                       'Indentured Date is After')"
        ),
        parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
    )

    # Zero-by-rule: Residential Apprentice * Pension = 0 (Local 483
    # convention; no pension allocation for residential trainees).
    r = rds.execute_statement(
        **common,
        sql=(
            "UPDATE rate_cells SET value = 0, value_type = 'currency', "
            "       provenance = jsonb_set("
            "         jsonb_set(COALESCE(provenance, '{}'::jsonb), "
            "                   '{method}', '\"zero_by_rule\"'::jsonb), "
            "         '{rule}', '\"Residential apprentices have no pension allocation\"'::jsonb, true), "
            "       confidence = 1.0 "
            " WHERE period_id = :pid::uuid "
            "   AND zone = 'Residential' "
            "   AND column_name = 'Pension' "
            "   AND package ILIKE 'Apprentice%' "
            "   AND value IS NULL"
        ),
        parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
    )
    derived_filled += r.get("numberOfRecordsUpdated", 0) or 0

    # Zero-by-rule: PAC (Political Action Committee) is opt-in $0 by
    # convention when the source doesn't specify. Applies to any union's
    # PAC fund column (PAC 821, PAC 704, etc.).
    r = rds.execute_statement(
        **common,
        sql=(
            "UPDATE rate_cells SET value = 0, value_type = 'currency', "
            "       provenance = jsonb_set("
            "         jsonb_set(COALESCE(provenance, '{}'::jsonb), "
            "                   '{method}', '\"zero_by_rule\"'::jsonb), "
            "         '{rule}', '\"PAC contribution is opt-in; $0 by Local convention when source unspecified\"'::jsonb, true), "
            "       confidence = 1.0 "
            " WHERE period_id = :pid::uuid "
            "   AND column_name ILIKE 'PAC%' "
            "   AND value IS NULL"
        ),
        parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
    )
    derived_filled += r.get("numberOfRecordsUpdated", 0) or 0
    logger.info("publisher: derived/zero-by-rule filled %d cells", derived_filled)

    # Post-step: recount actual NULL cells in Aurora for this period. In
    # merge mode an earlier PDF may have flagged 7 gaps; a follow-up PDF
    # might have filled some of them. The kernel's `gaps` list was for
    # THIS run only — Aurora is the source of truth.
    # Drop (zone, package) rows whose every cell is NULL. These are
    # phantom rows the LLM emitted with row structure but no extractable
    # values — e.g., the 483-tuned CBA prompt emits a Residential
    # Foreman + Journeyman pair for 704, but 704 has no Residential
    # section so all cells come back NULL. Such rows show up as gaps
    # for cells that legitimately shouldn't exist. Filter them out
    # before we recount; the reviewer should only see real gaps.
    rds.execute_statement(
        **common,
        sql=(
            "DELETE FROM rate_cells rc "
            "  USING ("
            "    SELECT zone, COALESCE(package,'') AS package "
            "      FROM rate_cells "
            "     WHERE period_id = :pid::uuid "
            "     GROUP BY zone, COALESCE(package,'') "
            "    HAVING bool_and(value IS NULL)"
            "  ) empties "
            " WHERE rc.period_id = :pid::uuid "
            "   AND COALESCE(rc.zone,'') = COALESCE(empties.zone,'') "
            "   AND COALESCE(rc.package,'') = empties.package"
        ),
        parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
    )

    null_count_r = rds.execute_statement(
        **common,
        sql=(
            "SELECT COUNT(*) FROM rate_cells "
            "WHERE period_id = :pid::uuid AND value IS NULL"
        ),
        parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
    )
    null_now = null_count_r["records"][0][0].get("longValue") or 0
    # Pull canonical_json so we can refresh gap_count + optionally append
    # this run's gaps_detail to the merged set.
    cj_r = rds.execute_statement(
        **common,
        sql=(
            "SELECT COALESCE(canonical_json::text, '{}'), approval_state "
            "FROM rate_periods WHERE id = :pid::uuid"
        ),
        parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
    )
    try:
        prior_cj = json.loads(cj_r["records"][0][0].get("stringValue") or "{}")
    except Exception:
        prior_cj = {}
    prior_state = cj_r["records"][0][1].get("stringValue") or "pending_review"
    run_gaps = canonical.get("gaps") or []
    # ------------------------------------------------------------------
    # Rebuild gaps_detail from Aurora truth, not from accumulated runtime
    # responses. The prior merge approach had a race window in multi-PDF
    # batches where two SFN executions publish in parallel and stale gap
    # entries (cells filled by the OTHER execution after this filter ran)
    # survived in the canonical_json. The UI banner then showed reasons
    # that no longer matched reality (e.g. "Wage not in docs" when the
    # cell was already 22.47 from a CBA-derived calculation).
    #
    # New approach: SELECT every still-NULL cell from rate_cells in one
    # query; attach a reason from THIS run's runtime gaps if the row
    # matches, else use a sensible default.
    # ------------------------------------------------------------------
    run_reasons: dict[tuple[str, str, str], str] = {}
    for g in run_gaps:
        if isinstance(g, (list, tuple)) and len(g) >= 4:
            z, pk, col, reason = g[0], g[1], g[2], g[3]
            run_reasons[(z or "", pk or "", col or "")] = reason
    null_r = rds.execute_statement(
        **common,
        sql=(
            "SELECT zone, COALESCE(package, ''), column_name FROM rate_cells "
            "WHERE period_id = :pid::uuid AND value IS NULL "
            "ORDER BY zone, package, column_name"
        ),
        parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
    )
    # ------------------------------------------------------------------
    # Deterministic Residential Apprentice SOP rules.
    #
    # Per Dan's SOP §4 + 483 CBA Article 8, certain Residential Apprentice
    # cells are determinable from the SOP regardless of what Claude saw:
    #   * Work Assessment #2 (column suffix "Union Dues 2 *") = $0 for
    #     ALL Residential Apprentices (only applies to JM/Foreman).
    #   * Vacation Withholding (column suffix "Vacation *") = $0 for
    #     ALL Residential Apprentices.
    #   * Work Assessment #1 (column suffix "Union Dues 1 *") = $0 for
    #     Apprentice Class 1-2 only (Class 3-5 inherits JM value, which
    #     a separate inheritance pass would handle when the master
    #     profile flags that column as JM-inheritable).
    # Claude's mapping of "Work Assessment #N" to the actual master
    # column name varies; the publisher does this translation
    # deterministically once cells are in Aurora.
    _ZERO_BY_RULE: list[tuple[str, str, str | None]] = [
        ("Union Dues 2", "Apprentice Class", None),         # WA #2 — all classes
        ("Vacation", "Apprentice Class", None),              # Vacation — all classes
        ("Union Dues 1", "Apprentice Class", "1-2"),        # WA #1 — class 1 and 2 only
    ]
    rules_applied = 0
    for col_prefix, pkg_prefix, class_range in _ZERO_BY_RULE:
        class_pred = ""
        params = [
            {"name": "pid", "value": {"stringValue": period_id}},
            {"name": "cp", "value": {"stringValue": col_prefix + "%"}},
            {"name": "pp", "value": {"stringValue": pkg_prefix + "%"}},
        ]
        if class_range == "1-2":
            class_pred = " AND (package LIKE '%Class 1' OR package LIKE '%Class 2')"
        rule_r = rds.execute_statement(
            **common,
            sql=(
                "UPDATE rate_cells SET value = 0.0, confidence = 1.0, "
                "       provenance = COALESCE(provenance, '{}'::jsonb) || jsonb_build_object("
                "         'method', 'zero_by_rule', 'rule_source', 'SOP §4 Residential Apprentice'"
                "       ) "
                "WHERE period_id = :pid::uuid AND zone = 'Residential' "
                "  AND value IS NULL AND column_name LIKE :cp AND package LIKE :pp" + class_pred
            ),
            parameters=params,
        )
        n = rule_r.get("numberOfRecordsUpdated", 0)
        if n:
            logger.info(
                "publisher: applied SOP zero_by_rule col_prefix=%s pkg=%s range=%s -> %d cells",
                col_prefix, pkg_prefix, class_range, n,
            )
            rules_applied += n

    still_null_gaps: list[list[str]] = []
    if rules_applied:
        # Re-fetch NULL set after the SOP rules ran.
        null_r = rds.execute_statement(
            **common,
            sql=(
                "SELECT zone, COALESCE(package, ''), column_name FROM rate_cells "
                "WHERE period_id = :pid::uuid AND value IS NULL "
                "ORDER BY zone, package, column_name"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
    for row in null_r.get("records") or []:
        z = row[0].get("stringValue") or ""
        pk = row[1].get("stringValue") or ""
        col = row[2].get("stringValue") or ""
        reason = run_reasons.get((z, pk, col), "value not present in uploaded PDFs; reviewer should triage")
        still_null_gaps.append([z, pk, col, reason])
    prior_cj["gap_count"] = len(still_null_gaps)
    prior_cj["gaps_detail"] = still_null_gaps

    # Move 3 — Deterministic Rule 1-12 validation against the customer's
    # Master Fund / Package / Zone lists. Emits a dispositions array
    # (OK / DRIFT / NOT_FOUND per fund column, package, zone) that the
    # gap_report.json + Inbox banner surface to the reviewer.
    dispositions = []
    try:
        import master_validation

        cells_q = rds.execute_statement(
            **common,
            sql=(
                "SELECT zone, package, column_name, value::text, "
                "       COALESCE(provenance::text, '{}') "
                "  FROM rate_cells WHERE period_id = :pid::uuid"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
        cells_for_val = []
        for r2 in cells_q.get("records") or []:
            cells_for_val.append(
                {
                    "zone": r2[0].get("stringValue") or "",
                    "package": r2[1].get("stringValue") or "",
                    "column_name": r2[2].get("stringValue") or "",
                    "value": r2[3].get("stringValue"),
                    "provenance": json.loads(r2[4].get("stringValue") or "{}"),
                }
            )
        dispositions = master_validation.validate_rate_period(local, cells_for_val)
        prior_cj["master_dispositions"] = dispositions
        prior_cj["master_disposition_summary"] = master_validation.summarize(
            dispositions
        )
        logger.info(
            "publisher: master validation — %d dispositions (%d OK / %d DRIFT / %d NOT_FOUND)",
            len(dispositions),
            prior_cj["master_disposition_summary"]["ok"],
            prior_cj["master_disposition_summary"]["drift"],
            prior_cj["master_disposition_summary"]["not_found"],
        )
    except Exception:  # pragma: no cover
        logger.exception("publisher: master validation failed (non-fatal)")

    # Generate gap_report.json + gap_report.md artifacts. The JSON is for
    # API consumers; the .md is what the reviewer downloads + reads.
    # Both live in the outputs bucket under the batch directory so the
    # UI's artifact-card slot can presign them.
    gap_report_key_json = None
    gap_report_key_md = None
    try:
        import boto3 as _boto3

        s3_out = _boto3.client("s3")
        # Per-period gap details from rate_cells (the authoritative view)
        all_nulls_q = rds.execute_statement(
            **common,
            sql=(
                "SELECT zone, package, column_name "
                "  FROM rate_cells "
                " WHERE period_id = :pid::uuid AND value IS NULL "
                " ORDER BY zone, package, column_name"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
        all_nulls = [
            {
                "zone": r[0].get("stringValue") or "",
                "package": r[1].get("stringValue") or "",
                "column_name": r[2].get("stringValue") or "",
            }
            for r in (all_nulls_q.get("records") or [])
        ]
        # Reason map from still_null_gaps (kernel/LLM-emitted)
        reason_for = {}
        for g in still_null_gaps:
            if isinstance(g, (list, tuple)) and len(g) >= 4:
                reason_for[(g[0] or "", g[1] or "", g[2] or "")] = g[3]

        gap_payload = {
            "rate_period_id": period_id,
            "union_local": local,
            "start_date": start_date,
            "total_cells": null_now + (
                rds.execute_statement(
                    **common,
                    sql=(
                        "SELECT COUNT(*) FROM rate_cells "
                        "WHERE period_id = :pid::uuid AND value IS NOT NULL"
                    ),
                    parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
                )["records"][0][0].get("longValue", 0)
            ),
            "null_cells": null_now,
            "coverage_pct": None,  # filled below
            "sources": (prior_sf.get("uploads") if prior_sf else []) or [],
            "gaps": [
                {
                    "zone": n["zone"],
                    "package": n["package"],
                    "column": n["column_name"],
                    "reason": reason_for.get(
                        (n["zone"], n["package"], n["column_name"]),
                        "value not present in any provided document",
                    ),
                }
                for n in all_nulls
            ],
        }
        if gap_payload["total_cells"]:
            gap_payload["coverage_pct"] = round(
                100 * (gap_payload["total_cells"] - null_now)
                / gap_payload["total_cells"],
                1,
            )

        # Markdown — plain English. Group by reason for the reviewer.
        from collections import defaultdict as _dd

        by_reason: dict[str, list[dict[str, str]]] = _dd(list)
        for g in gap_payload["gaps"]:
            by_reason[g["reason"]].append(g)
        md = [
            f"# Gap Report — Local {local} · {start_date}",
            "",
            f"**Coverage:** {gap_payload['coverage_pct']}%"
            f" ({gap_payload['total_cells'] - null_now}/{gap_payload['total_cells']} filled, "
            f"{null_now} blank).",
            "",
            f"**Sources merged into this period:**",
        ]
        for u in gap_payload["sources"]:
            md.append(f"- `{u.rsplit('/', 1)[-1] if u else '?'}`")
        md.append("")
        if not gap_payload["gaps"]:
            md.append("## ✓ No blank cells")
            md.append("")
            md.append(
                "Every cell in this rate sheet is filled. "
                "Sources covered the full schema, or derived/zero-by-rule rules "
                "filled the remainder."
            )
        else:
            md.append(f"## {null_now} blank cells, grouped by reason")
            md.append("")
            for reason, items in sorted(
                by_reason.items(), key=lambda kv: -len(kv[1])
            ):
                md.append(f"### {len(items)} cell(s) — {reason}")
                md.append("")
                for g in items:
                    pkg = g["package"] or "*"
                    md.append(
                        f"- `{g['zone']} · {pkg} · {g['column']}`"
                    )
                md.append("")
        md_text = "\n".join(md)

        # S3 keys live alongside the source PDFs in the batch dir.
        batch_dir = source_pdf_key.rsplit("/", 1)[0] if "/" in source_pdf_key else ""
        if batch_dir:
            gap_report_key_json = f"{batch_dir}/gap_report.json"
            gap_report_key_md = f"{batch_dir}/gap_report.md"
            s3_out.put_object(
                Bucket=os.environ.get(
                    "OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs"
                ),
                Key=gap_report_key_json,
                Body=json.dumps(gap_payload, indent=2).encode("utf-8"),
                ContentType="application/json",
                ServerSideEncryption="aws:kms",
            )
            s3_out.put_object(
                Bucket=os.environ.get(
                    "OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs"
                ),
                Key=gap_report_key_md,
                Body=md_text.encode("utf-8"),
                ContentType="text/markdown",
                ServerSideEncryption="aws:kms",
            )
            logger.info(
                "publisher: wrote gap_report.json + .md (%d gaps) to %s",
                len(gap_payload["gaps"]), batch_dir,
            )
    except Exception:  # pragma: no cover
        logger.exception("publisher: gap_report generation failed (non-fatal)")

    # Generate a customer-format CSV + xlsx from the FINAL Aurora state
    # (post-derived, post-zero-by-rule, post-phantom-row-delete). This is
    # the artifact the reviewer downloads and diffs against the customer's
    # existing rate sheet — the canonical output of the whole pipeline.
    final_csv_key = None
    final_xlsx_key = None
    try:
        import csv as _csv
        import io as _io
        import boto3 as _boto3_x

        # Pull every cell for this period, ordered for a stable layout.
        cells_q = rds.execute_statement(
            **common,
            sql=(
                "SELECT zone, package, column_name, value::text "
                "  FROM rate_cells "
                " WHERE period_id = :pid::uuid "
                " ORDER BY zone, package, column_name"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
        rows_by_key: dict[tuple[str, str], dict[str, str]] = {}
        all_columns: list[str] = []
        seen_cols: set[str] = set()
        for r2 in cells_q.get("records") or []:
            zone = r2[0].get("stringValue") or ""
            pkg = r2[1].get("stringValue") or ""
            col = r2[2].get("stringValue") or ""
            val = r2[3].get("stringValue", "")
            rows_by_key.setdefault((zone, pkg), {})[col] = val
            if col not in seen_cols:
                seen_cols.add(col)
                all_columns.append(col)

        # Trade / Union Local from the unions row (drive the leading columns
        # so the output matches the customer's existing xlsx layout).
        meta_q = rds.execute_statement(
            **common,
            sql=(
                "SELECT u.trade, u.local FROM unions u "
                "  JOIN rate_periods rp ON rp.union_id = u.id "
                " WHERE rp.id = :pid::uuid"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
        trade_val, local_val = "", local or ""
        if meta_q.get("records"):
            r3 = meta_q["records"][0]
            trade_val = r3[0].get("stringValue") or ""
            local_val = r3[1].get("stringValue") or local_val

        header = [
            "Union Group", "Trade", "Union Local",
            "Zone", "Package", "Start Date", "End Date",
            *all_columns,
        ]
        end_date_str = end_date or ""
        buf = _io.StringIO()
        writer = _csv.writer(buf, lineterminator="\n")
        writer.writerow(header)
        for (zone, pkg), cells_dict in sorted(rows_by_key.items()):
            row_out = [
                "UA", trade_val, local_val,
                zone, pkg, start_date, end_date_str,
                *[cells_dict.get(col, "") for col in all_columns],
            ]
            writer.writerow(row_out)
        final_csv_text = buf.getvalue()

        batch_dir = source_pdf_key.rsplit("/", 1)[0] if "/" in source_pdf_key else ""
        if batch_dir:
            outputs_bucket = os.environ.get(
                "OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs"
            )
            final_csv_key = f"{batch_dir}/final_ratesheet.csv"
            s3_x = _boto3_x.client("s3")
            s3_x.put_object(
                Bucket=outputs_bucket,
                Key=final_csv_key,
                Body=final_csv_text.encode("utf-8"),
                ContentType="text/csv",
                ServerSideEncryption="aws:kms",
            )

            # Invoke xlsx-renderer Lambda (already in the repo with
            # openpyxl) to convert the CSV to xlsx, same column order.
            xlsx_renderer = os.environ.get(
                "XLSX_RENDERER_FN", "laboraid-dev-l7-fn-renderer-xlsx"
            )
            final_xlsx_key = f"{batch_dir}/final_ratesheet.xlsx"
            try:
                lc_x = _boto3_x.client("lambda")
                lc_x.invoke(
                    FunctionName=xlsx_renderer,
                    InvocationType="RequestResponse",
                    Payload=json.dumps({
                        "csv_s3_key": final_csv_key,
                        "out_s3_key": final_xlsx_key,
                        "local": local,
                    }).encode("utf-8"),
                )
            except Exception:
                logger.exception(
                    "publisher: xlsx-renderer invoke failed (csv still produced)"
                )
                final_xlsx_key = None
    except Exception:  # pragma: no cover
        logger.exception("publisher: final xlsx generation failed (non-fatal)")

    # Persist source_files.gap_report so ratesheet-get can find the artifact.
    if gap_report_key_json or final_xlsx_key or final_csv_key:
        try:
            sf_now_q = rds.execute_statement(
                **common,
                sql=(
                    "SELECT COALESCE(source_files::text, '{}') "
                    "  FROM rate_periods WHERE id = :pid::uuid"
                ),
                parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
            )
            sf_now = json.loads(
                sf_now_q["records"][0][0].get("stringValue") or "{}"
            )
        except Exception:
            sf_now = {}
        if gap_report_key_json:
            sf_now["gap_report"] = gap_report_key_json
        if gap_report_key_md:
            sf_now["gap_report_md"] = gap_report_key_md
        if final_csv_key:
            # The "output_csv" slot drives the "Canonical CSV" artifact card.
            # Override the per-run CSV with the final pivoted version — that's
            # what the reviewer wants to download (matches what's in Aurora).
            sf_now["output_csv"] = final_csv_key
        if final_xlsx_key:
            sf_now["output_xlsx"] = final_xlsx_key
        rds.execute_statement(
            **common,
            sql=(
                "UPDATE rate_periods SET source_files = :sf::jsonb "
                " WHERE id = :pid::uuid"
            ),
            parameters=[
                {"name": "pid", "value": {"stringValue": period_id}},
                {"name": "sf", "value": {"stringValue": json.dumps(sf_now)}},
            ],
        )

    rds.execute_statement(
        **common,
        sql=(
            "UPDATE rate_periods SET canonical_json = :cj::jsonb "
            " WHERE id = :pid::uuid"
        ),
        parameters=[
            {"name": "pid", "value": {"stringValue": period_id}},
            {"name": "cj", "value": {"stringValue": json.dumps(prior_cj)}},
        ],
    )
    logger.info(
        "publisher: period=%s null_cells=%d gaps_remaining=%d (approval_state=%s, unchanged)",
        period_id, null_now, len(still_null_gaps), prior_state,
    )

    return {
        "published": True,
        "merge_mode": merge_mode,
        "rate_period_id": period_id,
        "union_id": union_id,
        "local": local,
        "period": start_date,
        "method": method,
        "source_pdf": source_pdf_name,
        "cells_inserted": inserted,
        "cells_filled_null": filled_null,
        "cells_skipped_collision": skipped_collision,
        "rows_skipped_no_package": skipped_no_package,
        "null_cells_after": null_now,
        "gaps_remaining": len(still_null_gaps),
    }


def _normalize_date(s: str) -> str | None:
    """Coerce '1/1/26', '2026-01-01', '01-Jan-2026' → 'YYYY-MM-DD'.

    The kernel emits dates as M/D/YY without leading zeros; Aurora's DATE type
    only accepts ISO. Return None when unparseable.
    """
    if not s:
        return None
    s = s.strip()
    # Already ISO?
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    # M/D/YY or M/D/YYYY
    parts = s.replace("-", "/").split("/")
    if len(parts) == 3:
        try:
            m, d, y = (int(p) for p in parts)
            if y < 100:
                y += 2000
            return f"{y:04d}-{m:02d}-{d:02d}"
        except ValueError:
            return None
    return None


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        classify = event.get("classify") or {}
        # The orchestration stack sets result_path="$.extract" for ExtractViaAgent.
        # Older code paths and direct invocations may use $.extractviaagent or a
        # flat $.canonical — tolerate all three.
        agent_state = (
            event.get("extract")
            or event.get("extractviaagent")
            or {}
        )
        canonical = (
            agent_state.get("canonical")
            or event.get("canonical")
            or {}
        )
        if not classify or not canonical:
            raise RuntimeError(
                "publisher: SFN state missing classify or extractviaagent.canonical"
            )

        import boto3

        rds = boto3.client("rds-data")
        s3 = boto3.client("s3")
        common = {
            "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
            "secretArn": os.environ["AURORA_SECRET_ARN"],
            "database": "laboraid",
        }

        result = _publish(
            rds=rds,
            s3=s3,
            common=common,
            classify=classify,
            canonical=canonical,
        )
        logger.info("publisher: %s", json.dumps(result))

        # Back-fill the file_hashes row that upload-presign pre-wrote at
        # presign time (keyed by content_hash, value points at s3_key). We
        # don't know the hash here, but we can scan-by-s3_key with a GSI...
        # or simpler: skip the back-fill if we can't find a row to update.
        # The existing row (if any) keeps its `first_seen_at` and gets
        # period_id populated so future dedup lookups return the existing
        # period.
        #
        # Implementation: query the table by s3_key via filter expression.
        # The table is tiny so a scan is fine for the POC. Update by PK
        # (content_hash) using returned value.
        hashes_table = os.environ.get("FILE_HASHES_TABLE") or ""
        if hashes_table and result.get("published"):
            try:
                pdf_key = classify.get("s3_key") or ""
                if pdf_key:
                    table = boto3.resource("dynamodb").Table(hashes_table)
                    scan = table.scan(
                        FilterExpression="s3_key = :sk",
                        ExpressionAttributeValues={":sk": pdf_key},
                        ProjectionExpression="content_hash",
                    )
                    items = scan.get("Items") or []
                    for it in items:
                        ch = it.get("content_hash")
                        if not ch:
                            continue
                        table.update_item(
                            Key={"content_hash": ch},
                            UpdateExpression=(
                                "SET period_id = :pid, "
                                "    union_local = :loc, "
                                "    period_date = :pd"
                            ),
                            ExpressionAttributeValues={
                                ":pid": result.get("rate_period_id"),
                                ":loc": result.get("local"),
                                ":pd": result.get("period"),
                            },
                        )
                        logger.info(
                            "publisher: back-filled file_hashes for %s -> period %s",
                            ch[:12],
                            result.get("rate_period_id"),
                        )
            except Exception as e:  # pragma: no cover
                logger.warning("publisher: file_hashes back-fill failed: %s", e)

        # Emit a lifecycle event so anything observing the bus learns about it.
        bus_name = os.environ.get("ENGINE_BUS_NAME") or ""
        if bus_name and result.get("published"):
            try:
                boto3.client("events").put_events(
                    Entries=[
                        {
                            "Source": "laboraid.pipeline",
                            "DetailType": "laboraid.rate-sheet.created",
                            "Detail": json.dumps(result),
                            "EventBusName": bus_name,
                        }
                    ]
                )
            except Exception as e:  # pragma: no cover
                logger.warning("publisher: event emit failed: %s", e)
        return result
    except Exception:
        logger.exception("publisher failed")
        raise
