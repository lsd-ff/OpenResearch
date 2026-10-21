import json

from openresearch.models.recovery import CodeRecoveryReport
from openresearch.research.environment_preparation import preparation_summary


def recovery_summary(report: CodeRecoveryReport) -> str:
    """给汇总模型实际历史，不让大段源码挤掉先前失败和最终退出码。"""
    return json.dumps(
        {
            "version": report.version,
            "success": report.success,
            "error": report.error,
            "runtime": report.runtime.model_dump() if report.runtime else None,
            "syntax_validation": report.syntax_validation,
            "original_code_sha256": report.original_code_sha256,
            "final_code_sha256": report.final_code_sha256,
            "attempts": [
                {
                    "number": attempt.number,
                    "phase": attempt.phase,
                    "error": attempt.error[-2000:],
                    "code_sha256": attempt.code_sha256,
                    "execution_requested": attempt.execution_requested,
                    "sandbox_id": attempt.sandbox_id,
                    "exit_code": attempt.output.exit_code if attempt.output else None,
                    "stdout": attempt.output.stdout[-2000:] if attempt.output else "",
                    "stderr": attempt.output.stderr[-2000:] if attempt.output else "",
                    "environment": attempt.environment.model_dump()
                    if attempt.environment
                    else None,
                    "runtime": attempt.runtime.model_dump() if attempt.runtime else None,
                    "reproduction": attempt.reproduction.model_dump()
                    if attempt.reproduction
                    else None,
                }
                for attempt in report.attempts
            ],
            "decisions": [
                {
                    "accepted": decision.accepted,
                    "error": decision.error,
                    "action": decision.proposal.action if decision.proposal else None,
                    "reason": decision.proposal.reason if decision.proposal else "",
                    "environment_preparation": json.loads(preparation_summary(decision.preparation))
                    if decision.preparation
                    else None,
                }
                for decision in report.decisions
            ],
        },
        ensure_ascii=False,
    )
