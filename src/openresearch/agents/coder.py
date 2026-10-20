import asyncio

from openresearch.agents.code import CodeAgent
from openresearch.agents.code_execute import CodeExecutor
from openresearch.agents.compute import ComputeAgent
from openresearch.agents.context import AgentContext
from openresearch.agents.environment import EnvironmentAgent
from openresearch.agents.framework_design import FrameworkDesigner
from openresearch.agents.research_design import ResearchDesigner
from openresearch.agents.research_execute import ResearchExecutor
from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.code_environment import CodeEnvironment
from openresearch.models.legacy import LegacyEnvironmentState
from openresearch.models.runtime import TaskResult
from openresearch.research.interpreter import interpreter_probe
from openresearch.research.legacy_environment import LegacyEnvironment
from openresearch.research.source_context import document_context
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.session import SandboxSessions
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository
from openresearch.tools.execution import ExecutionTool


class CoderAgent:
    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        self.files = files
        self.settings = settings
        self.sessions = SandboxSessions(repository, sandbox)
        self.legacy_environment = LegacyEnvironment(settings, self.sessions, files)
        self.code = CodeAgent(LLMClient(settings))
        self.execution = ExecutionTool(repository, sandbox, settings.max_output_bytes)
        self.code_execution = CodeExecutor(settings, repository, sandbox)
        self.compute = ComputeAgent(settings, SandboxSessions(repository, sandbox))
        self.environment = EnvironmentAgent(settings)
        self.frameworks = FrameworkDesigner(settings, files)
        self.designer = ResearchDesigner(settings, files)
        self.preparer = ResearchPreparer(settings, files)
        self.research_executor = ResearchExecutor(settings, repository, files, sandbox)

    async def execute(self, context: AgentContext) -> TaskResult:
        plan, task, lease = context.plan, context.task, context.lease
        probe = interpreter_probe(self.settings, self.sessions, lease, task, plan.user_intent)
        if task.type == "legacy_environment":
            return await self.legacy_environment.execute(plan, task, lease)
        if task.type == "runtime_prepare":
            return await self.compute.execute(task, lease)
        if task.type == "framework_design":
            return await self.frameworks.execute(plan, task, probe=probe)
        if task.type == "code_generate":
            sources = await asyncio.to_thread(document_context, plan, self.files)
            return await self.code.execute(task, sources, plan.user_intent)
        if task.type == "code_execute":
            code = await asyncio.to_thread(self.files.read, plan.artifacts["generated_code"])
            if "code_recovery_report" in task.output_artifacts:
                if task.inputs.get("legacy_environment"):
                    if "legacy_environment_state" not in task.required_artifacts:
                        raise ContractError(
                            "prepared environment must come from a declared ancestor"
                        )
                    state = LegacyEnvironmentState.model_validate_json(
                        await asyncio.to_thread(
                            self.files.read, plan.artifacts["legacy_environment_state"]
                        )
                    )
                    if state.phase == "probe":
                        raise ContractError(
                            "code execution requires a locked dependency environment"
                        )
                    return await self.code_execution.execute(
                        task,
                        lease,
                        code,
                        plan.user_intent,
                        bootstrap=CodeEnvironment(
                            state.lock,
                            tuple(state.requests),
                            state.runtime,
                            tuple(state.constraints),
                        ),
                    )
                return await self.code_execution.execute(task, lease, code, plan.user_intent)
            return await self.execution.execute(task, lease, code)
        if task.type == "environment_lock":
            return await self.environment.execute(task, plan, self.files, probe=probe)
        if task.type == "research_design":
            return await self.designer.execute(plan, task, probe=probe)
        if task.type == "research_prepare":
            return await self.preparer.execute(plan, task)
        if task.type == "benchmark_preflight":
            return await self.research_executor.preflight(plan, task, lease)
        if task.type == "research_execute":
            return await self.research_executor.execute(plan, task, lease)
        raise ContractError("unsupported coder agent task")
