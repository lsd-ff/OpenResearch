import asyncio
import json
from dataclasses import dataclass

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from openresearch.agents.code_repair import dependency_constraint
from openresearch.config import Settings
from openresearch.errors import (
    ContractError,
    DependencyResolutionError,
    EnvironmentLockError,
    ModelError,
)
from openresearch.models.environment_preparation import (
    EnvironmentPreparationAttempt,
    EnvironmentPreparationDecision,
    EnvironmentPreparationReport,
)
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec
from openresearch.research.dependency_recovery import merge_requests, protected_requests
from openresearch.research.environment_repair import ResearchEnvironmentRepairer
from openresearch.research.runtime_selection import compatible_runtime, runtime_python
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.packages import PackageSource
from openresearch.sources.stdlib import is_stdlib
from openresearch.sources.wheels import WheelSource


@dataclass(frozen=True)
class PreparedEnvironment:
    report: EnvironmentPreparationReport
    requests: list[str]
    spec: EnvironmentSpec | None = None
    lock: EnvironmentLock | None = None


def preparation_summary(report: EnvironmentPreparationReport) -> str:
    return json.dumps(
        {
            "success": report.success,
            "runtime": report.runtime.model_dump() if report.runtime else None,
            "attempts": [
                {
                    "number": attempt.number,
                    "phase": attempt.phase,
                    "exit_code": attempt.exit_code,
                    "diagnostic": attempt.diagnostic[-2000:],
                    "error": attempt.error[-1000:],
                    "resolved_packages": len(attempt.resolved_requirements),
                }
                for attempt in report.attempts
            ],
            "decisions": [
                {
                    "proposal": decision.proposal.model_dump() if decision.proposal else None,
                    "accepted": decision.accepted,
                    "error": decision.error[-1000:],
                    "runtime_after": decision.runtime_after.model_dump()
                    if decision.runtime_after
                    else None,
                }
                for decision in report.decisions
            ],
        },
        ensure_ascii=False,
    )


