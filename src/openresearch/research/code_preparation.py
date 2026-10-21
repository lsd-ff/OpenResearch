import sys

from openresearch.agents.code_repair import CodeRepairer, validate_proposal
from openresearch.errors import ContractError, ModelError
from openresearch.models.recovery import CodeAttempt, CodeRecoveryReport, RecoveryDecision
from openresearch.models.runtime_profiles import RuntimeBinding, TargetPython
from openresearch.research.code_guards import code_digest, syntax_tree, validate_code_size


async def prepare_code(
    code: str,
    goal: str,
    repairer: CodeRepairer,
    max_repairs: int,
    *,
    python: TargetPython = "3.12",
    runtime: RuntimeBinding | None = None,
) -> tuple[str, CodeRecoveryReport]:
    report = CodeRecoveryReport(
        max_repairs=max_repairs,
        original_code_sha256=code_digest(code),
        runtime=runtime,
        syntax_validation="host_ast",
    )
    for number in range(1, max_repairs + 2):
        attempt = CodeAttempt(
            number=number, phase="syntax", code=code, code_sha256=code_digest(code)
        )
        report.attempts.append(attempt)
        try:
            if tuple(map(int, python.split("."))) > sys.version_info[:2]:
                # 宿主机不能判断更新语法；保留原文交给实际目标容器，不能误报语法已通过。
                validate_code_size(code)
                attempt.phase, report.syntax_validation = "syntax_pending", "deferred_to_sandbox"
            else:
                syntax_tree(code, python)
            report.success = True
            break
        except ContractError as exc:
            attempt.error = str(exc)
        if number > max_repairs:
            break
        decision = RecoveryDecision(accepted=False)
        report.decisions.append(decision)
        try:
            proposal = await repairer.propose(
                goal,
                code,
                attempt.error,
                [],
                False,
                **({"python": python} if python != "3.12" else {}),
            )
            decision.proposal = proposal
            validate_proposal(proposal, code, False, python=python)
            if proposal.action == "stop":
                break
            if proposal.action != "code" or proposal.code is None:
                raise ContractError("syntax repair cannot change dependency or runtime contracts")
            code, decision.accepted = proposal.code, True
        except (ContractError, ModelError, ValueError) as exc:
            decision.error = str(exc)
            report.error = f"repair unavailable: {exc}"
            break
    report.error = report.attempts[-1].error + ("; " + report.error if report.error else "")
    report.final_code_sha256 = code_digest(code)
    return code, report
