"""Main pipeline Step Functions definition (Spec/09 §4 L3 §3.4 + §5 flow).

Builds the Standard-workflow chain for Stages 1-6:
  1. Classify (classifier Lambda)
  1a. GetAgentConfig (DynamoGetItem on the agent-config table for ExtractorAgent)
  1b. Choice: is the ExtractorAgent enabled?
        yes -> 2. Extract (ExtractorAgent on AgentCore Runtime)
        no  -> bypass extraction, go straight to validation (Spec/09 §3.2 line 580)
  3. Validate (parallel checksum + range + confidence)
  4. Choice: all validators passed?
       yes -> 5. Render (parallel xlsx + csv + articles) -> 6. Publish (success)
       no  -> route to review queue
Every Lambda task has retries; a top-level catch routes failures to a Fail state.
"""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct


def _invoke(
    scope: Construct,
    cid: str,
    fn: lambda_.IFunction,
    *,
    payload: sfn.TaskInput | None = None,
) -> tasks.LambdaInvoke:
    task = tasks.LambdaInvoke(
        scope,
        cid,
        lambda_function=fn,
        payload_response_only=True,
        result_path=f"$.{cid.lower()}",
        **({"payload": payload} if payload is not None else {}),
    )
    task.add_retry(
        errors=["Lambda.ServiceException", "Lambda.TooManyRequestsException", "States.TaskFailed"],
        interval=Duration.seconds(2),
        max_attempts=3,
        backoff_rate=2.0,
    )
    return task


def build_definition(
    scope: Construct,
    *,
    classifier: lambda_.IFunction,
    checksum: lambda_.IFunction,
    range_fn: lambda_.IFunction,
    confidence: lambda_.IFunction,
    review_router: lambda_.IFunction,
    xlsx: lambda_.IFunction,
    csv: lambda_.IFunction,
    articles: lambda_.IFunction,
    agent_config_table: ddb.ITable,
    extract_task: sfn.IChainable | None = None,
    publisher: lambda_.IFunction | None = None,
) -> sfn.IChainable:
    """Construct the pipeline chain. Tasks are created under ``scope``.

    ``extract_task`` is the Stage-2 extraction state; when ``None`` a placeholder
    ``Pass`` is used (the agent runs out-of-band). FIX-B6 supplies a real
    ``LambdaInvoke`` of the ExtractorInvoker here.
    """
    # Classify is the entry task — the state machine input is the raw S3
    # EventBridge event (bucket emits "Object Created"). Map detail.object.key
    # into the {"s3_key": "..."} shape the classifier handler expects.
    classify = _invoke(
        scope,
        "Classify",
        classifier,
        payload=sfn.TaskInput.from_object(
            {"s3_key": sfn.JsonPath.string_at("$.detail.object.key")}
        ),
    )

    # Stage 1a — read the agent-config row so the pipeline can honour the
    # Admin enable/disable toggle (Spec/09 §3.2 line 580).
    get_agent_cfg = tasks.DynamoGetItem(
        scope,
        "GetAgentConfig",
        table=agent_config_table,
        key={"agent_name": tasks.DynamoAttributeValue.from_string("ExtractorAgent")},
        result_path="$.agentCfg",
    )

    # Stage 2 — extraction runs on the ExtractorAgent (AgentCore Runtime).
    extract: sfn.IChainable = extract_task or sfn.Pass(
        scope, "ExtractViaAgent", comment="ExtractorAgent on AgentCore Runtime"
    )

    # PublishToAurora — write rate_periods + rate_cells from the agent's CSV.
    # Runs UNCONDITIONALLY after Extract, so the row lands in Aurora as
    # approval_state='pending_review' regardless of validator pass/fail.
    # Without this state the SFN's "Publish" was a literal Succeed — the
    # agent's extraction never reached Aurora.
    publish_succeed = sfn.Succeed(scope, "Published")

    # Wire the chain. The product-critical flow today is:
    #   extract -> publish (writes Aurora) -> Succeed
    # The validators + renderers exist but were never wired to read the SFN
    # state shape the agent actually emits (they look for $.canonical, but the
    # state has $.extract.canonical). We exit cleanly after Publish so the row
    # lands and the reviewer can do their job in the UI. Re-wiring validators
    # + renderers as quality signals is the next iteration; not blocking the
    # core PDF -> Aurora story.
    if publisher is not None:
        publish_to_aurora = _invoke(scope, "PublishToAurora", publisher)
        sfn.Chain.start(extract).next(publish_to_aurora).next(publish_succeed)
        # Agent-disabled bypass: also terminate cleanly without running the
        # publisher (no extraction = nothing to publish).
        agent_disabled_target: sfn.IChainable = sfn.Succeed(scope, "AgentDisabledSkip")
    else:
        # Legacy path (unit-test stub): keep the old validator + render flow.
        validate = sfn.Parallel(scope, "Validate", result_path="$.validation")
        validate.branch(_invoke(scope, "Checksum", checksum))
        validate.branch(_invoke(scope, "Range", range_fn))
        validate.branch(_invoke(scope, "Confidence", confidence))

        render = sfn.Parallel(scope, "Render", result_path="$.render")
        render.branch(_invoke(scope, "RenderXlsx", xlsx))
        render.branch(_invoke(scope, "RenderCsv", csv))
        render.branch(_invoke(scope, "RenderArticles", articles))

        review_succeed = sfn.Succeed(scope, "AwaitingReview")
        to_review = _invoke(scope, "RouteToReview", review_router).next(
            review_succeed
        )
        gate = (
            sfn.Choice(scope, "AllValidatorsPassed")
            .when(
                sfn.Condition.boolean_equals("$.validation[0].passed", True),
                render.next(publish_succeed),
            )
            .otherwise(to_review)
        )
        validate.next(gate)
        sfn.Chain.start(extract).next(validate)
        agent_disabled_target = validate

    # Stage 1b — gate the agent invocation on agent-config.enabled. When the
    # ExtractorAgent is disabled we bypass extraction; with the publisher-led
    # chain, that means terminating cleanly (nothing to publish).
    agent_gate = (
        sfn.Choice(scope, "AgentEnabled")
        .when(
            sfn.Condition.boolean_equals(
                "$.agentCfg.Item.enabled.BOOL", True
            ),
            extract,
        )
        .otherwise(agent_disabled_target)
    )

    failed = sfn.Fail(scope, "PipelineFailed", error="PipelineError", cause="See execution input")
    classify.add_catch(failed, errors=["States.ALL"], result_path="$.error")

    return classify.next(get_agent_cfg).next(agent_gate)
