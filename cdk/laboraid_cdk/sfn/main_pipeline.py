"""Main pipeline Step Functions definition (Spec/09 §4 L3 §3.4 + §5 flow).

Builds the Standard-workflow chain for Stages 1-6:
  1. Classify (classifier Lambda)
  2. Extract (ExtractorAgent on AgentCore Runtime — invoked out-of-band; modelled
     here as a wait/pass since AgentCore has no native SFN service integration)
  3. Validate (parallel checksum + range + confidence)
  4. Choice: all validators passed?
       yes -> 5. Render (parallel xlsx + csv + articles) -> 6. Publish (success)
       no  -> route to review queue
Every Lambda task has retries; a top-level catch routes failures to a Fail state.
"""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct


def _invoke(scope: Construct, cid: str, fn: lambda_.IFunction) -> tasks.LambdaInvoke:
    task = tasks.LambdaInvoke(
        scope,
        cid,
        lambda_function=fn,
        payload_response_only=True,
        result_path=f"$.{cid.lower()}",
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
) -> sfn.IChainable:
    """Construct the pipeline chain. Tasks are created under ``scope``."""
    classify = _invoke(scope, "Classify", classifier)

    # Stage 2 — extraction runs on the ExtractorAgent (AgentCore Runtime). There
    # is no native SFN->AgentCore integration in the POC, so this is a wait point;
    # the agent writes canonical rows that the validators consume.
    extract = sfn.Pass(scope, "ExtractViaAgent", comment="ExtractorAgent on AgentCore Runtime")

    validate = sfn.Parallel(scope, "Validate", result_path="$.validation")
    validate.branch(_invoke(scope, "Checksum", checksum))
    validate.branch(_invoke(scope, "Range", range_fn))
    validate.branch(_invoke(scope, "Confidence", confidence))

    render = sfn.Parallel(scope, "Render", result_path="$.render")
    render.branch(_invoke(scope, "RenderXlsx", xlsx))
    render.branch(_invoke(scope, "RenderCsv", csv))
    render.branch(_invoke(scope, "RenderArticles", articles))

    publish = sfn.Succeed(scope, "Published")
    to_review = _invoke(scope, "RouteToReview", review_router).next(
        sfn.Succeed(scope, "AwaitingReview")
    )

    # Choice on the aggregate validator verdict (set on $.validation[*].passed).
    gate = (
        sfn.Choice(scope, "AllValidatorsPassed")
        .when(
            sfn.Condition.boolean_equals("$.validation[0].passed", True),
            render.next(publish),
        )
        .otherwise(to_review)
    )

    failed = sfn.Fail(scope, "PipelineFailed", error="PipelineError", cause="See execution input")
    classify.add_catch(failed, errors=["States.ALL"], result_path="$.error")

    return classify.next(extract).next(validate).next(gate)
