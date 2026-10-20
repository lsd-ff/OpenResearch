import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.errors import ContractError, ModelError
from openresearch.models.environment_repair import EnvironmentRepairDecision
from openresearch.models.research import BenchmarkPreflightReport, RunAttempt
from openresearch.research.environment_repair import ResearchEnvironmentRepairer, dependency_failure
from openresearch.research.gap import validate_gap_patch
from openresearch.research.patches import apply_patch, cumulative_patch
from openresearch.research.runner import ExperimentInput, ExperimentOutput, ExperimentRunner


@dataclass(frozen=True)
class RepairOutcome:
    output: ExperimentOutput
    request: ExperimentInput
    rechecks: tuple[BenchmarkPreflightReport, ...] = ()
    environment_changes: tuple[EnvironmentRepairDecision, ...] = ()


class RepairPipeline:
    def __init__(
        self,
        preparer: ResearchPreparer,
        runner: ExperimentRunner,
        environment_repairer: ResearchEnvironmentRepairer | None = None,
    ) -> None:
        self.preparer = preparer
        self.runner = runner
        self.environment_repairer = environment_repairer

    async def run(
        self,
        intent: str,
        original: dict[str, bytes],
        request: ExperimentInput,
        *,
        recheck: Callable[[ExperimentInput], Awaitable[BenchmarkPreflightReport]] | None = None,
        allow_repairs: bool = True,
        repair_limit: int | None = None,
    ) -> RepairOutcome:
        request = replace(request, number=1)
        output = await self.runner.run(request)
        attempts = list(output.run.attempts)
        failure = attempts[-1]
        rechecks = []
        environment_changes = []
        remaining = request.spec.max_repairs if allow_repairs else 0
        if repair_limit is not None:
            remaining = min(remaining, max(0, repair_limit))
        while remaining > 0:
            if not failure.error:
                break
            if failure.phase == "resources":
                break
            try:
                remaining -= 1
                if self.environment_repairer and dependency_failure(request, failure):
                    revision = await self.environment_repairer.revise(
                        intent,
                        request,
                        failure,
                        **({"runtime_budget": remaining} if request.allow_runtime_select else {}),
                    )
                    environment_changes.append(revision.decision)
                    if not revision.decision.accepted:
                        failure.error += "; environment repair unavailable: " + (
                            revision.decision.error or "no justified dependency change"
                        )
                        break
                    if revision.decision.repair_cost > remaining + 1:
                        revision.decision.accepted = False
                        revision.decision.error = (
                            "environment recovery exceeds the shared repair budget"
                        )
                        raise ContractError("environment recovery exceeds the shared repair budget")
                    remaining -= revision.decision.repair_cost - 1
                    request = replace(
                        request,
                        lock=revision.lock,
                        dependency_requests=revision.requests,
                        spec=request.spec.model_copy(update={"runtime": revision.runtime})
                        if revision.decision.runtime_after
                        else request.spec,
                    )
                else:
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
            details = "; ".join(item for item in (failure.error, failure.stderr) if item)
            attempts[-1].error += "; revised benchmark preflight failed: " + details[-4000:]
        return RepairOutcome(output, request, tuple(rechecks), tuple(environment_changes))

    async def _revise(
        self, intent: str, original: dict[str, bytes], request: ExperimentInput, failure: RunAttempt
    ) -> ExperimentInput:
        if not request.allow_patch_repair or failure.phase == "install":
            raise ContractError(
                "source changes cannot repair this environment or bypass permissions"
            )
        # 修复读取本次实际回溯，预检与正式运行共用原节点的有界修复次数。
        feedback = json.dumps(
            {
                "error": failure.error,
                "stdout": failure.stdout[-3000:],
                "stderr": failure.stderr[-5000:],
                "phase": failure.phase,
                "effective_environment": [wheel.requirement for wheel in request.lock.wheels],
                "runtime_python": request.lock.python,
            },
            ensure_ascii=False,
        )
        proposal = await self.preparer.propose(intent, request.spec, request.files, feedback)
        if request.preserve_checks:
            patched, _ = await asyncio.to_thread(
                validate_gap_patch, request.files, proposal, request.spec, request.lock.python
            )
        else:
            patched = await asyncio.to_thread(apply_patch, request.files, proposal, request.spec)
        if patched == request.files:
            raise ContractError("repair did not change the failed program")
        patch = cumulative_patch(original, patched, request.spec)
        return replace(request, files=patched, patch=patch)
