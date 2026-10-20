"""解释器证据绑定当前租约；解析前探测，安装前再验证，禁止共享可变目标。"""

from collections.abc import Awaitable, Callable
from importlib import resources

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.execution import ExecutionLease
from openresearch.models.interpreters import InterpreterIdentity
from openresearch.models.runtime import Task
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.research.runtime_selection import (
    bind_runtime,
    compatible_runtime,
    intent_python,
    package_python_bounds,
    possible_python,
    runtime_change_forbidden,
    runtime_python,
)
from openresearch.sandbox.session import SandboxSessions

InterpreterProbe = Callable[[RuntimeBinding | None], Awaitable[InterpreterIdentity]]


class RuntimeInterpreterProbe:
    def __init__(
        self, settings: Settings, sessions: SandboxSessions, lease: ExecutionLease, intent: str
    ) -> None:
        self.settings, self.sessions, self.lease, self.intent = settings, sessions, lease, intent
        self.observed: dict[str | None, InterpreterIdentity] = {}

    async def __call__(self, runtime: RuntimeBinding | None) -> InterpreterIdentity:
        expected = runtime_python(self.settings, runtime)
        key = runtime.sha256 if runtime else None
        if key in self.observed:
            return self.observed[key]
        name = runtime.profile if runtime else None
        async with self.sessions.open(self.lease, name) as session:
            program = resources.files("openresearch.sandbox.programs").joinpath("interpreter.py")
            result = await session.execute(program.read_text(encoding="utf-8"))
            if result.exit_code:
                raise ContractError("runtime interpreter probe failed: " + result.stderr[-2000:])
            try:
                identity = InterpreterIdentity.model_validate_json(result.stdout)
                identity = identity.model_copy(
                    update={"container_image_id": await session.adapter.image_identity(session.id)}
                )
                identity = InterpreterIdentity.model_validate(identity.model_dump())
            except ValueError:
                raise ContractError(
                    "runtime interpreter probe returned unsupported metadata"
                ) from None
        if identity.python != expected:
            raise ContractError("actual interpreter differs from the registered Python runtime")
        requested = intent_python(self.intent)
        if requested is not None and identity.python_full_version not in requested:
            raise ContractError("actual interpreter conflicts with the requested Python version")
        self.observed[key] = identity
        return identity


def interpreter_probe(
    settings: Settings, sessions: SandboxSessions, lease: ExecutionLease, task: Task, intent: str
) -> InterpreterProbe | None:
    # 新模板显式授予只读版本探测权限；已持久化的旧任务不在执行时自动扩权。
    if "runtime.version" not in task.contract.allowed_tools:
        return None
    return RuntimeInterpreterProbe(settings, sessions, lease, intent)


async def compatible_interpreter(
    settings: Settings,
    intent: str,
    current: RuntimeBinding | None,
    evidence: list[dict[str, str]],
    requests: list[str],
    probe: InterpreterProbe | None,
) -> RuntimeBinding | None:
    if probe is None:
        return compatible_runtime(settings, intent, current, evidence, requests)
    bounds = package_python_bounds(evidence, requests)
    if not bounds or runtime_change_forbidden(intent):
        return None
    actual = await probe(current)
    if all(actual.python_full_version in bound for bound in bounds):
        return None
    versions = {current.profile if current else "": actual.python_full_version}
    requested = intent_python(intent)
    for name, profile in sorted(settings.runtime_profiles.items()):
        if profile.gpu or name == (current.profile if current else ""):
            continue
        if requested is not None and not possible_python(profile.python, requested):
            continue
        try:
            alternate = await probe(bind_runtime(settings, name))
        except ContractError:
            continue
        versions[name] = alternate.python_full_version
    return compatible_runtime(settings, intent, current, evidence, requests, versions=versions)
