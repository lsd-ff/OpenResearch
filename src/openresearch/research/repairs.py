import asyncio
import json
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
                failure = attempts[-1]
                # 修复必须看到本次真实回溯；仅有退出码无法定位导入、参数或数值断言错误。
                feedback = json.dumps(
                    {
                        "error": error,
                        "stdout": failure.stdout[-3000:],
                        "stderr": failure.stderr[-5000:],
                    },
                    ensure_ascii=False,
                )
                proposal = await self.preparer.propose(
                    intent, request.spec, request.files, feedback
                )
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
