import asyncio
from dataclasses import dataclass, replace

from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.errors import ContractError, ModelError
from openresearch.research.patches import apply_patch, cumulative_patch
from openresearch.research.runner import ExperimentInput, ExperimentOutput, ExperimentRunner


@dataclass(frozen=True)
class RepairOutcome:
    output: ExperimentOutput
    request: ExperimentInput


class RepairPipeline:
    def __init__(self, preparer: ResearchPreparer, runner: ExperimentRunner) -> None:
        self.preparer = preparer
        self.runner = runner

    async def run(
        self, intent: str, original: dict[str, bytes], request: ExperimentInput
    ) -> RepairOutcome:
        attempts = []
        for number in range(1, request.spec.max_repairs + 2):
            request = replace(request, number=number)
            output = await self.runner.run(request)
            attempts.extend(output.run.attempts)
            output.run.attempts = attempts
            error = attempts[-1].error
            if not error or number > request.spec.max_repairs:
                return RepairOutcome(output, request)
            try:
                proposal = await self.preparer.propose(intent, request.spec, request.files, error)
                patched = await asyncio.to_thread(
                    apply_patch, request.files, proposal, request.spec
                )
                patch = cumulative_patch(original, patched, request.spec)
            except (ContractError, ModelError, ValueError) as exc:
                attempts[-1].error += f"; repair unavailable: {exc}"
                return RepairOutcome(output, request)
            # 验证成功后才切换工作区；下一次执行使用新容器，前一次不会污染下一次。
            request = replace(request, files=patched, patch=patch)
        raise AssertionError("bounded repair loop did not return")
