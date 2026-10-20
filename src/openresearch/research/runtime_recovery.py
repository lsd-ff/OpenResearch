"""执行恢复共用的版本解析器；运行时调整单独消耗原节点修复预算。"""

import asyncio
from dataclasses import dataclass

from openresearch.config import Settings
from openresearch.errors import ContractError, DependencyResolutionError, EnvironmentLockError
from openresearch.models.environment_preparation import (
    EnvironmentPreparationAttempt,
    EnvironmentPreparationDecision,
    EnvironmentPreparationReport,
)
from openresearch.models.runtime_profiles import RuntimeBinding, TargetPython
from openresearch.models.sources import EnvironmentLock
from openresearch.research.interpreter import InterpreterProbe, compatible_interpreter
from openresearch.research.package_evidence import package_evidence
from openresearch.research.runtime_selection import runtime_python
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.packages import PackageSource
from openresearch.sources.wheel_modules import wheel_sources
from openresearch.sources.wheels import WheelSource


@dataclass(frozen=True)
class RuntimeRecoveryPolicy:
    intent: str
    runtime: RuntimeBinding | None = None
    python: TargetPython = "3.12"
    maximum_changes: int = 0
    runtime_fixed: bool = False
    probe: InterpreterProbe | None = None


@dataclass(frozen=True)
class RuntimeResolution:
    lock: EnvironmentLock
    runtime: RuntimeBinding | None
    report: EnvironmentPreparationReport


class RuntimeResolutionError(ContractError):
    def __init__(self, report: EnvironmentPreparationReport, original: ContractError) -> None:
        self.report, self.original = report, original
        super().__init__(str(original))


def runtime_change_count(report: EnvironmentPreparationReport | None) -> int:
    return (
        sum(
            decision.accepted and decision.runtime_after is not None
            for decision in report.decisions
        )
        if report
        else 0
    )


class RuntimeResolver:
    def __init__(
        self,
        settings: Settings,
        resolver: DependencyResolver,
        source: WheelSource,
        packages: PackageSource,
    ) -> None:
        self.settings, self.resolver, self.source, self.packages = (
            settings,
            resolver,
            source,
            packages,
        )
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)

    async def resolve(
        self,
        requests: list[str],
        constraints: list[str],
        policy: RuntimeRecoveryPolicy,
        missing: str | None = None,
    ) -> RuntimeResolution:
        if not 0 <= policy.maximum_changes <= 2:
            raise ContractError("runtime recovery exceeds the node repair budget")
        if policy.runtime is None and policy.python != "3.12" and policy.maximum_changes:
            raise ContractError("a non-default recovery target requires a runtime binding")
        report = EnvironmentPreparationReport(constraints=constraints, runtime=policy.runtime)
        for number in range(1, policy.maximum_changes + 2):
            attempt = EnvironmentPreparationAttempt(
                number=number,
                phase="resolve",
                requests=requests,
                runtime=report.runtime,
            )
            report.attempts.append(attempt)
            try:
                python = (
                    runtime_python(self.settings, report.runtime)
                    if report.runtime
                    else policy.python
                )
                resolver = self.resolver.for_python(python)
                source = self.source.for_python(python)
                if policy.probe:
                    attempt.phase = "probe"
                    attempt.interpreter = await policy.probe(report.runtime)
                    if attempt.interpreter.python != python:
                        raise ContractError("probed interpreter differs from the recovery target")
                    resolver = resolver.for_interpreter(attempt.interpreter)
                    source = source.for_interpreter(attempt.interpreter)
                attempt.phase = "resolve"
                spec = await (
                    resolver.resolve(requests)
                    if len(requests) <= 32
                    else resolver.resolve_recovery(requests)
                )
                attempt.exit_code, attempt.resolved_requirements = 0, spec.requirements
                attempt.phase, attempt.exit_code = "lock", None
                lock, wheels = await source.fetch(spec)
                if missing:
                    await asyncio.to_thread(wheel_sources, wheels, [missing])
                for raw in wheels.values():
                    await asyncio.to_thread(self.blobs.write, raw)
                report.success = True
                return RuntimeResolution(lock, report.runtime, report)
            except ContractError as exc:
                attempt.error, attempt.diagnostic = str(exc)[-4000:], str(exc)[-16000:]
                if isinstance(exc, DependencyResolutionError):
                    attempt.exit_code, attempt.diagnostic = exc.exit_code, exc.diagnostic
                if (
                    not isinstance(exc, (DependencyResolutionError, EnvironmentLockError))
                    or number > policy.maximum_changes
                    or policy.runtime_fixed
                    or not await self._revise(requests, constraints, policy, report)
                ):
                    raise RuntimeResolutionError(report, exc) from exc
        raise AssertionError("bounded runtime resolution did not terminate")

    async def _revise(
        self,
        requests: list[str],
        constraints: list[str],
        policy: RuntimeRecoveryPolicy,
        report: EnvironmentPreparationReport,
    ) -> bool:
        decision = EnvironmentPreparationDecision(before_requests=requests)
        report.decisions.append(decision)
        try:
            decision.package_evidence = await package_evidence(self.packages, requests, constraints)
            alternate = await compatible_interpreter(
                self.settings,
                policy.intent,
                report.runtime,
                decision.package_evidence,
                requests,
                policy.probe,
            )
            if alternate is None:
                decision.error = (
                    "no registered compatible CPU runtime satisfies the preserved constraints"
                )
                return False
            decision.runtime_before, decision.runtime_after = report.runtime, alternate
            decision.after_requests, decision.accepted = requests, True
            report.runtime = alternate
            return True
        except (ContractError, ValueError) as exc:
            decision.error = str(exc)[-4000:]
            return False
