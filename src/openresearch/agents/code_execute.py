import json
from dataclasses import dataclass, replace

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
from openresearch.models.code_environment import CodeEnvironment
from openresearch.models.compute import ReproductionRequest
from openresearch.models.execution import ExecutionLease
from openresearch.models.recovery import CodeRecoveryReport, RecoveryDecision, RecoveryProposal
from openresearch.models.runtime import Task, TaskResult
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock
from openresearch.research.code_execution import CodeProgramRunner, ProgramOptions, ProgramOutcome
from openresearch.research.code_guards import code_digest
from openresearch.research.dependency_recovery import DependencyRecovery, DependencyUpdate
from openresearch.research.interpreter import InterpreterProbe, interpreter_probe
from openresearch.research.runtime_recovery import (
    RuntimeRecoveryPolicy,
    RuntimeResolutionError,
    runtime_change_count,
)
from openresearch.research.runtime_selection import runtime_python, task_runtime
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.session import SandboxSessions
from openresearch.sources.stdlib import is_stdlib
from openresearch.storage.repository import Repository


@dataclass(frozen=True)
class CodeState:
    code: str
    requests: list[str]
    lock: EnvironmentLock
    runtime: RuntimeBinding | None = None
    probe: InterpreterProbe | None = None
    constraints: tuple[str, ...] = ()


class CodeExecutor:
    def __init__(self, settings: Settings, repository: Repository, sandbox: Sandbox) -> None:
        self.settings = settings
        self.runner = CodeProgramRunner(settings, SandboxSessions(repository, sandbox))
        self.repairer = CodeRepairer(LLMClient(settings))
        self.dependencies = DependencyRecovery(settings)
        self.max_repairs = settings.code_max_repairs

    async def execute(
        self,
        task: Task,
        lease: ExecutionLease,
        code: str,
        goal: str | None = None,
        *,
        bootstrap: CodeEnvironment | None = None,
    ) -> TaskResult:
        if "sandbox.python" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to execute code")
        maximum = (
            self.max_repairs
            if {"code.repair", "environment.lock"}.intersection(task.contract.allowed_tools)
            else 0
        )
        intent = goal or task.description
        binding = task_runtime(self.settings, task, intent)
        compute = (
            ReproductionRequest.model_validate(task.inputs["reproduction"])
            if task.inputs.get("reproduction")
            else None
        )
        if compute and "runtime.probe" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to admit compute resources")
        report = CodeRecoveryReport(max_repairs=maximum, original_code_sha256=code_digest(code))
        probe = interpreter_probe(self.settings, self.runner.sessions, lease, task, intent)
        identity = await probe(binding) if probe else None
        if bootstrap and (
            bootstrap.runtime != binding
            or (identity and bootstrap.lock.interpreter and bootstrap.lock.interpreter != identity)
        ):
            raise ContractError("prepared code environment differs from the frozen runtime")
        state = CodeState(
            code,
            list(bootstrap.requests) if bootstrap else [],
            bootstrap.lock
            if bootstrap
            else EnvironmentLock(
                python=runtime_python(self.settings, binding), interpreter=identity
            ),
            binding,
            probe,
            bootstrap.constraints if bootstrap else (),
        )
        remaining = maximum
        while True:
            arguments: ProgramOptions = {"runtime": state.runtime} if state.runtime else {}
            if compute:
                arguments["reproduction"] = compute
            outcome = await self.runner.run(
                lease, state.code, state.lock, len(report.attempts) + 1, **arguments
            )
            report.attempts.append(outcome.attempt)
            error = outcome.attempt.error
            if not error or remaining <= 0:
                break
            recoverable = (outcome.attempt.phase == "install" and outcome.output is not None) or (
                outcome.attempt.phase == "execute" and RECOVERABLE.search(error)
            )
            if not recoverable:
                break
            decision = RecoveryDecision(accepted=False)
            report.decisions.append(decision)
            remaining -= 1
            module = missing_module(error)
            allow_dependencies = "environment.lock" in task.contract.allowed_tools and not (
                module and is_stdlib(module.split(".")[0])
            )
            try:
                proposal = await self.repairer.propose(
                    intent,
                    state.code,
                    error,
                    [wheel.requirement for wheel in state.lock.wheels] or state.requests,
                    allow_dependencies,
                    **({"python": state.lock.python} if state.lock.python != "3.12" else {}),
                )
                decision.proposal = proposal
                validate_proposal(
                    proposal,
                    state.code,
                    allow_dependencies,
                    allow_code="code.repair" in task.contract.allowed_tools,
                    python=state.lock.python,
                )
                if proposal.action == "stop":
                    break
                if proposal.action == "code":
                    state = replace(state, code=proposal.code or state.code)
                else:
                    if "environment.lock" not in task.contract.allowed_tools:
                        raise ContractError("task is not authorized to recover dependencies")
                    state = await self._dependencies(
                        task, intent, state, proposal, error, remaining, decision
                    )
                    remaining -= runtime_change_count(decision.preparation)
                decision.accepted = True
            except RuntimeResolutionError as exc:
                decision.preparation = exc.report
                decision.error = str(exc)
                report.error = f"repair unavailable: {exc}"
                break
            except (ContractError, ModelError, ValueError) as exc:
                decision.error = str(exc)
                report.error = f"repair unavailable: {exc}"
                break
        report.success = not report.attempts[-1].error
        report.error = report.attempts[-1].error + ("; " + report.error if report.error else "")
        report.runtime = state.runtime
        report.final_code_sha256 = code_digest(state.code)
        return code_result(task, report, outcome, state.code)

    async def _dependencies(
        self,
        task: Task,
        intent: str,
        state: CodeState,
        proposal: RecoveryProposal,
        error: str,
        remaining: int,
        decision: RecoveryDecision,
    ) -> CodeState:
        constraints = list(state.constraints) + [
            constraint
            for value in [*state.requests, *proposal.requirements]
            if (constraint := dependency_constraint(intent, Requirement(value).name))
        ]
        if "runtime.select" in task.contract.allowed_tools:
            recovered = await self.dependencies.recover(
                DependencyUpdate(
                    state.requests, proposal.requirements, constraints, missing_module(error)
                ),
                RuntimeRecoveryPolicy(
                    intent,
                    state.runtime,
                    state.lock.python,
                    remaining,
                    runtime_fixed=task.inputs.get("runtime") is not None,
                    probe=state.probe,
                ),
            )
            decision.preparation = recovered.preparation
            if runtime_change_count(recovered.preparation) > remaining:
                raise ContractError("runtime recovery exceeds the shared repair budget")
        else:
            recovered = await self.dependencies.resolve(
                state.requests,
                proposal.requirements,
                missing_module(error),
                constraints,
                probe=state.probe,
            )
        return replace(
            state,
            requests=recovered.requests,
            lock=recovered.lock,
            runtime=recovered.runtime or state.runtime,
        )


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
    repairs = sum(
        item.accepted + runtime_change_count(item.preparation) for item in report.decisions
    )
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
