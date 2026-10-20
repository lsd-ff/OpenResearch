import asyncio

from openresearch.agents.code import CodeAgent
from openresearch.agents.code_execute import CodeExecutor
from openresearch.agents.context import AgentContext
from openresearch.agents.environment import EnvironmentAgent
from openresearch.agents.framework_design import FrameworkDesigner
from openresearch.agents.research_design import ResearchDesigner
from openresearch.agents.research_execute import ResearchExecutor
from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.runtime import TaskResult
from openresearch.research.source_context import document_context
from openresearch.sandbox.base import Sandbox
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository
from openresearch.tools.execution import ExecutionTool


class CoderAgent:
    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        self.files = files
        self.code = CodeAgent(LLMClient(settings))
        self.execution = ExecutionTool(repository, sandbox, settings.max_output_bytes)
        self.code_execution = CodeExecutor(settings, repository, sandbox)
        self.environment = EnvironmentAgent(settings)
        self.frameworks = FrameworkDesigner(settings, files)
        self.designer = ResearchDesigner(settings, files)
        self.preparer = ResearchPreparer(settings, files)
        self.research_executor = ResearchExecutor(settings, repository, files, sandbox)

    async def execute(self, context: AgentContext) -> TaskResult:
        plan, task, lease = context.plan, context.task, context.lease
        if task.type == "framework_design":
            return await self.frameworks.execute(plan, task)
        if task.type == "code_generate":
            sources = await asyncio.to_thread(document_context, plan, self.files)
            return await self.code.execute(task, sources, plan.user_intent)
        if task.type == "code_execute":
            code = await asyncio.to_thread(self.files.read, plan.artifacts["generated_code"])
            if "code_recovery_report" in task.output_artifacts:
                return await self.code_execution.execute(task, lease, code, plan.user_intent)
            return await self.execution.execute(task, lease, code)
        if task.type == "environment_lock":
            return await self.environment.execute(task, plan, self.files)
        if task.type == "research_design":
            return await self.designer.execute(plan, task)
        if task.type == "research_prepare":
            return await self.preparer.execute(plan, task)
        if task.type == "benchmark_preflight":
            return await self.research_executor.preflight(plan, task, lease)
        if task.type == "research_execute":
            return await self.research_executor.execute(plan, task, lease)
        raise ContractError("unsupported coder agent task")
