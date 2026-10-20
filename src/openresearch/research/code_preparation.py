from openresearch.agents.code_repair import CodeRepairer, validate_proposal
from openresearch.errors import ContractError, ModelError
from openresearch.models.recovery import CodeAttempt, CodeRecoveryReport, RecoveryDecision
from openresearch.research.code_guards import code_digest, syntax_tree


async def prepare_code(
    code: str, goal: str, repairer: CodeRepairer, max_repairs: int
) -> tuple[str, CodeRecoveryReport]:
    report = CodeRecoveryReport(max_repairs=max_repairs, original_code_sha256=code_digest(code))
    for number in range(1, max_repairs + 2):
        attempt = CodeAttempt(
            number=number, phase="syntax", code=code, code_sha256=code_digest(code)
        )
        report.attempts.append(attempt)
        try:
            syntax_tree(code)
            report.success = True
            break
        except ContractError as exc:
            attempt.error = str(exc)
        if number > max_repairs:
            break
        decision = RecoveryDecision(accepted=False)
        report.decisions.append(decision)
        try:
            proposal = await repairer.propose(goal, code, attempt.error, [], False)
            decision.proposal = proposal
            validate_proposal(proposal, code, False)
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
