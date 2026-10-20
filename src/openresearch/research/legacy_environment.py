"""旧环境阶段交付可验证配置；每次安装和运行仍使用独立受管容器。"""

import ast
import asyncio
from dataclasses import dataclass

from packaging.requirements import Requirement

from openresearch.agents.code_repair import MODULE_PACKAGES, dependency_constraint
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.compute import ReproductionRequest
from openresearch.models.environment_preparation import (
    EnvironmentPreparationAttempt,
    EnvironmentPreparationReport,
)
from openresearch.models.execution import ExecutionLease
from openresearch.models.interpreters import InterpreterIdentity
from openresearch.models.legacy import LegacyEnvironmentState
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec
from openresearch.research.environment import install_environment
from openresearch.research.environment_preparation import (
    EnvironmentPreparation,
    PreparedEnvironment,
)
from openresearch.research.interpreter import RuntimeInterpreterProbe
from openresearch.research.reproduction_mode import decide_mode
from openresearch.research.runtime_selection import task_runtime
from openresearch.sandbox.session import SandboxSessions
from openresearch.sources.dependency_resolution import dependency_requests
from openresearch.sources.stdlib import is_stdlib
from openresearch.storage.files import ArtifactStore


def imported_requirements(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        raise ContractError(
            "declare dependencies explicitly when source imports cannot be parsed"
        ) from None
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(item.name.split(".")[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split(".")[0])
    return sorted({MODULE_PACKAGES.get(name, name) for name in names if not is_stdlib(name)})


@dataclass(frozen=True)
class EnvironmentContext:
    plan: Plan
    task: Task
    runtime: RuntimeBinding | None
    probe: RuntimeInterpreterProbe
    previous: LegacyEnvironmentState | None


class LegacyEnvironment:
    def __init__(self, settings: Settings, sessions: SandboxSessions, files: ArtifactStore) -> None:
        self.settings, self.sessions, self.files = settings, sessions, files
        self.preparation = EnvironmentPreparation(settings)

    async def execute(self, plan: Plan, task: Task, lease: ExecutionLease) -> TaskResult:
        if not {"environment.lock", "runtime.version"} <= set(task.contract.allowed_tools):
            raise ContractError("legacy environment task lacks its managed capabilities")
        runtime = task_runtime(self.settings, task, plan.user_intent)
        probe = RuntimeInterpreterProbe(self.settings, self.sessions, lease, plan.user_intent)
        identity = await probe(runtime)
        initial = task.inputs.get("initial_environment")
        previous = LegacyEnvironmentState.model_validate(initial) if initial else None
        if previous and (previous.lock.interpreter != identity or previous.runtime != runtime):
            raise ContractError("prepared runtime differs from its frozen interpreter")
        context = EnvironmentContext(plan, task, runtime, probe, previous)
        if task.inputs["mode"] == "prepare":
            return self._probe_result(context, identity)
        if task.inputs["mode"] == "restore":
            if previous is None or previous.phase == "probe":
                raise ContractError("restoration requires a locked environment receipt")
            return self._result(plan, previous)
        prepared = await self._lock(context)
        if not prepared.report.success or prepared.spec is None or prepared.lock is None:
            return TaskResult(
                success=False,
                error=prepared.report.attempts[-1].error,
                result="实际依赖准备失败，未交付可用运行时。",
                outputs={"environment_preparation_report": prepared.report.model_dump_json()},
            )
        state = LegacyEnvironmentState(
            requests=prepared.requests,
            spec=prepared.spec,
            lock=prepared.lock,
            runtime=prepared.report.runtime,
            constraints=prepared.report.constraints,
        )
        if task.inputs["mode"] == "install":
            await self._install(task, lease, state)
        result = self._result(plan, state)
        result.outputs["environment_preparation_report"] = prepared.report.model_dump_json()
        return result

    async def _requests(self, context: EnvironmentContext) -> tuple[list[str], list[str]]:
        task, plan = context.task, context.plan
        requests = task.inputs.get("requirements")
        if requests is None:
            code = task.inputs.get("code")
            if code is None and "generated_code" in plan.artifacts:
                code = await asyncio.to_thread(self.files.read, plan.artifacts["generated_code"])
            requests = dependency_requests(imported_requirements(code or ""))
        if not isinstance(requests, list) or any(not isinstance(value, str) for value in requests):
            raise ContractError("legacy environment requires declared package requirements")
        requests = dependency_requests(requests, limit=128)
        constraints = list(context.previous.constraints) if context.previous else []
        if task.inputs.get("explicit_requirements"):
            constraints.extend(requests)
        constraints.extend(
            constraint
            for value in requests
            if (constraint := dependency_constraint(plan.user_intent, Requirement(value).name))
        )
        return requests, constraints

    async def _lock(self, context: EnvironmentContext) -> PreparedEnvironment:
        requests, constraints = await self._requests(context)
        previous, task = context.previous, context.task
        return await self.preparation.run(
            context.plan.user_intent,
            requests,
            constraints,
            self.settings.code_max_repairs
            if "environment.repair" in task.contract.allowed_tools
            else 0,
            lock_wheels=True,
            runtime=context.runtime,
            initial_spec=previous.spec
            if previous and previous.phase != "probe" and set(requests) == set(previous.requests)
            else None,
            allow_runtime_select="runtime.select" in task.contract.allowed_tools,
            probe=context.probe,
        )

    async def _install(
        self, task: Task, lease: ExecutionLease, state: LegacyEnvironmentState
    ) -> None:
        async with self.sessions.open(
            lease, state.runtime.profile if state.runtime else None
        ) as session:
            if task.inputs.get("reproduction"):
                decision = decide_mode(
                    self.settings,
                    ReproductionRequest.model_validate(task.inputs["reproduction"]),
                    await session.adapter.resources(session.id),
                )
                if not decision.execution_allowed:
                    raise ContractError("actual resource admission refused dependency installation")
            state.installation = await install_environment(session, state.lock, self.settings)
            state.phase = "installed"

    def _probe_result(
        self, context: EnvironmentContext, identity: InterpreterIdentity
    ) -> TaskResult:
        declared = dependency_requests(context.task.inputs.get("requirements") or [], limit=128)
        state = LegacyEnvironmentState(
            phase="probe",
            requests=declared,
            constraints=declared,
            spec=EnvironmentSpec(interpreter=identity),
            lock=EnvironmentLock(python=identity.python, interpreter=identity),
            runtime=context.runtime,
        )
        result = self._result(context.plan, state)
        result.outputs["environment_preparation_report"] = EnvironmentPreparationReport(
            success=True,
            constraints=declared,
            runtime=context.runtime,
            attempts=[
                EnvironmentPreparationAttempt(
                    number=1, phase="probe", requests=declared, interpreter=identity, exit_code=0
                )
            ],
        ).model_dump_json()
        return result

    @staticmethod
    def _result(plan: Plan, state: LegacyEnvironmentState) -> TaskResult:
        return TaskResult(
            result="openresearch:" + plan.id,
            structured_data=state.model_dump_json(),
            outputs={"legacy_environment_state": state.model_dump_json()},
        )
