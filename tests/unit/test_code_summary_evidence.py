import json

from openresearch.models.recovery import CodeAttempt, CodeRecoveryReport
from openresearch.models.runtime import ExecutionResult
from openresearch.research.code_summaries import recovery_summary


def test_large_code_cannot_hide_original_failure_or_final_output_from_chat_summary():
    code = "# large source\n" + "x" * 20000
    report = CodeRecoveryReport(
        success=True,
        max_repairs=2,
        original_code_sha256="a" * 64,
        final_code_sha256="b" * 64,
        attempts=[
            CodeAttempt(
                number=1,
                phase="execute",
                code=code,
                code_sha256="a" * 64,
                output=ExecutionResult(exit_code=1, stderr="AttributeError: old API"),
                error="first actual failure",
                execution_requested=True,
            ),
            CodeAttempt(
                number=2,
                phase="outputs",
                code=code,
                code_sha256="b" * 64,
                output=ExecutionResult(exit_code=0, stdout="7"),
                execution_requested=True,
            ),
        ],
    )
    summary = recovery_summary(report)
    evidence = json.loads(summary)
    assert code not in summary and len(summary) < 4000
    assert [attempt["exit_code"] for attempt in evidence["attempts"]] == [1, 0]
    assert evidence["attempts"][0]["error"] == "first actual failure"
    assert evidence["attempts"][1]["stdout"] == "7"