class EnvironmentPreparation:
    """先保存真实准备错误，再有界调整依赖；只有实际解析/下载校验成功才交付锁。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.resolver = DependencyResolver()
        self.source = WheelSource(settings)
        self.packages = PackageSource(settings)
        self.repairer = ResearchEnvironmentRepairer(settings)
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)

    async def run(
        self,
        intent: str,
        requests: list[str],
        constraints: list[str],
        max_repairs: int,
        *,
        initial_spec: EnvironmentSpec | None = None,
        lock_wheels: bool = False,
        runtime: RuntimeBinding | None = None,
        allow_runtime_select: bool = False,
    ) -> PreparedEnvironment:
        limits = list(constraints)
        limits.extend(
            constraint
            for value in requests
            if (constraint := dependency_constraint(intent, Requirement(value).name))
        )
        limits = merge_requests([], [], limits)
        current = merge_requests(requests, [], limits)
        if runtime and not allow_runtime_select:
            raise ContractError("task is not authorized to select a research runtime")
        report = EnvironmentPreparationReport(constraints=limits, runtime=runtime)
        for number in range(1, max_repairs + 2):
            attempt = EnvironmentPreparationAttempt(
                number=number, phase="resolve", requests=current, runtime=report.runtime
            )
            report.attempts.append(attempt)
            try:
                python = runtime_python(self.settings, report.runtime)
                resolver = self.resolver.for_python(python)
                source = self.source.for_python(python)
                standard = [
                    Requirement(value).name
                    for value in current
                    if is_stdlib(Requirement(value).name)
                ]
                if standard:
                    raise EnvironmentLockError(
                        "Python standard library modules are not PyPI dependencies: "
                        + ", ".join(standard)
                    )
                # 第一轮锁定使用已有精确版本，避免无故换版本；修复后才重新解析完整闭包。
                spec = (
                    initial_spec
                    if number == 1 and initial_spec is not None
                    else await (
                        resolver.resolve(current)
                        if len(current) <= 32
                        else resolver.resolve_recovery(current)
                    )
                )
                attempt.exit_code = None if number == 1 and initial_spec is not None else 0
                attempt.resolved_requirements = spec.requirements
                versions = dict(item.split("==", 1) for item in spec.requirements)
                for constraint in limits:
                    requirement = Requirement(constraint)
                    version = versions.get(requirement.name)
                    if version is None or version not in requirement.specifier:
                        raise EnvironmentLockError(
                            "prepared environment violates an explicit dependency constraint: "
                            + constraint
                        )
                lock = None
                if lock_wheels:
                    attempt.phase, attempt.exit_code = "lock", None
                    lock, wheels = await source.fetch(spec)
                    for raw in wheels.values():
                        await asyncio.to_thread(self.blobs.write, raw)
                report.success = True
                return PreparedEnvironment(report, current, spec, lock)
            except ContractError as exc:
                attempt.error, attempt.diagnostic = str(exc)[-4000:], str(exc)[-16000:]
                if isinstance(exc, DependencyResolutionError):
                    attempt.exit_code, attempt.diagnostic = exc.exit_code, exc.diagnostic
                recoverable = isinstance(exc, (DependencyResolutionError, EnvironmentLockError))
                if not recoverable or number > max_repairs:
                    break
            if not await self._revise(
                intent,
                current,
                limits,
                report,
                allow_runtime_select=allow_runtime_select and runtime is None,
            ):
                break
            current = report.decisions[-1].after_requests
        return PreparedEnvironment(report, current)

    async def _revise(
        self,
        intent: str,
        current: list[str],
        constraints: list[str],
        report: EnvironmentPreparationReport,
        *,
        allow_runtime_select: bool = False,
    ) -> bool:
        attempt = report.attempts[-1]
        decision = EnvironmentPreparationDecision(before_requests=current)
        report.decisions.append(decision)
        try:
            constraints = list(constraints) + protected_requests(intent, current)
            decision.package_evidence = await self._package_evidence(current, constraints)
            # 只有真实失败后的公开 Requires-Python 证据允许换已注册 CPU 运行时。
            # 包版本、代码、验收规则和资源预算均不变；下一轮重新解析完整闭包。
            alternate = (
                compatible_runtime(
                    self.settings, intent, report.runtime, decision.package_evidence, current
                )
                if allow_runtime_select
                else None
            )
            if alternate is not None:
                decision.runtime_before, decision.runtime_after = report.runtime, alternate
                decision.after_requests, decision.accepted = list(current), True
                report.runtime = alternate
                return True
            diagnostic = (
                attempt.diagnostic
                + "\nVerified public package metadata (not installed):\n"
                + json.dumps(decision.package_evidence, ensure_ascii=False)
            )
            proposal = await self.repairer.diagnose(
                intent,
                attempt.phase,
                attempt.error,
                diagnostic,
                current,
                constraints,
                attempt.resolved_requirements,
                **(
                    {"python": runtime_python(self.settings, report.runtime)}
                    if report.runtime
                    else {}
                ),
            )
            decision.proposal = proposal
            if proposal.action == "stop":
                if proposal.requirements or proposal.remove_requirements:
                    raise ContractError("a stop proposal cannot change dependencies")
                return False
            if not proposal.requirements and not proposal.remove_requirements:
                raise ContractError("dependency repair must name an actual package")
            limits = list(constraints)
            limits.extend(
                constraint
                for value in proposal.requirements
                if (constraint := dependency_constraint(intent, Requirement(value).name))
            )
            for value in proposal.requirements:
                changed = Requirement(value)
                for limit in limits:
                    fixed = Requirement(limit)
                    if canonicalize_name(changed.name) != canonicalize_name(fixed.name):
                        continue
                    if any(
                        pin.operator == "=="
                        and "*" not in pin.version
                        and pin.version not in changed.specifier
                        for pin in fixed.specifier
                    ):
                        raise ContractError(
                            "dependency proposal violates an explicit fixed version"
                        )
            revised = merge_requests(
                current, proposal.requirements, limits, proposal.remove_requirements
            )
            if revised == current:
                raise ContractError("dependency repair did not change the requested environment")
            decision.after_requests, decision.accepted = revised, True
        except (ContractError, ModelError, ValueError) as exc:
            decision.error = str(exc)[-4000:]
        return decision.accepted

    async def _package_evidence(
        self, requests: list[str], constraints: list[str]
    ) -> list[dict[str, str]]:
        selected = {}
        candidates = [*constraints, *requests]
        # 用户精确约束及当前固定版本先于裸包名；最新发行版不代表失败的实际版本。
        candidates.sort(
            key=lambda value: (
                not any(
                    pin.operator == "==" and "*" not in pin.version
                    for pin in Requirement(value).specifier
                )
            )
        )
        for value in candidates:
            requirement = Requirement(value)
            name = canonicalize_name(requirement.name)
            if is_stdlib(name):
                continue
            if name in selected:
                continue
            pins = list(requirement.specifier)
            selected[name] = (
                pins[0].version if len(pins) == 1 and pins[0].operator == "==" else None
            )
            if len(selected) == 4:
                break
        try:
            async with asyncio.timeout(10):
                descriptions = await asyncio.gather(
                    *(self.packages.describe(name, version) for name, version in selected.items()),
                    return_exceptions=True,
                )
        except TimeoutError:
            return []
        # 查不到只表示本次来源不可用；不能据此断言该版本不存在。
        return [
            {
                key: str(value)
                for key, value in description.model_dump().items()
                if key in {"name", "version", "requires_python", "metadata_url", "metadata_sha256"}
            }
            for description in descriptions
            if not isinstance(description, BaseException)
        ]
