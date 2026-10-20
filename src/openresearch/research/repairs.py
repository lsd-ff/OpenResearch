import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.errors import ContractError, ModelError
from openresearch.models.research import BenchmarkPreflightReport, RunAttempt
from openresearch.research.patches import apply_patch, cumulative_patch
from openresearch.research.runner import ExperimentInput, ExperimentOutput, ExperimentRunner


@dataclass(frozen=True)
class RepairOutcome:
    output: ExperimentOutput
    request: ExperimentInput
    rechecks: tuple[BenchmarkPreflightReport, ...] = ()


class RepairPipeline:
    def __init__(self, preparer: ResearchPreparer, runner: ExperimentRunner) -> None:
        self.preparer = preparer
        self.runner = runner

    async def run(
        self,
        intent: str,
        original: dict[str, bytes],
        request: ExperimentInput,
        *,
        recheck: Callable[[ExperimentInput], Awaitable[BenchmarkPreflightReport]] | None = None,
        allow_repairs: bool = True,
    ) -> RepairOutcome:
        request = replace(request, number=1)
        output = await self.runner.run(request)
        attempts = list(output.run.attempts)
        failure = attempts[-1]
        rechecks = []
        for _ in range(request.spec.max_repairs if allow_repairs else 0):
            if not failure.error:
                break
            try:
                request = await self._revise(intent, original, request, failure)
            except (ContractError, ModelError, ValueError) as exc:
                failure.error += f"; repair unavailable: {exc}"
                break
            if recheck is not None:
                # 任何修复都会使旧预检失效；预检失败先修预检，绝不直接重跑全量数据。
                checked = await recheck(request)
                rechecks.append(checked)
                failure = checked.run.attempts[-1]
                if checked.status != "passed" or failure.error:
                    continue
            request = replace(request, number=len(attempts) + 1)
            output = await self.runner.run(request)
            attempts.extend(output.run.attempts)
            failure = attempts[-1]
        output.run.attempts = attempts
        if rechecks and rechecks[-1].status != "passed":
            attempts[-1].error += "; revised benchmark preflight failed: " + failure.error[-2000:]
        return RepairOutcome(output, request, tuple(rechecks))

    async def _revise(
        self, intent: str, original: dict[str, bytes], request: ExperimentInput, failure: RunAttempt
    ) -> ExperimentInput:
        # 修复读取本次实际回溯，预检与正式运行共用原节点的有界修复次数。
        feedback = json.dumps(
            {
                "error": failure.error,
                "stdout": failure.stdout[-3000:],
                "stderr": failure.stderr[-5000:],
            },
            ensure_ascii=False,
        )
        proposal = await self.preparer.propose(intent, request.spec, request.files, feedback)
        patched = await asyncio.to_thread(apply_patch, request.files, proposal, request.spec)
        if patched == request.files:
            raise ContractError("repair did not change the failed program")
        patch = cumulative_patch(original, patched, request.spec)
        return replace(request, files=patched, patch=patch)
