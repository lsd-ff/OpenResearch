import asyncio
from typing import Protocol

from openresearch.checkpoint.feedback import excerpt
from openresearch.sandbox.core.models import CommandResult
from openresearch.watchdog.models import CheckerConfig, CheckReport, DiagnosisInput


class AdviceProvider(Protocol):
    async def suggest(self, diagnostic: DiagnosisInput) -> str: ...


def has_crash_signal(output: str) -> bool:
    lowered = output.lower()
    return any(
        signal in lowered
        for signal in (
            "segmentation fault",
            "core dumped",
            "bus error",
            "illegal instruction",
            "fatal python error",
        )
    )


class Checker:
    def __init__(
        self, advisor: AdviceProvider | None = None, config: CheckerConfig | None = None
    ) -> None:
        self.advisor = advisor
        self.config = config or CheckerConfig()

    async def audit(self, command: str, result: CommandResult, feedback: str = "") -> CheckReport:
        if result.timed_out:
            return CheckReport(False, "实际执行超过命令时限。")
        if result.output_limited:
            return CheckReport(False, "实际输出超过预算，回执仅保留已捕获部分。")
        if has_crash_signal(result.output):
            return CheckReport(
                False, "Execution output indicates a crash; inspect or restore the runtime."
            )
        if result.succeeded:
            return CheckReport(True)
        hint = self._hint(result)
        if self.advisor is None:
            return CheckReport(False, hint)
        diagnostic = DiagnosisInput(
            excerpt(command, 2000), excerpt(result.output, 2000), result.exit_code, feedback
        )
        errors: list[str] = []
        for attempt in range(1, self.config.max_advice_attempts + 1):
            try:
                async with asyncio.timeout(self.config.advice_timeout_seconds):
                    advice = (await self.advisor.suggest(diagnostic)).strip()
                if advice:
                    return CheckReport(False, excerpt(advice, 2000), attempt, tuple(errors))
                errors.append("EmptyAdvice")
            except Exception as exc:
                # 诊断失败不能变成执行成功；取消继承 BaseException，继续向上传播。
                errors.append(type(exc).__name__)
        return CheckReport(False, hint, self.config.max_advice_attempts, tuple(errors))

    @staticmethod
    def _hint(result: CommandResult) -> str:
        lowered = result.output.lower()
        if "permission denied" in lowered:
            return "检查容器内目标权限，或选择允许写入的路径。"
        if "command not found" in lowered:
            return "命令不存在；检查受管环境的依赖和镜像。"
        return f"实际命令失败，退出码 {result.exit_code}；请依据原始回执调整策略。"
