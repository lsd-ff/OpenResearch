import json

from packaging.requirements import Requirement

from openresearch.agents.code_repair import (
    RECOVERABLE,
    CodeRepairer,
    dependency_constraint,
    missing_module,
    validate_proposal,
)
from openresearch.config import Settings
from openresearch.errors import ContractError, ModelError
from openresearch.llm.client import LLMClient
from openresearch.models.execution import ExecutionLease
from openresearch.models.recovery import CodeRecoveryReport, RecoveryDecision
from openresearch.models.runtime import Task, TaskResult
from openresearch.models.sources import EnvironmentLock
from openresearch.research.code_execution import CodeProgramRunner, ProgramOutcome
from openresearch.research.code_guards import code_digest
from openresearch.research.dependency_recovery import DependencyRecovery
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.session import SandboxSessions
from openresearch.sources.stdlib import is_stdlib
from openresearch.storage.repository import Repository


class CodeExecutor:
    def __init__(self, settings: Settings, repository: Repository, sandbox: Sandbox) -> None:
        self.runner = CodeProgramRunner(settings, SandboxSessions(repository, sandbox))
        self.repairer = CodeRepairer(LLMClient(settings))
        self.dependencies = DependencyRecovery(settings)
        self.max_repairs = settings.code_max_repairs

    async def execute(
        self, task: Task, lease: ExecutionLease, code: str, goal: str | None = None
    ) -> TaskResult:
        if "sandbox.python" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to execute code")
        maximum = (
            self.max_repairs
            if {"code.repair", "environment.lock"}.intersection(task.contract.allowed_tools)
            else 0
        )
        report = CodeRecoveryReport(max_repairs=maximum, original_code_sha256=code_digest(code))
        requests: list[str] = []
        lock = EnvironmentLock()
        for number in range(1, maximum + 2):
            outcome = await self.runner.run(lease, code, lock, number)
            report.attempts.append(outcome.attempt)
            error = outcome.attempt.error
            if not error or number > maximum:
                break
            recoverable = (outcome.attempt.phase == "install" and outcome.output is not None) or (
                outcome.attempt.phase == "execute" and RECOVERABLE.search(error)
            )
            if not recoverable:
                break
            decision = RecoveryDecision(accepted=False)
            report.decisions.append(decision)
            module = missing_module(error)
            allow_dependencies = "environment.lock" in task.contract.allowed_tools and not (
                module and is_stdlib(module.split(".")[0])
            )
            try:
                proposal = await self.repairer.propose(
                    goal or task.description,
                    code,
                    error,
                    [wheel.requirement for wheel in lock.wheels] or requests,
                    allow_dependencies,
                )
                decision.proposal = proposal
                validate_proposal(
                    proposal,
                    code,
                    allow_dependencies,
                    allow_code="code.repair" in task.contract.allowed_tools,
                )
                if proposal.action == "stop":
                    break
                if proposal.action == "code":
                    code = proposal.code or code
                else:
                    if "environment.lock" not in task.contract.allowed_tools:
                        raise ContractError("task is not authorized to recover dependencies")
                    environment = await self.dependencies.resolve(
                        requests,
                        proposal.requirements,
                        missing_module(error),
                        constraints=[
                            constraint
                            for value in [*requests, *proposal.requirements]
                            if (
                                constraint := dependency_constraint(
                                    goal or task.description, Requirement(value).name
                                )
                            )
                        ],
                    )
                    requests, lock = environment.requests, environment.lock
                decision.accepted = True
            except (ContractError, ModelError, ValueError) as exc:
                decision.error = str(exc)
                report.error = f"repair unavailable: {exc}"
                break
        report.success = not report.attempts[-1].error
        report.error = report.attempts[-1].error + ("; " + report.error if report.error else "")
        report.final_code_sha256 = code_digest(code)
        return code_result(task, report, outcome, code)


def code_result(
    task: Task, report: CodeRecoveryReport, outcome: ProgramOutcome, code: str
) -> TaskResult:
    metrics = (
        outcome.output.model_dump_json()
        if outcome.output
        else json.dumps(
            {
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "execution_requested": outcome.attempt.execution_requested,
                "error": report.error,
            }
        )
    )
    outputs = {
        "run_metrics": metrics,
        "execution_files": outcome.files.model_dump_json(),
        "executed_code": code,
        "code_recovery_report": report.model_dump_json(),
    }
    if not report.success:
        outputs.pop("execution_files")
    stdout = outcome.output.stdout if outcome.output and outcome.attempt.phase != "install" else ""
    repairs = sum(item.accepted for item in report.decisions)
    note = f"程序运行/安装尝试 {len(report.attempts)} 次，采用 {repairs} 次修复。"
    return TaskResult(
        success=report.success,
        error=report.error,
        code=code,
        result=stdout[:4096] if stdout else note,
        structured_data=report.model_dump_json(),
        image_base64=outcome.files.preview(),
        outputs={key: value for key, value in outputs.items() if key in task.output_artifacts},
    )
