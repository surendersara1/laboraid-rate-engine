"""Main pipeline Step Functions definition — objective-driven synthesizer.

Three sequential stages (replaces the old per-doc classify→extract→validate→
render→publish chain):

  1. Plan         — batch-planner: classify + order the uploaded PDFs, resolve
                    the union + target rate period.
  2. Synthesize   — synthesizer: Claude Opus 4.5 reads ALL docs against the
                    union's Aurora profile → rate sheet (CSV/XLSX/JSON). Auto-
                    onboards an unknown union from its CBA.
  3. SynthPublish — synth-publish: clean-replace write to Aurora (cohorts in
                    rate_cells.dimensions), record source-PDF lineage.

Each stage passes its Lambda response straight to the next (payload_response_only)
and has retries; a top-level catch routes failures to a Fail state.
"""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

_RETRY = dict(
    errors=["Lambda.ServiceException", "Lambda.TooManyRequestsException", "States.TaskFailed"],
    interval=Duration.seconds(5),
    max_attempts=2,
    backoff_rate=2.0,
)


def build_definition(
    scope: Construct,
    *,
    batch_planner: lambda_.IFunction,
    synthesizer: lambda_.IFunction,
    synth_publish: lambda_.IFunction,
) -> sfn.IChainable:
    """Construct the Plan → Synthesize → SynthPublish chain under ``scope``.

    The state machine input is the batch payload
    ``{batch_id, batch_period, files:[{s3_key, filename}, ...]}`` posted by the
    batch-process API. Each task's Lambda response becomes the next task's input.
    """
    failed = sfn.Fail(scope, "PipelineFailed", cause="Rate-sheet pipeline failed")

    def _task(cid: str, fn: lambda_.IFunction) -> tasks.LambdaInvoke:
        t = tasks.LambdaInvoke(scope, cid, lambda_function=fn, payload_response_only=True)
        t.add_retry(**_RETRY)
        t.add_catch(failed, errors=["States.ALL"])
        return t

    plan = _task("Plan", batch_planner)
    synthesize = _task("Synthesize", synthesizer)
    synth_publish_task = _task("SynthPublish", synth_publish)
    published = sfn.Succeed(scope, "Published")

    return plan.next(synthesize).next(synth_publish_task).next(published)
